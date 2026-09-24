"""Transactional receptionist intake orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.persistence.models import Business, Contact, Tenant
from app.persistence.repositories import EntityNotFoundError
from app.persistence.unit_of_work import UnitOfWork
from app.policies import PolicyChecker
from app.schemas import InboundRequest
from app.services.escalation_service import EscalationService
from app.services.routing_service import RoutingService
from app.services.scheduling_service import SchedulingService


class InboundTenantMismatchError(Exception):
    """The request tenant assertion differs from trusted application context."""


class InboundProvisioningError(EntityNotFoundError):
    """A required tenant, business, or contact was not provisioned."""


URGENT_TERMS = (
    "urgent", "emergency", "asap", "immediately", "severe pain", "can't breathe",
    "cannot breathe", "fire", "unsafe", "threat", "bleeding",
)
APPOINTMENT_TERMS = ("appointment", "schedule", "booking", "book", "reschedule")
SUPPORTED_CHANNELS = {"chat", "sms", "voicemail", "phone", "whatsapp", "email"}


class IntakeService:
    """Process one intake using trusted records and one Unit of Work transaction."""

    def __init__(
        self,
        *,
        trusted_tenant_id: str,
        uow_factory: Callable[[], UnitOfWork],
        policy_checker: PolicyChecker | None = None,
        routing_service: RoutingService | None = None,
        scheduling_service: SchedulingService | None = None,
        escalation_service: EscalationService | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if not trusted_tenant_id.strip():
            raise ValueError("trusted_tenant_id must not be blank")
        self._trusted_tenant_id = trusted_tenant_id
        self._uow_factory = uow_factory
        self._policy = policy_checker or PolicyChecker()
        self._routing = routing_service or RoutingService()
        self._scheduling = scheduling_service or SchedulingService(self._routing)
        self._escalation = escalation_service or EscalationService()
        self._now = now_provider or (lambda: datetime.now(UTC))

    @staticmethod
    def _classify(text: str) -> tuple[str, str]:
        normalized = text.casefold()
        if any(term in normalized for term in URGENT_TERMS):
            return "urgent", "urgent"
        if SchedulingService.is_confirmation(text):
            return "appointment_confirmation", "routine"
        if any(term in normalized for term in APPOINTMENT_TERMS):
            return "appointment", "routine"
        if any(term in normalized for term in ("business hours", "opening hours", "open", "close")):
            return "business_hours", "routine"
        return "unknown", "routine"

    @staticmethod
    def _event(
        uow: Any,
        *,
        tenant_id: str,
        business_id: str,
        conversation_id,
        event_type: str,
        payload: dict[str, Any],
        source: str = "intake_service",
    ) -> None:
        uow.events.add(
            tenant_id=tenant_id,
            business_id=business_id,
            conversation_id=conversation_id,
            event_type=event_type,
            payload=payload,
            source=source,
            correlation_id=str(conversation_id),
        )

    def _transition(
        self,
        uow: Any,
        *,
        tenant_id: str,
        business_id: str,
        conversation_id,
        state: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        uow.workflows.append(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            state=state,
            context=context or {},
        )
        self._event(
            uow,
            tenant_id=tenant_id,
            business_id=business_id,
            conversation_id=conversation_id,
            event_type="workflow.state.changed",
            payload={"state": state},
        )

    @staticmethod
    def _outbound(
        uow: Any,
        *,
        tenant_id: str,
        conversation_id,
        channel: str,
        content: str,
    ) -> None:
        uow.messages.add(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            sender="agent",
            channel=channel,
            content=content,
            direction="outbound",
        )

    def handle(self, request: InboundRequest) -> dict[str, Any]:
        if request.tenant_id != self._trusted_tenant_id:
            raise InboundTenantMismatchError("request tenant does not match trusted tenant")

        tenant_id = self._trusted_tenant_id
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        now = now.astimezone(UTC)

        with self._uow_factory() as uow:
            tenant: Tenant | None = uow.tenants.get(tenant_id=tenant_id)
            business: Business | None = uow.businesses.get(
                tenant_id=tenant_id, business_id=request.business_id
            )
            contact: Contact | None = uow.contacts.get(
                tenant_id=tenant_id, contact_id=request.contact_id
            )
            if tenant is None or business is None or contact is None:
                raise InboundProvisioningError("tenant, business, or contact is not provisioned")
            if contact.business_id != business.id:
                raise InboundProvisioningError("contact is not provisioned for this business")

            conversation = uow.conversations.create(
                tenant_id=tenant_id,
                business_id=business.id,
                contact_id=contact.id,
                channel=request.channel,
            )
            self._transition(
                uow,
                tenant_id=tenant_id,
                business_id=business.id,
                conversation_id=conversation.id,
                state="intake_received",
                context={"channel": request.channel},
            )
            uow.messages.add(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                sender="contact",
                channel=request.channel,
                content=request.text or "",
                direction="inbound",
            )
            self._event(
                uow,
                tenant_id=tenant_id,
                business_id=business.id,
                conversation_id=conversation.id,
                event_type="intake.received",
                payload={"channel": request.channel},
            )

            # Consent is provisioned on Contact. The request field and metadata
            # are retained for contract compatibility but are not authoritative.
            allowed, reason = self._policy.validate(
                business.region, request.channel, contact.consent_status
            )
            if not allowed:
                self._transition(
                    uow,
                    tenant_id=tenant_id,
                    business_id=business.id,
                    conversation_id=conversation.id,
                    state="blocked",
                    context={"reason": reason},
                )
                return {
                    "success": False,
                    "status": "blocked",
                    "workflow_state": "blocked",
                    "reason": reason,
                }

            intent, priority = self._classify(request.text or "")
            if request.channel not in SUPPORTED_CHANNELS:
                intent = "unknown"
            self._transition(
                uow,
                tenant_id=tenant_id,
                business_id=business.id,
                conversation_id=conversation.id,
                state="intent_classified",
                context={"intent": intent, "priority": priority},
            )

            rules = uow.routing_rules.list_for_business(
                tenant_id=tenant_id, business_id=business.id
            )
            routing = self._routing.evaluate(
                business=business,
                contact=contact,
                tenant=tenant,
                rules=rules,
                now=now,
            )
            self._transition(
                uow,
                tenant_id=tenant_id,
                business_id=business.id,
                conversation_id=conversation.id,
                state="business_hours_checked",
                context={
                    "is_open": routing.is_open,
                    "business_timezone": routing.business_timezone,
                },
            )
            self._event(
                uow,
                tenant_id=tenant_id,
                business_id=business.id,
                conversation_id=conversation.id,
                event_type="business.hours.checked",
                payload={"is_open": routing.is_open},
            )

            if priority == "urgent":
                return self._escalate(
                    uow, tenant_id, business, contact, conversation,
                    request.channel, "urgent language detected", "urgent",
                )

            if not routing.safely_configured:
                return self._escalate(
                    uow, tenant_id, business, contact, conversation,
                    request.channel, "business timezone could not be resolved", "normal",
                )

            if not routing.is_open:
                action = routing.after_hours_action
                state = "after_hours_voicemail" if action == "voicemail" else "callback_requested"
                response = (
                    "We're closed right now. Please leave a voicemail and our team will follow up."
                    if action == "voicemail"
                    else "We're closed right now. I've recorded your request for a callback during business hours."
                )
                self._transition(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, state=state,
                    context={"after_hours_action": action},
                )
                self._outbound(
                    uow, tenant_id=tenant_id, conversation_id=conversation.id,
                    channel=request.channel, content=response,
                )
                self._event(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id,
                    event_type="after_hours.request.routed",
                    payload={"action": action},
                )
                return {
                    "success": True, "status": "after_hours",
                    "workflow_state": state, "agent_response": response,
                }

            if intent == "appointment_confirmation":
                scheduled = self._scheduling.confirm(
                    uow=uow, tenant_id=tenant_id, business_id=business.id,
                    contact_id=contact.id, contact_timezone=routing.contact_timezone,
                )
                if scheduled is None:
                    return self._escalate(
                        uow, tenant_id, business, contact, conversation,
                        request.channel, "appointment confirmation could not be matched", "normal",
                    )
                self._transition(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, state="appointment_confirmed",
                    context={"appointment_id": str(scheduled.appointment.id)},
                )
                self._event(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, event_type="appointment.confirmed",
                    payload={"appointment_id": str(scheduled.appointment.id)},
                )
                self._transition(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, state="reminder_prepared",
                    context={"appointment_id": str(scheduled.appointment.id)},
                )
                self._event(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, event_type="appointment.reminder.prepared",
                    payload={"appointment_id": str(scheduled.appointment.id)},
                )
                self._outbound(
                    uow, tenant_id=tenant_id, conversation_id=conversation.id,
                    channel=request.channel, content=scheduled.response,
                )
                return {
                    "success": True, "status": "ok",
                    "workflow_state": "appointment_confirmed",
                    "agent_response": scheduled.response,
                }

            if intent == "appointment":
                scheduled = self._scheduling.propose(
                    uow=uow, tenant_id=tenant_id, business_id=business.id,
                    contact_id=contact.id, conversation_id=conversation.id,
                    text=request.text or "", now=now,
                    contact_timezone=routing.contact_timezone,
                )
                if scheduled is None:
                    return self._escalate(
                        uow, tenant_id, business, contact, conversation,
                        request.channel, "no safe appointment slot could be selected", "normal",
                    )
                self._transition(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, state="appointment_proposed",
                    context={"appointment_id": str(scheduled.appointment.id)},
                )
                self._event(
                    uow, tenant_id=tenant_id, business_id=business.id,
                    conversation_id=conversation.id, event_type="appointment.proposed",
                    payload={"appointment_id": str(scheduled.appointment.id)},
                )
                self._outbound(
                    uow, tenant_id=tenant_id, conversation_id=conversation.id,
                    channel=request.channel, content=scheduled.response,
                )
                return {
                    "success": True, "status": "ok",
                    "workflow_state": "appointment_proposed",
                    "agent_response": scheduled.response,
                }

            if intent == "business_hours":
                rule = next((item for item in rules if routing.checked_at_local
                             and item.weekday == routing.checked_at_local.weekday()), None)
                if rule is None:
                    response = "Our business hours are not currently available. I can ask the team to follow up."
                    return self._escalate(
                        uow, tenant_id, business, contact, conversation,
                        request.channel, "business hours are not configured for this day", "normal",
                    )
                response = (
                    f"Our hours today are {rule.opens_at.strftime('%I:%M %p').lstrip('0')} to "
                    f"{rule.closes_at.strftime('%I:%M %p').lstrip('0')} "
                    f"({routing.business_timezone})."
                )
                self._outbound(
                    uow, tenant_id=tenant_id, conversation_id=conversation.id,
                    channel=request.channel, content=response,
                )
                return {
                    "success": True, "status": "ok",
                    "workflow_state": "business_hours_checked",
                    "agent_response": response,
                }

            return self._escalate(
                uow, tenant_id, business, contact, conversation,
                request.channel, "intake intent could not be safely handled", "normal",
            )

    def _escalate(
        self, uow: Any, tenant_id: str, business: Business, contact: Contact,
        conversation: Any, channel: str, reason: str, priority: str,
    ) -> dict[str, Any]:
        self._escalation.create_ticket(
            uow=uow,
            tenant_id=tenant_id,
            business_id=business.id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            reason=reason,
            priority=priority,
        )
        self._transition(
            uow, tenant_id=tenant_id, business_id=business.id,
            conversation_id=conversation.id, state="escalated",
            context={"reason": reason, "priority": priority},
        )
        response = "I've sent this to our team so a person can help you."
        self._outbound(
            uow, tenant_id=tenant_id, conversation_id=conversation.id,
            channel=channel, content=response,
        )
        return {
            "success": True, "status": "escalated",
            "workflow_state": "escalated", "agent_response": response,
        }


__all__ = ["InboundProvisioningError", "InboundTenantMismatchError", "IntakeService"]
