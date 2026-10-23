"""Poll and execute due work directly from the PostgreSQL durable queue."""

from datetime import UTC, datetime, timedelta
import logging

from app.observability.metrics import metrics


logger = logging.getLogger("app.observability")


class DatabaseQueuePoller:
    def __init__(self, *, uow_factory, runner, now_provider=None,
                 lease_seconds: int = 3600) -> None:
        self._uow_factory = uow_factory
        self._runner = runner
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._lease = timedelta(seconds=lease_seconds)

    def run_once(self, *, limit: int = 1) -> int:
        now = self._now()
        try:
            with self._uow_factory() as uow:
                rows = uow.queue_tasks.list_due(
                    now=now, stale_before=now - self._lease, limit=limit,
                )
                due = [(row.id, row.domain, row.tenant_id, row.business_id,
                        row.correlation_id) for row in rows]
        except Exception as exc:
            logger.error("database queue poll", extra={
                "component": "workflow", "operation": "poll", "claimed": 0,
                "task_id": "none", "conversation_id": "none", "outcome": "failed",
                "error_type": type(exc).__name__,
            })
            raise

        if not due:
            logger.info("database queue poll", extra={
                "component": "workflow", "operation": "poll", "claimed": 0,
                "task_id": "none", "conversation_id": "none", "outcome": "idle",
            })
            return 0

        completed = 0
        for task_id, domain, tenant_id, business_id, correlation_id in due:
            try:
                result = self._runner.execute(task_id, expected_domain=domain)
            except Exception as exc:
                # Keep database exception strings out of logs because drivers can
                # include connection details in them.
                logger.error("database queue execution failed", extra={
                    "component": "workflow", "operation": "poll",
                    "claimed": 1, "task_id": str(task_id),
                    "conversation_id": "none", "outcome": "failed",
                    "error_type": type(exc).__name__,
                })
                metrics.queue_event(domain, "dispatch", "failure")
                continue
            if result.get("status") not in {"duplicate_or_terminal", "not_found"}:
                completed += 1
            conversation_id, workflow_state = result.get("conversation_id"), result.get("workflow_state")
            if not conversation_id:
                try:
                    with self._uow_factory() as uow:
                        conversation = uow.conversations.get_by_correlation_id(
                            tenant_id=tenant_id, business_id=business_id,
                            correlation_id=correlation_id,
                        )
                        if conversation is not None:
                            conversation_id = str(conversation.id)
                            workflow_state = conversation.workflow_state
                except Exception:
                    pass
            logger.info("database queue poll", extra={
                "component": "workflow", "operation": "poll",
                "claimed": int(result.get("status") not in {"duplicate_or_terminal", "not_found"}),
                "task_id": str(task_id),
                "conversation_id": str(conversation_id or "none"),
                "outcome": str(result.get("status", "unknown")),
                "workflow_state": str(workflow_state or "unknown"),
            })
        return completed


__all__ = ["DatabaseQueuePoller"]
