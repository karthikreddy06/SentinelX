from datetime import datetime, timezone
import math
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.alert import Alert, AlertEvent, AlertStatus
from app.models.investigation import (
    EvidenceType,
    Investigation,
    InvestigationEvidence,
    InvestigationNote,
    InvestigationOutcome,
    InvestigationPriority,
    InvestigationStatus,
    investigation_alerts,
)
from app.models.user import User, UserRole
from app.schemas.investigation import (
    InvestigationCloseRequest,
    InvestigationCreate,
    InvestigationEvidenceCreate,
    InvestigationNoteCreate,
    InvestigationTimelineItem,
    InvestigationUpdate,
)


def get_investigation_by_id(db: Session, investigation_id: uuid.UUID) -> Investigation:
    investigation = db.execute(
        select(Investigation)
        .options(
            selectinload(Investigation.alerts).joinedload(Alert.events),
            selectinload(Investigation.notes),
            selectinload(Investigation.evidence),
        )
        .where(Investigation.id == investigation_id)
    ).scalar_one_or_none()

    if not investigation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Investigation '{investigation_id}' not found.",
        )
    return investigation


def build_investigation_timeline(investigation: Investigation) -> list[InvestigationTimelineItem]:
    timeline: list[InvestigationTimelineItem] = []

    # 1. Investigation created
    timeline.append(
        InvestigationTimelineItem(
            event_type="investigation_created",
            timestamp=investigation.created_at,
            actor_id=investigation.owner_id,
            title="Investigation Opened",
            description=f"Investigation created with title: '{investigation.title}'",
        )
    )

    # 2. Notes
    for note in investigation.notes:
        timeline.append(
            InvestigationTimelineItem(
                event_type="note",
                timestamp=note.created_at,
                actor_id=note.author_id,
                title="Investigation Note Added",
                description=note.body,
            )
        )

    # 3. Evidence
    for ev in investigation.evidence:
        timeline.append(
            InvestigationTimelineItem(
                event_type="evidence",
                timestamp=ev.created_at,
                actor_id=ev.added_by,
                title=f"Evidence Attached ({ev.evidence_type.value})",
                description=ev.description,
                metadata={"reference_id": ev.reference_id, "evidence_type": ev.evidence_type.value},
            )
        )

    # 4. Alert events for linked alerts
    for alert in investigation.alerts:
        for a_event in alert.events:
            timeline.append(
                InvestigationTimelineItem(
                    event_type="alert_event",
                    timestamp=a_event.created_at,
                    actor_id=a_event.actor_id,
                    title=f"Alert Event: {a_event.action}",
                    description=a_event.notes,
                    metadata={
                        "alert_id": str(a_event.alert_id),
                        "action": a_event.action,
                        "old_status": a_event.old_status,
                        "new_status": a_event.new_status,
                    },
                )
            )

    # 5. Closed event if applicable
    if investigation.closed_at and investigation.outcome:
        timeline.append(
            InvestigationTimelineItem(
                event_type="investigation_closed",
                timestamp=investigation.closed_at,
                actor_id=None,
                title=f"Investigation Closed: {investigation.outcome.value}",
                description=f"Closed with outcome: {investigation.outcome.value}",
                metadata={"outcome": investigation.outcome.value},
            )
        )

    # Sort chronological
    timeline.sort(key=lambda x: x.timestamp)
    return timeline


def create_investigation(
    db: Session,
    create_data: InvestigationCreate,
    current_user: User,
) -> Investigation:
    owner_id = create_data.owner_id or current_user.id
    owner = db.get(User, owner_id)
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{owner_id}' not found.",
        )

    investigation = Investigation(
        title=create_data.title,
        description=create_data.description,
        priority=create_data.priority,
        owner_id=owner_id,
        status=InvestigationStatus.OPEN,
    )
    db.add(investigation)
    db.flush()

    # Link initial alerts if provided
    if create_data.alert_ids:
        for alert_id in create_data.alert_ids:
            alert = db.get(Alert, alert_id)
            if not alert:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Alert '{alert_id}' not found.",
                )
            investigation.alerts.append(alert)
            # Auto sync alert status to investigating
            if alert.status != AlertStatus.INVESTIGATING:
                old_status = alert.status.value
                alert.status = AlertStatus.INVESTIGATING
                alert_event = AlertEvent(
                    alert_id=alert.id,
                    actor_id=current_user.id,
                    action="status_changed",
                    old_status=old_status,
                    new_status=AlertStatus.INVESTIGATING.value,
                    notes=f"Linked to investigation '{investigation.title}'",
                )
                db.add(alert_event)

    db.commit()
    db.refresh(investigation)
    return get_investigation_by_id(db, investigation.id)


def list_investigations(
    db: Session,
    status_filter: InvestigationStatus | None = None,
    priority_filter: InvestigationPriority | None = None,
    owner_id: uuid.UUID | None = None,
    outcome_filter: InvestigationOutcome | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Investigation], int]:
    base_query = select(Investigation)

    if status_filter:
        base_query = base_query.where(Investigation.status == status_filter)
    if priority_filter:
        base_query = base_query.where(Investigation.priority == priority_filter)
    if owner_id:
        base_query = base_query.where(Investigation.owner_id == owner_id)
    if outcome_filter:
        base_query = base_query.where(Investigation.outcome == outcome_filter)

    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0

    items = (
        db.execute(
            base_query.options(
                selectinload(Investigation.alerts).joinedload(Alert.events),
                selectinload(Investigation.notes),
                selectinload(Investigation.evidence),
            )
            .order_by(Investigation.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return list(items), total


def update_investigation(
    db: Session,
    investigation_id: uuid.UUID,
    update_data: InvestigationUpdate,
    current_user: User,
) -> Investigation:
    investigation = get_investigation_by_id(db, investigation_id)

    if investigation.status == InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update a closed investigation. Reopen it first.",
        )

    if update_data.owner_id is not None:
        new_owner = db.get(User, update_data.owner_id)
        if not new_owner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User '{update_data.owner_id}' not found.",
            )
        investigation.owner_id = update_data.owner_id

    if update_data.title is not None:
        investigation.title = update_data.title
    if update_data.description is not None:
        investigation.description = update_data.description
    if update_data.priority is not None:
        investigation.priority = update_data.priority
    if update_data.status is not None:
        if update_data.status == InvestigationStatus.CLOSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use the close endpoint to close an investigation with an outcome.",
            )
        investigation.status = update_data.status

    db.commit()
    db.refresh(investigation)
    return get_investigation_by_id(db, investigation.id)


def add_note(
    db: Session,
    investigation_id: uuid.UUID,
    note_data: InvestigationNoteCreate,
    current_user: User,
) -> InvestigationNote:
    investigation = get_investigation_by_id(db, investigation_id)
    if investigation.status == InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add notes to a closed investigation.",
        )

    # Progress status if currently OPEN
    if investigation.status == InvestigationStatus.OPEN:
        investigation.status = InvestigationStatus.IN_PROGRESS

    note = InvestigationNote(
        investigation_id=investigation.id,
        author_id=current_user.id,
        body=note_data.body,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def add_evidence(
    db: Session,
    investigation_id: uuid.UUID,
    evidence_data: InvestigationEvidenceCreate,
    current_user: User,
) -> InvestigationEvidence:
    investigation = get_investigation_by_id(db, investigation_id)
    if investigation.status == InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot attach evidence to a closed investigation.",
        )

    # Progress status if currently OPEN
    if investigation.status == InvestigationStatus.OPEN:
        investigation.status = InvestigationStatus.IN_PROGRESS

    evidence = InvestigationEvidence(
        investigation_id=investigation.id,
        evidence_type=evidence_data.evidence_type,
        reference_id=evidence_data.reference_id,
        description=evidence_data.description,
        added_by=current_user.id,
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


def link_alerts(
    db: Session,
    investigation_id: uuid.UUID,
    alert_ids: list[uuid.UUID],
    current_user: User,
) -> Investigation:
    investigation = get_investigation_by_id(db, investigation_id)
    if investigation.status == InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot link alerts to a closed investigation.",
        )

    existing_alert_ids = {a.id for a in investigation.alerts}
    for alert_id in alert_ids:
        if alert_id in existing_alert_ids:
            continue

        alert = db.get(Alert, alert_id)
        if not alert:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alert '{alert_id}' not found.",
            )

        investigation.alerts.append(alert)
        if alert.status != AlertStatus.INVESTIGATING:
            old_status = alert.status.value
            alert.status = AlertStatus.INVESTIGATING
            alert_event = AlertEvent(
                alert_id=alert.id,
                actor_id=current_user.id,
                action="status_changed",
                old_status=old_status,
                new_status=AlertStatus.INVESTIGATING.value,
                notes=f"Linked to investigation '{investigation.title}'",
            )
            db.add(alert_event)

    db.commit()
    return get_investigation_by_id(db, investigation.id)


def unlink_alert(
    db: Session,
    investigation_id: uuid.UUID,
    alert_id: uuid.UUID,
    current_user: User,
) -> Investigation:
    investigation = get_investigation_by_id(db, investigation_id)
    if investigation.status == InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unlink alerts from a closed investigation.",
        )

    target_alert = next((a for a in investigation.alerts if a.id == alert_id), None)
    if not target_alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' is not linked to this investigation.",
        )

    investigation.alerts.remove(target_alert)
    db.commit()
    return get_investigation_by_id(db, investigation.id)


def close_investigation(
    db: Session,
    investigation_id: uuid.UUID,
    close_data: InvestigationCloseRequest,
    current_user: User,
) -> Investigation:
    investigation = get_investigation_by_id(db, investigation_id)

    if investigation.status == InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Investigation is already closed.",
        )

    # RBAC: Only owner or admin can close
    if current_user.role != UserRole.ADMIN and investigation.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the investigation owner or an admin can close this investigation.",
        )

    now = datetime.now(timezone.utc)
    investigation.status = InvestigationStatus.CLOSED
    investigation.outcome = close_data.outcome
    investigation.closed_at = now

    # Add closing note if provided
    if close_data.closing_notes:
        note = InvestigationNote(
            investigation_id=investigation.id,
            author_id=current_user.id,
            body=f"Investigation closed with outcome '{close_data.outcome.value}': {close_data.closing_notes}",
        )
        db.add(note)

    # Auto sync linked alerts to corresponding resolved state
    target_alert_status = None
    if close_data.outcome == InvestigationOutcome.CONFIRMED_FRAUD:
        target_alert_status = AlertStatus.RESOLVED_FRAUD
    elif close_data.outcome == InvestigationOutcome.FALSE_POSITIVE:
        target_alert_status = AlertStatus.RESOLVED_FALSE_POSITIVE

    if target_alert_status is not None:
        for alert in investigation.alerts:
            if alert.status not in (AlertStatus.RESOLVED_FRAUD, AlertStatus.RESOLVED_FALSE_POSITIVE):
                old_status = alert.status.value
                alert.status = target_alert_status
                alert.resolution_notes = (
                    close_data.closing_notes
                    or f"Auto-resolved from investigation '{investigation.title}' ({close_data.outcome.value})"
                )
                alert_event = AlertEvent(
                    alert_id=alert.id,
                    actor_id=current_user.id,
                    action="status_changed",
                    old_status=old_status,
                    new_status=target_alert_status.value,
                    notes=f"Auto-resolved upon investigation closure ({close_data.outcome.value})",
                )
                db.add(alert_event)

    db.commit()
    return get_investigation_by_id(db, investigation.id)


def reopen_investigation(
    db: Session,
    investigation_id: uuid.UUID,
    current_user: User,
) -> Investigation:
    investigation = get_investigation_by_id(db, investigation_id)

    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can reopen a closed investigation.",
        )

    if investigation.status != InvestigationStatus.CLOSED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only closed investigations can be reopened.",
        )

    investigation.status = InvestigationStatus.IN_PROGRESS
    investigation.outcome = None
    investigation.closed_at = None

    note = InvestigationNote(
        investigation_id=investigation.id,
        author_id=current_user.id,
        body="Investigation reopened by administrator.",
    )
    db.add(note)
    db.commit()
    return get_investigation_by_id(db, investigation.id)
