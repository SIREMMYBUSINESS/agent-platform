"""Campaign delivery worker on the independent outreach queue."""

from app.workers.runtime import execute_domain_task


def process_outreach(task_id: str) -> dict:
    return execute_domain_task("outreach", task_id)
