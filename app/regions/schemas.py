"""Validated API requests for tenant region provisioning and policy review."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Channel = Literal["sms", "email", "whatsapp", "call", "chat", "voicemail"]


class RegionEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9-]+$")
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    timezone: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default=None, max_length=16)
    allowed_channels: list[Channel] = Field(default_factory=list, max_length=6)
    allowed_storage_regions: list[str] = Field(default_factory=list, max_length=10)
    retention_policy: dict[str, int] = Field(default_factory=dict, max_length=16)
    provider_preferences: dict[str, str] = Field(default_factory=dict)
    contact_preferences: dict[str, str] = Field(default_factory=dict)
    local_phone_route: str | None = Field(default=None, max_length=40)
    make_default: bool = False


class RegionPolicyOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=8, max_length=1000)
    blocked_channels: dict[Literal["inbound", "outbound"], list[Channel]] = Field(
        default_factory=dict,
    )


class RegionFlagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flag_key: str = Field(min_length=3, max_length=100, pattern=r"^[a-z][a-z0-9_.-]+$")
    enabled: bool
    rollout_percentage: int = Field(ge=0, le=100)
    config: dict = Field(default_factory=dict)


class RegionFeatureFlagView(BaseModel):
    tenant_id: str
    region_id: str
    flag_key: str
    enabled: bool
    rollout_percentage: int
    config: dict


class RegionInfoView(BaseModel):
    region_id: str
    region_group: str
    country_code: str
    timezone: str
    language: str
    data_residency_region: str
    retention_policy: dict[str, int]
    configured: bool
    active: bool
    approval_status: str


class RegionListResponse(BaseModel):
    items: list[RegionInfoView]


class TenantRegionConfigView(BaseModel):
    tenant_id: str
    region_id: str
    country_code: str
    timezone: str
    language: str
    data_residency_region: str
    allowed_storage_regions: list[str]
    provider_preferences: dict[str, str]
    compliance_profile: dict
    allowed_channels: list[str]
    local_phone_route_configured: bool
    contact_preferences: dict[str, str]
    active: bool
    approval_status: str
    approved_by: str | None
    approved_at: object | None


class TenantRegionsResponse(BaseModel):
    tenant_id: str
    default_region_id: str
    supported_region_ids: list[str]
    regions: list[TenantRegionConfigView]


class RegionPolicyResponse(BaseModel):
    region_id: str
    country_code: str
    region_group: str
    country_policy_active: bool
    country_policy: dict | None
    safe_mode: bool
    overrides: list[dict]


class RegionPolicyOverrideView(BaseModel):
    id: object
    region_id: str
    override_rules: dict
    approved_by: str
    created_at: object
