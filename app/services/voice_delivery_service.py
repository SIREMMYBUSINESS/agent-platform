"""Policy-gated, tenant-scoped outbound calls."""
from __future__ import annotations

from collections.abc import Callable

from app.persistence.unit_of_work import UnitOfWork
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
                 contact_id: str, callback_url: str, idempotency_key: str) -> str:
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
            recipient=recipient, callback_url=callback_url,
            idempotency_key=idempotency_key,
        )


__all__ = ["VoiceDeliveryBlocked", "VoiceDeliveryService"]
