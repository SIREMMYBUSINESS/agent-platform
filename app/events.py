from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

@dataclass
class EventEnvelope:
    event_id: str
    tenant_id: str
    business_id: str
    conversation_id: str
    event_type: str
    payload: Dict[str, Any]
    source: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    correlation_id: Optional[str] = None

class InMemoryEventBus:
    def __init__(self):
        self.events: List[EventEnvelope] = []

    def publish(self, event: EventEnvelope) -> EventEnvelope:
        self.events.append(event)
        return event

    def list_events(self) -> List[EventEnvelope]:
        return self.events.copy()

    def get_by_conversation(self, conversation_id: str) -> List[EventEnvelope]:
        return [e for e in self.events if e.conversation_id == conversation_id]
