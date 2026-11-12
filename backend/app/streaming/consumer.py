import logging
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.kafka import send_kafka_message
from app.models.alert import Alert
from app.models.transaction import Transaction
from app.services.fraud_service import score_transaction
from app.services.outbox_service import record_outbox_event

logger = logging.getLogger("sentinelx.streaming.consumer")


def handle_transaction_created_event(
    db: Session,
    payload: dict[str, Any],
    direct_emit: bool = False,
) -> dict[str, Any]:
    """
    Consume a 'transactions.created' event:
      1. Fetch transaction and compute fraud score.
      2. Record or emit 'transactions.scored' event.
      3. If an alert was generated, record or emit 'alerts.created' event.
    """
    raw_tx_id = payload.get("transaction_id")
    if not raw_tx_id:
        raise ValueError("Missing 'transaction_id' in event payload")

    tx_id = uuid.UUID(str(raw_tx_id))
    transaction = db.execute(select(Transaction).where(Transaction.id == tx_id)).scalar_one_or_none()
    if not transaction:
        raise ValueError(f"Transaction with id '{tx_id}' not found in database.")

    # 1. Score transaction
    fraud_score = score_transaction(db, tx_id)
    score_val = fraud_score.score
    risk_lvl = fraud_score.level.value

    # 2. Prepare scored event
    scored_payload = {
        "transaction_id": str(tx_id),
        "sender_account_id": transaction.sender_account_id,
        "receiver_account_id": transaction.receiver_account_id,
        "amount": float(transaction.amount),
        "score": score_val,
        "risk_level": risk_lvl,
        "engine_version": fraud_score.engine_version,
        "reasons": fraud_score.reasons_json,
        "scored_at": fraud_score.scored_at.isoformat(),
    }

    if direct_emit:
        send_kafka_message(
            settings.KAFKA_TOPIC_TRANSACTIONS_SCORED,
            scored_payload,
            key=str(tx_id),
        )
    else:
        record_outbox_event(
            db=db,
            event_type="transactions.scored",
            topic=settings.KAFKA_TOPIC_TRANSACTIONS_SCORED,
            payload=scored_payload,
        )

    # 3. Check if an alert exists for this transaction
    alert = db.execute(
        select(Alert).where(Alert.transaction_id == tx_id)
    ).scalar_one_or_none()

    alert_created = False
    if alert:
        alert_created = True
        alert_payload = {
            "alert_id": str(alert.id),
            "transaction_id": str(tx_id),
            "severity": alert.severity.value,
            "status": alert.status.value,
            "reason_codes": alert.reason_codes,
            "risk_score": alert.risk_score,
            "created_at": alert.created_at.isoformat(),
        }
        if direct_emit:
            send_kafka_message(
                settings.KAFKA_TOPIC_ALERTS_CREATED,
                alert_payload,
                key=str(alert.id),
            )
        else:
            record_outbox_event(
                db=db,
                event_type="alerts.created",
                topic=settings.KAFKA_TOPIC_ALERTS_CREATED,
                payload=alert_payload,
            )

    db.commit()
    logger.info(
        f"Processed stream event for tx '{tx_id}': score={score_val}, risk={risk_lvl}, alert_created={alert_created}"
    )

    return {
        "transaction_id": str(tx_id),
        "score": score_val,
        "risk_level": risk_lvl,
        "alert_created": alert_created,
    }
