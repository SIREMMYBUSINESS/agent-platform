"""Time-based scheduling worker on its own queue."""

from app.workers.runtime import execute_domain_task


def process_scheduling(task_id: str) -> dict:
    return execute_domain_task("scheduling", task_id)
