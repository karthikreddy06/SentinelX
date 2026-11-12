from datetime import datetime, timedelta, timezone
import time
import uuid

import pytest

from app.core.config import settings
from app.core.rate_limiter import rate_limiter
from app.core.security import create_access_token


# === AUTHENTICATION TESTS ===

def test_login_success(client, create_test_user):
    user = create_test_user(email="login_user@test.local", password="MySecurePassword123!")

    res = client.post(
        "/api/v1/auth/login",
        json={"email": "login_user@test.local", "password": "MySecurePassword123!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert "password" not in data


def test_login_failure_invalid_password(client, create_test_user):
    create_test_user(email="fail_user@test.local", password="MySecurePassword123!")

    res = client.post(
        "/api/v1/auth/login",
        json={"email": "fail_user@test.local", "password": "WrongPassword!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_login_lockout_after_repeated_failures(client, create_test_user):
    create_test_user(email="lockout_user@test.local", password="MySecurePassword123!")

    # Perform 5 failed attempts
    for _ in range(5):
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "lockout_user@test.local", "password": "WrongPassword!"},
        )

    # 6th attempt should be locked out (429)
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "lockout_user@test.local", "password": "MySecurePassword123!"},
    )
    assert res.status_code == 429
    assert "temporarily locked" in res.json()["detail"]
    assert "Retry-After" in res.headers


def test_refresh_token_rotation_and_revocation(client, create_test_user):
    create_test_user(email="refresh_user@test.local", password="MySecurePassword123!")

    # 1. Login to get initial tokens
    login_res = client.post(
        "/api/v1/auth/login",
        json={"email": "refresh_user@test.local", "password": "MySecurePassword123!"},
    )
    initial_refresh = login_res.json()["refresh_token"]

    # 2. Refresh tokens -> rotation
    ref_res = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": initial_refresh},
    )
    assert ref_res.status_code == 200
    new_data = ref_res.json()
    new_refresh = new_data["refresh_token"]
    assert new_refresh != initial_refresh

    # 3. Replaying the old refresh token must fail with 401
    old_res = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": initial_refresh},
    )
    assert old_res.status_code == 401

    # 4. Logout revokes the active refresh token
    logout_res = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": new_refresh},
    )
    assert logout_res.status_code == 200

    # 5. Using revoked refresh token fails
    rev_res = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": new_refresh},
    )
    assert rev_res.status_code == 401


def test_get_current_user_me(client, investigator_user, investigator_headers):
    res = client.get("/api/v1/auth/me", headers=investigator_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == investigator_user.email
    assert data["role"] == "investigator"
    assert "password" not in data
    assert "hashed_password" not in data


def test_expired_token_rejected(client, investigator_user):
    # Create an expired token (10 minutes in the past)
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    import jwt

    payload = {
        "sub": str(investigator_user.id),
        "email": investigator_user.email,
        "role": investigator_user.role.value,
        "type": "access",
        "iat": past - timedelta(minutes=15),
        "exp": past,
    }
    expired_token = jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )

    res = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert res.status_code == 401


# === USER MANAGEMENT & RBAC TESTS ===

def test_admin_user_crud_endpoints(client, admin_headers, investigator_headers):
    # 1. Admin creates user -> 201
    new_user_payload = {
        "email": "new_analyst@test.local",
        "password": "Password123!",
        "full_name": "New Analyst",
        "role": "analyst",
    }
    r_create = client.post(
        "/api/v1/users", json=new_user_payload, headers=admin_headers
    )
    assert r_create.status_code == 201
    user_id = r_create.json()["id"]

    # 2. Duplicate email -> 409
    r_dup = client.post("/api/v1/users", json=new_user_payload, headers=admin_headers)
    assert r_dup.status_code == 409

    # 3. Non-admin cannot create user -> 403
    r_forbid = client.post(
        "/api/v1/users",
        json={**new_user_payload, "email": "other@test.local"},
        headers=investigator_headers,
    )
    assert r_forbid.status_code == 403

    # 4. Admin lists users -> 200
    r_list = client.get("/api/v1/users", headers=admin_headers)
    assert r_list.status_code == 200
    assert r_list.json()["total"] >= 1

    # 5. Admin updates user role -> 200
    r_patch = client.patch(
        f"/api/v1/users/{user_id}",
        json={"role": "investigator", "is_active": True},
        headers=admin_headers,
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["role"] == "investigator"


def test_rbac_transaction_endpoints(client, analyst_headers, investigator_headers):
    # 1. Analyst cannot create transactions -> 403
    payload = {
        "sender_account_id": "acc_1",
        "receiver_account_id": "acc_2",
        "amount": "100.00",
        "currency": "USD",
        "transaction_type": "transfer",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    r_analyst_post = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4()), **analyst_headers},
    )
    assert r_analyst_post.status_code == 403

    # 2. Investigator can create transaction -> 201
    r_inv_post = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4()), **investigator_headers},
    )
    assert r_inv_post.status_code == 201
    tx_id = r_inv_post.json()["id"]

    # 3. Analyst can READ transactions -> 200
    r_analyst_get = client.get(f"/api/v1/transactions/{tx_id}", headers=analyst_headers)
    assert r_analyst_get.status_code == 200

    r_analyst_list = client.get("/api/v1/transactions", headers=analyst_headers)
    assert r_analyst_list.status_code == 200

    # 4. Analyst cannot update transactions -> 403
    r_analyst_patch = client.patch(
        f"/api/v1/transactions/{tx_id}",
        json={"description": "Hacked"},
        headers=analyst_headers,
    )
    assert r_analyst_patch.status_code == 403

    # 5. Analyst cannot cancel transactions -> 403
    r_analyst_cancel = client.post(
        f"/api/v1/transactions/{tx_id}/cancel",
        headers=analyst_headers,
    )
    assert r_analyst_cancel.status_code == 403

    # 6. Unauthenticated request -> 401
    r_no_auth = client.get("/api/v1/transactions")
    assert r_no_auth.status_code == 401


# === AUDIT LOG TESTS ===

def test_audit_logs_recorded_and_queried(
    client, admin_headers, investigator_headers, create_test_user
):
    # 1. Login writes audit entry
    user = create_test_user(email="audit_check@test.local", password="Password123!")
    client.post(
        "/api/v1/auth/login",
        json={"email": "audit_check@test.local", "password": "Password123!"},
    )

    # 2. Admin queries audit logs -> 200
    r_logs = client.get("/api/v1/audit-logs?action=auth.login", headers=admin_headers)
    assert r_logs.status_code == 200
    body = r_logs.json()
    assert body["total"] >= 1
    assert any(item["action"] == "auth.login" for item in body["items"])

    # 3. Non-admin cannot query audit logs -> 403
    r_forbid = client.get("/api/v1/audit-logs", headers=investigator_headers)
    assert r_forbid.status_code == 403


# === MIDDLEWARE & HARDENING TESTS ===

def test_security_headers_and_request_id(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert "X-Request-ID" in res.headers
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["X-XSS-Protection"] == "1; mode=block"


# === JWT SECRET SAFETY TESTS ===

def test_validate_jwt_secret_enforcement():
    from app.core.config import Settings, validate_jwt_secret

    # 1. In DEBUG=True mode, defaults pass for local development convenience
    debug_cfg = Settings(DEBUG=True, JWT_SECRET_KEY="short-dev-secret", DATABASE_URL=settings.DATABASE_URL)
    validate_jwt_secret(debug_cfg)  # must not raise

    # 2. In DEBUG=False mode, empty secret raises ValueError
    prod_empty = Settings(DEBUG=False, JWT_SECRET_KEY="", DATABASE_URL=settings.DATABASE_URL)
    with pytest.raises(ValueError, match="JWT_SECRET_KEY must be configured"):
        validate_jwt_secret(prod_empty)

    # 3. In DEBUG=False mode, secret < 32 chars raises ValueError
    prod_short = Settings(DEBUG=False, JWT_SECRET_KEY="too-short-secret-key-12345", DATABASE_URL=settings.DATABASE_URL)
    with pytest.raises(ValueError, match="under 32 characters"):
        validate_jwt_secret(prod_short)

    # 4. In DEBUG=False mode, default placeholder secret raises ValueError
    prod_placeholder = Settings(
        DEBUG=False,
        JWT_SECRET_KEY="sentinelx-insecure-secret-key-change-in-production-12345",
        DATABASE_URL=settings.DATABASE_URL,
    )
    with pytest.raises(ValueError, match="known default placeholder"):
        validate_jwt_secret(prod_placeholder)

    # 5. Strong secret >= 32 characters passes in DEBUG=False mode
    prod_strong = Settings(
        DEBUG=False,
        JWT_SECRET_KEY="a-very-strong-cryptographically-random-secret-key-32chars!",
        DATABASE_URL=settings.DATABASE_URL,
    )
    validate_jwt_secret(prod_strong)  # must not raise

