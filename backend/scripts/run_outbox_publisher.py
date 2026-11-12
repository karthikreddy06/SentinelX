"""
SentinelX Transactional Outbox Publisher Service.
Polls the outbox_events table for pending events using SKIP LOCKED
and publishes them to Redpanda/Kafka topics.
"""
from pathlib import Path
import sys
import time

backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database.connection import SessionLocal
from app.streaming.publisher import publish_outbox_batch


def run_publisher_loop(poll_interval: float = 1.0, max_iterations: int | None = None) -> None:
    print("=== SentinelX Outbox Publisher Started ===")
    iterations = 0
    while True:
        db = SessionLocal()
        try:
            result = publish_outbox_batch(db, limit=50)
            if result["published"] > 0 or result["failed"] > 0:
                print(f"Outbox Batch: Published={result['published']}, Failed={result['failed']}, DLQ={result['dlq']}")
        except Exception as exc:
            print(f"Error during outbox publish loop: {exc}")
        finally:
            db.close()

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            break

        time.sleep(poll_interval)


if __name__ == "__main__":
    run_publisher_loop()
