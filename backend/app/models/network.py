from datetime import datetime
import enum
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class NetworkRiskFlag(str, enum.Enum):
    NORMAL = "normal"
    MULE_CYCLE = "mule_cycle"
    FAN_IN_AGGREGATOR = "fan_in_aggregator"
    FAN_OUT_DISTRIBUTOR = "fan_out_distributor"
    HIGH_CENTRALITY_HUB = "high_centrality_hub"


class AccountNetworkRisk(Base):
    __tablename__ = "account_network_risk"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
    )
    account_id: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    centrality_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
    )
    in_degree: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    out_degree: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    fan_in_out_ratio: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
    )
    total_volume_in: Mapped[float] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=0.0,
        server_default=text("0.00"),
    )
    total_volume_out: Mapped[float] = mapped_column(
        Numeric(18, 2),
        nullable=False,
        default=0.0,
        server_default=text("0.00"),
    )
    is_mule_suspect: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )
    cycle_length: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    community_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    risk_flag: Mapped[NetworkRiskFlag] = mapped_column(
        Enum(
            NetworkRiskFlag,
            native_enum=False,
            create_constraint=True,
            length=30,
            values_callable=lambda x: [e.value for e in x],
            name="ck_account_network_risk_flag",
        ),
        nullable=False,
        default=NetworkRiskFlag.NORMAL,
        server_default=text("'normal'"),
    )
    risk_reasons: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_account_network_risk_flag", "risk_flag"),
        Index("ix_account_network_risk_calculated_at", "calculated_at"),
    )

