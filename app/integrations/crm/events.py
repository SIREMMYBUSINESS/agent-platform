"""CRM event contracts used to request provider synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class CRMEventType(StrEnum):
    CONTACT_CREATED = "contact.created"
    CONTACT_UPDATED = "contact.updated"
    CONTACT_STATUS_UPDATED = "contact.status_updated"
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_CLOSED = "conversation.closed"
    FOLLOW_UP_SCHEDULED = "conversation.follow_up_scheduled"
    OUTREACH_ACTIVITY = "outreach.activity"
    CAMPAIGN_CREATED = "campaign.created"
    DELIVERY_RESULT = "campaign.delivery_result"
    ENGAGEMENT = "campaign.engagement"


@dataclass(frozen=True)
class CRMEvent:
    entity_type: str
    entity_id: str
    event_type: CRMEventType | str
    idempotency_key: str
    payload: dict[str, Any] | None = None


__all__ = ["CRMEvent", "CRMEventType"]
