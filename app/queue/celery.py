"""Celery transport configuration; Redis remains a broker, never the source of truth."""

from celery import Celery
from kombu import Queue

from app.config import settings
from app.queue.routing import worker_pool_assumptions


_queues = [Queue("agent.control")] + [Queue(f"agent.{domain}") for domain in worker_pool_assumptions()]
celery_app = Celery(
    "agent_platform",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.inbound", "app.workers.outreach", "app.workers.scheduling",
        "app.workers.escalation", "app.workers.analytics", "app.workers.dispatcher",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_queues=tuple(_queues),
    task_default_queue="agent.control",
    task_routes={
        f"agent_platform.worker.{domain}": {"queue": f"agent.{domain}"}
        for domain in worker_pool_assumptions()
    } | {"agent_platform.queue.dispatch_due": {"queue": "agent.control"}},
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_publish_retry=True,
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "dispatch-durable-queue-outbox": {
            "task": "agent_platform.queue.dispatch_due",
            "schedule": 5.0,
            "options": {"queue": "agent.control"},
        },
    },
)

__all__ = ["celery_app"]
