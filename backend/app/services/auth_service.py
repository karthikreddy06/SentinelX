from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limiter import rate_limiter
from app.core.security import (
    create_access_token,
    create_raw_refresh_token,
    hash_token,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.schemas.auth import TokenResponse
from app.services.audit_service import record_audit_entry


class AuthServiceError(Exception):
    pass


class AuthenticationError(AuthServiceError):
    pass


class AccountLockedError(AuthServiceError):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitError(AuthServiceError):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TokenRevokedOrExpiredError(AuthServiceError):
    pass


def login_user(
    db: Session,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenResponse:
    norm_email = email.lower().strip()
    lockout_key = f"login:lockout:{norm_email}"

    # 1. Check existing lockout
    is_locked, retry_after = rate_limiter.check_login_lockout(lockout_key)
    if is_locked:
        record_audit_entry(
            db=db,
            action="auth.login",
            outcome="failure",
            user_email=norm_email,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"reason": "account_temporarily_locked"},
        )
        raise AccountLockedError(
            f"Account temporarily locked due to repeated failed logins. Try again in {retry_after} seconds.",
            retry_after=retry_after,
        )

    # 2. General endpoint rate limit (e.g. 20 requests per minute per IP)
    ip_key = f"rate:login:{ip_address or 'unknown'}"
    is_limited, ip_retry = rate_limiter.check_rate_limit(ip_key, limit=30, window_seconds=60)
    if is_limited:
        raise RateLimitError(
            f"Rate limit exceeded. Try again in {ip_retry} seconds.",
            retry_after=ip_retry,
        )

    # 3. Lookup user
    user = db.execute(select(User).where(User.email == norm_email)).scalar_one_or_none()

    # 4. Verify credentials
    if not user or not user.is_active or not verify_password(password, user.hashed_password):
        # Record failure and check if lockout threshold reached
        is_now_locked, lock_seconds = rate_limiter.record_login_failure(
            key=lockout_key,
            max_attempts=settings.RATE_LIMIT_LOGIN_MAX_ATTEMPTS,
            window_seconds=settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS,
            lockout_seconds=settings.RATE_LIMIT_LOGIN_LOCKOUT_SECONDS,
        )
        record_audit_entry(
            db=db,
            action="auth.login",
            outcome="failure",
            user=user,
            user_email=norm_email,
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"reason": "invalid_credentials", "locked": is_now_locked},
        )
        if is_now_locked:
            raise AccountLockedError(
                f"Account temporarily locked due to repeated failed logins. Try again in {lock_seconds} seconds.",
                retry_after=lock_seconds,
            )
        raise AuthenticationError("Invalid email or password.")

    # 5. Success
    rate_limiter.record_login_success(lockout_key)
    user.last_login_at = datetime.now(timezone.utc)

    # Issue tokens
    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
    )
    raw_refresh = create_raw_refresh_token()
    token_h = hash_token(raw_refresh)

    refresh_entity = RefreshToken(
        user_id=user.id,
        token_hash=token_h,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(refresh_entity)
    db.commit()

    record_audit_entry(
        db=db,
        action="auth.login",
        outcome="success",
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def refresh_user_tokens(
    db: Session,
    raw_refresh_token: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> TokenResponse:
    token_h = hash_token(raw_refresh_token)
    stored_token = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_h)
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if not stored_token or stored_token.revoked_at is not None or stored_token.expires_at <= now:
        record_audit_entry(
            db=db,
            action="auth.refresh",
            outcome="failure",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"reason": "token_invalid_revoked_or_expired"},
        )
        raise TokenRevokedOrExpiredError("Invalid, revoked, or expired refresh token.")

    user = stored_token.user
    if not user or not user.is_active:
        raise AuthenticationError("User is inactive or not found.")

    # Rotate refresh token: revoke current token
    stored_token.revoked_at = now

    # Issue replacement refresh token
    new_raw_refresh = create_raw_refresh_token()
    new_token_h = hash_token(new_raw_refresh)
    new_token_entity = RefreshToken(
        user_id=user.id,
        token_hash=new_token_h,
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(new_token_entity)

    # Issue replacement access token
    new_access_token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        role=user.role.value,
    )
    db.commit()

    record_audit_entry(
        db=db,
        action="auth.refresh",
        outcome="success",
        user=user,
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_raw_refresh,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def logout_user(
    db: Session,
    raw_refresh_token: str,
    user: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    token_h = hash_token(raw_refresh_token)
    stored_token = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_h)
    ).scalar_one_or_none()

    if stored_token and stored_token.revoked_at is None:
        stored_token.revoked_at = datetime.now(timezone.utc)
        db.commit()

    record_audit_entry(
        db=db,
        action="auth.logout",
        outcome="success",
        user=user or (stored_token.user if stored_token else None),
        ip_address=ip_address,
        user_agent=user_agent,
    )
