"""Inbound intake worker on the independent inbound queue."""

from app.queue.celery import celery_app
from app.workers.runtime import execute_domain_task


@celery_app.task(bind=True, name="agent_platform.worker.inbound", acks_late=True,
                 reject_on_worker_lost=True, track_started=True)
def process_inbound(self, task_id: str) -> dict:
    return execute_domain_task("inbound", task_id)
