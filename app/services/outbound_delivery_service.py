"""Policy checked creation of durable, idempotent outbound deliveries."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.queue.contracts import QueueDomain
from app.services.policy_service import PolicyService


class OutboundDeliveryService:
    def __init__(self, *, policy_service: PolicyService | None = None):
        self._policy = policy_service or PolicyService()

    def queue(self, uow, *, tenant, business, contact, conversation,
              channel: str, content: str, message_id: UUID | None = None,
              correlation_id: str | None = None, subject: str | None = None,
              campaign_step_run_id: UUID | None = None):
        if (tenant.id != business.tenant_id or contact.tenant_id != tenant.id
                or contact.business_id != business.id or conversation.tenant_id != tenant.id
                or conversation.business_id != business.id or conversation.contact_id != contact.id):
            raise PermissionError("outbound delivery scope mismatch")
        existing = (uow.messages.get_in_tenant(tenant_id=tenant.id, message_id=message_id)
                    if message_id else None)
        if existing is not None:
            if (existing.channel != channel or existing.content != content
                    or existing.subject != subject):
                raise ValueError("message id is already bound to different outbound content")
            if existing.delivery_status in {"sent", "blocked", "dry_run"}:
                return existing
            message = existing
        else:
            message = uow.messages.add(
                tenant_id=tenant.id, conversation_id=conversation.id, sender="agent",
                channel=channel, content=content, direction="outbound",
                delivery_status="pending", message_id=message_id, subject=subject,
            )
        decision = self._policy.evaluate(
            uow, tenant=tenant, business=business, contact=contact,
            channel=channel, direction="outbound",
        )
        if not decision.allowed:
            message = uow.messages.set_delivery(
                tenant_id=tenant.id, message_id=message.id, status="blocked",
                error_code=decision.reason_code,
            )
            self._event(uow, tenant, business, conversation, message,
                        correlation_id,
                        "outbound.delivery.blocked", {"reason_code": decision.reason_code})
            return message
        if channel == "chat":
            # Synchronous chat callers receive the response in the HTTP body.
            message = uow.messages.set_delivery(
                tenant_id=tenant.id, message_id=message.id, status="sent",
            )
            self._event(uow, tenant, business, conversation, message,
                        correlation_id,
                        "outbound.delivery.returned", {})
            return message
        destination = (contact.email if channel == "email" else
                       contact.whatsapp if channel == "whatsapp" else
                       contact.phone if channel == "sms" else None)
        if not destination:
            message = uow.messages.set_delivery(
                tenant_id=tenant.id, message_id=message.id,
                status="blocked", error_code="recipient_address_missing",
            )
            self._event(uow, tenant, business, conversation, message,
                        correlation_id, "outbound.delivery.blocked",
                        {"reason_code": "recipient_address_missing"})
            return message
        uow.queue_tasks.enqueue(
            tenant_id=tenant.id, business_id=business.id,
            domain=QueueDomain.OUTREACH.value, task_name="communication.deliver_message",
            idempotency_key=f"message:{message.id}:delivery",
            aggregate_type="message", aggregate_id=str(message.id),
            payload={"message_id": str(message.id),
                     **({"campaign_step_run_id": str(campaign_step_run_id)}
                        if campaign_step_run_id else {})},
            correlation_id=correlation_id or str(getattr(conversation, "correlation_id", None) or conversation.id),
            traceparent=None, available_at=datetime.now(UTC), max_attempts=6,
        )
        self._event(uow, tenant, business, conversation, message,
                    correlation_id,
                    "outbound.delivery.queued", {})
        return message

    @staticmethod
    def _event(uow, tenant, business, conversation, message, correlation_id, event_type, extra):
        uow.events.add(
            tenant_id=tenant.id, business_id=business.id,
            conversation_id=conversation.id, event_type=event_type,
            payload={"message_id": str(message.id), **extra},
            source="outbound_delivery_service",
            correlation_id=correlation_id or str(getattr(conversation, "correlation_id", None) or conversation.id),
        )


__all__ = ["OutboundDeliveryService"]
