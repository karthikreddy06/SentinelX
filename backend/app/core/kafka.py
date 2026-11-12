import json
import logging
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger("sentinelx.kafka")

_producer: Any | None = None
_warned_unreachable: bool = False
_is_connected: bool = False
_last_check_time: float = 0.0
_COOLDOWN_SECONDS: float = 10.0


def get_kafka_producer() -> Any | None:
    """
    Get a thread-safe KafkaProducer instance with graceful degradation.
    Returns None if streaming is disabled or Redpanda/Kafka broker is unreachable.
    """
    global _producer, _warned_unreachable, _is_connected, _last_check_time

    if not settings.STREAMING_ENABLED:
        return None

    now = time.time()
    if _is_connected and _producer is not None:
        return _producer

    # Cooldown before re-attempting connection if previously failed
    if not _is_connected and (now - _last_check_time) < _COOLDOWN_SECONDS:
        return None

    _last_check_time = now
    try:
        from kafka import KafkaProducer

        _producer = KafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            client_id=settings.KAFKA_CLIENT_ID,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            request_timeout_ms=1500,
            api_version_auto_timeout_ms=1500,
            max_block_ms=1500,
            retries=2,
        )
        # Test broker metadata connectivity
        _producer.partitions_for(settings.KAFKA_TOPIC_TRANSACTIONS_CREATED)
        _is_connected = True
        _warned_unreachable = False
        return _producer
    except Exception as exc:
        _is_connected = False
        _producer = None
        if not _warned_unreachable:
            logger.warning(
                f"Kafka/Redpanda broker unreachable at {settings.KAFKA_BOOTSTRAP_SERVERS} ({exc}). Operating with outbox/in-memory fallback."
            )
            _warned_unreachable = True
        return None


def send_kafka_message(
    topic: str,
    payload: dict[str, Any],
    key: str | None = None,
) -> bool:
    """Send a message to a Kafka/Redpanda topic. Returns True if successfully queued, False otherwise."""
    producer = get_kafka_producer()
    if producer is None:
        return False

    try:
        future = producer.send(topic, value=payload, key=key)
        producer.flush(timeout=2.0)
        return True
    except Exception as exc:
        logger.error(f"Failed to publish to Kafka topic '{topic}': {exc}")
        return False


def check_kafka_health() -> tuple[str, str]:
    """Check Kafka/Redpanda broker health: ok, unreachable, or disabled."""
    if not settings.STREAMING_ENABLED:
        return "disabled", "Event streaming is disabled in configuration"

    try:
        from kafka import KafkaConsumer

        consumer = KafkaConsumer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            request_timeout_ms=1500,
            consumer_timeout_ms=1500,
        )
        topics = consumer.topics()
        consumer.close()
        return "ok", f"Redpanda/Kafka connected ({len(topics)} topics active)"
    except Exception as exc:
        return "unreachable", f"Redpanda/Kafka connection failed: {exc}"


def close_kafka_producer() -> None:
    """Close Kafka producer gracefully on shutdown."""
    global _producer, _is_connected
    if _producer:
        try:
            _producer.close(timeout=2.0)
        except Exception:
            pass
        _producer = None
    _is_connected = False
