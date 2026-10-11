"""Inbound intake worker on the independent inbound queue."""

from app.workers.runtime import execute_domain_task


def process_inbound(task_id: str) -> dict:
    return execute_domain_task("inbound", task_id)
