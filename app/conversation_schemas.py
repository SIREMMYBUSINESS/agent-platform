"""Explicit conversation management API contracts; tenant identity is not input."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models import Channel, ConversationStatus


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_id: str = Field(min_length=1)
    channel: Channel


class ConversationStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ConversationStatus
    expected_version: int = Field(ge=0, strict=True)


class ConversationAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staff_id: UUID | None
    expected_version: int = Field(ge=0, strict=True)


class ConversationReadResponse(BaseModel):
    id: UUID
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    status: ConversationStatus
    version: int
    workflow_state: str
    assigned_staff_id: UUID | None
    started_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationReadResponse]
    offset: int
    limit: int
