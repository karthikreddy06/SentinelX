from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.core.config import settings
from app.database.connection import SessionLocal
from app.models.alert import Alert, AlertEvent, AlertSeverity, AlertStatus
from app.models.fraud_score import FraudScore
from app.models.outbox import OutboxEvent, OutboxStatus
from app.models.user import RefreshToken

logger = logging.getLogger("sentinelx.tasks.maintenance")


@celery_app.task(name="app.tasks.maintenance_tasks.escalate_stale_alerts_task")
def escalate_stale_alerts_task() -> dict[str, int]:
    """
    Find open alerts older than CELERY_STALE_ALERT_THRESHOLD_HOURS (default 24h)
    that have not been triaged, and escalate their severity to HIGH.
    """
    logger.info("Executing periodic stale alert escalation check...")
    db = SessionLocal()
    escalated_count = 0
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=settings.CELERY_STALE_ALERT_THRESHOLD_HOURS
        )
        query = select(Alert).where(
            Alert.status == AlertStatus.OPEN,
            Alert.created_at < cutoff,
            Alert.severity != AlertSeverity.HIGH,
        )
        stale_alerts = list(db.execute(query).scalars().all())

        for alert in stale_alerts:
            old_sev = alert.severity.value
            alert.severity = AlertSeverity.HIGH

            event = AlertEvent(
                alert_id=alert.id,
                action="escalate_sla",
                old_status=alert.status.value,
                new_status=alert.status.value,
                notes=f"Automated SLA escalation: alert open for > {settings.CELERY_STALE_ALERT_THRESHOLD_HOURS} hours without resolution. Severity increased from {old_sev} to HIGH.",
            )
            db.add(event)
            escalated_count += 1

        db.commit()
        logger.info(f"Escalated {escalated_count} stale alerts to HIGH severity.")
        return {"escalated_count": escalated_count}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error during stale alert escalation: {exc}")
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.maintenance_tasks.cleanup_expired_tokens_task")
def cleanup_expired_tokens_task() -> dict[str, int]:
    """
    Purge expired or explicitly revoked JWT refresh tokens to maintain
    database cleanliness and query efficiency.
    """
    logger.info("Executing periodic expired token purge...")
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stmt = delete(RefreshToken).where(
            (RefreshToken.expires_at < now) | (RefreshToken.revoked_at.is_not(None))
        )
        result = db.execute(stmt)
        deleted_count = result.rowcount
        db.commit()
        logger.info(f"Purged {deleted_count} expired/revoked refresh tokens.")
        return {"deleted_tokens": deleted_count}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error purging refresh tokens: {exc}")
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.maintenance_tasks.cleanup_published_outbox_task")
def cleanup_published_outbox_task() -> dict[str, int]:
    """
    Purge outbox events that have been successfully PUBLISHED and are
    older than CELERY_OUTBOX_RETENTION_DAYS (default 7 days).
    """
    logger.info("Executing periodic published outbox purge...")
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.CELERY_OUTBOX_RETENTION_DAYS
        )
        stmt = delete(OutboxEvent).where(
            OutboxEvent.status == OutboxStatus.PUBLISHED,
            OutboxEvent.published_at < cutoff,
        )
        result = db.execute(stmt)
        cleaned_count = result.rowcount
        db.commit()
        logger.info(f"Purged {cleaned_count} published outbox events older than {settings.CELERY_OUTBOX_RETENTION_DAYS} days.")
        return {"cleaned_outbox_count": cleaned_count}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error purging published outbox events: {exc}")
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.maintenance_tasks.check_model_drift_task")
def check_model_drift_task() -> dict[str, Any]:
    """
    Analyze recent fraud scoring distributions over the last 7 days to detect
    statistical drift or sudden anomalies in fraud score prevalence.
    """
    logger.info("Executing periodic model drift and distribution analysis...")
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        scores_query = select(FraudScore.score).where(FraudScore.scored_at >= cutoff)
        scores = list(db.execute(scores_query).scalars().all())

        total = len(scores)
        if total == 0:
            return {"status": "insufficient_data", "samples": 0}

        high_count = sum(1 for s in scores if s >= 70)
        med_count = sum(1 for s in scores if 30 <= s < 70)
        low_count = sum(1 for s in scores if s < 30)

        high_ratio = high_count / total
        drift_detected = high_ratio > 0.25  # Baseline expectation: < 10% high risk

        summary = {
            "status": "drift_warning" if drift_detected else "normal",
            "samples": total,
            "high_risk_pct": round(high_ratio * 100, 2),
            "med_risk_pct": round((med_count / total) * 100, 2),
            "low_risk_pct": round((low_count / total) * 100, 2),
            "drift_detected": drift_detected,
        }
        if drift_detected:
            logger.warning(f"Model drift warning: high risk transaction ratio spiked to {summary['high_risk_pct']}%")
        return summary
    finally:
        db.close()


@celery_app.task(name="app.tasks.maintenance_tasks.recalculate_network_graph_task")
def recalculate_network_graph_task(lookback_days: int = 30) -> dict[str, Any]:
    """
    Periodic graph computation task: builds transaction topology, detects mule cycles
    and high-centrality hubs, and updates account_network_risk table.
    """
    logger.info("Executing periodic network graph topology recalculation...")
    from app.services.network_service import recalculate_network_risk

    db = SessionLocal()
    try:
        res = recalculate_network_risk(db, lookback_days=lookback_days)
        return {
            "status": "completed",
            "accounts_analyzed": res.accounts_analyzed,
            "mule_suspects_detected": res.mule_suspects_detected,
            "cycles_detected": res.cycles_detected,
            "hubs_detected": res.hubs_detected,
        }
    finally:
        db.close()
