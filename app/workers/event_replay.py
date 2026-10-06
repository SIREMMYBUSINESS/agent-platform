"""Explicit subscribers for replaying persisted application events."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from app.queue.runtime import QueueTaskEnvelope


@dataclass(frozen=True)
class PersistedEvent:
    id: UUID
    tenant_id: str
    business_id: str
    conversation_id: UUID
    event_type: str
    payload: dict[str, Any]
    source: str
    correlation_id: str | None
    created_at: datetime


EventSubscriber = Callable[[PersistedEvent, QueueTaskEnvelope], dict[str, Any] | None]


class EventSubscriberRegistry:
    def __init__(self) -> None:
        self._subscribers: dict[str, EventSubscriber] = {}

    def register(self, event_type: str, subscriber: EventSubscriber) -> None:
        if not event_type.strip() or event_type in self._subscribers:
            raise ValueError("event type must be unique and nonblank")
        self._subscribers[event_type] = subscriber

    def get(self, event_type: str) -> EventSubscriber:
        try:
            return self._subscribers[event_type]
        except KeyError:
            raise LookupError("event replay subscriber is not configured") from None


subscribers = EventSubscriberRegistry()


def register_event_subscriber(event_type: str, subscriber: EventSubscriber) -> None:
    subscribers.register(event_type, subscriber)


__all__ = ["EventSubscriberRegistry", "PersistedEvent", "register_event_subscriber", "subscribers"]
