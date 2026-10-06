"""Time-based scheduling worker on its own queue."""

from app.queue.celery import celery_app
from app.workers.runtime import execute_domain_task


@celery_app.task(bind=True, name="agent_platform.worker.scheduling", acks_late=True,
                 reject_on_worker_lost=True, track_started=True)
def process_scheduling(self, task_id: str) -> dict:
    return execute_domain_task("scheduling", task_id)
