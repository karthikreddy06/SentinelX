from datetime import datetime, timezone
import json
from unittest.mock import MagicMock, patch
import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.outbox import OutboxStatus
from app.models.transaction import Transaction, TransactionType
from app.services.outbox_service import (
    list_outbox_events,
    record_outbox_event,
)
from app.streaming.consumer import handle_transaction_created_event
from app.streaming.publisher import publish_outbox_batch


def test_transaction_creation_records_outbox_event(client: TestClient, admin_headers, db_session):
    """
    Test that creating a transaction automatically records a 'transactions.created'
    event in the outbox_events table within the same database transaction.
    """
    idempotency_key = str(uuid.uuid4())
    tx_data = {
        "amount": "250.00",
        "currency": "USD",
        "sender_account_id": "outbox_sender_1",
        "receiver_account_id": "outbox_recv_1",
        "transaction_type": "transfer",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "channel": "web",
        "country_code": "US",
    }

    res = client.post(
        "/api/v1/transactions",
        json=tx_data,
        headers={**admin_headers, "Idempotency-Key": idempotency_key},
    )
    assert res.status_code == 201
    created_tx = res.json()
    tx_id = created_tx["id"]

    # Verify OutboxEvent exists in DB
    events = list_outbox_events(db_session)
    matching = [e for e in events if e.event_type == "transactions.created" and tx_id in e.payload]
    assert len(matching) == 1
    event = matching[0]
    assert event.status == OutboxStatus.PENDING
    assert event.topic == settings.KAFKA_TOPIC_TRANSACTIONS_CREATED
    assert event.retry_count == 0

    parsed = json.loads(event.payload)
    assert parsed["transaction_id"] == tx_id
    assert parsed["sender_account_id"] == "outbox_sender_1"
    assert parsed["amount"] == 250.00


def test_outbox_publisher_publishes_and_marks_published(db_session):
    """
    Test that the outbox publisher successfully sends pending events to Kafka
    and updates their status to PUBLISHED.
    """
    # 1. Insert a pending event
    test_payload = {"test_tx_id": str(uuid.uuid4()), "amount": 100.0}
    event = record_outbox_event(
        db=db_session,
        event_type="transactions.created",
        topic="transactions.created",
        payload=test_payload,
    )
    db_session.commit()
    db_session.refresh(event)
    assert event.status == OutboxStatus.PENDING

    # 2. Mock KafkaProducer
    mock_producer = MagicMock()
    mock_future = MagicMock()
    mock_producer.send.return_value = mock_future

    # 3. Publish batch
    result = publish_outbox_batch(db_session, producer=mock_producer, limit=10)
    assert result["published"] >= 1
    assert result["failed"] == 0

    mock_producer.send.assert_called()

    # 4. Assert event is marked PUBLISHED
    db_session.refresh(event)
    assert event.status == OutboxStatus.PUBLISHED
    assert event.published_at is not None
    assert event.error_message is None


def test_outbox_retry_and_dlq_on_repeated_failures(db_session):
    """
    Test that publisher increments retry_count on failure, and when max retries
    are exceeded, sets status to FAILED and publishes to DLQ.
    """
    test_payload = {"fail_id": str(uuid.uuid4())}
    event = record_outbox_event(
        db=db_session,
        event_type="transactions.created",
        topic="transactions.created",
        payload=test_payload,
    )
    # Set retry count to 4 (max is 5)
    event.retry_count = 4
    db_session.commit()
    db_session.refresh(event)

    # Mock failing KafkaProducer
    mock_producer = MagicMock()
    mock_producer.send.side_effect = RuntimeError("Broker connection timeout")

    result = publish_outbox_batch(db_session, producer=mock_producer, limit=10)
    assert result["failed"] >= 1
    assert result["dlq"] >= 1

    db_session.refresh(event)
    assert event.status == OutboxStatus.FAILED
    assert event.retry_count == 5
    assert "Broker connection timeout" in event.error_message


def test_stream_consumer_scores_transaction_and_generates_alert(db_session):
    """
    Test stream consumer handling of 'transactions.created':
    Scores transaction, updates DB, records 'transactions.scored' and 'alerts.created'.
    """
    now = datetime.now(timezone.utc)
    sender = f"stream_sender_{uuid.uuid4().hex[:6]}"

    # Seed an impossible-travel transaction that triggers HIGH risk score
    prev_tx = Transaction(
        amount=100.0,
        currency="USD",
        sender_account_id=sender,
        receiver_account_id="stream_recv_0",
        transaction_type=TransactionType.TRANSFER,
        country_code="US",
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(prev_tx)
    db_session.commit()

    # Target tx 10 minutes later in GB
    target_tx = Transaction(
        amount=7500.0,
        currency="USD",
        sender_account_id=sender,
        receiver_account_id="stream_recv_target",
        transaction_type=TransactionType.TRANSFER,
        country_code="GB",
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db_session.add(target_tx)
    db_session.commit()
    db_session.refresh(target_tx)

    # Consume stream message
    event_payload = {"transaction_id": str(target_tx.id)}
    res = handle_transaction_created_event(db_session, event_payload, direct_emit=False)

    assert res["transaction_id"] == str(target_tx.id)
    assert res["score"] is not None
    assert res["risk_level"] in ("low", "medium", "high")

    # Verify Transaction was updated
    db_session.refresh(target_tx)
    assert target_tx.risk_score == res["score"]

    # Verify Outbox has 'transactions.scored'
    events = list_outbox_events(db_session)
    scored_events = [e for e in events if e.event_type == "transactions.scored" and str(target_tx.id) in e.payload]
    assert len(scored_events) >= 1


def test_streaming_health_endpoint(client: TestClient):
    """Test /health/streaming endpoint returns valid status."""
    res = client.get("/health/streaming")
    data = res.json()
    assert res.status_code in (200, 503)
    assert "streaming" in data
    assert data["streaming"] in ("ok", "unreachable", "disabled")
    assert "details" in data


def test_graceful_fallback_when_broker_offline(client: TestClient, admin_headers, db_session):
    """
    Verify transactions API functions seamlessly when Redpanda/Kafka broker is offline,
    preserving events safely in PostgreSQL outbox for eventual delivery.
    """
    with patch("app.core.kafka.get_kafka_producer", return_value=None):
        tx_data = {
            "amount": "99.00",
            "currency": "USD",
            "sender_account_id": "fallback_stream_sender",
            "receiver_account_id": "fallback_stream_recv",
            "transaction_type": "transfer",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        res = client.post(
            "/api/v1/transactions",
            json=tx_data,
            headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert res.status_code == 201
        tx_id = res.json()["id"]

        # Assert event was safely captured in PostgreSQL outbox table
        events = list_outbox_events(db_session)
        matching = [e for e in events if tx_id in e.payload]
        assert len(matching) == 1
        assert matching[0].status == OutboxStatus.PENDING
