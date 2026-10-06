"""Requeue work from tenant-scoped durable events with replay idempotency."""

from uuid import UUID
import hashlib

from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueDomain, QueueTaskSubmission, QueueTaskView
from app.queue.service import QueueTaskService


class EventReplayService:
    def __init__(self, *, uow_factory, queue_tasks: QueueTaskService) -> None:
        self._uow_factory = uow_factory
        self._queue_tasks = queue_tasks

    def replay(self, principal, *, business_id: str, event_id: UUID,
               domain: QueueDomain, task_name: str, replay_key: str) -> QueueTaskView:
        """Queue a handler-specific replay using only the persisted event reference."""
        with self._uow_factory() as uow:
            event = uow.events.get_by_id(
                tenant_id=principal.tenant_id, business_id=business_id, event_id=event_id,
            )
            if event is None:
                raise LookupError("event is not available in trusted scope")
            conversation_id = str(event.conversation_id)
            event_type = event.event_type
        if not isinstance(replay_key, str) or not replay_key.strip():
            raise ValueError("replay_key must be nonblank")
        stable_replay_key = hashlib.sha256(replay_key.encode("utf-8")).hexdigest()
        return self._queue_tasks.enqueue(principal, QueueTaskSubmission(
            domain=domain, task_name=task_name, business_id=business_id,
            idempotency_key=f"event-replay:{event_id}:{stable_replay_key}",
            aggregate_type="event", aggregate_id=str(event_id),
            payload={"source_event_id": str(event_id), "source_event_type": event_type,
                     "conversation_id": conversation_id},
            correlation_id=f"replay:{event_id}",
        ))


__all__ = ["EventReplayService"]
