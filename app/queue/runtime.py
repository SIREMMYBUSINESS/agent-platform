"""Durable worker claims, bounded retry, execution history, and dead-letter handling."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import re
import logging
import time
from typing import Any, Callable
from uuid import UUID

from app.observability.metrics import metrics
from app.observability.request_context import current_request_context, create_request_context
from app.observability.telemetry import extract_context, span_context_fields, start_span
from app.observability.telemetry import configure_telemetry
from app.observability.logging import configure_structured_logging
from app.config import settings
from app.persistence.models import QueueTaskRecord
from app.persistence.unit_of_work import UnitOfWork


logger = logging.getLogger("app.observability")
configure_structured_logging(settings.log_level)
configure_telemetry(environment=settings.environment, metrics_registry=metrics)


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
    region_id: str | None = None


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
        self._region_id = settings.database_region_id or settings.deployment_region_id
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
                region_id=getattr(row, "region_id", None),
                domain=row.domain, task_name=row.task_name,
                idempotency_key=row.idempotency_key,
                aggregate_type=row.aggregate_type, aggregate_id=row.aggregate_id,
                payload=dict(row.payload), correlation_id=row.correlation_id,
                traceparent=row.traceparent, attempts=row.attempts + 1,
                max_attempts=row.max_attempts,
            )
            available_at = row.available_at
            if (settings.tenant_region_routing_enabled
                    and row.region_id != self._region_id):
                return {"status": "region_mismatch"}
            claimed = uow.queue_tasks.claim(
                task_id=task_id, now=now, lease=self._lease,
                region_id=self._region_id if settings.tenant_region_routing_enabled else None,
            )
        if claimed is None:
            return {"status": "duplicate_or_terminal"}
        if available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=UTC)
        metrics.observe("queue_task_wait_seconds",
                        max(0.0, (now - available_at.astimezone(UTC)).total_seconds()),
                        queue_domain=task.domain)
        context = create_request_context(task.correlation_id, task.traceparent)
        from dataclasses import replace
        context = replace(context, tenant_id=task.tenant_id, business_id=task.business_id)
        trace_token = current_request_context.set(context)
        started = time.perf_counter()
        parent = extract_context({"traceparent": task.traceparent or ""})
        try:
            if (task.region_id is not None and task.region_id != self._region_id):
                raise RuntimeError("regional_worker_mismatch")
            with start_span("queue.task.execute", parent_context=parent, attributes={
                "queue.domain": task.domain, "task.name": task.task_name,
                "task.id": str(task.id), "tenant.id": task.tenant_id,
                "business.id": task.business_id,
                "region.id": task.region_id or "unknown",
            }) as span:
                span_fields = span_context_fields(span)
                if span_fields:
                    current_request_context.set(replace(context, trace_id=span_fields[0],
                                                        span_id=span_fields[1]))
                logger.info("queue task started", extra={"tenant_id": task.tenant_id,
                    "business_id": task.business_id, "component": "workflow",
                    "operation": "execution", "outcome": "attempted"})
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
            metrics.observe("queue_task_execution_seconds", time.perf_counter() - started,
                            queue_domain=task.domain)
            logger.error("queue task failed", extra={"tenant_id": task.tenant_id,
                "business_id": task.business_id, "component": "workflow",
                "operation": "failure", "outcome": "failed", "error_type": error_code})
            current_request_context.reset(trace_token)
            return {"status": status, "error_code": error_code}
        with self._uow_factory() as uow:
            row = uow.queue_tasks.succeed(task_id=task.id, now=self._now(), result=result)
            status = row.status if row is not None else "lost_claim"
        metrics.event("workflow", "execution", "succeeded")
        metrics.queue_event(task.domain, "execution", "succeeded")
        metrics.observe("queue_task_execution_seconds", time.perf_counter() - started,
                        queue_domain=task.domain)
        logger.info("queue task completed", extra={"tenant_id": task.tenant_id,
            "business_id": task.business_id, "component": "workflow",
            "operation": "execution", "outcome": "succeeded"})
        current_request_context.reset(trace_token)
        return {"status": status, "result": result}


__all__ = ["QueueTaskEnvelope", "QueueTaskRunner", "TaskHandler", "TaskHandlerRegistry"]
