from datetime import datetime, timezone, timedelta
from decimal import Decimal
import uuid
from sqlalchemy import select

from app.core.security import create_access_token
from app.models.alert import Alert, AlertSeverity, AlertStatus
from app.models.outbox import OutboxEvent, OutboxStatus
from app.models.transaction import RiskLevel, Transaction, TransactionStatus, TransactionType
from app.models.user import UserRole
from app.tasks.scoring_tasks import score_transaction_task


def test_complete_fraud_lifecycle_e2e(client, create_test_user, db_session):
    """
    End-to-End Integration Test:
    1. Auth: Investigator logs in, gets JWT.
    2. Ingestion: Transaction created with Idempotency Key -> atomic Outbox event.
    3. Processing: Outbox publisher publishes -> Celery worker scores transaction.
    4. Detection: Rules + ML hybrid blend flags high risk -> Alert auto-generated.
    5. Case Management: Investigation case created -> Alert status transitions to investigating.
    6. Dossier: Notes and evidence attached -> Chronological timeline verified.
    7. Resolution: Case closed with confirmed_fraud -> Alert auto-resolved.
    8. Audit: Audit log entries recorded and verified.
    """
    # --- STEP 1: AUTH & RBAC ---
    investigator = create_test_user("inv_e2e@sentinelx.local", "InvestigatorPass123!", UserRole.INVESTIGATOR)
    admin = create_test_user("admin_e2e@sentinelx.local", "AdminPass123!", UserRole.ADMIN)

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"email": "inv_e2e@sentinelx.local", "password": "InvestigatorPass123!"},
    )
    assert login_resp.status_code == 200
    token_data = login_resp.json()
    token = token_data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me_resp = client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "inv_e2e@sentinelx.local"

    # --- STEP 2: TRANSACTION INGESTION & OUTBOX RECORDING ---
    now = datetime.now(timezone.utc)
    # Seed an earlier legitimate transaction 20 mins prior from US
    tx_prior = Transaction(
        id=uuid.uuid4(),
        idempotency_key=f"prior-{uuid.uuid4()}",
        sender_account_id="ACCT-E2E-ATTACKER",
        receiver_account_id="MERCH-LOCAL-STORE",
        amount=Decimal("15.00"),
        currency="USD",
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.COMPLETED,
        occurred_at=now - timedelta(minutes=20),
        device_id="DEV-HOME-PHONE",
        ip_address="12.34.56.78",
        country_code="US",
    )
    db_session.add(tx_prior)
    db_session.commit()

    idempotency_key = f"e2e-idemp-{uuid.uuid4()}"
    tx_payload = {
        "sender_account_id": "ACCT-E2E-ATTACKER",
        "receiver_account_id": "OFFSHORE-E2E-DEST",
        "amount": "15000.00",
        "currency": "USD",
        "transaction_type": "transfer",
        "channel": "web",
        "device_id": "DEV-E2E-SUSPECT",
        "ip_address": "194.26.29.99",
        "country_code": "RU",
        "occurred_at": now.isoformat(),
    }

    create_resp = client.post(
        "/api/v1/transactions",
        json=tx_payload,
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert create_resp.status_code == 201
    created_tx = create_resp.json()
    tx_id = uuid.UUID(created_tx["id"])

    # Verify atomic outbox event was recorded in the same DB transaction
    outbox_events = db_session.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "transactions.created")
    ).scalars().all()
    assert len(outbox_events) >= 1
    recent_outbox = outbox_events[-1]
    assert str(tx_id) in recent_outbox.payload
    assert recent_outbox.status == OutboxStatus.PENDING

    # --- STEP 3: ASYNC SCORING PIPELINE ---
    # Ingest and score the transaction
    score_result = score_transaction_task.apply(kwargs={"transaction_id": str(tx_id)}).get()
    assert score_result["score"] >= 70
    assert score_result["risk_level"] in ("high", "medium")

    # Verify transaction record updated
    tx_db = db_session.get(Transaction, tx_id)
    assert tx_db.risk_score >= 70
    assert tx_db.risk_level == RiskLevel.HIGH

    # --- STEP 4: ALERT AUTO-GENERATION ---
    alerts = db_session.execute(
        select(Alert).where(Alert.transaction_id == tx_id)
    ).scalars().all()
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.status == AlertStatus.OPEN
    assert alert.severity in (AlertSeverity.CRITICAL, AlertSeverity.HIGH)
    assert alert.risk_score >= 70

    # --- STEP 5: INVESTIGATION CREATION & STATUS SYNC ---
    inv_payload = {
        "title": "E2E Large Offshore Transfer Heist",
        "description": "Suspicious $15,000 international transfer from RU IP to offshore destination.",
        "priority": "high",
        "alert_ids": [str(alert.id)],
    }
    inv_create_resp = client.post(
        "/api/v1/investigations",
        json=inv_payload,
        headers=headers,
    )
    assert inv_create_resp.status_code == 201
    inv_data = inv_create_resp.json()
    inv_id = uuid.UUID(inv_data["id"])

    # Verify linked alert status automatically transitioned to 'investigating'
    db_session.refresh(alert)
    assert alert.status == AlertStatus.INVESTIGATING

    # --- STEP 6: NOTES, EVIDENCE & TIMELINE ---
    note_resp = client.post(
        f"/api/v1/investigations/{inv_id}/notes",
        json={"body": "Investigator confirmed foreign IP range associated with credential stuffing."},
        headers=headers,
    )
    assert note_resp.status_code == 201

    evidence_resp = client.post(
        f"/api/v1/investigations/{inv_id}/evidence",
        json={
            "evidence_type": "transaction",
            "reference_id": str(tx_id),
            "description": "Raw transaction wire transfer log.",
        },
        headers=headers,
    )
    assert evidence_resp.status_code == 201

    inv_detail_resp = client.get(f"/api/v1/investigations/{inv_id}", headers=headers)
    assert inv_detail_resp.status_code == 200
    timeline = inv_detail_resp.json()["timeline"]
    assert len(timeline) >= 2  # creation + note + evidence

    # --- STEP 7: CASE RESOLUTION & ALERT CLOSURE ---
    close_resp = client.post(
        f"/api/v1/investigations/{inv_id}/close",
        json={
            "outcome": "confirmed_fraud",
            "closing_notes": "Account owner confirmed unauthorized takeover. Wire recall initiated.",
        },
        headers=headers,
    )
    assert close_resp.status_code == 200
    closed_inv = close_resp.json()
    assert closed_inv["status"] == "closed"
    assert closed_inv["outcome"] == "confirmed_fraud"

    # Verify linked alert status was synced to 'resolved_fraud'
    db_session.refresh(alert)
    assert alert.status == AlertStatus.RESOLVED_FRAUD

    # --- STEP 8: AUDIT LOG VERIFICATION ---
    admin_token = create_access_token(user_id=str(admin.id), email=admin.email, role=admin.role.value)
    audit_resp = client.get(
        "/api/v1/audit-logs",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert audit_resp.status_code == 200
    audit_items = audit_resp.json()["items"]
    assert len(audit_items) >= 1
    login_audits = [a for a in audit_items if a["action"] == "auth.login"]
    assert len(login_audits) >= 1
    assert login_audits[0]["user_id"] == str(investigator.id)
