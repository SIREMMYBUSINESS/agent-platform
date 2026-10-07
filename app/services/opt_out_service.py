"""Transactional channel-specific campaign opt-out behavior."""
from __future__ import annotations

from uuid import UUID

from app.services.consent_service import ConsentService
from app.observability.metrics import metrics
from app.optimization.feedback import FeedbackEventCapture


class OptOutService:
    def __init__(self, consent_service: ConsentService | None = None) -> None:
        self._consent = consent_service or ConsentService()
        self._feedback = FeedbackEventCapture()

    def apply(self, uow, *, tenant_id: str, business_id: str, campaign_id: UUID,
              enrollment_id: UUID, lead) -> None:
        campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id,
                                     campaign_id=campaign_id)
        if campaign is not None:
            metrics.opt_out(channel=campaign.channel if campaign.channel in {
                "sms", "email", "voice", "call", "chat", "voicemail", "whatsapp",
            } else "unknown", tenant_id=str(tenant_id), business_id=str(business_id))
            self._feedback.capture(
                uow=uow, tenant_id=tenant_id, business_id=business_id,
                event_key=f"campaign-optout:{enrollment_id}",
                correlation_id=f"campaign:{campaign_id}:enrollment:{enrollment_id}",
                event_type="opted_out", outcome="opt_out", lead_id=lead.id,
                campaign_id=campaign_id,
                decision={"channel": campaign.channel, "step": getattr(lead, "current_step_order", -1)},
                event_metadata={"reason_available": False},
            )
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

    def apply_contact(self, uow, *, tenant_id: str, business_id: str,
                      contact_id: str, channel: str, source: str) -> int:
        """Revoke one channel and stop every active campaign enrollment on it."""
        self._consent.revoke(
            uow, tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, channel=channel, source=source,
        )
        cancelled = 0
        enrollments = uow.campaign_enrollments.list_active_for_contact(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
        )
        for enrollment in enrollments:
            campaign = uow.campaigns.get(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=enrollment.campaign_id,
            )
            if campaign is None or campaign.channel != channel:
                continue
            lead = uow.leads.get(
                tenant_id=tenant_id, business_id=business_id, lead_id=enrollment.lead_id,
            )
            if lead is None:
                continue
            self.apply(
                uow, tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign.id, enrollment_id=enrollment.id, lead=lead,
            )
            cancelled += 1
        return cancelled


__all__ = ["OptOutService"]
