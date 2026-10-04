"""Transactional channel-specific campaign opt-out behavior."""
from __future__ import annotations

from uuid import UUID

from app.services.consent_service import ConsentService


class OptOutService:
    def __init__(self, consent_service: ConsentService | None = None) -> None:
        self._consent = consent_service or ConsentService()

    def apply(self, uow, *, tenant_id: str, business_id: str, campaign_id: UUID,
              enrollment_id: UUID, lead) -> None:
        campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id,
                                     campaign_id=campaign_id)
        # The caller has already validated the campaign and enrollment. Keep all
        # writes in its UnitOfWork so revocation and step blocking are atomic.
        self._consent.revoke(
            uow, tenant_id=tenant_id, business_id=business_id,
            contact_id=lead.contact_id, channel=campaign.channel,
            source="campaign_reply_opt_out",
        )
        uow.campaign_reply_enrollments.cancel(
            tenant_id=tenant_id, business_id=business_id,
            campaign_id=campaign_id, enrollment_id=enrollment_id,
        )
        if lead.status not in {"opted_out", "converted", "closed"}:
            uow.lead_transitions.transition(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                expected_state=lead.status, new_state="opted_out",
                context={"source": "campaign_reply", "channel": campaign.channel},
            )
            uow.lead_events.add(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                event_type="lead.opted_out", payload={"channel": campaign.channel},
            )
        uow.campaign_events.add(
            tenant_id=tenant_id, business_id=business_id,
            campaign_id=campaign_id, enrollment_id=enrollment_id,
            event_type="campaign.opted_out",
            payload={"lead_id": str(lead.id), "channel": campaign.channel},
        )


__all__ = ["OptOutService"]
