import logging
from typing import Any
import uuid

from app.celery_app import celery_app
from app.database.connection import SessionLocal
from app.services.fraud_service import score_transaction

logger = logging.getLogger("sentinelx.tasks.scoring")


@celery_app.task(name="app.tasks.scoring_tasks.score_transaction_task", bind=True, max_retries=3)
def score_transaction_task(self, transaction_id: str) -> dict[str, Any]:
    """
    Asynchronous Celery task for computing fraud risk scores.
    Invoked when processing transactions asynchronously or through queue offloading.
    """
    logger.info(f"Starting async fraud scoring task for transaction {transaction_id}")
    db = SessionLocal()
    try:
        tx_uuid = uuid.UUID(transaction_id)
        fraud_score = score_transaction(db, tx_uuid)
        return {
            "transaction_id": transaction_id,
            "score": fraud_score.score,
            "risk_level": fraud_score.level.value,
            "engine_version": fraud_score.engine_version,
            "scored_at": fraud_score.scored_at.isoformat(),
        }
    except Exception as exc:
        logger.error(f"Error scoring transaction {transaction_id}: {exc}")
        db.rollback()
        raise self.retry(exc=exc, countdown=5)
    finally:
        db.close()

