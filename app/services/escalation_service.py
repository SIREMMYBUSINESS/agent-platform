"""Persist human handoff tickets and alert events."""

from __future__ import annotations

from typing import Any
from uuid import UUID

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
        uow.events.add(
            tenant_id=tenant_id,
            business_id=business_id,
            conversation_id=conversation_id,
            event_type="human.escalation.alert",
            payload={"ticket_id": str(ticket.id), "priority": priority},
            source="escalation_service",
            correlation_id=str(conversation_id),
        )
        return ticket


__all__ = ["EscalationService"]
