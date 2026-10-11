"""Analytics worker on the independent analytics queue."""

from app.workers.runtime import execute_domain_task


def process_analytics(task_id: str) -> dict:
    return execute_domain_task("analytics", task_id)
