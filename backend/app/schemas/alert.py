from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict

from app.models.alert import AlertSeverity, AlertStatus


class AlertEventResponse(BaseModel):
    id: uuid.UUID
    alert_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    action: str
    old_status: str | None = None
    new_status: str | None = None
    notes: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AlertResponse(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    severity: AlertSeverity
    status: AlertStatus
    reason_codes: list[str]
    risk_score: int
    assigned_to: uuid.UUID | None = None
    resolution_notes: str | None = None
    created_at: datetime
    updated_at: datetime
    events: list[AlertEventResponse] = []

    model_config = ConfigDict(from_attributes=True)


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AlertUpdate(BaseModel):
    status: AlertStatus | None = None
    assigned_to: uuid.UUID | None = None
    resolution_notes: str | None = None
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class AlertAssignRequest(BaseModel):
    assigned_to: uuid.UUID | None = None
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class AlertStatsResponse(BaseModel):
    counts_by_status: dict[str, int]
    counts_by_severity: dict[str, int]
    open_alerts_count: int
    avg_time_to_acknowledge_seconds: float | None = None
