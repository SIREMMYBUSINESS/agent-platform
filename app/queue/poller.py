"""Poll and execute due work directly from the PostgreSQL durable queue."""

from datetime import UTC, datetime, timedelta
import logging

from app.observability.metrics import metrics


logger = logging.getLogger("app.observability")


class DatabaseQueuePoller:
    def __init__(self, *, uow_factory, runner, now_provider=None,
                 lease_seconds: int = 3600, region_id: str | None = None) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._lease = timedelta(seconds=lease_seconds)
        self._region_id = region_id

    def run_once(self, *, limit: int = 100) -> int:
        now = self._now()
        with self._uow_factory() as uow:
            rows = uow.queue_tasks.list_due(
                now=now, stale_before=now - self._lease, limit=limit,
                region_id=self._region_id,
            )
            due = [(row.id, row.domain) for row in rows]

        completed = 0
        for task_id, domain in due:
            try:
                result = self._runner.execute(task_id, expected_domain=domain)
            except Exception as exc:
                # Keep database exception strings out of logs because drivers can
                # include connection details in them.
                logger.error("database queue execution failed", extra={
                    "component": "workflow", "operation": "poll",
                    "outcome": "failed", "error_type": type(exc).__name__,
                })
                metrics.queue_event(domain, "dispatch", "failure")
                continue
            if result.get("status") not in {"duplicate_or_terminal", "not_found"}:
                completed += 1
        return completed


__all__ = ["DatabaseQueuePoller"]
