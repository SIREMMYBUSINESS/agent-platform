"""Escalation worker on the independent escalation queue."""

from app.workers.runtime import execute_domain_task


def process_escalation(task_id: str) -> dict:
    return execute_domain_task("escalation", task_id)
