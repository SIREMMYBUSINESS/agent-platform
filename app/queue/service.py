"""Principal-scoped durable enqueue, idempotency, retries, and DLQ redrive."""

from datetime import UTC, datetime
from collections.abc import Callable
from uuid import UUID

from app.observability.metrics import metrics
from app.observability.request_context import current_request_context
from app.persistence.repositories import DuplicateEntityError
from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueTaskSubmission, QueueTaskView
from app.security import AuthenticatedPrincipal
from app.config import settings


_REQUIRED_ROLE = {
    "inbound": "inbound_writer",
    "outreach": "campaign_writer",
    "scheduling": "workflow_executor",
    "escalation": "workflow_executor",
    "analytics": "campaign_reader",
}


class QueueAuthorizationError(Exception):
    """Queue task scope is not authorized by the trusted principal."""


class QueueTaskConflict(Exception):
    """An idempotency key was reused for different work."""


class QueueTaskService:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork], now_provider=None) -> None:
        self._uow_factory = uow_factory
        self._now = now_provider or (lambda: datetime.now(UTC))

    @staticmethod
    def _authorize(principal: AuthenticatedPrincipal, task: QueueTaskSubmission) -> None:
        required = _REQUIRED_ROLE[task.domain.value]
        now = datetime.now(UTC)
        if (not isinstance(principal, AuthenticatedPrincipal)
                or principal.issued_at > now
                or (principal.expires_at is not None and principal.expires_at <= now)
                or required not in principal.roles
                or not principal.tenant_id.strip()):
            raise QueueAuthorizationError("queue operation denied")

    def enqueue(self, principal: AuthenticatedPrincipal,
                task: QueueTaskSubmission) -> QueueTaskView:
        self._authorize(principal, task)
        context = current_request_context.get()
        try:
            with self._uow_factory() as uow:
                business = uow.businesses.get(tenant_id=principal.tenant_id,
                                              business_id=task.business_id)
                tenant = uow.tenants.get(tenant_id=principal.tenant_id)
                if business is None or tenant is None:
                    raise QueueAuthorizationError("queue operation denied")
                region_id = None
                if getattr(tenant, "supported_region_ids", None):
                    region_id = getattr(business, "region_id", None) or tenant.default_region_id
                    region = uow.regions.get(tenant_id=principal.tenant_id,
                                             region_id=region_id)
                    runtime_region = settings.database_region_id or settings.deployment_region_id
                    if (region is None or region_id not in tenant.supported_region_ids
                            or runtime_region not in region.allowed_storage_regions):
                        uow.regions.add_audit(
                            tenant_id=principal.tenant_id,
                            region_id=region_id or tenant.default_region_id,
                            action="regional_task_enqueue", decision="block",
                            resource_type="queue_task", resource_id=task.idempotency_key,
                            actor_id=principal.principal_id,
                            details={"domain": task.domain.value,
                                     "runtime_region": runtime_region},
                        )
                        uow.commit()
                        raise QueueAuthorizationError("regional queue access denied")
                    uow.regions.add_audit(
                        tenant_id=principal.tenant_id, region_id=region_id,
                        action="regional_task_enqueue", decision="allow",
                        resource_type="queue_task", resource_id=task.idempotency_key,
                        actor_id=principal.principal_id,
                        details={"domain": task.domain.value,
                                 "runtime_region": runtime_region},
                    )
                    metrics.region_event(region_id=region_id,
                                         operation="queue_dispatch", outcome="queued")
                elif getattr(business, "region_id", None):
                    region_id = business.region_id
                if task.aggregate_type == "contact":
                    contact = uow.contacts.get(
                        tenant_id=principal.tenant_id, contact_id=task.aggregate_id,
                    )
                    if contact is None or contact.business_id != task.business_id:
                        raise QueueAuthorizationError("queue operation denied")
                try:
                    row, created = uow.queue_tasks.enqueue(
                        tenant_id=principal.tenant_id, business_id=task.business_id,
                        domain=task.domain.value, task_name=task.task_name,
                        idempotency_key=task.idempotency_key,
                        aggregate_type=task.aggregate_type, aggregate_id=task.aggregate_id,
                        payload=task.payload, correlation_id=(context.request_id if context else task.correlation_id),
                        traceparent=(f"00-{context.trace_id}-{context.span_id}-01" if context else None),
                        available_at=task.available_at, max_attempts=task.max_attempts,
                        region_id=region_id,
                    )
                except ValueError as exc:
                    raise QueueTaskConflict("idempotency key conflicts with different queued work") from exc
                result = self._view(row)
        except DuplicateEntityError:
            # A concurrent first submission won the unique-key race. Read the
            # committed row and apply the same-payload conflict check.
            with self._uow_factory() as uow:
                row = uow.queue_tasks.get_by_key(
                    tenant_id=principal.tenant_id, business_id=task.business_id,
                    idempotency_key=task.idempotency_key,
                )
                if row is None or not self._matches(row, task, region_id):
                    raise QueueTaskConflict("idempotency key conflicts with different queued work") from None
                result = self._view(row)
            created = False
        if created:
            metrics.event("workflow", "execution", "scheduled")
            metrics.queue_event(task.domain.value, "enqueue", "created")
        else:
            metrics.queue_event(task.domain.value, "enqueue", "reused")
        return result

    def redrive(self, principal: AuthenticatedPrincipal, *, business_id: str,
                task_id: UUID) -> QueueTaskView:
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "workflow_executor" not in principal.roles):
            raise QueueAuthorizationError("queue operation denied")
        now = self._now()
        with self._uow_factory() as uow:
            row = uow.queue_tasks.redrive(
                tenant_id=principal.tenant_id, business_id=business_id,
                task_id=task_id, now=now,
            )
            if row is None:
                raise QueueAuthorizationError("queue operation denied")
            result = self._view(row)
        metrics.event("workflow", "retry", "scheduled")
        metrics.queue_event(result.domain.value, "redrive", "queued")
        return result

    @staticmethod
    def _matches(row, task: QueueTaskSubmission, region_id: str | None = None) -> bool:
        return (row.domain == task.domain.value and row.task_name == task.task_name
                and row.aggregate_type == task.aggregate_type
                and row.aggregate_id == task.aggregate_id and row.payload == task.payload
                and row.max_attempts == task.max_attempts
                and row.region_id == region_id)

    @staticmethod
    def _view(row) -> QueueTaskView:
        return QueueTaskView(
            id=row.id, tenant_id=row.tenant_id, business_id=row.business_id,
            region_id=getattr(row, "region_id", None),
            domain=row.domain, task_name=row.task_name,
            idempotency_key=row.idempotency_key, status=row.status,
            attempts=row.attempts, max_attempts=row.max_attempts,
            available_at=row.available_at, correlation_id=row.correlation_id,
            error_code=row.error_code,
        )


__all__ = ["QueueAuthorizationError", "QueueTaskConflict", "QueueTaskService"]
