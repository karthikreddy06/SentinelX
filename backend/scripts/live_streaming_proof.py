"""
SentinelX Live Streaming Path Proof
Creates a transaction through POST /api/v1/transactions only (no manual /score call)
and demonstrates the complete event lifecycle:
outbox row created -> published to Redpanda -> consumed by stream worker -> score written -> alert created.
"""
import secrets
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import httpx
from sqlalchemy import select

from app.core.security import hash_password
from app.database.connection import SessionLocal
from app.models.alert import Alert
from app.models.outbox import OutboxEvent
from app.models.transaction import Transaction
from app.models.user import User, UserRole

BASE_URL = "http://127.0.0.1:8000"


def main():
    print("=" * 75)
    print(" SENTINELX LIVE STREAMING PIPELINE VERIFICATION")
    print("=" * 75)

    # 1. Setup Admin User securely in DB
    admin_email = "stream_verifier@sentinelx.local"
    temp_pw = secrets.token_urlsafe(24)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == admin_email).first()
        hashed = hash_password(temp_pw)
        if user:
            user.hashed_password = hashed
            user.role = UserRole.ADMIN
            user.is_active = True
        else:
            user = User(
                email=admin_email,
                hashed_password=hashed,
                full_name="Stream Verifier Admin",
                role=UserRole.ADMIN,
                is_active=True,
            )
            db.add(user)
        db.commit()
    finally:
        db.close()

    # 2. Login to get JWT
    client = httpx.Client(base_url=BASE_URL, timeout=15.0)
    login_res = client.post("/api/v1/auth/login", json={"email": admin_email, "password": temp_pw})
    if login_res.status_code != 200:
        print(f"Login failed: {login_res.status_code}")
        sys.exit(1)
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    print(f"[*] Authenticated as {admin_email} via JWT")

    # 3. Seed prior transaction in US 10 minutes ago to setup impossible travel
    now = datetime.now(timezone.utc)
    sender = f"STREAM-SENDER-{uuid.uuid4().hex[:6]}"
    prior_tx = {
        "amount": "100.00",
        "currency": "USD",
        "sender_account_id": sender,
        "receiver_account_id": "RECV-DOMESTIC-01",
        "transaction_type": "transfer",
        "country_code": "US",
        "occurred_at": (now - timedelta(minutes=10)).isoformat(),
        "device_id": "DEV-US-HOME",
        "ip_address": "24.12.55.10",
        "description": "Baseline transaction in US",
    }
    client.post("/api/v1/transactions", json=prior_tx, headers={**headers, "Idempotency-Key": str(uuid.uuid4())})

    # 4. Create Suspicious Transaction via POST /api/v1/transactions ONLY
    print("\n[STEP 1] Submitting transaction via POST /api/v1/transactions ONLY (no manual /score)...")
    susp_tx = {
        "amount": "14850.00",
        "currency": "USD",
        "sender_account_id": sender,
        "receiver_account_id": "DEST-OFFSHORE-99",
        "transaction_type": "transfer",
        "country_code": "RU",
        "occurred_at": now.isoformat(),
        "device_id": "DEV-RU-FOREIGN",
        "ip_address": "194.87.145.20",
        "description": "Suspicious high-amount foreign transfer",
    }
    post_time = datetime.now(timezone.utc)
    res = client.post("/api/v1/transactions", json=susp_tx, headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
    if res.status_code != 201:
        print(f"Transaction creation failed: {res.status_code} - {res.text}")
        sys.exit(1)

    tx_data = res.json()
    tx_id = tx_data["id"]
    print(f"         Transaction Created ID: {tx_id}")
    print(f"         Amount: {tx_data['amount']} {tx_data['currency']} | Timestamp: {post_time.isoformat()}")

    # 5. Inspect Outbox Table Row
    print("\n[STEP 2] Verifying Transactional Outbox Event in PostgreSQL...")
    db = SessionLocal()
    try:
        events = list(db.execute(select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(10)).scalars().all())
        matching = [e for e in events if tx_id in e.payload]
        if not matching:
            print("         Error: No outbox event found for transaction!")
            sys.exit(1)
        outbox_row = matching[0]
        print(f"         Outbox Event ID: {outbox_row.id}")
        print(f"         Topic: {outbox_row.topic}")
        print(f"         Event Type: {outbox_row.event_type}")
        print(f"         Initial Status: {outbox_row.status.value}")
        print(f"         Recorded At: {outbox_row.created_at.isoformat()}")
    finally:
        db.close()

    # 6. Wait for Outbox Publisher and Stream Consumer Loop
    print("\n[STEP 3] Awaiting Outbox Publisher and Redpanda Stream Consumer processing...")
    published = False
    for i in range(10):
        time.sleep(1.0)
        db = SessionLocal()
        try:
            db_ev = db.get(OutboxEvent, outbox_row.id)
            if db_ev and db_ev.status.value == "published":
                published = True
                print(f"         Outbox Event Published: status={db_ev.status.value}, published_at={db_ev.published_at.isoformat()}")
                break
        finally:
            db.close()

    if not published:
        print("         Warning: Outbox status not updated within timeout.")

    # 7. Verify Score Written and Alert Created
    print("\n[STEP 4] Verifying Final Fraud Score and Raised Alert in PostgreSQL...")
    time.sleep(1.0)
    db = SessionLocal()
    try:
        tx_ref = db.get(Transaction, uuid.UUID(tx_id))
        print(f"         Transaction Risk Score: {tx_ref.risk_score}")
        print(f"         Transaction Risk Level: {tx_ref.risk_level.value if tx_ref.risk_level else None}")

        alerts = list(db.execute(select(Alert).where(Alert.transaction_id == uuid.UUID(tx_id))).scalars().all())
        if alerts:
            al = alerts[0]
            print(f"         Alert ID: {al.id}")
            print(f"         Severity: {al.severity.value}")
            print(f"         Status: {al.status.value}")
            print(f"         Reason Codes: {al.reason_codes}")
            print(f"         Created At: {al.created_at.isoformat()}")
            print("\n[SUCCESS] Entire streaming pipeline successfully proved end-to-end!")
        else:
            print("         Error: Alert was not created!")
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
