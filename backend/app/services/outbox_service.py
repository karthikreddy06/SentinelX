from datetime import datetime, timezone
import json
import logging
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.outbox import OutboxEvent, OutboxStatus

logger = logging.getLogger("sentinelx.outbox")


def record_outbox_event(
    db: Session,
    event_type: str,
    topic: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    """
    Record an event in the transactional outbox table within the caller's DB transaction.
    Guarantees atomic persistence alongside business domain entities.
    """
    serialized_payload = json.dumps(payload, default=str)
    event = OutboxEvent(
        event_type=event_type,
        topic=topic,
        payload=serialized_payload,
        status=OutboxStatus.PENDING,
        retry_count=0,
    )
    db.add(event)
    return event


def fetch_pending_outbox_events(
    db: Session,
    batch_size: int = 50,
) -> list[OutboxEvent]:
    """
    Fetch pending events ordered by creation time with row-level locks
    (FOR UPDATE SKIP LOCKED) to allow parallel publisher workers without contention.
    """
    query = (
        select(OutboxEvent)
        .where(
            OutboxEvent.status == OutboxStatus.PENDING,
            OutboxEvent.retry_count < settings.OUTBOX_MAX_RETRIES,
        )
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    return list(db.execute(query).scalars().all())


def mark_outbox_event_published(db: Session, event: OutboxEvent) -> None:
    """Mark outbox event as successfully published."""
    event.status = OutboxStatus.PUBLISHED
    event.published_at = datetime.now(timezone.utc)
    event.error_message = None
    db.add(event)


def mark_outbox_event_failed(
    db: Session,
    event: OutboxEvent,
    error_message: str,
    max_retries: int | None = None,
) -> None:
    """
    Increment retry count on failure.
    If retry count reaches max_retries, mark as permanently FAILED.
    """
    max_r = max_retries or settings.OUTBOX_MAX_RETRIES
    event.retry_count += 1
    event.error_message = error_message[:2000] if error_message else "Unknown error"

    if event.retry_count >= max_r:
        event.status = OutboxStatus.FAILED
        logger.error(
            f"Outbox event {event.id} ({event.event_type}) failed permanently after {event.retry_count} retries."
        )
    else:
        logger.warning(
            f"Outbox event {event.id} failed attempt {event.retry_count}/{max_r}: {error_message}"
        )
    db.add(event)


def list_outbox_events(
    db: Session,
    status_filter: OutboxStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[OutboxEvent]:
    """List outbox events for monitoring and administrative inspection."""
    query = select(OutboxEvent)
    if status_filter:
        query = query.where(OutboxEvent.status == status_filter)
    query = query.order_by(OutboxEvent.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(query).scalars().all())
