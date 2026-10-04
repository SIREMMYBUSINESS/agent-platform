"""Transactional campaign reply classification, persistence, and state changes."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from app.services.opt_out_service import OptOutService
from app.services.reply_parser_service import ReplyClassification, ReplyParserService


class CampaignReplyNotFoundError(Exception):
    """Campaign or enrollment is absent from trusted tenant/business scope."""


class CampaignReplyTenantMismatchError(Exception):
    """Request tenant assertion does not match trusted configuration."""


class ReplyService:
    def __init__(self, *, trusted_tenant_id: str, trusted_business_id: str | None,
                 uow_factory: Callable, parser: ReplyParserService | None = None,
                 opt_out: OptOutService | None = None) -> None:
        self._tenant_id = trusted_tenant_id
        self._business_id = trusted_business_id.strip() if trusted_business_id else None
        self._uow_factory = uow_factory
        self._parser = parser or ReplyParserService()
        self._opt_out = opt_out or OptOutService()

    def process(self, campaign_id: UUID, request) -> dict:
        if request.tenant_id != self._tenant_id:
            raise CampaignReplyTenantMismatchError("request tenant does not match trusted tenant")
        if not self._business_id:
            raise CampaignReplyNotFoundError("campaign is not available in trusted business scope")
        tenant_id, business_id = self._tenant_id, self._business_id
        with self._uow_factory() as uow:
            campaign = uow.campaigns.get(tenant_id=tenant_id, business_id=business_id,
                                         campaign_id=campaign_id)
            if campaign is None:
                raise CampaignReplyNotFoundError("campaign not found")
            enrollment = uow.campaign_enrollments.get(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign_id, enrollment_id=request.enrollment_id,
            )
            if enrollment is None:
                raise CampaignReplyNotFoundError("campaign enrollment not found")
            if request.external_message_id:
                existing = uow.campaign_replies.get_by_external_id(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign_id,
                    external_message_id=request.external_message_id,
                )
                if existing is not None:
                    return {"id": existing.id, "campaign_id": campaign_id,
                            "enrollment_id": existing.enrollment_id,
                            "classification": existing.classification, "duplicate": True}
            lead = uow.leads.get(tenant_id=tenant_id, business_id=business_id,
                                 lead_id=enrollment.lead_id)
            if lead is None:
                raise CampaignReplyNotFoundError("campaign lead not found")
            classification = self._parser.classify(request.text)
            reply = uow.campaign_replies.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign_id, enrollment_id=enrollment.id,
                lead_id=lead.id, channel=campaign.channel,
                classification=classification.value,
                external_message_id=request.external_message_id,
            )
            uow.campaign_events.add(
                tenant_id=tenant_id, business_id=business_id,
                campaign_id=campaign_id, enrollment_id=enrollment.id,
                event_type="campaign.reply.received",
                payload={"reply_id": str(reply.id), "classification": classification.value,
                         "channel": campaign.channel},
            )
            if classification == ReplyClassification.OPT_OUT:
                self._opt_out.apply(
                    uow, tenant_id=tenant_id, business_id=business_id,
                    campaign_id=campaign_id, enrollment_id=enrollment.id, lead=lead,
                )
            elif classification != ReplyClassification.UNKNOWN:
                self._record_response_state(uow, tenant_id, business_id, lead,
                                            classification)
            return {"id": reply.id, "campaign_id": campaign_id,
                    "enrollment_id": enrollment.id,
                    "classification": classification.value, "duplicate": False}

    @staticmethod
    def _record_response_state(uow, tenant_id: str, business_id: str, lead,
                               classification: ReplyClassification) -> None:
        if lead.status == "qualified":
            uow.lead_transitions.transition(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                expected_state="qualified", new_state="contacted",
                context={"source": "campaign_reply"},
            )
            current = "contacted"
        else:
            current = lead.status
        if current == "contacted":
            uow.lead_transitions.transition(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                expected_state="contacted", new_state="responded",
                context={"source": "campaign_reply", "classification": classification.value},
            )
            uow.lead_events.add(
                tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
                event_type="lead.responded", payload={"classification": classification.value},
            )


__all__ = ["CampaignReplyNotFoundError", "CampaignReplyTenantMismatchError", "ReplyService"]
