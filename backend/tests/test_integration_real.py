import json
from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.kafka import check_kafka_health, get_kafka_producer
from app.core.redis import get_redis_client
from app.models.fraud_score import FraudScore
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.streaming.consumer import handle_transaction_created_event
from app.tasks.scoring_tasks import score_transaction_task


@pytest.mark.integration
def test_redpanda_streaming_ingestion_smoke(db_session):
    """
    Real integration test for Redpanda event ingestion.
    Produces a 'transactions.created' event to Redpanda and consumes it.
    Gracefully skips if Redpanda is unreachable on localhost:9092.
    """
    health_status, reason = check_kafka_health()
    if health_status != "ok":
        pytest.skip(f"Redpanda not running on {settings.KAFKA_BOOTSTRAP_SERVERS}: {reason}")

    producer = get_kafka_producer()
    if producer is None:
        pytest.skip(f"Redpanda producer unavailable on {settings.KAFKA_BOOTSTRAP_SERVERS}")

    # Seed transaction in DB
    now = datetime.now(timezone.utc)
    tx_id = uuid.uuid4()
    tx = Transaction(
        id=tx_id,
        idempotency_key=f"integration-kafka-{uuid.uuid4()}",
        sender_account_id="ACCT-STREAM-INTEG",
        receiver_account_id="MERCH-STREAM-DEST",
        amount=Decimal("12500.00"),
        currency="USD",
        transaction_type=TransactionType.TRANSFER,
        status=TransactionStatus.PENDING,
        occurred_at=now,
        device_id="DEV-INTEG-1",
        ip_address="10.0.0.1",
        country_code="US",
    )
    db_session.add(tx)
    db_session.commit()

    # Produce event to Redpanda
    event_payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "transactions.created",
        "transaction_id": str(tx_id),
        "sender_account_id": "ACCT-STREAM-INTEG",
        "receiver_account_id": "MERCH-STREAM-DEST",
        "amount": 12500.0,
        "occurred_at": now.isoformat(),
    }

    try:
        from kafka import KafkaConsumer

        # Send event
        future = producer.send(
            settings.KAFKA_TOPIC_TRANSACTIONS_CREATED,
            value=event_payload,
            key=str(tx_id),
        )
        producer.flush(timeout=3.0)

        # Consume 1 iteration via handle_transaction_created_event
        consumer = KafkaConsumer(
            settings.KAFKA_TOPIC_TRANSACTIONS_CREATED,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            auto_offset_reset="earliest",
            consumer_timeout_ms=5000,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )

        found_event = None
        for message in consumer:
            if message.value.get("transaction_id") == str(tx_id):
                found_event = message.value
                break
        consumer.close()

        if not found_event:
            pytest.fail("Message published to Redpanda was not received by consumer within timeout.")

        result = handle_transaction_created_event(db_session, found_event)
        assert result["transaction_id"] == str(tx_id)
        assert "score" in result
        assert "risk_level" in result

        # Verify score persisted in DB
        fraud_score = db_session.execute(
            select(FraudScore).where(FraudScore.transaction_id == tx_id)
        ).scalar_one_or_none()
        assert fraud_score is not None
        assert fraud_score.score == result["score"]

    except Exception as exc:
        pytest.fail(f"Real Redpanda ingestion failed: {exc}")


@pytest.mark.integration
def test_celery_async_worker_scoring_smoke(db_session):
    """
    Real integration test for Celery async task execution.
    Executes scoring task with real Redis / eager worker.
    Gracefully skips if Redis is unreachable.
    """
    redis_client = get_redis_client()
    if redis_client is None:
        pytest.skip("Redis not running - cannot verify Celery broker")

    # Seed transaction
    now = datetime.now(timezone.utc)
    tx_id = uuid.uuid4()
    tx = Transaction(
        id=tx_id,
        idempotency_key=f"integration-celery-{uuid.uuid4()}",
        sender_account_id="ACCT-CELERY-INTEG",
        receiver_account_id="MERCH-CELERY-DEST",
        amount=Decimal("500.00"),
        currency="USD",
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.PENDING,
        occurred_at=now,
        device_id="DEV-CELERY-1",
        ip_address="10.0.0.2",
        country_code="US",
    )
    db_session.add(tx)
    db_session.commit()

    # Execute celery task
    async_res = score_transaction_task.apply(kwargs={"transaction_id": str(tx_id)})
    res = async_res.get(timeout=5)

    assert res["transaction_id"] == str(tx_id)
    assert "score" in res
    assert "risk_level" in res

    # Verify score persisted in PostgreSQL
    fraud_score = db_session.execute(
        select(FraudScore).where(FraudScore.transaction_id == tx_id)
    ).scalar_one_or_none()
    assert fraud_score is not None
    assert fraud_score.score == res["score"]

    # Verify transaction table updated
    db_session.refresh(tx)
    assert tx.risk_score == res["score"]
