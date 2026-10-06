"""Validated internal task messages; payloads carry identifiers, not authority."""

from datetime import UTC, datetime
from enum import StrEnum
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QueueDomain(StrEnum):
    INBOUND = "inbound"
    OUTREACH = "outreach"
    SCHEDULING = "scheduling"
    ESCALATION = "escalation"
    ANALYTICS = "analytics"


class TaskStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"


class QueueTaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: QueueDomain
    task_name: str = Field(min_length=3, max_length=120, pattern=r"^[a-z][a-z0-9_.-]+$")
    business_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=200)
    aggregate_type: str = Field(min_length=1, max_length=60)
    aggregate_id: str = Field(min_length=1, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(min_length=1, max_length=100)
    available_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    max_attempts: int = Field(default=5, ge=1, le=20)

    @field_validator("available_at")
    @classmethod
    def require_aware_schedule(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("available_at must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def require_bounded_json_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("payload must contain JSON values") from None
        if len(encoded.encode("utf-8")) > 65_536:
            raise ValueError("payload exceeds the 64 KiB task limit")
        return json.loads(encoded)


class QueueTaskView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: str
    business_id: str
    domain: QueueDomain
    task_name: str
    idempotency_key: str
    status: TaskStatus
    attempts: int
    max_attempts: int
    available_at: datetime
    correlation_id: str
    error_code: str | None = None
