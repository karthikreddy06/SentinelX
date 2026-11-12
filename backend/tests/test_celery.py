from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import select

from app.celery_app import celery_app
from app.models.alert import Alert, AlertEvent, AlertSeverity, AlertStatus
from app.models.fraud_score import FraudScore
from app.models.outbox import OutboxEvent, OutboxStatus
from app.models.transaction import RiskLevel, Transaction, TransactionStatus, TransactionType
from app.models.user import RefreshToken, User, UserRole
from app.tasks.maintenance_tasks import (
    check_model_drift_task,
    cleanup_expired_tokens_task,
    cleanup_published_outbox_task,
    escalate_stale_alerts_task,
)
from app.tasks.scoring_tasks import score_transaction_task


@pytest.fixture(autouse=True)
def configure_celery_eager():
    """Configure Celery to run tasks eagerly (synchronously in-process) for unit testing."""
    original_eager = celery_app.conf.task_always_eager
    original_prop = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = original_eager
    celery_app.conf.task_eager_propagates = original_prop


def test_score_transaction_task(db_session):
    """Test asynchronous transaction scoring via Celery task."""
    now = datetime.now(timezone.utc)
    tx = Transaction(
        amount=150.0,
        currency="USD",
        sender_account_id=f"celery_sender_{uuid.uuid4().hex[:6]}",
        receiver_account_id="celery_recv_1",
        transaction_type=TransactionType.PAYMENT,
        country_code="US",
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)

    # Dispatch Celery task
    async_result = score_transaction_task.delay(str(tx.id))
    result = async_result.get()

    assert result["transaction_id"] == str(tx.id)
    assert "score" in result
    assert result["risk_level"] in ("low", "medium", "high")

    # Verify DB was updated
    db_session.refresh(tx)
    assert tx.risk_score == result["score"]


def test_escalate_stale_alerts_task(db_session):
    """Test periodic escalation of unhandled open alerts older than SLA threshold."""
    now = datetime.now(timezone.utc)

    # 1. Stale alert (30 hours old) with MEDIUM severity
    tx1 = Transaction(
        amount=500.0,
        currency="USD",
        sender_account_id="stale_sender_1",
        receiver_account_id="stale_recv_1",
        transaction_type=TransactionType.TRANSFER,
        country_code="US",
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(tx1)
    db_session.flush()

    stale_alert = Alert(
        transaction_id=tx1.id,
        severity=AlertSeverity.MEDIUM,
        status=AlertStatus.OPEN,
        reason_codes=["VELOCITY_BURST"],
        risk_score=60,
        created_at=now - timedelta(hours=30),
    )
    db_session.add(stale_alert)

    # 2. Fresh alert (2 hours old) with LOW severity
    tx2 = Transaction(
        amount=200.0,
        currency="USD",
        sender_account_id="fresh_sender_2",
        receiver_account_id="fresh_recv_2",
        transaction_type=TransactionType.TRANSFER,
        country_code="US",
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(tx2)
    db_session.flush()

    fresh_alert = Alert(
        transaction_id=tx2.id,
        severity=AlertSeverity.LOW,
        status=AlertStatus.OPEN,
        reason_codes=["NEW_DEVICE"],
        risk_score=20,
        created_at=now - timedelta(hours=2),
    )
    db_session.add(fresh_alert)
    db_session.commit()

    # Run escalation task
    res = escalate_stale_alerts_task.delay().get()
    assert res["escalated_count"] >= 1

    # Verify stale alert is escalated to HIGH
    db_session.refresh(stale_alert)
    assert stale_alert.severity == AlertSeverity.HIGH

    # Verify alert event audit entry
    events = list(
        db_session.execute(
            select(AlertEvent).where(AlertEvent.alert_id == stale_alert.id)
        ).scalars().all()
    )
    assert len(events) >= 1
    assert events[0].action == "escalate_sla"

    # Verify fresh alert was not escalated
    db_session.refresh(fresh_alert)
    assert fresh_alert.severity == AlertSeverity.LOW


def test_cleanup_expired_tokens_task(db_session):
    """Test purging expired and revoked JWT refresh tokens."""
    now = datetime.now(timezone.utc)
    user = User(
        email=f"token_test_{uuid.uuid4().hex[:6]}@test.local",
        hashed_password="hashed_pw_test",
        full_name="Token User",
        role=UserRole.ANALYST,
    )
    db_session.add(user)
    db_session.flush()

    # 1. Valid active token
    t_valid = RefreshToken(
        token_hash="hash_valid_1",
        user_id=user.id,
        expires_at=now + timedelta(days=7),
        revoked_at=None,
    )
    # 2. Expired token
    t_expired = RefreshToken(
        token_hash="hash_expired_2",
        user_id=user.id,
        expires_at=now - timedelta(days=2),
        revoked_at=None,
    )
    # 3. Explicitly revoked token
    t_revoked = RefreshToken(
        token_hash="hash_revoked_3",
        user_id=user.id,
        expires_at=now + timedelta(days=5),
        revoked_at=now - timedelta(days=1),
    )
    db_session.add_all([t_valid, t_expired, t_revoked])
    db_session.commit()

    res = cleanup_expired_tokens_task.delay().get()
    assert res["deleted_tokens"] >= 2

    remaining = list(db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)).scalars().all())
    assert len(remaining) == 1
    assert remaining[0].token_hash == "hash_valid_1"


def test_cleanup_published_outbox_task(db_session):
    """Test purging outbox events older than retention period that are PUBLISHED."""
    now = datetime.now(timezone.utc)

    # 1. Old published event (10 days old)
    e_old_pub = OutboxEvent(
        event_type="test.event",
        topic="test.topic",
        payload='{"old": true}',
        status=OutboxStatus.PUBLISHED,
        created_at=now - timedelta(days=10),
        published_at=now - timedelta(days=10),
    )
    # 2. Recent published event (1 day old)
    e_rec_pub = OutboxEvent(
        event_type="test.event",
        topic="test.topic",
        payload='{"recent": true}',
        status=OutboxStatus.PUBLISHED,
        created_at=now - timedelta(days=1),
        published_at=now - timedelta(days=1),
    )
    # 3. Old PENDING event (should NOT be deleted!)
    e_pending = OutboxEvent(
        event_type="test.event",
        topic="test.topic",
        payload='{"pending": true}',
        status=OutboxStatus.PENDING,
        created_at=now - timedelta(days=10),
    )
    db_session.add_all([e_old_pub, e_rec_pub, e_pending])
    db_session.commit()
    old_id = e_old_pub.id
    rec_id = e_rec_pub.id
    pend_id = e_pending.id

    res = cleanup_published_outbox_task.delay().get()
    assert res["cleaned_outbox_count"] >= 1

    db_session.expunge_all()
    remaining_ids = set(
        db_session.execute(select(OutboxEvent.id)).scalars().all()
    )
    assert old_id not in remaining_ids
    assert rec_id in remaining_ids
    assert pend_id in remaining_ids


def test_check_model_drift_task(db_session):
    """Test model drift task handles insufficient data and computes risk ratios."""
    # 1. No data
    res_empty = check_model_drift_task.delay().get()
    assert res_empty["status"] == "insufficient_data"

    # 2. Add sample fraud scores
    now = datetime.now(timezone.utc)
    tx = Transaction(
        amount=100.0,
        currency="USD",
        sender_account_id="drift_sender",
        receiver_account_id="drift_recv",
        transaction_type=TransactionType.PAYMENT,
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(tx)
    db_session.flush()

    for score in [15, 20, 25, 85]:
        fs = FraudScore(
            transaction_id=tx.id,
            score=score,
            level=RiskLevel.HIGH if score >= 70 else RiskLevel.LOW,
            engine_version="rule_engine_v1.0.0",
            reasons_json=[],
            features_json={},
            scored_at=now,
        )
        db_session.add(fs)
    db_session.commit()

    res = check_model_drift_task.delay().get()
    assert res["status"] in ("normal", "drift_warning")
    assert res["samples"] == 4
    assert res["high_risk_pct"] == 25.0


def test_celery_beat_schedule_registered():
    """Verify all periodic tasks are registered in Celery beat schedule."""
    schedule = celery_app.conf.beat_schedule
    assert "escalate-stale-alerts-hourly" in schedule
    assert "cleanup-expired-tokens-6h" in schedule
    assert "cleanup-published-outbox-12h" in schedule
    assert "check-model-drift-daily" in schedule
