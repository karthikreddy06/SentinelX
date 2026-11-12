from datetime import datetime
import math
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, require_roles
from app.database.connection import get_db
from app.models.alert import AlertSeverity, AlertStatus
from app.models.user import User, UserRole
from app.schemas.alert import (
    AlertAssignRequest,
    AlertListResponse,
    AlertResponse,
    AlertStatsResponse,
    AlertUpdate,
)
from app.services import alert_service
from app.services.audit_service import record_audit_entry

router = APIRouter()

analyst_or_higher = require_roles(UserRole.ANALYST, UserRole.INVESTIGATOR, UserRole.ADMIN)
investigator_or_higher = require_roles(UserRole.INVESTIGATOR, UserRole.ADMIN)


@router.get(
    "/stats/summary",
    response_model=AlertStatsResponse,
    summary="Get alert statistics summary",
)
def get_alert_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> AlertStatsResponse:
    stats = alert_service.get_stats_summary(db)
    return AlertStatsResponse(**stats)


@router.get(
    "",
    response_model=AlertListResponse,
    summary="List alerts with filtering and pagination",
)
def list_alerts(
    status: AlertStatus | None = Query(None, description="Filter by status"),
    severity: AlertSeverity | None = Query(None, description="Filter by severity"),
    assigned_to: uuid.UUID | None = Query(None, description="Filter by assigned user UUID"),
    date_from: datetime | None = Query(None, description="Filter created on or after date"),
    date_to: datetime | None = Query(None, description="Filter created on or before date"),
    min_score: int | None = Query(None, ge=0, le=100, description="Minimum risk score"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> AlertListResponse:
    items, total = alert_service.list_alerts(
        db=db,
        status_filter=status,
        severity_filter=severity,
        assigned_to=assigned_to,
        date_from=date_from,
        date_to=date_to,
        min_score=min_score,
        page=page,
        page_size=page_size,
    )
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return AlertListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/{id}",
    response_model=AlertResponse,
    summary="Get alert by ID with event history timeline",
)
def get_alert(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> AlertResponse:
    alert = alert_service.get_alert_by_id(db, id)
    return AlertResponse.model_validate(alert)


@router.patch(
    "/{id}",
    response_model=AlertResponse,
    summary="Update alert status, assignment, or notes",
)
def update_alert(
    id: uuid.UUID,
    update_data: AlertUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> AlertResponse:
    alert = alert_service.update_alert(
        db=db,
        alert_id=id,
        update_data=update_data,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="alert.update",
        outcome="success",
        user=current_user,
        resource_type="alert",
        resource_id=str(alert.id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata=update_data.model_dump(exclude_unset=True),
    )

    return AlertResponse.model_validate(alert)


@router.post(
    "/{id}/assign",
    response_model=AlertResponse,
    summary="Assign alert to an investigator or self",
)
def assign_alert(
    id: uuid.UUID,
    request: Request,
    assign_data: AlertAssignRequest = AlertAssignRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> AlertResponse:
    alert = alert_service.assign_alert(
        db=db,
        alert_id=id,
        assigned_to=assign_data.assigned_to,
        notes=assign_data.notes,
        current_user=current_user,
    )

    record_audit_entry(
        db=db,
        action="alert.assign",
        outcome="success",
        user=current_user,
        resource_type="alert",
        resource_id=str(alert.id),
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
        metadata={
            "assigned_to": str(alert.assigned_to) if alert.assigned_to else None,
            "notes": assign_data.notes,
        },
    )

    return AlertResponse.model_validate(alert)
