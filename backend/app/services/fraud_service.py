import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fraud.features import extract_point_in_time_features
from app.fraud.scorer import default_rules_engine
from app.models.fraud_score import FraudScore
from app.models.transaction import Transaction
from app.models.user import User
from app.services.alert_service import create_alert_if_eligible
from app.services.audit_service import record_audit_entry
from app.services.transaction_service import get_transaction


def score_transaction(
    db: Session,
    transaction_id: uuid.UUID,
    user: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> FraudScore:
    """
    Score a transaction using point-in-time feature extraction and the rules engine.
    Persists a FraudScore record and updates transaction.risk_score/risk_level.
    """
    transaction = get_transaction(db, transaction_id)

    # 1. Point-in-time feature extraction
    features = extract_point_in_time_features(db, transaction)

    # 2. Rule evaluation
    score, level, reasons, engine_version = default_rules_engine.evaluate(transaction, features)

    # 3. Create persistent fraud score record
    fraud_score = FraudScore(
        transaction_id=transaction.id,
        score=score,
        level=level,
        engine_version=engine_version,
        reasons_json=reasons,
        features_json=features,
    )
    db.add(fraud_score)

    # 4. Update transaction table risk fields (only updated through fraud service)
    transaction.risk_score = score
    transaction.risk_level = level

    db.commit()
    db.refresh(fraud_score)

    # 5. Auto-generate alert if threshold reached
    create_alert_if_eligible(
        db=db,
        transaction_id=transaction.id,
        risk_score=score,
        risk_level=level,
        reason_codes=[r["reason_code"] for r in reasons],
        actor_id=user.id if user else None,
    )

    # 6. Audit entry
    record_audit_entry(
        db=db,
        action="fraud.score",
        outcome="success",
        user=user,
        resource_type="transaction",
        resource_id=str(transaction.id),
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={
            "score": score,
            "level": level.value,
            "reasons_count": len(reasons),
            "reason_codes": [r["reason_code"] for r in reasons],
        },
    )

    return fraud_score


def get_transaction_risk_history(
    db: Session, transaction_id: uuid.UUID
) -> list[FraudScore]:
    """Retrieve full risk evaluation history for a transaction."""
    # Ensure transaction exists
    get_transaction(db, transaction_id)

    query = (
        select(FraudScore)
        .where(FraudScore.transaction_id == transaction_id)
        .order_by(FraudScore.scored_at.desc(), FraudScore.id.desc())
    )
    return list(db.execute(query).scalars().all())
