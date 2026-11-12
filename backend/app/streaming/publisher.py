import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.kafka import get_kafka_producer, send_kafka_message
from app.models.outbox import OutboxEvent, OutboxStatus
from app.services.outbox_service import (
    fetch_pending_outbox_events,
    mark_outbox_event_failed,
    mark_outbox_event_published,
)

logger = logging.getLogger("sentinelx.streaming.publisher")


def publish_outbox_batch(
    db: Session,
    producer: Any | None = None,
    limit: int = 50,
) -> dict[str, int]:
    """
    Publish a batch of pending outbox events to Redpanda/Kafka.
    Uses SKIP LOCKED concurrency control and automatically moves exhausted retries to DLQ.

    Returns:
        dict: {"published": count, "failed": count, "dlq": count}
    """
    events = fetch_pending_outbox_events(db, batch_size=limit)
    if not events:
        return {"published": 0, "failed": 0, "dlq": 0}

    published_count = 0
    failed_count = 0
    dlq_count = 0

    active_producer = producer or get_kafka_producer()

    for event in events:
        try:
            payload = json.loads(event.payload)
            key = payload.get("transaction_id") or payload.get("alert_id") or str(event.id)

            success = False
            if active_producer is not None:
                if hasattr(active_producer, "send"):
                    # Real KafkaProducer or compatible mock
                    future = active_producer.send(
                        event.topic,
                        value=payload,
                        key=key,
                    )
                    if hasattr(active_producer, "flush"):
                        active_producer.flush(timeout=2.0)
                    success = True
                else:
                    success = send_kafka_message(event.topic, payload, key=key)
            else:
                # Direct helper send
                success = send_kafka_message(event.topic, payload, key=key)

            if success:
                mark_outbox_event_published(db, event)
                published_count += 1
            else:
                raise RuntimeError(f"Failed to publish to topic {event.topic}")

        except Exception as exc:
            failed_count += 1
            error_str = str(exc)
            mark_outbox_event_failed(db, event, error_message=error_str)

            # If event reached max retries, route to Dead-Letter Queue (DLQ)
            if event.status == OutboxStatus.FAILED:
                dlq_count += 1
                try:
                    dlq_payload = {
                        "original_event_id": str(event.id),
                        "original_topic": event.topic,
                        "original_event_type": event.event_type,
                        "payload": json.loads(event.payload),
                        "error_message": error_str,
                        "retry_count": event.retry_count,
                    }
                    if active_producer is not None:
                        active_producer.send(
                            settings.KAFKA_TOPIC_TRANSACTIONS_DLQ,
                            value=dlq_payload,
                            key=str(event.id),
                        )
                except Exception as dlq_exc:
                    logger.error(f"Failed to publish to DLQ for event {event.id}: {dlq_exc}")

    db.commit()
    return {"published": published_count, "failed": failed_count, "dlq": dlq_count}
