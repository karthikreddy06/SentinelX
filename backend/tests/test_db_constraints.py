from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def insert_raw_transaction(db_session, **overrides):
    now = datetime.now(timezone.utc)
    base = {
        "idempotency_key": f"key-{uuid.uuid4()}",
        "sender_account_id": "acc_sender",
        "receiver_account_id": "acc_receiver",
        "amount": Decimal("100.00"),
        "currency": "USD",
        "transaction_type": "transfer",
        "status": "pending",
        "description": "DB constraint test",
        "channel": "api",
        "country_code": "US",
        "device_id": "device_1",
        "ip_address": "127.0.0.1",
        "risk_score": None,
        "risk_level": None,
        "occurred_at": now,
    }
    base.update(overrides)

    stmt = text("""
        INSERT INTO transactions (
            idempotency_key, sender_account_id, receiver_account_id, amount,
            currency, transaction_type, status, description, channel,
            country_code, device_id, ip_address, risk_score, risk_level, occurred_at
        ) VALUES (
            :idempotency_key, :sender_account_id, :receiver_account_id, :amount,
            :currency, :transaction_type, :status, :description, :channel,
            :country_code, :device_id, :ip_address, :risk_score, :risk_level, :occurred_at
        )
    """)
    db_session.execute(stmt, base)
    db_session.commit()


def test_db_constraint_amount_positive(db_session):
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, amount=Decimal("-10.00"))
    assert "ck_transactions_amount_positive" in str(exc_info.value)
    db_session.rollback()

    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, amount=Decimal("0.00"))
    assert "ck_transactions_amount_positive" in str(exc_info.value)
    db_session.rollback()


def test_db_constraint_currency_format(db_session):
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, currency="usd")
    assert "ck_transactions_currency_format" in str(exc_info.value)
    db_session.rollback()

    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, currency="US1")
    assert "ck_transactions_currency_format" in str(exc_info.value)
    db_session.rollback()


def test_db_constraint_distinct_accounts(db_session):
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(
            db_session,
            sender_account_id="ACC_SAME",
            receiver_account_id="ACC_SAME",
        )
    assert "ck_transactions_distinct_accounts" in str(exc_info.value)
    db_session.rollback()


def test_db_constraint_risk_score_range(db_session):
    # Risk score < 0 fails
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, risk_score=-1)
    assert "ck_transactions_risk_score_range" in str(exc_info.value)
    db_session.rollback()

    # Risk score > 100 fails
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, risk_score=101)
    assert "ck_transactions_risk_score_range" in str(exc_info.value)
    db_session.rollback()

    # Boundary values 0, 100, and NULL succeed
    insert_raw_transaction(db_session, risk_score=0)
    insert_raw_transaction(db_session, risk_score=100)
    insert_raw_transaction(db_session, risk_score=None)


def test_db_constraint_unique_idempotency_key(db_session):
    key = "unique-test-key-db"
    insert_raw_transaction(db_session, idempotency_key=key)

    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, idempotency_key=key)
    assert "transactions_idempotency_key_key" in str(exc_info.value)
    db_session.rollback()


def test_db_constraint_enums(db_session):
    # Invalid transaction_type
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, transaction_type="invalid_type")
    assert "ck_transactions_transaction_type" in str(exc_info.value)
    db_session.rollback()

    # Invalid status
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, status="unknown_status")
    assert "ck_transactions_status" in str(exc_info.value)
    db_session.rollback()

    # Invalid risk_level
    with pytest.raises(IntegrityError) as exc_info:
        insert_raw_transaction(db_session, risk_level="critical")
    assert "ck_transactions_risk_level" in str(exc_info.value)
    db_session.rollback()
