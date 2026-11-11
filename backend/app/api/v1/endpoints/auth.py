from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_current_user
from app.database.connection import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshTokenRequest, TokenResponse
from app.schemas.user import UserResponse
from app.services.auth_service import (
    AccountLockedError,
    AuthenticationError,
    RateLimitError,
    TokenRevokedOrExpiredError,
    login_user,
    logout_user,
    refresh_user_tokens,
)

router = APIRouter()


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="User Login",
    description="Authenticate with email and password to receive JWT access and refresh tokens. Protected by rate limiting and lockout.",
    responses={
        200: {"description": "Authentication successful."},
        401: {"description": "Invalid email or password."},
        429: {"description": "Rate limit exceeded or account temporarily locked."},
    },
)
def login_endpoint(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        return login_user(
            db=db,
            email=payload.email,
            password=payload.password,
            ip_address=ip,
            user_agent=user_agent,
        )
    except AccountLockedError as exc:
        response.headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        )
    except RateLimitError as exc:
        response.headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        )
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh Tokens",
    description="Rotate and exchange a valid refresh token for a new access token and rotated refresh token.",
    responses={
        200: {"description": "Tokens successfully rotated."},
        401: {"description": "Invalid, expired, or revoked refresh token."},
    },
)
def refresh_endpoint(
    payload: RefreshTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        return refresh_user_tokens(
            db=db,
            raw_refresh_token=payload.refresh_token,
            ip_address=ip,
            user_agent=user_agent,
        )
    except (TokenRevokedOrExpiredError, AuthenticationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "/logout",
    summary="User Logout",
    description="Revoke the provided refresh token.",
    responses={
        200: {"description": "Logged out successfully."},
    },
)
def logout_endpoint(
    payload: RefreshTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    logout_user(
        db=db,
        raw_refresh_token=payload.refresh_token,
        ip_address=ip,
        user_agent=user_agent,
    )
    return {"message": "Logged out successfully."}


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get Current User Profile",
    description="Retrieve the profile of the currently authenticated user.",
)
def me_endpoint(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    return UserResponse.model_validate(current_user)
