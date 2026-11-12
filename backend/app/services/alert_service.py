from datetime import datetime
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.alert import Alert, AlertEvent, AlertSeverity, AlertStatus
from app.models.transaction import RiskLevel
from app.models.user import User, UserRole
from app.schemas.alert import AlertUpdate


def determine_severity(score: int, level: RiskLevel | None = None) -> AlertSeverity:
    if score >= 85 or level == RiskLevel.HIGH:
        return AlertSeverity.CRITICAL if score >= 85 else AlertSeverity.HIGH
    if score >= 70:
        return AlertSeverity.HIGH
    if score >= 50 or level == RiskLevel.MEDIUM:
        return AlertSeverity.MEDIUM
    return AlertSeverity.LOW


def create_alert_if_eligible(
    db: Session,
    transaction_id: uuid.UUID,
    risk_score: int,
    risk_level: RiskLevel | None,
    reason_codes: list[str],
    actor_id: uuid.UUID | None = None,
) -> Alert | None:
    """
    Auto-generate a fraud alert if risk_score meets the threshold or level is high.
    Deduplicates: never creates duplicate open/acknowledged/investigating alerts.
    """
    if risk_score < settings.ALERT_AUTO_CREATE_THRESHOLD and risk_level != RiskLevel.HIGH:
        return None

    # Check for existing active alert for this transaction
    active_statuses = [
        AlertStatus.OPEN,
        AlertStatus.ACKNOWLEDGED,
        AlertStatus.INVESTIGATING,
    ]
    existing_alert = db.execute(
        select(Alert).where(
            Alert.transaction_id == transaction_id,
            Alert.status.in_(active_statuses),
        )
    ).scalar_one_or_none()

    if existing_alert is not None:
        return existing_alert

    severity = determine_severity(risk_score, risk_level)

    alert = Alert(
        transaction_id=transaction_id,
        severity=severity,
        status=AlertStatus.OPEN,
        reason_codes=reason_codes,
        risk_score=risk_score,
    )
    db.add(alert)
    db.flush()  # populate alert.id

    initial_event = AlertEvent(
        alert_id=alert.id,
        actor_id=actor_id,
        action="created",
        old_status=None,
        new_status=AlertStatus.OPEN.value,
        notes=f"Auto-generated alert: score={risk_score}, severity={severity.value}, reasons={', '.join(reason_codes)}",
    )
    db.add(initial_event)
    db.commit()
    db.refresh(alert)
    return alert


def get_alert_by_id(db: Session, alert_id: uuid.UUID) -> Alert:
    alert = db.execute(
        select(Alert)
        .options(joinedload(Alert.events))
        .where(Alert.id == alert_id)
    ).unique().scalar_one_or_none()

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' not found.",
        )
    return alert


def list_alerts(
    db: Session,
    status_filter: AlertStatus | None = None,
    severity_filter: AlertSeverity | None = None,
    assigned_to: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_score: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Alert], int]:
    base_query = select(Alert)

    if status_filter:
        base_query = base_query.where(Alert.status == status_filter)
    if severity_filter:
        base_query = base_query.where(Alert.severity == severity_filter)
    if assigned_to:
        base_query = base_query.where(Alert.assigned_to == assigned_to)
    if date_from:
        base_query = base_query.where(Alert.created_at >= date_from)
    if date_to:
        base_query = base_query.where(Alert.created_at <= date_to)
    if min_score is not None:
        base_query = base_query.where(Alert.risk_score >= min_score)

    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    items = (
        db.execute(
            base_query.options(joinedload(Alert.events))
            .order_by(Alert.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .unique()
        .scalars()
        .all()
    )
    return list(items), total


ALLOWED_TRANSITIONS = {
    AlertStatus.OPEN: [
        AlertStatus.ACKNOWLEDGED,
        AlertStatus.INVESTIGATING,
        AlertStatus.DISMISSED,
    ],
    AlertStatus.ACKNOWLEDGED: [
        AlertStatus.INVESTIGATING,
        AlertStatus.DISMISSED,
    ],
    AlertStatus.INVESTIGATING: [
        AlertStatus.RESOLVED_FRAUD,
        AlertStatus.RESOLVED_FALSE_POSITIVE,
        AlertStatus.OPEN,
    ],
    AlertStatus.DISMISSED: [
        AlertStatus.OPEN,
    ],
    AlertStatus.RESOLVED_FRAUD: [],
    AlertStatus.RESOLVED_FALSE_POSITIVE: [],
}


def update_alert(
    db: Session,
    alert_id: uuid.UUID,
    update_data: AlertUpdate,
    current_user: User,
) -> Alert:
    alert = get_alert_by_id(db, alert_id)

    # 1. Handle assignment
    if update_data.assigned_to is not None and update_data.assigned_to != alert.assigned_to:
        # Verify user exists
        assigned_user = db.get(User, update_data.assigned_to)
        if not assigned_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{update_data.assigned_to}' not found.",
            )
        alert.assigned_to = update_data.assigned_to
        event = AlertEvent(
            alert_id=alert.id,
            actor_id=current_user.id,
            action="assigned",
            notes=f"Assigned to {assigned_user.full_name} ({assigned_user.email})",
        )
        db.add(event)

    # 2. Handle resolution notes
    if update_data.resolution_notes is not None:
        alert.resolution_notes = update_data.resolution_notes
        if update_data.status is None:
            event = AlertEvent(
                alert_id=alert.id,
                actor_id=current_user.id,
                action="note_added",
                notes=update_data.resolution_notes,
            )
            db.add(event)

    # 3. Handle status transition
    if update_data.status is not None and update_data.status != alert.status:
        target_status = update_data.status
        allowed = ALLOWED_TRANSITIONS.get(alert.status, [])

        if target_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid transition from '{alert.status.value}' to '{target_status.value}'.",
            )

        # RBAC: Only assigned investigator or admin can resolve
        if target_status in (AlertStatus.RESOLVED_FRAUD, AlertStatus.RESOLVED_FALSE_POSITIVE):
            if current_user.role != UserRole.ADMIN and alert.assigned_to != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only the assigned investigator or an admin can resolve this alert.",
                )
            # Require resolution notes
            eff_notes = update_data.resolution_notes or alert.resolution_notes
            if not eff_notes or not eff_notes.strip():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Resolution notes are required when resolving an alert.",
                )

        # Reopen dismissed alert: admin only
        if alert.status == AlertStatus.DISMISSED and target_status == AlertStatus.OPEN:
            if current_user.role != UserRole.ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only admins can reopen a dismissed alert.",
                )

        old_status = alert.status.value
        alert.status = target_status
        event = AlertEvent(
            alert_id=alert.id,
            actor_id=current_user.id,
            action="status_changed",
            old_status=old_status,
            new_status=target_status.value,
            notes=update_data.notes or f"Status changed from {old_status} to {target_status.value}",
        )
        db.add(event)

    db.commit()
    db.refresh(alert)
    return alert


def assign_alert(
    db: Session,
    alert_id: uuid.UUID,
    assigned_to: uuid.UUID | None,
    notes: str | None,
    current_user: User,
) -> Alert:
    target_user_id = assigned_to or current_user.id
    target_user = db.get(User, target_user_id)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{target_user_id}' not found.",
        )

    alert = get_alert_by_id(db, alert_id)
    alert.assigned_to = target_user.id

    event = AlertEvent(
        alert_id=alert.id,
        actor_id=current_user.id,
        action="assigned",
        notes=notes or f"Assigned to {target_user.full_name} ({target_user.email})",
    )
    db.add(event)
    db.commit()
    db.refresh(alert)
    return alert


def get_stats_summary(db: Session) -> dict:
    # Counts by status
    status_counts = {}
    for st in AlertStatus:
        status_counts[st.value] = 0

    st_results = db.execute(
        select(Alert.status, func.count(Alert.id)).group_by(Alert.status)
    ).all()
    for st, cnt in st_results:
        status_counts[st.value if isinstance(st, AlertStatus) else str(st)] = cnt

    # Counts by severity
    severity_counts = {}
    for sev in AlertSeverity:
        severity_counts[sev.value] = 0

    sev_results = db.execute(
        select(Alert.severity, func.count(Alert.id)).group_by(Alert.severity)
    ).all()
    for sev, cnt in sev_results:
        severity_counts[sev.value if isinstance(sev, AlertSeverity) else str(sev)] = cnt

    open_alerts_count = status_counts.get(AlertStatus.OPEN.value, 0)

    # Average time to acknowledge:
    # Join alerts and alert_events where alert_events.new_status == 'acknowledged'
    ack_query = (
        select(
            func.avg(
                func.extract("epoch", AlertEvent.created_at)
                - func.extract("epoch", Alert.created_at)
            )
        )
        .join(Alert, AlertEvent.alert_id == Alert.id)
        .where(AlertEvent.new_status == AlertStatus.ACKNOWLEDGED.value)
    )
    avg_ack_seconds = db.scalar(ack_query)
    avg_ack_seconds_float = float(avg_ack_seconds) if avg_ack_seconds is not None else None

    return {
        "counts_by_status": status_counts,
        "counts_by_severity": severity_counts,
        "open_alerts_count": open_alerts_count,
        "avg_time_to_acknowledge_seconds": avg_ack_seconds_float,
    }
