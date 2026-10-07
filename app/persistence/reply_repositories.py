"""Persistence contracts for campaign replies, opt-outs, and analytics."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, update
from sqlmodel import Session, select

from app.persistence.models import (
    Appointment, Business, Campaign, CampaignEnrollment, CampaignEventLog, CampaignReply,
    CampaignSequence, CampaignStepRun, ContactChannelConsent, Lead, LeadStateTransition,
)
from app.persistence.repositories import _database_call, _require_tenant


class SQLContactChannelConsentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, business_id: str, contact_id: str, channel: str):
        _require_tenant(tenant_id)
        stmt = select(ContactChannelConsent).where(
            ContactChannelConsent.tenant_id == tenant_id,
            ContactChannelConsent.business_id == business_id,
            ContactChannelConsent.contact_id == contact_id,
            ContactChannelConsent.channel == channel,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def revoke(self, *, tenant_id: str, business_id: str, contact_id: str, channel: str):
        _require_tenant(tenant_id)
        def persist():
            row = self.get(tenant_id=tenant_id, business_id=business_id,
                           contact_id=contact_id, channel=channel)
            if row is None:
                row = ContactChannelConsent(tenant_id=tenant_id, business_id=business_id,
                                            contact_id=contact_id, channel=channel,
                                            status="revoked")
                self._session.add(row)
            else:
                row.status = "revoked"
                row.updated_at = datetime.now(UTC)
            self._session.flush()
            return row
        return _database_call(persist)


class SQLCampaignReplyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_external_id(self, *, tenant_id: str, business_id: str,
                           campaign_id: UUID, external_message_id: str):
        _require_tenant(tenant_id)
        stmt = select(CampaignReply).where(
            CampaignReply.tenant_id == tenant_id,
            CampaignReply.business_id == business_id,
            CampaignReply.campaign_id == campaign_id,
            CampaignReply.external_message_id == external_message_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def add(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
            enrollment_id: UUID, lead_id: UUID, channel: str, classification: str,
            external_message_id: str | None = None):
        _require_tenant(tenant_id)
        row = CampaignReply(tenant_id=tenant_id, business_id=business_id,
                            campaign_id=campaign_id, enrollment_id=enrollment_id,
                            lead_id=lead_id, channel=channel,
                            classification=classification,
                            external_message_id=external_message_id)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_for_campaign(self, *, tenant_id: str, business_id: str, campaign_id: UUID):
        _require_tenant(tenant_id)
        stmt = select(CampaignReply).where(
            CampaignReply.tenant_id == tenant_id,
            CampaignReply.business_id == business_id,
            CampaignReply.campaign_id == campaign_id,
        ).order_by(CampaignReply.created_at, CampaignReply.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))


class SQLCampaignAnalyticsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def aggregate(self, *, tenant_id: str, business_id: str, campaign_id: UUID) -> dict:
        _require_tenant(tenant_id)
        campaign_stmt = select(Campaign.id).where(
            Campaign.tenant_id == tenant_id, Campaign.business_id == business_id,
            Campaign.id == campaign_id,
        )
        def read():
            if self._session.exec(campaign_stmt).first() is None:
                return None
            runs_stmt = select(func.count(CampaignStepRun.id)).join(
                CampaignEnrollment,
                (CampaignEnrollment.tenant_id == CampaignStepRun.tenant_id)
                & (CampaignEnrollment.business_id == CampaignStepRun.business_id)
                & (CampaignEnrollment.id == CampaignStepRun.enrollment_id),
            ).where(CampaignStepRun.tenant_id == tenant_id,
                    CampaignStepRun.business_id == business_id,
                    CampaignEnrollment.campaign_id == campaign_id,
                    CampaignStepRun.status == "sent")
            replies = self._session.exec(select(CampaignReply).where(
                CampaignReply.tenant_id == tenant_id,
                CampaignReply.business_id == business_id,
                CampaignReply.campaign_id == campaign_id,
            )).all()
            sequence_rows = self._session.exec(select(
                CampaignSequence.id, CampaignSequence.name,
            ).where(
                CampaignSequence.tenant_id == tenant_id,
                CampaignSequence.business_id == business_id,
                CampaignSequence.campaign_id == campaign_id,
            )).all()
            enrollment_rows = self._session.exec(select(
                CampaignEnrollment.id, CampaignEnrollment.sequence_id,
            ).where(
                CampaignEnrollment.tenant_id == tenant_id,
                CampaignEnrollment.business_id == business_id,
                CampaignEnrollment.campaign_id == campaign_id,
            )).all()
            sent_enrollments = set(self._session.exec(select(
                CampaignStepRun.enrollment_id,
            ).join(CampaignEnrollment,
                (CampaignEnrollment.tenant_id == CampaignStepRun.tenant_id)
                & (CampaignEnrollment.business_id == CampaignStepRun.business_id)
                & (CampaignEnrollment.id == CampaignStepRun.enrollment_id),
            ).where(
                CampaignStepRun.tenant_id == tenant_id,
                CampaignStepRun.business_id == business_id,
                CampaignEnrollment.campaign_id == campaign_id,
                CampaignStepRun.status == "sent",
            )).all())
            converted_enrollments = {
                reply.enrollment_id for reply in replies
                if reply.classification in {"positive", "book_appointment", "callback"}
            }
            variant_metrics = []
            for sequence_id, variant_name in sorted(sequence_rows, key=lambda row: row[1]):
                variant_enrollments = {
                    enrollment_id for enrollment_id, enrolled_sequence_id in enrollment_rows
                    if enrolled_sequence_id == sequence_id
                }
                variant_sent = variant_enrollments & sent_enrollments
                variant_conversions = variant_enrollments & converted_enrollments
                variant_metrics.append({
                    "variant_id": variant_name,
                    "enrolled_count": len(variant_enrollments),
                    "sent_count": len(variant_sent),
                    "conversions": len(variant_conversions),
                    "conversion_rate": (len(variant_conversions) / len(variant_sent)
                                        if variant_sent else 0.0),
                })
            event_counts = self._session.exec(select(
                CampaignEventLog.event_type, func.count(CampaignEventLog.id)
            ).where(CampaignEventLog.tenant_id == tenant_id,
                    CampaignEventLog.business_id == business_id,
                    CampaignEventLog.campaign_id == campaign_id,
                    CampaignEventLog.event_type.in_(
                        ["step.opened", "campaign.appointment.booked", "campaign.appointment.completed"]
                    )
            ).group_by(CampaignEventLog.event_type)).all()
            counts = {event_type: count for event_type, count in event_counts}
            sent = self._session.exec(runs_stmt).one()
            positive = sum(reply.classification in {"positive", "book_appointment", "callback"} for reply in replies)
            total = len(replies)
            return {
                "campaign_id": campaign_id,
                "sent_count": int(sent or 0),
                "contacted_count": len(sent_enrollments),
                "opened_count": int(counts.get("step.opened", 0)),
                "reply_count": total,
                "positive_reply_rate": (positive / total) if total else 0.0,
                "opt_out_count": sum(reply.classification == "opt_out" for reply in replies),
                "booked_appointments": int(counts.get("campaign.appointment.booked", 0)),
                "completed_appointments": int(counts.get("campaign.appointment.completed", 0)),
                "variant_metrics": variant_metrics,
            }
        return _database_call(read)

    def aggregate_business_funnel(self, *, tenant_id: str, business_id: str) -> dict | None:
        _require_tenant(tenant_id)
        def read():
            business = self._session.exec(select(Business.id).where(
                Business.tenant_id == tenant_id, Business.id == business_id,
            )).first()
            if business is None:
                return None
            lead_count = self._session.exec(select(func.count(Lead.id)).where(
                Lead.tenant_id == tenant_id, Lead.business_id == business_id,
            )).one()
            def transitioned_leads(state: str) -> set:
                rows = self._session.exec(select(LeadStateTransition.lead_id).where(
                    LeadStateTransition.tenant_id == tenant_id,
                    LeadStateTransition.business_id == business_id,
                    LeadStateTransition.to_state == state,
                )).all()
                return set(rows)
            sent_leads = set(self._session.exec(select(CampaignEnrollment.lead_id).join(
                CampaignStepRun,
                (CampaignStepRun.tenant_id == CampaignEnrollment.tenant_id)
                & (CampaignStepRun.business_id == CampaignEnrollment.business_id)
                & (CampaignStepRun.enrollment_id == CampaignEnrollment.id),
            ).where(
                CampaignEnrollment.tenant_id == tenant_id,
                CampaignEnrollment.business_id == business_id,
                CampaignStepRun.status == "sent",
            )).all())
            replied_leads = set(self._session.exec(select(CampaignReply.lead_id).where(
                CampaignReply.tenant_id == tenant_id,
                CampaignReply.business_id == business_id,
            )).all())
            def transition_count(state: str) -> int:
                return len(transitioned_leads(state))
            booked = self._session.exec(select(func.count(Appointment.id)).where(
                Appointment.tenant_id == tenant_id,
                Appointment.business_id == business_id,
                Appointment.status.in_(("confirmed", "completed")),
            )).one()
            completed = self._session.exec(select(func.count(Appointment.id)).where(
                Appointment.tenant_id == tenant_id,
                Appointment.business_id == business_id,
                Appointment.status == "completed",
            )).one()
            return {
                "business_id": business_id,
                "lead_count": int(lead_count or 0),
                "contacted_count": len(sent_leads | transitioned_leads("contacted")),
                "replied_count": len(replied_leads | transitioned_leads("responded")),
                "booked_count": int(booked or 0),
                "completed_count": int(completed or 0),
                "converted_count": transition_count("converted"),
            }
        return _database_call(read)


class SQLCampaignReplyEnrollmentRepository:
    """Mutation kept separate from campaign progression API contract."""
    def __init__(self, session: Session) -> None:
        self._session = session

    def cancel(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
               enrollment_id: UUID) -> None:
        _require_tenant(tenant_id)
        def persist():
            self._session.exec(update(CampaignEnrollment).where(
                CampaignEnrollment.tenant_id == tenant_id,
                CampaignEnrollment.business_id == business_id,
                CampaignEnrollment.campaign_id == campaign_id,
                CampaignEnrollment.id == enrollment_id,
                CampaignEnrollment.status == "active",
            ).values(status="canceled", next_step_at=None, updated_at=datetime.now(UTC)))
        _database_call(persist)
