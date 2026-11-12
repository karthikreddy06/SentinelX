"""
SentinelX Streaming Fraud Worker Service.
Consumes 'transactions.created' events from Redpanda/Kafka,
executes real-time fraud scoring, updates risk tables, and generates alerts.
"""
import json
import logging
from pathlib import Path
import sys

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.database.connection import SessionLocal
from app.streaming.consumer import handle_transaction_created_event

logger = logging.getLogger("sentinelx.stream_consumer")


def run_consumer_loop(max_messages: int | None = None) -> None:
    print(f"=== SentinelX Fraud Stream Consumer Started on {settings.KAFKA_BOOTSTRAP_SERVERS} ===")
    from kafka import KafkaConsumer

    consumer = KafkaConsumer(
        settings.KAFKA_TOPIC_TRANSACTIONS_CREATED,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=settings.KAFKA_GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    )

    count = 0
    for message in consumer:
        payload = message.value
        db = SessionLocal()
        try:
            res = handle_transaction_created_event(db, payload, direct_emit=True)
            print(f"Processed stream message: tx={res['transaction_id']}, score={res['score']}, risk={res['risk_level']}")
        except Exception as exc:
            print(f"Error handling stream message: {exc}")
        finally:
            db.close()

        count += 1
        if max_messages is not None and count >= max_messages:
            break

    consumer.close()


if __name__ == "__main__":
    run_consumer_loop()
