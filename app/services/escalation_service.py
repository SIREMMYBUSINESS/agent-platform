"""Persist human handoff tickets and alert events."""

from __future__ import annotations

from typing import Any
from uuid import UUID
from datetime import UTC, datetime

from app.queue.contracts import QueueDomain

class EscalationService:
    def create_ticket(
        self,
        *,
        uow: Any,
        tenant_id: str,
        business_id: str,
        contact_id: str,
        conversation_id: UUID,
        reason: str,
        priority: str,
    ):
        ticket = uow.escalations.create(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
        )
        conversation = uow.conversations.get(
            tenant_id=tenant_id, conversation_id=conversation_id,
        )
        uow.events.add(
            tenant_id=tenant_id,
            business_id=business_id,
            conversation_id=conversation_id,
            event_type="human.escalation.alert",
            payload={"ticket_id": str(ticket.id), "priority": priority},
            source="escalation_service",
            correlation_id=(getattr(conversation, "correlation_id", None)
                            or str(conversation_id)),
        )
        correlation_id = (getattr(conversation, "correlation_id", None)
                          or str(conversation_id))
        uow.queue_tasks.enqueue(
            tenant_id=tenant_id, business_id=business_id,
            domain=QueueDomain.ESCALATION.value,
            task_name="escalation.notify_staff",
            idempotency_key=f"escalation:{ticket.id}:notify_staff",
            aggregate_type="escalation", aggregate_id=str(ticket.id),
            payload={"ticket_id": str(ticket.id)},
            correlation_id=correlation_id, traceparent=None,
            available_at=datetime.now(UTC), max_attempts=6,
        )
        return ticket


__all__ = ["EscalationService"]
