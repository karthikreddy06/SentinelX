from datetime import datetime
import enum
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.connection import Base


class AlertSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    INVESTIGATING = "investigating"
    RESOLVED_FRAUD = "resolved_fraud"
    RESOLVED_FALSE_POSITIVE = "resolved_false_positive"
    DISMISSED = "dismissed"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(
            AlertSeverity,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_alerts_severity",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[AlertStatus] = mapped_column(
        Enum(
            AlertStatus,
            native_enum=False,
            create_constraint=True,
            length=30,
            values_callable=lambda x: [e.value for e in x],
            name="ck_alerts_status",
        ),
        nullable=False,
        default=AlertStatus.OPEN,
        server_default=text("'open'"),
        index=True,
    )
    reason_codes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
    )
    risk_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    resolution_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=func.now(),
    )

    # Relationships
    transaction = relationship("Transaction", backref="alerts")
    assignee = relationship("User", foreign_keys=[assigned_to])
    events = relationship(
        "AlertEvent",
        back_populates="alert",
        cascade="all, delete-orphan",
        order_by="AlertEvent.created_at.asc()",
    )

    __table_args__ = (
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_alerts_risk_score_range",
        ),
        Index("ix_alerts_status_severity", "status", "severity"),
    )


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alerts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    old_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    new_status: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    # Relationships
    alert = relationship("Alert", back_populates="events")
    actor = relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        Index("ix_alert_events_alert_id_created_at", "alert_id", "created_at"),
    )
