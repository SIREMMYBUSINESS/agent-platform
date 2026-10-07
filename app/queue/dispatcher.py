"""At-least-once outbox dispatcher; worker claims make duplicate delivery safe."""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.observability.metrics import metrics
from app.persistence.unit_of_work import UnitOfWork
from app.queue.routing import route_for


class TaskPublisher(Protocol):
    def send_task(self, name: str, *, args: list, task_id: str,
                  queue: str, headers: dict) -> object: ...


class DurableQueueDispatcher:
    def __init__(self, *, uow_factory, publisher: TaskPublisher,
                 now_provider=None, lease_seconds: int = 3600) -> None:
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._lease = timedelta(seconds=lease_seconds)

    def dispatch_due(self, *, limit: int = 100) -> int:
        now = self._now()
        with self._uow_factory() as uow:
            rows = uow.queue_tasks.list_due(
                now=now, stale_before=now - self._lease, limit=limit,
            )
            tasks = [(row.id, row.domain, row.tenant_id, row.business_id,
                      getattr(row, "region_id", None), row.correlation_id,
                      row.traceparent) for row in rows]
        dispatched = 0
        for task_id, domain, tenant_id, business_id, region_id, correlation_id, traceparent in tasks:
            route = route_for(domain, tenant_id=tenant_id, business_id=business_id,
                              region_id=region_id)
            headers = {
                "tenant_id": tenant_id, "business_id": business_id,
                "correlation_id": correlation_id,
            }
            if region_id:
                headers["region_id"] = region_id
            if traceparent:
                headers["traceparent"] = traceparent
            try:
                self._publisher.send_task(
                    route.task_name, args=[str(task_id)], task_id=str(task_id),
                    queue=route.queue, headers=headers,
                )
            except Exception:
                metrics.event("workflow", "execution", "failure")
                metrics.queue_event(domain, "dispatch", "failure")
                continue
            with self._uow_factory() as uow:
                mark_now = self._now()
                changed = uow.queue_tasks.mark_dispatched(
                    task_id=task_id, now=mark_now,
                    stale_before=mark_now - self._lease,
                )
            if changed:
                dispatched += 1
                metrics.event("workflow", "execution", "scheduled")
                metrics.queue_event(domain, "dispatch", "queued")
        return dispatched


__all__ = ["DurableQueueDispatcher", "TaskPublisher"]
