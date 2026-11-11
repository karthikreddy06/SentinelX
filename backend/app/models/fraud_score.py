from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.transaction import RiskLevel


class FraudScore(Base):
    __tablename__ = "fraud_scores"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    level: Mapped[RiskLevel] = mapped_column(
        Enum(
            RiskLevel,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_fraud_scores_level",
        ),
        nullable=False,
    )
    engine_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    reasons_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    features_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    transaction: Mapped["Transaction"] = relationship(  # type: ignore # noqa: F821
        "Transaction",
        back_populates="fraud_scores",
    )

    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_fraud_scores_score_range"),
        Index("ix_fraud_scores_tx_scored", "transaction_id", "scored_at"),
    )
