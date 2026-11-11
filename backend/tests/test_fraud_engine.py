from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest

from app.core.config import settings
from app.fraud.features import extract_point_in_time_features
from app.fraud.scorer import default_rules_engine
from app.models.transaction import RiskLevel, Transaction, TransactionStatus, TransactionType
from app.services.fraud_service import get_transaction_risk_history, score_transaction


def create_raw_tx(
    db_session,
    sender: str = "sender_1",
    receiver: str = "receiver_1",
    amount: str = "100.00",
    occurred_at: datetime | None = None,
    status: TransactionStatus = TransactionStatus.COMPLETED,
    country: str = "US",
    device: str = "dev_1",
    ip: str = "10.0.0.1",
) -> Transaction:
    tx = Transaction(
        idempotency_key=str(uuid.uuid4()),
        sender_account_id=sender,
        receiver_account_id=receiver,
        amount=Decimal(amount),
        currency="USD",
        transaction_type=TransactionType.TRANSFER,
        status=status,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        country_code=country,
        device_id=device,
        ip_address=ip,
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


# === RULES SCENARIOS (POSITIVE & NEGATIVE) ===

def test_rule_velocity_burst(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "vel_sender"

    # Pre-populate 3 transactions in the last minute
    create_raw_tx(db_session, sender=sender, occurred_at=now - timedelta(seconds=40))
    create_raw_tx(db_session, sender=sender, occurred_at=now - timedelta(seconds=25))
    create_raw_tx(db_session, sender=sender, occurred_at=now - timedelta(seconds=10))

    target_tx = create_raw_tx(db_session, sender=sender, occurred_at=now)
    features = extract_point_in_time_features(db_session, target_tx)
    score, level, reasons = default_rules_engine.evaluate(target_tx, features)

    reason_codes = [r["reason_code"] for r in reasons]
    assert "VEL_BURST" in reason_codes
    assert score >= 25


def test_rule_amount_anomaly(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "amt_sender"

    # 1. Hard ceiling trigger ($15,000)
    tx_hard = create_raw_tx(db_session, sender=sender, amount="15000.00", occurred_at=now)
    feat_hard = extract_point_in_time_features(db_session, tx_hard)
    _, _, reasons_hard = default_rules_engine.evaluate(tx_hard, feat_hard)
    assert "AMT_ANOMALY" in [r["reason_code"] for r in reasons_hard]

    # 2. Normal amount does NOT trigger anomaly
    create_raw_tx(db_session, sender="normal_user", amount="100.00", occurred_at=now - timedelta(hours=2))
    create_raw_tx(db_session, sender="normal_user", amount="110.00", occurred_at=now - timedelta(hours=1))
    tx_normal = create_raw_tx(db_session, sender="normal_user", amount="105.00", occurred_at=now)
    feat_norm = extract_point_in_time_features(db_session, tx_normal)
    _, _, reasons_norm = default_rules_engine.evaluate(tx_normal, feat_norm)
    assert "AMT_ANOMALY" not in [r["reason_code"] for r in reasons_norm]


def test_rule_new_receiver_high_amount(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "new_recv_sender"

    # Known recipient with low amounts
    create_raw_tx(db_session, sender=sender, receiver="trusted_friend", amount="50.00", occurred_at=now - timedelta(days=1))

    # New recipient with high amount ($2,500)
    target_tx = create_raw_tx(db_session, sender=sender, receiver="stranger_acc", amount="2500.00", occurred_at=now)
    features = extract_point_in_time_features(db_session, target_tx)
    _, _, reasons = default_rules_engine.evaluate(target_tx, features)
    assert "NEW_RECV_HIGH_AMT" in [r["reason_code"] for r in reasons]


def test_rule_impossible_travel(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "travel_sender"

    # Transaction in US at 11:30
    create_raw_tx(db_session, sender=sender, country="US", occurred_at=now - timedelta(minutes=30))

    # Transaction in GB at 12:00 (30 minutes later) -> Impossible travel
    target_tx = create_raw_tx(db_session, sender=sender, country="GB", occurred_at=now)
    features = extract_point_in_time_features(db_session, target_tx)
    _, _, reasons = default_rules_engine.evaluate(target_tx, features)
    assert "IMPOSSIBLE_TRAVEL" in [r["reason_code"] for r in reasons]


def test_rule_structuring(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "structuring_sender"

    # 1st transaction in band: $9,500
    create_raw_tx(db_session, sender=sender, amount="9500.00", occurred_at=now - timedelta(hours=3))

    # 2nd transaction in band: $9,800 -> triggers structuring
    target_tx = create_raw_tx(db_session, sender=sender, amount="9800.00", occurred_at=now)
    features = extract_point_in_time_features(db_session, target_tx)
    _, _, reasons = default_rules_engine.evaluate(target_tx, features)
    assert "STRUCTURING" in [r["reason_code"] for r in reasons]


def test_rule_round_number(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

    # Round number $5,000 -> triggers
    tx_round = create_raw_tx(db_session, amount="5000.00", occurred_at=now)
    f_round = extract_point_in_time_features(db_session, tx_round)
    _, _, r_round = default_rules_engine.evaluate(tx_round, f_round)
    assert "ROUND_NUMBER" in [r["reason_code"] for r in r_round]

    # Non-round number $1,234.56 -> negative
    tx_odd = create_raw_tx(db_session, amount="1234.56", occurred_at=now)
    f_odd = extract_point_in_time_features(db_session, tx_odd)
    _, _, r_odd = default_rules_engine.evaluate(tx_odd, f_odd)
    assert "ROUND_NUMBER" not in [r["reason_code"] for r in r_odd]


def test_rule_pass_through(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    mule_acc = "mule_account"

    # Inbound $3,000 arrived 30 minutes ago
    create_raw_tx(db_session, sender="victim", receiver=mule_acc, amount="3000.00", occurred_at=now - timedelta(minutes=30))

    # Outbound $2,900 from mule account -> pass-through
    target_tx = create_raw_tx(db_session, sender=mule_acc, receiver="boss", amount="2900.00", occurred_at=now)
    features = extract_point_in_time_features(db_session, target_tx)
    _, _, reasons = default_rules_engine.evaluate(target_tx, features)
    assert "PASS_THROUGH" in [r["reason_code"] for r in reasons]


def test_rule_burst_failures(db_session):
    now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "burst_sender"

    # 2 failed transactions in the last 20 minutes
    create_raw_tx(db_session, sender=sender, status=TransactionStatus.FAILED, occurred_at=now - timedelta(minutes=20))
    create_raw_tx(db_session, sender=sender, status=TransactionStatus.CANCELLED, occurred_at=now - timedelta(minutes=10))

    # Current transaction -> burst failures triggered
    target_tx = create_raw_tx(db_session, sender=sender, occurred_at=now)
    features = extract_point_in_time_features(db_session, target_tx)
    _, _, reasons = default_rules_engine.evaluate(target_tx, features)
    assert "BURST_FAILURES" in [r["reason_code"] for r in reasons]


# === POINT-IN-TIME CORRECTNESS ===

def test_point_in_time_correctness_no_future_leakage(db_session):
    t_past = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)
    t_target = datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc)
    t_future = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    sender = "pit_sender"

    # 1 transaction in past
    create_raw_tx(db_session, sender=sender, amount="100.00", occurred_at=t_past)

    # Target transaction
    target_tx = create_raw_tx(db_session, sender=sender, amount="100.00", occurred_at=t_target)

    # 5 transactions in FUTURE relative to target
    for i in range(5):
        create_raw_tx(db_session, sender=sender, amount="5000.00", occurred_at=t_future + timedelta(minutes=i))

    # Features of target_tx must only count the 1 past transaction
    features = extract_point_in_time_features(db_session, target_tx)
    assert features["total_history_count"] == 1
    assert features["avg_amount"] == 100.00
    assert features["velocity_24h_count"] == 1


# === ENDPOINTS & RBAC ===

def test_score_and_risk_endpoints(client, admin_headers, analyst_headers, investigator_headers):
    # 1. Create a transaction
    tx_res = client.post(
        "/api/v1/transactions",
        json={
            "sender_account_id": "acc_api_sender",
            "receiver_account_id": "acc_api_receiver",
            "amount": "12000.00",  # High amount -> AMT_ANOMALY
            "currency": "USD",
            "transaction_type": "transfer",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Idempotency-Key": str(uuid.uuid4()), **investigator_headers},
    )
    assert tx_res.status_code == 201
    tx_id = tx_res.json()["id"]

    # If FRAUD_SCORING_ENABLED was active, it has a score
    score_val = tx_res.json()["risk_score"]
    assert score_val is not None
    assert score_val >= 30

    # 2. Trigger re-scoring endpoint via POST /transactions/{id}/score
    score_res = client.post(
        f"/api/v1/transactions/{tx_id}/score",
        headers=investigator_headers,
    )
    assert score_res.status_code == 200
    s_data = score_res.json()
    assert s_data["transaction_id"] == tx_id
    assert s_data["score"] >= 30
    assert s_data["level"] in ["medium", "high"]
    assert len(s_data["reasons_json"]) >= 1

    # 3. GET risk history via /transactions/{id}/risk (accessible to Analyst)
    risk_res = client.get(
        f"/api/v1/transactions/{tx_id}/risk",
        headers=analyst_headers,
    )
    assert risk_res.status_code == 200
    r_body = risk_res.json()
    assert r_body["transaction_id"] == tx_id
    assert len(r_body["scores"]) >= 1

    # 4. Analyst CANNOT trigger re-scoring -> 403
    forbid_res = client.post(
        f"/api/v1/transactions/{tx_id}/score",
        headers=analyst_headers,
    )
    assert forbid_res.status_code == 403
