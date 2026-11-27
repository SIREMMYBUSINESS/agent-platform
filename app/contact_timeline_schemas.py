"""Read-only contact conversation and timeline response contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.conversation_schemas import ConversationReadResponse


class ContactConversationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConversationReadResponse]
    conversations_included: int
    conversation_limit: int
    conversations_truncated: bool


class ContactTimelineItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["message", "event"]
    conversation_id: UUID
    created_at: datetime
    channel: str | None = None
    direction: str | None = None
    sender: str | None = None
    subject: str | None = None
    content: str | None = None
    delivery_status: str | None = None
    event_type: str | None = None
    payload: dict | None = None
    source: str | None = None


class ContactTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ContactTimelineItemResponse]
    limit: int
    older_cursor: str | None
    newer_cursor: str | None
    conversations_included: int
    conversation_limit: int
    conversations_truncated: bool
