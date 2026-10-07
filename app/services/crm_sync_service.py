"""CRM activity outbox enqueueing; provider execution stays in workers."""
from datetime import UTC, datetime

from app.queue.contracts import QueueDomain


class CRMSyncService:
    def enqueue_appointment_activity(self, uow, *, appointment, activity_type: str) -> None:
        if activity_type not in {"inbound_appointment", "outreach_appointment", "appointment_completed"}:
            raise ValueError("unsupported appointment CRM activity")
        correlation_id = appointment.correlation_id or str(appointment.id)
        uow.queue_tasks.enqueue(
            tenant_id=appointment.tenant_id, business_id=appointment.business_id,
            domain=QueueDomain.ANALYTICS.value, task_name="crm.sync_activity",
            idempotency_key=f"appointment:{appointment.id}:crm:{activity_type}",
            aggregate_type="appointment", aggregate_id=str(appointment.id),
            payload={
                "appointment_id": str(appointment.id),
                "conversation_id": str(appointment.conversation_id),
                "activity_type": activity_type,
            },
            correlation_id=correlation_id, traceparent=None,
            available_at=datetime.now(UTC), max_attempts=6,
        )


__all__ = ["CRMSyncService"]
