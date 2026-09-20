from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, ConfigDict

class InboundRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    text: Optional[str] = None
    contact_name: Optional[str] = None
    consent_status: str = "granted"
    metadata: Dict[str, Any] = Field(default_factory=dict)

class InboundResponse(BaseModel):
    success: bool
    status: str
    workflow_state: str
    agent_response: Optional[str] = None
    reason: Optional[str] = None
