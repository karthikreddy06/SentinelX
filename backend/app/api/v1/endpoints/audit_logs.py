from datetime import datetime
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.database.connection import get_db
from app.models.user import User, UserRole
from app.schemas.audit_log import AuditLogListResponse, AuditLogResponse
from app.services.audit_service import list_audit_logs

router = APIRouter()


@router.get(
    "",
    response_model=AuditLogListResponse,
    summary="List Audit Logs (Admin only)",
    description="Retrieve paginated security and system audit logs with optional filtering.",
)
def list_audit_logs_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> AuditLogListResponse:
    items, total = list_audit_logs(
        db=db,
        limit=limit,
        offset=offset,
        action=action,
        resource_type=resource_type,
        outcome=outcome,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
    )
    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
