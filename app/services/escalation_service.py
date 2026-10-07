"""Persist human handoff tickets and alert events."""

from __future__ import annotations

from typing import Any
from uuid import UUID
from datetime import UTC, datetime
import hashlib

from app.queue.contracts import QueueDomain
from app.optimization.feedback import FeedbackEventCapture

class EscalationService:
    def __init__(self) -> None:
        self._feedback = FeedbackEventCapture()

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
        staff_repository = getattr(uow, "staff", None)
        staff = (staff_repository.list_active(
            tenant_id=tenant_id, business_id=business_id,
        ) if staff_repository is not None else [])
        assigned_staff = self._select_staff(
            uow, tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, staff=staff,
        )
        ticket = uow.escalations.create(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
            assigned_staff_id=assigned_staff.id if assigned_staff else None,
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
        self._feedback.capture(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=f"escalation-created:{ticket.id}", correlation_id=correlation_id,
            event_type="contact_escalated", outcome="escalated",
            contact_id=contact_id,
            decision={"ticket_id": str(ticket.id), "priority": priority, "reason_code": reason[:120],
                      "staff_id": str(getattr(ticket, "assigned_staff_id", "") or "")},
            event_metadata={"staff_id": str(getattr(ticket, "assigned_staff_id", "") or "")},
        )
        self._feedback.capture(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=f"action-taken:escalation:{ticket.id}",
            correlation_id=correlation_id, event_type="action_taken",
            outcome="escalated", contact_id=contact_id,
            decision={"action": "escalate_to_human", "state": "new_lead"},
        )
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

    @staticmethod
    def _select_staff(uow, *, tenant_id: str, business_id: str,
                      contact_id: str, staff: list):
        if not staff:
            return None
        optimization = getattr(uow, "optimization", None)
        history = (optimization.feedback(
            tenant_id=tenant_id, business_id=business_id,
            event_type="contact_escalated", limit=50000,
        ) if optimization is not None else [])
        results: dict[str, list[tuple[float, float]]] = {}
        for event in history:
            staff_id = str(event.event_metadata.get("staff_id", ""))
            if not staff_id or event.outcome != "resolved":
                continue
            duration = max(0, float(event.event_metadata.get("resolution_time_seconds", 0)))
            satisfaction = float(event.event_metadata.get("customer_satisfaction", 3))
            results.setdefault(staff_id, []).append((satisfaction, duration))
        staff_by_id = {str(row.id): row for row in staff}
        eligible = []
        for staff_id, outcomes in results.items():
            if staff_id not in staff_by_id or len(outcomes) < 3:
                continue
            satisfaction = sum(item[0] for item in outcomes) / len(outcomes)
            duration = sum(item[1] for item in outcomes) / len(outcomes)
            eligible.append((satisfaction, -duration, staff_by_id[staff_id]))
        if eligible:
            return max(eligible, key=lambda item: (item[0], item[1], str(item[2].id)))[2]
        ordered = sorted(staff, key=lambda row: str(row.id))
        bucket = int.from_bytes(hashlib.sha256(contact_id.encode()).digest()[:4], "big")
        return ordered[bucket % len(ordered)]


__all__ = ["EscalationService"]
