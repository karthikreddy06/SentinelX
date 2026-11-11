from datetime import datetime
import enum
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.connection import Base


class InvestigationStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class InvestigationPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class InvestigationOutcome(str, enum.Enum):
    CONFIRMED_FRAUD = "confirmed_fraud"
    FALSE_POSITIVE = "false_positive"
    INCONCLUSIVE = "inconclusive"


class EvidenceType(str, enum.Enum):
    TRANSACTION = "transaction"
    ALERT = "alert"
    ACCOUNT = "account"
    NOTE = "note"
    EXTERNAL_DOCUMENT = "external_document"


# Join table for investigations <-> alerts
investigation_alerts = Table(
    "investigation_alerts",
    Base.metadata,
    Column(
        "investigation_id",
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "alert_id",
        UUID(as_uuid=True),
        ForeignKey("alerts.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "added_at",
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    ),
)


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[InvestigationStatus] = mapped_column(
        Enum(
            InvestigationStatus,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_investigations_status",
        ),
        nullable=False,
        default=InvestigationStatus.OPEN,
        server_default=text("'open'"),
        index=True,
    )
    priority: Mapped[InvestigationPriority] = mapped_column(
        Enum(
            InvestigationPriority,
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda x: [e.value for e in x],
            name="ck_investigations_priority",
        ),
        nullable=False,
        default=InvestigationPriority.MEDIUM,
        server_default=text("'medium'"),
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    outcome: Mapped[InvestigationOutcome | None] = mapped_column(
        Enum(
            InvestigationOutcome,
            native_enum=False,
            create_constraint=True,
            length=30,
            values_callable=lambda x: [e.value for e in x],
            name="ck_investigations_outcome",
        ),
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
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    owner = relationship("User", foreign_keys=[owner_id])
    alerts = relationship(
        "Alert",
        secondary=investigation_alerts,
        backref="investigations",
        lazy="selectin",
    )
    notes = relationship(
        "InvestigationNote",
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="InvestigationNote.created_at.asc()",
    )
    evidence = relationship(
        "InvestigationEvidence",
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="InvestigationEvidence.created_at.asc()",
    )

    __table_args__ = (
        Index("ix_investigations_status_priority", "status", "priority"),
    )


class InvestigationNote(Base):
    __tablename__ = "investigation_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    # Relationships
    investigation = relationship("Investigation", back_populates="notes")
    author = relationship("User", foreign_keys=[author_id])


class InvestigationEvidence(Base):
    __tablename__ = "investigation_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    evidence_type: Mapped[EvidenceType] = mapped_column(
        Enum(
            EvidenceType,
            native_enum=False,
            create_constraint=True,
            length=30,
            values_callable=lambda x: [e.value for e in x],
            name="ck_investigation_evidence_type",
        ),
        nullable=False,
    )
    reference_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        index=True,
    )

    # Relationships
    investigation = relationship("Investigation", back_populates="evidence")
    author = relationship("User", foreign_keys=[added_by])
