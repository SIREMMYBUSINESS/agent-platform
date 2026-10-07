"""Durable appointment completion and post-appointment follow-up workflow."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.persistence.unit_of_work import UnitOfWork
from app.services.contact_preference_service import ContactPreferenceService
from app.services.crm_sync_service import CRMSyncService
from app.services.outbound_delivery_service import OutboundDeliveryService


class FollowUpService:
    def __init__(self, *, uow_factory):
        self._uow_factory = uow_factory

    def execute(self, *, tenant_id: str, business_id: str, appointment_id: UUID,
                correlation_id: str):
        message_id = uuid5(NAMESPACE_URL, f"appointment-reminder:{appointment_id}:followup")
        with self._uow_factory() as uow:
            appointment = uow.appointments.get_for_tenant(
                tenant_id=tenant_id, business_id=business_id,
                appointment_id=appointment_id,
            )
            if appointment is None:
                raise LookupError("appointment is unavailable")
            if appointment.status == "completed":
                return {"appointment_id": str(appointment_id), "status": "already_completed"}
            completed = uow.appointments.mark_completed(
                tenant_id=tenant_id, business_id=business_id,
                appointment_id=appointment_id, now=datetime.now(UTC),
            )
            if completed is None:
                return {"appointment_id": str(appointment_id), "status": "not_due_or_cancelled"}
            contact = uow.contacts.get(tenant_id=tenant_id, contact_id=completed.contact_id)
            conversation = (uow.conversations.get(
                tenant_id=tenant_id, conversation_id=completed.conversation_id,
            ) if completed.conversation_id else None)
            tenant = uow.tenants.get(tenant_id=tenant_id)
            business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
            if contact is None or conversation is None or tenant is None or business is None:
                raise LookupError("appointment follow-up scope is unavailable")
            uow.events.add(
                tenant_id=tenant_id, business_id=business_id,
                conversation_id=conversation.id, event_type="appointment.completed",
                payload={"appointment_id": str(appointment_id)},
                source="appointment_followup_worker", correlation_id=correlation_id,
            )
            if completed.campaign_id and completed.campaign_enrollment_id:
                uow.campaign_events.add(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=completed.campaign_id,
                    enrollment_id=completed.campaign_enrollment_id,
                    event_type="campaign.appointment.completed",
                    payload={"appointment_id": str(appointment_id)},
                )
                enrollment = uow.campaign_enrollments.get(
                    tenant_id=tenant_id, business_id=business_id,
                    campaign_id=completed.campaign_id,
                    enrollment_id=completed.campaign_enrollment_id,
                )
                lead = (uow.leads.get(
                    tenant_id=tenant_id, business_id=business_id,
                    lead_id=enrollment.lead_id,
                ) if enrollment else None)
                if lead is not None and lead.status == "booked":
                    uow.lead_transitions.transition(
                        tenant_id=tenant_id, business_id=business_id,
                        lead_id=lead.id, expected_state="booked", new_state="converted",
                        context={"appointment_id": str(appointment_id)},
                    )
            CRMSyncService().enqueue_appointment_activity(
                uow, appointment=completed, activity_type="appointment_completed",
            )
            preferences = ContactPreferenceService()
            channel = preferences.choose_channel(contact=contact, fallback=conversation.channel)
            destination = preferences.destination(contact, channel) if channel else None
            if channel is None or not destination:
                message = uow.messages.add(
                    tenant_id=tenant_id, conversation_id=conversation.id,
                    sender="agent", channel=channel or "sms",
                    content="Thank you for your appointment. We would appreciate your feedback.",
                    direction="outbound", delivery_status="blocked", message_id=message_id,
                )
                uow.events.add(
                    tenant_id=tenant_id, business_id=business_id,
                    conversation_id=conversation.id, event_type="appointment.followup.blocked",
                    payload={"appointment_id": str(appointment_id),
                             "reason_code": "preferred_channel_unavailable"},
                    source="appointment_followup_worker", correlation_id=correlation_id,
                )
                status = message.delivery_status
            else:
                message = OutboundDeliveryService().queue(
                    uow, tenant=tenant, business=business, contact=contact,
                    conversation=conversation, channel=channel,
                    content="Thank you for your appointment. How was your experience? Reply to share feedback.",
                    message_id=message_id, correlation_id=correlation_id,
                )
                status = message.delivery_status
            return {"appointment_id": str(appointment_id), "status": "completed",
                    "followup_status": status}


__all__ = ["FollowUpService"]
