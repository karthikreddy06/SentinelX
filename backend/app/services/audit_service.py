from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.user import User

SENSITIVE_KEYS = {"password", "token", "refresh_token", "access_token", "secret", "authorization"}


def _sanitize_metadata(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not data:
        return None
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        if any(sens in k.lower() for sens in SENSITIVE_KEYS):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_metadata(v)
        else:
            sanitized[k] = v
    return sanitized


def record_audit_entry(
    db: Session,
    action: str,
    outcome: str,
    user: User | None = None,
    user_id: uuid.UUID | None = None,
    user_email: str | None = None,
    role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an audit log entry in the database."""
    effective_user_id = user.id if user else user_id
    effective_user_email = user.email if user else user_email
    effective_role = user.role.value if (user and hasattr(user.role, "value")) else role

    log_entry = AuditLog(
        user_id=effective_user_id,
        user_email=effective_user_email,
        role=effective_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        user_agent=user_agent,
        outcome=outcome,
        metadata_json=_sanitize_metadata(metadata),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


def list_audit_logs(
    db: Session,
    limit: int = 20,
    offset: int = 0,
    action: str | None = None,
    resource_type: str | None = None,
    outcome: str | None = None,
    user_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[list[AuditLog], int]:
    """Query audit logs with pagination and optional filters."""
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.id))

    conditions = []
    if action is not None:
        conditions.append(AuditLog.action == action)
    if resource_type is not None:
        conditions.append(AuditLog.resource_type == resource_type)
    if outcome is not None:
        conditions.append(AuditLog.outcome == outcome)
    if user_id is not None:
        conditions.append(AuditLog.user_id == user_id)
    if date_from is not None:
        conditions.append(AuditLog.timestamp >= date_from)
    if date_to is not None:
        conditions.append(AuditLog.timestamp <= date_to)

    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)

    total = db.execute(count_query).scalar_one()
    query = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    items = list(db.execute(query).scalars().all())
    return items, total
