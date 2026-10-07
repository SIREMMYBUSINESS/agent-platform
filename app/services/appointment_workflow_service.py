"""Durable, tenant-scoped appointment actions and provider outbox work."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueDomain
from app.security import AuthenticatedPrincipal
from app.services.scheduling_service import SchedulingService
from app.services.outbound_delivery_service import OutboundDeliveryService
from app.services.contact_action_tokens import ContactActionTokens
from app.services.contact_preference_service import ContactPreferenceService


class AppointmentWorkflowError(Exception):
    pass


class AppointmentWorkflowService:
    def __init__(self, *, trusted_tenant_id: str, uow_factory, calendar_provider=None):
        self._tenant_id = trusted_tenant_id
        self._uow_factory = uow_factory
        self._calendar_provider = calendar_provider
        self._scheduling = SchedulingService(calendar_provider=calendar_provider)

    def get(self, principal: AuthenticatedPrincipal, appointment_id: UUID):
        self._authorize(principal)
        with self._uow_factory() as uow:
            return self._lookup(uow, appointment_id)

    def confirm(self, principal: AuthenticatedPrincipal, appointment_id: UUID,
                slot_number: int | None = None):
        self._authorize(principal)
        with self._uow_factory() as uow:
            row = self._lookup(uow, appointment_id)
            if row.status == "confirmed":
                return row
            contact = uow.contacts.get(tenant_id=self._tenant_id, contact_id=row.contact_id)
            if contact is None:
                raise LookupError("appointment contact not found")
            result = self._scheduling.confirm(
                uow=uow, tenant_id=self._tenant_id, business_id=row.business_id,
                contact_id=row.contact_id, contact_timezone=contact.timezone,
                now=datetime.now(UTC), appointment_id=row.id,
                selection_text=str(slot_number) if slot_number is not None else None,
            )
            if result is None:
                raise AppointmentWorkflowError("appointment is no longer available to confirm")
            appointment = result.appointment
            appointment.correlation_id = appointment.correlation_id or str(
                appointment.conversation_id or appointment.id
            )
            if result.reproposed:
                conversation = uow.conversations.get(
                    tenant_id=self._tenant_id, conversation_id=appointment.conversation_id,
                )
                tenant = uow.tenants.get(tenant_id=self._tenant_id)
                business = uow.businesses.get(
                    tenant_id=self._tenant_id, business_id=appointment.business_id,
                )
                if conversation is None or tenant is None or business is None:
                    raise LookupError("appointment proposal context is unavailable")
                OutboundDeliveryService().queue(
                    uow, tenant=tenant, business=business, contact=contact,
                    conversation=conversation, channel=conversation.channel,
                    content=result.response,
                )
                uow.events.add(
                    tenant_id=self._tenant_id, business_id=appointment.business_id,
                    conversation_id=conversation.id, event_type="appointment.reproposed",
                    payload={"appointment_id": str(appointment.id)}, source="workflow_api",
                    correlation_id=appointment.correlation_id,
                )
                uow.workflows.append(
                    tenant_id=self._tenant_id, conversation_id=conversation.id,
                    state="appointment_reproposed",
                    context={"appointment_id": str(appointment.id)},
                )
                return appointment
            uow.queue_tasks.enqueue(
                tenant_id=self._tenant_id, business_id=appointment.business_id,
                domain=QueueDomain.SCHEDULING.value, task_name="scheduling.calendar_sync",
                idempotency_key=f"appointment:{appointment.id}:calendar:create",
                aggregate_type="appointment", aggregate_id=str(appointment.id),
                payload={"appointment_id": str(appointment.id), "operation": "create"},
                correlation_id=appointment.correlation_id, traceparent=None,
                available_at=datetime.now(UTC), max_attempts=8,
            )
            conversation = uow.conversations.get(
                tenant_id=self._tenant_id, conversation_id=appointment.conversation_id,
            )
            tenant = uow.tenants.get(tenant_id=self._tenant_id)
            business = uow.businesses.get(
                tenant_id=self._tenant_id, business_id=appointment.business_id,
            )
            if conversation is None or tenant is None or business is None:
                raise LookupError("appointment confirmation context is unavailable")
            channel = ContactPreferenceService().choose_channel(
                contact=contact, fallback=conversation.channel,
            )
            if channel is not None:
                OutboundDeliveryService().queue(
                    uow, tenant=tenant, business=business, contact=contact,
                    conversation=conversation, channel=channel, content=result.response,
                    correlation_id=appointment.correlation_id,
                )
            uow.workflows.append(
                tenant_id=self._tenant_id, conversation_id=conversation.id,
                state="appointment_confirmed",
                context={"appointment_id": str(appointment.id)},
            )
            uow.events.add(
                tenant_id=self._tenant_id, business_id=appointment.business_id,
                conversation_id=appointment.conversation_id, event_type="appointment.confirmed",
                payload={"appointment_id": str(appointment.id)}, source="workflow_api",
                correlation_id=appointment.correlation_id,
            )
            return appointment

    def cancel(self, principal: AuthenticatedPrincipal, appointment_id: UUID, reason: str):
        self._authorize(principal)
        with self._uow_factory() as uow:
            row = self._lookup(uow, appointment_id)
            if row.status == "cancelled":
                return row
            cancelled = uow.appointments.cancel(
                tenant_id=self._tenant_id, business_id=row.business_id, appointment_id=row.id,
            )
            if cancelled is None:
                raise AppointmentWorkflowError("appointment cannot be cancelled in its current state")
            cancelled.correlation_id = cancelled.correlation_id or str(cancelled.id)
            if cancelled.calendar_event_id:
                uow.queue_tasks.enqueue(
                    tenant_id=self._tenant_id, business_id=cancelled.business_id,
                    domain=QueueDomain.SCHEDULING.value, task_name="scheduling.calendar_sync",
                    idempotency_key=f"appointment:{cancelled.id}:calendar:cancel",
                    aggregate_type="appointment", aggregate_id=str(cancelled.id),
                    payload={"appointment_id": str(cancelled.id), "operation": "cancel"},
                    correlation_id=cancelled.correlation_id, traceparent=None,
                    available_at=datetime.now(UTC), max_attempts=8,
                )
            uow.events.add(
                tenant_id=self._tenant_id, business_id=cancelled.business_id,
                conversation_id=cancelled.conversation_id, event_type="appointment.cancelled",
                payload={"appointment_id": str(cancelled.id)},
                source="workflow_api", correlation_id=cancelled.correlation_id,
            )
            uow.workflows.append(
                tenant_id=self._tenant_id, conversation_id=cancelled.conversation_id,
                state="appointment_cancelled",
                context={"appointment_id": str(cancelled.id), "reason": reason},
            )
            return cancelled

    def _contact_principal(self, token: str):
        token_hash = ContactActionTokens.digest(token)
        with self._uow_factory() as uow:
            row = uow.appointments.get_by_contact_action_token(token_hash=token_hash)
            starts_at = row.starts_at if row is not None else None
            if starts_at is not None and starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=UTC)
            if row is None or starts_at <= datetime.now(UTC) or row.status not in {"proposed", "confirmed"}:
                raise LookupError("appointment action is unavailable")
            tenant_id = row.tenant_id
            appointment_id = row.id
        scoped = AppointmentWorkflowService(
            trusted_tenant_id=tenant_id, uow_factory=self._uow_factory,
            calendar_provider=self._calendar_provider,
        )
        principal = AuthenticatedPrincipal(
            principal_id=f"contact-action:{appointment_id}", tenant_id=tenant_id,
            roles=("workflow_executor",), auth_source="appointment_action_token",
            issued_at=datetime.now(UTC),
        )
        return scoped, principal, appointment_id

    def confirm_contact_token(self, token: str, slot_number: int | None = None):
        service, principal, appointment_id = self._contact_principal(token)
        return service.confirm(principal, appointment_id, slot_number)

    def cancel_contact_token(self, token: str, reason: str):
        service, principal, appointment_id = self._contact_principal(token)
        return service.cancel(principal, appointment_id, reason)

    def _authorize(self, principal):
        if principal.tenant_id != self._tenant_id or "workflow_executor" not in principal.roles:
            raise PermissionError("workflow access denied")

    def _lookup(self, uow: UnitOfWork, appointment_id: UUID):
        # Resolve the business from the trusted tenant scoped row, never request data.
        row = uow.appointments.get_in_tenant(
            tenant_id=self._tenant_id, appointment_id=appointment_id,
        )
        if row is None:
            raise LookupError("appointment not found")
        return row

__all__ = ["AppointmentWorkflowError", "AppointmentWorkflowService"]
