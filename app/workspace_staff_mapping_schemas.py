"""Transport schemas for workspace-to-staff identity management."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkspaceStaffMappingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_user_id: UUID
    staff_id: UUID


class WorkspaceStaffMappingResponse(BaseModel):
    id: UUID
    tenant_id: str
    business_id: str
    workspace_user_id: UUID
    staff_id: UUID
    created_at: datetime


class WorkspaceStaffMappingListResponse(BaseModel):
    items: list[WorkspaceStaffMappingResponse]
