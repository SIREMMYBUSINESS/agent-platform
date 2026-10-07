"""Validated HTTP contracts for optimization review and feedback capture."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OptimizationMetricView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    metric_key: str
    metric_type: str
    segment: dict
    sample_size: int
    metric_values: dict
    period_start: datetime
    period_end: datetime
    created_at: datetime


class OptimizationRecommendationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    recommendation_type: str
    status: str
    change: dict
    confidence: float
    expected_impact: float
    sample_size: int
    rationale: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime


class OptimizationAuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    recommendation_id: UUID | None
    action: str
    actor_id: str
    details: dict
    created_at: datetime


class OptimizationMetricsResponse(BaseModel):
    items: list[OptimizationMetricView]


class OptimizationRecommendationsResponse(BaseModel):
    items: list[OptimizationRecommendationView]


class OptimizationAuditResponse(BaseModel):
    items: list[OptimizationAuditView]


class OptimizationRejectRequest(BaseModel):
    rationale: str = Field(min_length=1, max_length=1000)


class OptimizationFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_key: str = Field(min_length=1, max_length=200)
    correlation_id: str = Field(min_length=1, max_length=100)
    event_type: Literal[
        "appointment_no_show", "contact_resolved_without_escalation",
        "reply_received", "opted_out", "contact_escalated", "manual_feedback",
        "action_taken",
    ]
    outcome: str = Field(min_length=1, max_length=64)
    lead_id: UUID | None = None
    campaign_id: UUID | None = None
    appointment_id: UUID | None = None
    variant: str | None = Field(default=None, max_length=100)
    action: Literal[
        "send_sms", "send_email", "send_whatsapp", "propose_appointment",
        "send_reminder", "escalate_to_human", "nurture_with_follow_up", "close_lead",
    ] | None = None
    from_channel: Literal["sms", "email", "whatsapp", "voice", "chat", "unknown"] | None = None
    sentiment: Literal["positive", "negative", "neutral", "unknown"] | None = None
    sentiment_score: float | None = Field(default=None, ge=-1, le=1)
    response_time_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    resolution_time_seconds: int | None = Field(default=None, ge=0, le=31_536_000)
    customer_satisfaction: int | None = Field(default=None, ge=1, le=5)
    reason_code: str | None = Field(default=None, max_length=120)
    state: Literal[
        "new_lead", "lead_contacted_no_reply", "lead_replied_positive",
        "lead_replied_negative", "appointment_proposed", "appointment_confirmed",
        "appointment_no_show", "opted_out",
    ] | None = None
    test_group: Literal["test_group_a", "test_group_b", "all"] = "all"

    @model_validator(mode="after")
    def require_appointment_for_no_show(self):
        if self.event_type == "appointment_no_show" and self.appointment_id is None:
            raise ValueError("appointment_id is required for appointment_no_show feedback")
        if self.appointment_id is not None and self.event_type != "appointment_no_show":
            raise ValueError("appointment_id is only accepted for appointment_no_show feedback")
        if self.event_type == "action_taken" and (self.action is None or self.state is None):
            raise ValueError("action and state are required for action_taken feedback")
        return self


class OptimizationFeedbackResponse(BaseModel):
    id: UUID
    event_type: str
    outcome: str
    duplicate: bool = False


class NextBestActionView(BaseModel):
    action: str
    predicted_conversion_rate: float = Field(ge=0, le=1)
    sample_size: int = Field(ge=0)


class NextBestActionsResponse(BaseModel):
    lead_id: UUID
    state: str
    actions: list[NextBestActionView]
