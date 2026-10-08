"""Policy-gated, tenant-scoped outbound calls."""
from __future__ import annotations

from collections.abc import Callable

from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueDomain, QueueTaskSubmission, QueueTaskView
from app.queue.service import QueueTaskService
from app.security import AuthenticatedPrincipal
from app.services.policy_service import PolicyService


class VoiceDeliveryBlocked(Exception):
    """Call was rejected by trusted contact or policy state."""


class VoiceDeliveryService:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork], provider,
                 policy_service: PolicyService | None = None):
        self._uow_factory, self._provider = uow_factory, provider
        self._policy = policy_service or PolicyService()

    def initiate(self, principal: AuthenticatedPrincipal, *, business_id: str,
                 contact_id: str, idempotency_key: str,
                 provider_region: str | None = None) -> str:
        """Execute a previously queued call in the worker after rechecking policy."""
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "campaign_writer" not in principal.roles or not principal.tenant_id.strip()):
            raise VoiceDeliveryBlocked("call authorization denied")
        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            business = uow.businesses.get(tenant_id=principal.tenant_id, business_id=business_id)
            contact = uow.contacts.get(tenant_id=principal.tenant_id, contact_id=contact_id)
            if tenant is None or business is None or contact is None or contact.business_id != business.id:
                raise VoiceDeliveryBlocked("contact is unavailable in authorized scope")
            decision = self._policy.evaluate(
                uow, tenant=tenant, business=business, contact=contact,
                channel="call", direction="outbound",
            )
            if not decision.allowed:
                raise VoiceDeliveryBlocked(decision.reason_code)
            recipient = contact.phone
            scoped_tenant, scoped_business = tenant.id, business.id
        if not recipient:
            raise VoiceDeliveryBlocked("recipient phone is unavailable")
        return self._provider.initiate_call(
            tenant_id=scoped_tenant, business_id=scoped_business,
            recipient=recipient,
            idempotency_key=idempotency_key, region_id=provider_region,
        )

    def enqueue(self, principal: AuthenticatedPrincipal, *, business_id: str,
                contact_id: str, idempotency_key: str) -> QueueTaskView:
        """Persist a policy-checked call command; provider I/O happens in a worker."""
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "campaign_writer" not in principal.roles or not principal.tenant_id.strip()):
            raise VoiceDeliveryBlocked("call authorization denied")
        with self._uow_factory() as uow:
            tenant = uow.tenants.get(tenant_id=principal.tenant_id)
            business = uow.businesses.get(tenant_id=principal.tenant_id,
                                          business_id=business_id)
            contact = uow.contacts.get(tenant_id=principal.tenant_id,
                                       contact_id=contact_id)
            if tenant is None or business is None or contact is None or contact.business_id != business.id:
                raise VoiceDeliveryBlocked("contact is unavailable in authorized scope")
            if not contact.phone:
                raise VoiceDeliveryBlocked("recipient phone is unavailable")
            decision = self._policy.evaluate(
                uow, tenant=tenant, business=business, contact=contact,
                channel="call", direction="outbound",
            )
            if not decision.allowed:
                raise VoiceDeliveryBlocked(decision.reason_code)
        return QueueTaskService(uow_factory=self._uow_factory).enqueue(
            principal, QueueTaskSubmission(
                domain=QueueDomain.OUTREACH, task_name="voice.initiate_call",
                business_id=business_id, idempotency_key=f"voice:{idempotency_key}",
                aggregate_type="contact", aggregate_id=contact_id,
                payload={"contact_id": contact_id},
                correlation_id=f"voice:{idempotency_key}",
                # A timed-out Calls API request can have created a call. Keep
                # ambiguous outcomes in the DLQ for reconciliation instead
                # of automatically placing a potentially duplicate call.
                max_attempts=1,
            ),
        )


__all__ = ["VoiceDeliveryBlocked", "VoiceDeliveryService"]
