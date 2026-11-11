from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.investigation import (
    EvidenceType,
    InvestigationOutcome,
    InvestigationPriority,
    InvestigationStatus,
)
from app.schemas.alert import AlertResponse


class InvestigationNoteCreate(BaseModel):
    body: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


class InvestigationNoteResponse(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    author_id: uuid.UUID
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvestigationEvidenceCreate(BaseModel):
    evidence_type: EvidenceType
    reference_id: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


class InvestigationEvidenceResponse(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    evidence_type: EvidenceType
    reference_id: str
    description: str
    added_by: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InvestigationCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    priority: InvestigationPriority = InvestigationPriority.MEDIUM
    owner_id: uuid.UUID | None = None
    alert_ids: list[uuid.UUID] = []

    model_config = ConfigDict(extra="forbid")


class InvestigationUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    description: str | None = None
    priority: InvestigationPriority | None = None
    owner_id: uuid.UUID | None = None
    status: InvestigationStatus | None = None

    model_config = ConfigDict(extra="forbid")


class InvestigationLinkAlertsRequest(BaseModel):
    alert_ids: list[uuid.UUID] = Field(..., min_length=1)

    model_config = ConfigDict(extra="forbid")


class InvestigationCloseRequest(BaseModel):
    outcome: InvestigationOutcome
    closing_notes: str | None = None

    model_config = ConfigDict(extra="forbid")


class InvestigationTimelineItem(BaseModel):
    event_type: str
    timestamp: datetime
    actor_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    metadata: dict | None = None


class InvestigationResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    status: InvestigationStatus
    priority: InvestigationPriority
    owner_id: uuid.UUID
    outcome: InvestigationOutcome | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    alerts: list[AlertResponse] = []
    notes: list[InvestigationNoteResponse] = []
    evidence: list[InvestigationEvidenceResponse] = []
    timeline: list[InvestigationTimelineItem] = []

    model_config = ConfigDict(from_attributes=True)


class InvestigationListResponse(BaseModel):
    items: list[InvestigationResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
