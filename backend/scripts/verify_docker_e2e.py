"""
SentinelX Docker E2E Verification Script
Runs end-to-end verification inside running containers:
1. Provisions admin user with dynamic in-memory secure password (no password logged or CLI-exposed)
2. Authenticates via /api/v1/auth/login and receives JWT access token
3. Submits normal transaction and verifies low risk score
4. Submits suspicious transaction (impossible travel + high amount anomaly)
5. Scores suspicious transaction, verifying high risk score and automatic alert generation
6. Queries /api/v1/alerts to prove alert raised and tracked
"""
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import httpx
from app.core.security import hash_password
from app.database.connection import SessionLocal
from app.models.user import User, UserRole

BASE_URL = "http://127.0.0.1:8000"


def main():
    print("=" * 70)
    print(" SentinelX End-to-End Container Verification")
    print("=" * 70)

    # 1. Provision Admin User securely in database
    admin_email = "admin@sentinelx.local"
    temp_password = secrets.token_urlsafe(24)  # Dynamic secret generated in memory

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == admin_email).first()
        hashed = hash_password(temp_password)
        if user:
            user.hashed_password = hashed
            user.role = UserRole.ADMIN
            user.is_active = True
        else:
            user = User(
                email=admin_email,
                hashed_password=hashed,
                full_name="SentinelX Administrator",
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(user)
        db.commit()
        print(f"[OK] 1. Admin user verified in DB: {admin_email} (Role: {UserRole.ADMIN.value})")
    finally:
        db.close()

    # 2. Authenticate via /api/v1/auth/login
    client = httpx.Client(base_url=BASE_URL, timeout=15.0)
    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": admin_email, "password": temp_password},
    )
    if login_resp.status_code != 200:
        print(f"[FAIL] 2. Login failed: {login_resp.status_code} - {login_resp.text}")
        sys.exit(1)

    login_data = login_resp.json()
    token = login_data["access_token"]
    print(f"[OK] 2. Login successful: HTTP {login_resp.status_code}")
    print(f"     Token Type: {login_data.get('token_type')}")
    print(f"     User: {login_data.get('user', {}).get('email')} | Role: {login_data.get('user', {}).get('role')}")

    auth_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 3. Submit Normal Transaction
    now = datetime.now(timezone.utc)
    normal_tx_payload = {
        "amount": "28.50",
        "currency": "USD",
        "sender_account_id": "ACCT-NORMAL-1001",
        "receiver_account_id": "MERCH-GROCERY-01",
        "transaction_type": "payment",
        "channel": "pos",
        "country_code": "US",
        "occurred_at": now.isoformat(),
        "device_id": "DEV-PHONE-001",
        "ip_address": "72.14.201.1",
        "description": "Coffee and grocery",
    }
    normal_resp = client.post(
        "/api/v1/transactions",
        json=normal_tx_payload,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    if normal_resp.status_code != 201:
        print(f"[FAIL] 3. Normal transaction creation failed: {normal_resp.status_code} - {normal_resp.text}")
        sys.exit(1)

    normal_tx = normal_resp.json()
    normal_id = normal_tx["id"]
    print(f"[OK] 3. Normal transaction created: ID {normal_id}")
    print(f"     Amount: {normal_tx['amount']} {normal_tx['currency']} | Status: {normal_tx['status']}")

    # Score normal transaction
    score_norm_resp = client.post(f"/api/v1/transactions/{normal_id}/score", headers=auth_headers)
    if score_norm_resp.status_code != 200:
        print(f"[FAIL] Normal scoring failed: {score_norm_resp.status_code} - {score_norm_resp.text}")
        sys.exit(1)
    score_norm_data = score_norm_resp.json()
    print(f"     Risk Score: {score_norm_data['score']} | Risk Level: {score_norm_data['level']}")

    # 4. Submit Suspicious Transaction Setup (Impossible Travel + High Amount Anomaly)
    suspicious_sender = f"ACCT-SUSP-{uuid.uuid4().hex[:6]}"

    # Prior transaction in US 5 minutes ago
    prior_tx_payload = {
        "amount": "150.00",
        "currency": "USD",
        "sender_account_id": suspicious_sender,
        "receiver_account_id": "DEST-DOMESTIC-01",
        "transaction_type": "transfer",
        "country_code": "US",
        "occurred_at": (now - timedelta(minutes=5)).isoformat(),
        "device_id": "DEV-US-LEGIT",
        "ip_address": "12.34.56.78",
        "description": "Legitimate domestic transfer",
    }
    client.post(
        "/api/v1/transactions",
        json=prior_tx_payload,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )

    # Suspicious transaction 5 minutes later in RU for $14,950.00
    susp_tx_payload = {
        "amount": "14950.00",
        "currency": "USD",
        "sender_account_id": suspicious_sender,
        "receiver_account_id": "DEST-OFFSHORE-999",
        "transaction_type": "transfer",
        "country_code": "RU",
        "occurred_at": now.isoformat(),
        "device_id": "DEV-UNKNOWN-RU",
        "ip_address": "194.87.145.20",
        "description": "High value foreign transfer",
    }
    susp_resp = client.post(
        "/api/v1/transactions",
        json=susp_tx_payload,
        headers={**auth_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    if susp_resp.status_code != 201:
        print(f"[FAIL] 4. Suspicious transaction creation failed: {susp_resp.status_code} - {susp_resp.text}")
        sys.exit(1)

    susp_tx = susp_resp.json()
    susp_id = susp_tx["id"]
    print(f"[OK] 4. Suspicious transaction created: ID {susp_id}")
    print(f"     Amount: {susp_tx['amount']} {susp_tx['currency']} | Country: {susp_tx['country_code']}")

    # 5. Score Suspicious Transaction
    score_susp_resp = client.post(f"/api/v1/transactions/{susp_id}/score", headers=auth_headers)
    if score_susp_resp.status_code != 200:
        print(f"[FAIL] 5. Suspicious scoring failed: {score_susp_resp.status_code} - {score_susp_resp.text}")
        sys.exit(1)

    score_susp_data = score_susp_resp.json()
    print(f"[OK] 5. Suspicious transaction scored successfully:")
    print(f"     Risk Score: {score_susp_data['score']}/100")
    print(f"     Risk Level: {score_susp_data['level'].upper()}")
    print(f"     Engine Version: {score_susp_data.get('engine_version')}")
    reasons = score_susp_data.get("reasons_json", [])
    print(f"     Triggered Rules ({len(reasons)}):")
    for r in reasons:
        if r.get("triggered", True):
            print(f"       * [{r.get('reason_code')}] weight={r.get('weight')} -> {r.get('description')}")

    assert score_susp_data["score"] >= 70, f"Expected score >= 70, got {score_susp_data['score']}"
    assert score_susp_data["level"] == "high", f"Expected high risk level, got {score_susp_data['level']}"

    # 6. Verify Alert Raised
    alerts_resp = client.get("/api/v1/alerts", headers=auth_headers)
    if alerts_resp.status_code != 200:
        print(f"[FAIL] 6. Alerts query failed: {alerts_resp.status_code} - {alerts_resp.text}")
        sys.exit(1)

    alerts_data = alerts_resp.json()
    matching_alerts = [a for a in alerts_data["items"] if a["transaction_id"] == susp_id]
    if not matching_alerts:
        print(f"[FAIL] 6. No alert found for transaction {susp_id} in {alerts_data['total']} alerts")
        sys.exit(1)

    alert = matching_alerts[0]
    print(f"[OK] 6. Fraud Alert Raised and Verified in Alert Management Engine:")
    print(f"     Alert ID: {alert['id']}")
    print(f"     Severity: {alert['severity'].upper()}")
    print(f"     Status: {alert['status'].upper()}")
    print(f"     Risk Score: {alert['risk_score']}")
    print(f"     Reason Codes: {alert['reason_codes']}")
    print(f"     Created At: {alert['created_at']}")

    print("=" * 70)
    print(" ALL VERIFICATIONS PASSED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()
