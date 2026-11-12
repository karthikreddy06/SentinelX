import os
from celery import Celery
from app.core.config import settings

# Initialize Celery app
celery_app = Celery(
    "sentinelx",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.scoring_tasks",
        "app.tasks.maintenance_tasks",
    ],
)

# Celery Configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # Worker settings for reliability
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Periodic Beat Schedule
    beat_schedule={
        "escalate-stale-alerts-hourly": {
            "task": "app.tasks.maintenance_tasks.escalate_stale_alerts_task",
            "schedule": 3600.0,  # every 1 hour
        },
        "cleanup-expired-tokens-6h": {
            "task": "app.tasks.maintenance_tasks.cleanup_expired_tokens_task",
            "schedule": 21600.0,  # every 6 hours
        },
        "cleanup-published-outbox-12h": {
            "task": "app.tasks.maintenance_tasks.cleanup_published_outbox_task",
            "schedule": 43200.0,  # every 12 hours
        },
        "check-model-drift-daily": {
            "task": "app.tasks.maintenance_tasks.check_model_drift_task",
            "schedule": 86400.0,  # every 24 hours
        },
        "recalculate-network-graph-6h": {
            "task": "app.tasks.maintenance_tasks.recalculate_network_graph_task",
            "schedule": 21600.0,  # every 6 hours
        },
    },
)

