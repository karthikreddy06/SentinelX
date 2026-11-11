from datetime import datetime
from decimal import Decimal
import enum
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class TransactionType(str, enum.Enum):
    TRANSFER = "transfer"
    PAYMENT = "payment"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    sender_account_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    receiver_account_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_transactions_transaction_type",
        ),
        nullable=False,
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_transactions_status",
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    risk_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    risk_level: Mapped[RiskLevel | None] = mapped_column(
        Enum(
            RiskLevel,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_transactions_risk_level",
        ),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_transactions_currency_format"
        ),
        CheckConstraint(
            "sender_account_id <> receiver_account_id",
            name="ck_transactions_distinct_accounts",
        ),
        CheckConstraint(
            "risk_score BETWEEN 0 AND 100",
            name="ck_transactions_risk_score_range",
        ),
        Index("ix_transactions_sender_occurred", "sender_account_id", "occurred_at"),
        Index(
            "ix_transactions_receiver_occurred",
            "receiver_account_id",
            "occurred_at",
        ),
        Index("ix_transactions_status", "status"),
        Index("ix_transactions_occurred_at", "occurred_at"),
    )
