"""Validated receptionist workflow request and response schemas."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=200)
    staff_id: UUID | None = None
    escalation_reason: str | None = Field(default=None, min_length=1, max_length=240)
    priority: Literal["normal", "high", "urgent"] = "normal"
    scheduled_at: datetime | None = None
    follow_up_reason: Literal["callback_requested", "missed_response", "nurture"] | None = None


class ConversationWorkflowResponse(BaseModel):
    workflow_name: str
    idempotency_key: str
    status: Literal["succeeded"]
    attempts: int
    result: dict[str, Any]
    replayed: bool
