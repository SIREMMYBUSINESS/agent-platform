"""Create and query trusted tenant/business suppression rules."""
from __future__ import annotations

from uuid import UUID


class SuppressionService:
    def is_suppressed(self, uow, *, tenant_id: str, business_id: str,
                      contact_id: str, lead_id: UUID | None,
                      channel: str):
        return uow.suppressions.find_active(
            tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, lead_id=lead_id, channel=channel,
        )

    def add(self, uow, *, tenant_id: str, business_id: str,
            reason_code: str, contact_id: str | None = None,
            lead_id: UUID | None = None, channel: str | None = None,
            created_by: str = "trusted_admin"):
        if (contact_id is None) == (lead_id is None):
            raise ValueError("exactly one of contact_id or lead_id is required")
        rule = uow.suppressions.add(
            tenant_id=tenant_id, business_id=business_id,
            reason_code=reason_code, contact_id=contact_id,
            lead_id=lead_id, channel=channel, created_by=created_by,
        )
        business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
        tenant = uow.tenants.get(tenant_id=tenant_id)
        country_code = ((getattr(business, "country_code", None) or business.region)
                        if business else tenant.region if tenant else "UNKNOWN")
        uow.policy_decisions.add(
            tenant_id=tenant_id, business_id=business_id,
            direction="outbound", channel=channel or "all",
            country_code=country_code, resource_type="contact" if contact_id else "lead",
            resource_id=contact_id or str(lead_id), decision="suppression_added",
            reason_code=reason_code, consent_status="unknown",
            policy_source="suppression_service",
        )
        return rule


__all__ = ["SuppressionService"]
