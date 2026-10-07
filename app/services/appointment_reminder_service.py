"""Durably schedule and prepare appointment reminders."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from app.queue.contracts import QueueDomain
from app.services.contact_preference_service import ContactPreferenceService
from app.services.outbound_delivery_service import OutboundDeliveryService


class AppointmentReminderService:
    OFFSETS = {"24h": timedelta(hours=24), "1h": timedelta(hours=1)}

    def schedule(self, uow, *, appointment, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        correlation_id = appointment.correlation_id or str(appointment.id)
        starts_at = appointment.starts_at
        if starts_at.tzinfo is None:
            starts_at = starts_at.replace(tzinfo=UTC)
        scheduled = 0
        for reminder, offset in self.OFFSETS.items():
            due_at = starts_at - offset
            if due_at <= now:
                continue
            uow.queue_tasks.enqueue(
                tenant_id=appointment.tenant_id, business_id=appointment.business_id,
                domain=QueueDomain.SCHEDULING.value,
                task_name="appointment.reminder.prepare",
                idempotency_key=f"appointment:{appointment.id}:reminder:{reminder}",
                aggregate_type="appointment", aggregate_id=str(appointment.id),
                payload={"appointment_id": str(appointment.id), "reminder": reminder},
                correlation_id=correlation_id, traceparent=None,
                available_at=due_at, max_attempts=6,
            )
            scheduled += 1
        followup_at = appointment.ends_at
        if followup_at.tzinfo is None:
            followup_at = followup_at.replace(tzinfo=UTC)
        if followup_at > now:
            uow.queue_tasks.enqueue(
                tenant_id=appointment.tenant_id, business_id=appointment.business_id,
                domain=QueueDomain.SCHEDULING.value,
                task_name="appointment.followup.prepare",
                idempotency_key=f"appointment:{appointment.id}:followup:completion",
                aggregate_type="appointment", aggregate_id=str(appointment.id),
                payload={"appointment_id": str(appointment.id)},
                correlation_id=correlation_id, traceparent=None,
                available_at=followup_at, max_attempts=6,
            )
            scheduled += 1
        return scheduled

    def prepare(self, uow, *, tenant_id: str, business_id: str,
                appointment_id: UUID, reminder: str, correlation_id: str):
        if reminder not in self.OFFSETS:
            raise ValueError("unsupported appointment reminder")
        message_id = self.message_id(appointment_id, reminder)
        appointment = uow.appointments.get_for_tenant(
            tenant_id=tenant_id, business_id=business_id,
            appointment_id=appointment_id,
        )
        if appointment is None:
            raise LookupError("appointment is unavailable")
        if appointment.status != "confirmed":
            return {"appointment_id": str(appointment_id), "status": "cancelled"}
        contact = uow.contacts.get(tenant_id=tenant_id, contact_id=appointment.contact_id)
        conversation = (uow.conversations.get(
            tenant_id=tenant_id, conversation_id=appointment.conversation_id,
        ) if appointment.conversation_id else None)
        tenant = uow.tenants.get(tenant_id=tenant_id)
        business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
        if contact is None or tenant is None or business is None or conversation is None:
            raise LookupError("appointment contact scope is unavailable")
        preferences = ContactPreferenceService()
        channel = preferences.choose_channel(contact=contact, fallback=conversation.channel)
        destination_exists = bool(preferences.destination(contact, channel) if channel else None)
        if channel is None or not destination_exists:
            existing = uow.messages.get_in_tenant(tenant_id=tenant_id, message_id=message_id)
            if existing is None:
                uow.messages.add(
                    tenant_id=tenant_id, conversation_id=conversation.id,
                    sender="agent", channel=channel or "sms",
                    content=f"Appointment reminder ({reminder})", direction="outbound",
                    delivery_status="blocked", message_id=message_id,
                )
            else:
                uow.messages.set_delivery(
                    tenant_id=tenant_id, message_id=message_id,
                    status="blocked", error_code="preferred_channel_unavailable",
                )
            uow.events.add(
                tenant_id=tenant_id, business_id=business_id,
                conversation_id=conversation.id, event_type="appointment.reminder.blocked",
                payload={"appointment_id": str(appointment_id),
                         "reason_code": "preferred_channel_unavailable"},
                source="appointment_reminder_worker", correlation_id=correlation_id,
            )
            return {"message_id": str(message_id), "status": "blocked"}
        local_start = appointment.starts_at
        if local_start.tzinfo is None:
            local_start = local_start.replace(tzinfo=UTC)
        text = f"Reminder: your appointment is scheduled for {local_start.isoformat()}."
        message = OutboundDeliveryService().queue(
            uow, tenant=tenant, business=business, contact=contact,
            conversation=conversation, channel=channel, content=text,
            message_id=message_id, correlation_id=correlation_id,
        )
        uow.events.add(
            tenant_id=tenant_id, business_id=business_id,
            conversation_id=conversation.id, event_type="appointment.reminder.prepared",
            payload={"appointment_id": str(appointment_id), "message_id": str(message.id),
                     "reminder": reminder},
            source="appointment_reminder_worker", correlation_id=correlation_id,
        )
        return {"message_id": str(message_id), "status": message.delivery_status}

    @staticmethod
    def message_id(appointment_id: UUID, reminder: str) -> UUID:
        return uuid5(NAMESPACE_URL, f"appointment-reminder:{appointment_id}:{reminder}")


__all__ = ["AppointmentReminderService"]
