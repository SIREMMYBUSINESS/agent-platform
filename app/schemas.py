from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional
from uuid import UUID

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


class LeadImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    contact_id: str
    source_name: str = Field(default="manual", min_length=1, max_length=120)
    source_type: str = Field(default="manual", min_length=1, max_length=40)
    source_reference: str | None = Field(default=None, max_length=500)
    source_metadata: dict[str, Any] = Field(default_factory=dict)


class LeadScoresResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fit_score: int
    urgency_score: int
    intent_score: int
    priority_score: int


class LeadSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contact_id: str
    status: str
    created_at: datetime
    scores: LeadScoresResponse | None = None


class LeadImportResponse(LeadSummaryResponse):
    tenant_id: str
    business_id: str
    source_id: UUID


class LeadEnrichmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_info: dict[str, Any]
    public_profile_data: dict[str, Any]
    website_metadata: dict[str, Any]
    custom_business_context: dict[str, Any]
    lead_fit_signal: dict[str, Any]
    provider_name: str


class LeadTransitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_state: str | None = None
    to_state: str
    created_at: datetime


class LeadEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class LeadReadResponse(LeadImportResponse):
    enrichment: LeadEnrichmentResponse | None = None
    transitions: list[LeadTransitionResponse] = Field(default_factory=list)
    events: list[LeadEventResponse] = Field(default_factory=list)


class LeadListResponse(BaseModel):
    items: list[LeadSummaryResponse]
    offset: int
    limit: int


class CampaignMessageTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=10000)
    subject: str | None = Field(default=None, max_length=500)


class CampaignStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delay_seconds: int = Field(default=0, ge=0, le=31536000)
    template: CampaignMessageTemplate


class CampaignAudienceFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_priority_score: int = Field(default=65, ge=0, le=100)
    source_ids: list[UUID] | None = None


class CampaignCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    name: str = Field(min_length=1, max_length=160)
    channel: Literal["email", "sms", "whatsapp"]
    audience_filters: CampaignAudienceFilters = Field(default_factory=CampaignAudienceFilters)
    steps: list[CampaignStepInput] = Field(min_length=1, max_length=50)


class CampaignEnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    lead_ids: list[UUID] = Field(min_length=1, max_length=100)


class CampaignTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    enrollment_id: UUID | None = None


class CampaignStepResponse(BaseModel):
    id: UUID
    step_order: int
    delay_seconds: int
    template: dict[str, str]


class CampaignSequenceResponse(BaseModel):
    id: UUID
    name: str
    steps: list[CampaignStepResponse]


class CampaignEnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    status: str
    current_step_order: int
    next_step_at: datetime | None


class CampaignEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    enrollment_id: UUID | None = None
    payload: dict[str, Any]
    created_at: datetime


class CampaignStepRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    enrollment_id: UUID
    step_id: UUID
    step_order: int
    status: str
    rendered_content: dict[str, Any]
    skip_reason: str | None = None
    generated_at: datetime
    sent_at: datetime | None = None


class CampaignResponse(BaseModel):
    id: UUID
    tenant_id: str
    business_id: str
    name: str
    channel: str
    audience_filters: dict[str, Any]
    status: str
    sequences: list[CampaignSequenceResponse] = Field(default_factory=list)
    enrollments: list[CampaignEnrollmentResponse] = Field(default_factory=list)
    events: list[CampaignEventResponse] = Field(default_factory=list)
    step_runs: list[CampaignStepRunResponse] = Field(default_factory=list)


class CampaignEnrollResponse(BaseModel):
    campaign_id: UUID
    enrollments: list[CampaignEnrollmentResponse]


class CampaignTriggerResponse(BaseModel):
    campaign_id: UUID
    status: str
    processed: int
    sent: int
    skipped: int


class CampaignReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    enrollment_id: UUID
    text: str = Field(min_length=1, max_length=4000)
    external_message_id: str | None = Field(default=None, min_length=1, max_length=256)


class CampaignReplyResponse(BaseModel):
    id: UUID
    campaign_id: UUID
    enrollment_id: UUID
    classification: Literal["positive", "negative", "opt_out", "book_appointment", "callback", "unknown"]
    duplicate: bool = False


class CampaignAnalyticsResponse(BaseModel):
    campaign_id: UUID
    sent_count: int
    opened_count: int
    reply_count: int
    positive_reply_rate: float = Field(ge=0, le=1)
    opt_out_count: int
    booked_appointments: int
