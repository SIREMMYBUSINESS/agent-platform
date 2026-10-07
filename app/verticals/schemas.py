"""Strict tenant admin requests for vertical onboarding and assisted intake."""
from datetime import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntakeQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=2, max_length=48, pattern=r"^[a-z][a-z0-9_]*$")
    prompt: str = Field(min_length=1, max_length=500)
    answer_type: Literal["text", "date", "choice"] = "text"
    required: bool = True


class ServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=100)
    duration_minutes: int = Field(ge=10, le=240)


class BusinessHourInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time
    active: bool = True

    @model_validator(mode="after")
    def valid_times(self):
        if self.opens_at == self.closes_at:
            raise ValueError("business hours must have different open and close times")
        return self


class StaffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=160)
    email: str | None = Field(default=None, max_length=254)


class DentalOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vertical_id: Literal["healthcare_dental"] = "healthcare_dental"
    business_name: str = Field(min_length=2, max_length=160)
    region_id: str | None = Field(default=None, min_length=3, max_length=64)
    timezone: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default=None, max_length=16)
    intake_questions: list[IntakeQuestionInput] | None = Field(default=None, max_length=12)
    business_hours: list[BusinessHourInput] | None = Field(default=None, min_length=7, max_length=7)
    services: list[ServiceInput] | None = Field(default=None, min_length=1, max_length=30)
    message_templates: dict[str, str] = Field(default_factory=dict, max_length=16)
    escalation_rules: dict[str, list[str]] = Field(default_factory=dict, max_length=12)
    staff: list[StaffInput] = Field(min_length=1, max_length=50)
    provider_selection: dict[Literal["sms", "email", "calendar", "crm"], str] = Field(
        default_factory=lambda: {"sms": "twilio", "calendar": "google"}, max_length=4,
    )

    @field_validator("intake_questions")
    @classmethod
    def unique_question_keys(cls, value):
        if value is not None and len({item.key for item in value}) != len(value):
            raise ValueError("intake question keys must be unique")
        return value

    @field_validator("business_hours")
    @classmethod
    def unique_weekdays(cls, value):
        if value is not None and {item.weekday for item in value} != set(range(7)):
            raise ValueError("business hours must define each weekday exactly once")
        return value

    @field_validator("services")
    @classmethod
    def unique_service_names(cls, value):
        if value is not None and len({item.name.casefold() for item in value}) != len(value):
            raise ValueError("service names must be unique")
        return value


class VerticalIntakeStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_id: str = Field(min_length=1, max_length=160)
    channel: Literal["chat", "sms", "email", "whatsapp"]
    idempotency_key: str = Field(min_length=8, max_length=160)


class VerticalIntakeAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=160)


class VerticalIntakeResponse(BaseModel):
    session_id: UUID
    status: str
    question_key: str | None = None
    prompt: str | None = None
    appointment_id: UUID | None = None
    response: str | None = None
    escalation_id: UUID | None = None


class DentalOverdueOutreachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: UUID
    months_since_visit: int = Field(default=6, ge=1, le=60)
    limit: int = Field(default=500, ge=1, le=500)


class OnboardingResponse(BaseModel):
    tenant_id: str
    business_id: str
    vertical_id: str
    status: str
    region_id: str
    timezone: str


class HealthCheckResponse(BaseModel):
    tenant_id: str
    business_id: str
    vertical_id: str
    status: str
    ready: bool
    checks: dict[str, bool]
    provider_status: dict[str, str]
