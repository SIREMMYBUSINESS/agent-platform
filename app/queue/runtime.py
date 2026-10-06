"""Durable worker claims, bounded retry, execution history, and dead-letter handling."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any, Callable
from uuid import UUID

from app.observability.metrics import metrics
from app.observability.request_context import current_request_context, create_request_context
from app.persistence.models import QueueTaskRecord
from app.persistence.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class QueueTaskEnvelope:
    id: UUID
    tenant_id: str
    business_id: str
    domain: str
    task_name: str
    idempotency_key: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    correlation_id: str
    traceparent: str | None
    attempts: int
    max_attempts: int


TaskHandler = Callable[[QueueTaskEnvelope], dict[str, Any] | None]


class TaskHandlerRegistry:
    """Explicit command handlers; workers never contain business policy."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], TaskHandler] = {}

    def register(self, domain: str, task_name: str, handler: TaskHandler) -> None:
        key = (domain, task_name)
        if key in self._handlers:
            raise ValueError("a handler is already registered for this task")
        self._handlers[key] = handler

    def replace(self, domain: str, task_name: str, handler: TaskHandler) -> None:
        key = (domain, task_name)
        if key not in self._handlers:
            raise LookupError("no built-in handler exists to replace")
        self._handlers[key] = handler

    def get(self, domain: str, task_name: str) -> TaskHandler:
        try:
            return self._handlers[(domain, task_name)]
        except KeyError:
            raise LookupError("task handler is not configured") from None


class QueueTaskRunner:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork],
                 handlers: TaskHandlerRegistry, now_provider=None,
                 base_retry_seconds: int = 5, max_retry_seconds: int = 300,
                 lease_seconds: int = 3600) -> None:
        self._uow_factory = uow_factory
        self._handlers = handlers
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._base_retry_seconds = base_retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._lease = timedelta(seconds=lease_seconds)

    def execute(self, task_id: UUID, *, expected_domain: str) -> dict[str, Any]:
        now = self._now()
        with self._uow_factory() as uow:
            row = uow.session.get(QueueTaskRecord, task_id)
            if row is None or row.domain != expected_domain:
                return {"status": "not_found"}
            task = QueueTaskEnvelope(
                id=row.id, tenant_id=row.tenant_id, business_id=row.business_id,
                domain=row.domain, task_name=row.task_name,
                idempotency_key=row.idempotency_key,
                aggregate_type=row.aggregate_type, aggregate_id=row.aggregate_id,
                payload=dict(row.payload), correlation_id=row.correlation_id,
                traceparent=row.traceparent, attempts=row.attempts + 1,
                max_attempts=row.max_attempts,
            )
            claimed = uow.queue_tasks.claim(task_id=task_id, now=now, lease=self._lease)
        if claimed is None:
            return {"status": "duplicate_or_terminal"}
        trace_token = current_request_context.set(
            create_request_context(task.correlation_id, task.traceparent)
        )
        try:
            output = self._handlers.get(task.domain, task.task_name)(task) or {}
            if not isinstance(output, dict):
                raise TypeError("task handler must return an object")
            encoded = json.dumps(output, sort_keys=True, separators=(",", ":"), allow_nan=False)
            result = json.loads(encoded)
            if len(encoded.encode("utf-8")) > 32_768:
                raise ValueError("task result exceeds the persisted result limit")
        except Exception as exc:
            error_code = re.sub(r"[^A-Za-z0-9_.:-]", "_", type(exc).__name__)[:80]
            now = self._now()
            retry_at = now + timedelta(seconds=min(
                self._base_retry_seconds * 2 ** max(task.attempts - 1, 0),
                self._max_retry_seconds,
            ))
            with self._uow_factory() as uow:
                row = uow.queue_tasks.fail_or_retry(
                    task_id=task.id, now=now, error_code=error_code, retry_at=retry_at,
                )
                status = row.status if row is not None else "lost_claim"
            metrics.event("workflow", "retry", "attempted")
            if status == "dead_letter":
                metrics.event("workflow", "failure", "failed")
                metrics.queue_event(task.domain, "dead_letter", "dead_letter")
            else:
                metrics.queue_event(task.domain, "retry", "retrying")
            return {"status": status, "error_code": error_code}
        finally:
            current_request_context.reset(trace_token)
        with self._uow_factory() as uow:
            row = uow.queue_tasks.succeed(task_id=task.id, now=self._now(), result=result)
            status = row.status if row is not None else "lost_claim"
        metrics.event("workflow", "execution", "succeeded")
        metrics.queue_event(task.domain, "execution", "succeeded")
        return {"status": status, "result": result}


__all__ = ["QueueTaskEnvelope", "QueueTaskRunner", "TaskHandler", "TaskHandlerRegistry"]
