import math
import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, require_roles
from app.database.connection import get_db
from app.models.investigation import (
    InvestigationOutcome,
    InvestigationPriority,
    InvestigationStatus,
)
from app.models.user import User, UserRole
from app.schemas.investigation import (
    InvestigationCloseRequest,
    InvestigationCreate,
    InvestigationEvidenceCreate,
    InvestigationEvidenceResponse,
    InvestigationLinkAlertsRequest,
    InvestigationListResponse,
    InvestigationNoteCreate,
    InvestigationNoteResponse,
    InvestigationResponse,
    InvestigationUpdate,
)
from app.services import investigation_service
from app.services.audit_service import record_audit_entry

router = APIRouter()

analyst_or_higher = require_roles(UserRole.ANALYST, UserRole.INVESTIGATOR, UserRole.ADMIN)
investigator_or_higher = require_roles(UserRole.INVESTIGATOR, UserRole.ADMIN)
admin_only = require_roles(UserRole.ADMIN)


@router.post(
    "",
    response_model=InvestigationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new investigation case",
)
def create_investigation(
    create_data: InvestigationCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationResponse:
    investigation = investigation_service.create_investigation(
        db=db,
        create_data=create_data,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.create",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(investigation.id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={"title": investigation.title, "priority": investigation.priority.value},
    )

    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp


@router.get(
    "",
    response_model=InvestigationListResponse,
    summary="List investigations with filters and pagination",
)
def list_investigations(
    status: InvestigationStatus | None = Query(None, description="Filter by status"),
    priority: InvestigationPriority | None = Query(None, description="Filter by priority"),
    owner_id: uuid.UUID | None = Query(None, description="Filter by owner UUID"),
    outcome: InvestigationOutcome | None = Query(None, description="Filter by outcome"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> InvestigationListResponse:
    items, total = investigation_service.list_investigations(
        db=db,
        status_filter=status,
        priority_filter=priority,
        owner_id=owner_id,
        outcome_filter=outcome,
        page=page,
        page_size=page_size,
    )
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    response_items = []
    for inv in items:
        inv_resp = InvestigationResponse.model_validate(inv)
        inv_resp.timeline = investigation_service.build_investigation_timeline(inv)
        response_items.append(inv_resp)

    return InvestigationListResponse(
        items=response_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{id}",
    response_model=InvestigationResponse,
    summary="Get full investigation details and aggregated timeline",
)
def get_investigation(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> InvestigationResponse:
    investigation = investigation_service.get_investigation_by_id(db, id)
    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp


@router.patch(
    "/{id}",
    response_model=InvestigationResponse,
    summary="Update investigation fields",
)
def update_investigation(
    id: uuid.UUID,
    update_data: InvestigationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationResponse:
    investigation = investigation_service.update_investigation(
        db=db,
        investigation_id=id,
        update_data=update_data,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.update",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(investigation.id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata=update_data.model_dump(exclude_unset=True),
    )

    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp


@router.post(
    "/{id}/notes",
    response_model=InvestigationNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a note to an investigation",
)
def add_note(
    id: uuid.UUID,
    note_data: InvestigationNoteCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationNoteResponse:
    note = investigation_service.add_note(
        db=db,
        investigation_id=id,
        note_data=note_data,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.note_add",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={"note_id": str(note.id)},
    )

    return InvestigationNoteResponse.model_validate(note)


@router.post(
    "/{id}/evidence",
    response_model=InvestigationEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Attach evidence to an investigation",
)
def add_evidence(
    id: uuid.UUID,
    evidence_data: InvestigationEvidenceCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationEvidenceResponse:
    evidence = investigation_service.add_evidence(
        db=db,
        investigation_id=id,
        evidence_data=evidence_data,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.evidence_add",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={"evidence_id": str(evidence.id), "evidence_type": evidence.evidence_type.value},
    )

    return InvestigationEvidenceResponse.model_validate(evidence)


@router.post(
    "/{id}/alerts",
    response_model=InvestigationResponse,
    summary="Link additional alerts to an investigation",
)
def link_alerts(
    id: uuid.UUID,
    link_data: InvestigationLinkAlertsRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationResponse:
    investigation = investigation_service.link_alerts(
        db=db,
        investigation_id=id,
        alert_ids=link_data.alert_ids,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.alerts_link",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={"alert_ids": [str(a) for a in link_data.alert_ids]},
    )

    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp


@router.delete(
    "/{id}/alerts/{alert_id}",
    response_model=InvestigationResponse,
    summary="Unlink an alert from an investigation",
)
def unlink_alert(
    id: uuid.UUID,
    alert_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationResponse:
    investigation = investigation_service.unlink_alert(
        db=db,
        investigation_id=id,
        alert_id=alert_id,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.alert_unlink",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={"alert_id": str(alert_id)},
    )

    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp


@router.post(
    "/{id}/close",
    response_model=InvestigationResponse,
    summary="Close an investigation with an outcome and closing notes",
)
def close_investigation(
    id: uuid.UUID,
    close_data: InvestigationCloseRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> InvestigationResponse:
    investigation = investigation_service.close_investigation(
        db=db,
        investigation_id=id,
        close_data=close_data,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.close",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={"outcome": close_data.outcome.value, "closing_notes": close_data.closing_notes},
    )

    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp


@router.post(
    "/{id}/reopen",
    response_model=InvestigationResponse,
    summary="Reopen a closed investigation (admin only)",
)
def reopen_investigation(
    id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(admin_only),
) -> InvestigationResponse:
    investigation = investigation_service.reopen_investigation(
        db=db,
        investigation_id=id,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="investigation.reopen",
        outcome="success",
        user=current_user,
        resource_type="investigation",
        resource_id=str(id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={},
    )

    timeline = investigation_service.build_investigation_timeline(investigation)
    resp = InvestigationResponse.model_validate(investigation)
    resp.timeline = timeline
    return resp
