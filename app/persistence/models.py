from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Column,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models import Channel, LeadLifecycle, WorkflowState


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_storage_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_api_keys_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_api_keys_tenant_id"),
        UniqueConstraint("key_hash", name="uq_api_keys_hash"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    tenant_id: str
    key_hash: str = Field(repr=False)
    revoked: bool = False
    issued_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class ApiKeyGrant(SQLModel, table=True):
    __tablename__ = "api_key_grants"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_api_key_grants_tenant"),
        ForeignKeyConstraint(["tenant_id", "principal_id"], ["api_keys.tenant_id", "api_keys.id"], name="fk_api_key_grants_key"),
    )

    principal_id: str = Field(primary_key=True)
    tenant_id: str = Field(primary_key=True)
    roles: list[str] = Field(default_factory=list, sa_column=Column(json_storage_type(), nullable=False))
    enabled: bool = True
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    __table_args__ = (PrimaryKeyConstraint("id", name="pk_tenants"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    region: str = "US"
    default_timezone: str = "UTC"
    default_region_id: str = "us-east-1"
    supported_region_ids: list[str] = Field(default_factory=list, sa_column=Column(json_storage_type(), nullable=False, default=list))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class TenantRegionConfig(SQLModel, table=True):
    """Reviewed tenant-scoped deployment, residency, and localization settings."""
    __tablename__ = "tenant_region_configs"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "region_id", name="pk_tenant_region_configs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_region_configs_tenant"),
        CheckConstraint("approval_status IN ('pending', 'approved', 'rejected')",
                        name="ck_tenant_region_configs_approval"),
        CheckConstraint("length(country_code) = 2", name="ck_tenant_region_configs_country"),
        Index("ix_tenant_region_configs_scope_active", "tenant_id", "active"),
    )
    tenant_id: str
    region_id: str = Field(max_length=64)
    country_code: str = Field(max_length=2)
    timezone: str = Field(max_length=80)
    language: str = Field(max_length=16)
    data_residency_region: str = Field(max_length=64)
    retention_policy: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    allowed_storage_regions: list[str] = Field(default_factory=list, sa_column=Column(json_storage_type(), nullable=False, default=list))
    provider_preferences: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    compliance_profile: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    allowed_channels: list[str] = Field(default_factory=list, sa_column=Column(json_storage_type(), nullable=False, default=list))
    local_phone_route: str | None = Field(default=None, max_length=40, repr=False)
    contact_preferences: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    active: bool = True
    approval_status: str = "pending"
    approved_by: str | None = None
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class RegionPolicyOverride(SQLModel, table=True):
    """Approved region-scoped policy additions. Rules may only add restrictions."""
    __tablename__ = "region_policy_overrides"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_region_policy_overrides"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_region_policy_overrides_tenant"),
        ForeignKeyConstraint(["tenant_id", "region_id"],
                             ["tenant_region_configs.tenant_id", "tenant_region_configs.region_id"],
                             name="fk_region_policy_overrides_region"),
        Index("ix_region_policy_overrides_scope", "tenant_id", "region_id", "active"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    region_id: str
    override_rules: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    reason: str = Field(max_length=1000)
    active: bool = True
    approved_by: str
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class RegionAuditLog(SQLModel, table=True):
    """Auditable residency and region configuration decisions."""
    __tablename__ = "region_audit_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_region_audit_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_region_audit_logs_tenant"),
        Index("ix_region_audit_logs_scope_created", "tenant_id", "region_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    region_id: str
    action: str = Field(max_length=80)
    decision: str = Field(max_length=16)
    resource_type: str = Field(max_length=40)
    resource_id: str | None = Field(default=None, max_length=160)
    actor_id: str = Field(max_length=160)
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class RegionFeatureFlag(SQLModel, table=True):
    """Feature rollout state scoped to a single tenant and deployment region."""
    __tablename__ = "region_feature_flags"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "region_id", "flag_key", name="pk_region_feature_flags"),
        ForeignKeyConstraint(["tenant_id", "region_id"],
                             ["tenant_region_configs.tenant_id", "tenant_region_configs.region_id"],
                             name="fk_region_feature_flags_region"),
        CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100",
                        name="ck_region_feature_flags_rollout"),
    )
    tenant_id: str
    region_id: str
    flag_key: str = Field(max_length=100)
    enabled: bool = False
    rollout_percentage: int = 0
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class Business(SQLModel, table=True):
    __tablename__ = "businesses"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_businesses"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_businesses_tenant_id_tenants"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_businesses_tenant_id"),
        Index("ix_businesses_tenant_id", "tenant_id", "id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    tenant_id: str
    name: str
    vertical: str
    region: str = "US"
    country_code: Optional[str] = None
    region_id: str | None = None
    jurisdiction_code: str | None = Field(default=None, max_length=8)
    timezone: str = "UTC"
    default_language: str = "en"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class BusinessVerticalProfile(SQLModel, table=True):
    """Tenant-owned vertical configuration used by guided workflows."""
    __tablename__ = "business_vertical_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "business_id", name="pk_business_vertical_profiles"),
        ForeignKeyConstraint(["tenant_id", "business_id"],
                             ["businesses.tenant_id", "businesses.id"],
                             name="fk_business_vertical_profiles_business"),
        CheckConstraint("status IN ('draft', 'ready', 'live')",
                        name="ck_business_vertical_profiles_status"),
    )
    tenant_id: str
    business_id: str
    vertical_id: str = Field(max_length=64)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    status: str = "draft"
    created_by: str = Field(max_length=160)
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class VerticalIntakeSession(SQLModel, table=True):
    """Durable question-by-question intake tied to one conversation."""
    __tablename__ = "vertical_intake_sessions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_vertical_intake_sessions"),
        UniqueConstraint("tenant_id", "business_id", "idempotency_key",
                         name="uq_vertical_intake_session_idempotency"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"],
                             name="fk_vertical_intake_sessions_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"],
                             ["businesses.tenant_id", "businesses.id"],
                             name="fk_vertical_intake_sessions_business"),
        ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"],
                             ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
                             name="fk_vertical_intake_sessions_contact"),
        ForeignKeyConstraint(["tenant_id", "conversation_id"],
                             ["conversations.tenant_id", "conversations.id"],
                             name="fk_vertical_intake_sessions_conversation"),
        Index("ix_vertical_intake_sessions_scope_status",
              "tenant_id", "business_id", "status"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    conversation_id: UUID
    idempotency_key: str = Field(max_length=160)
    question_index: int = 0
    answers: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    answer_idempotency: dict[str, str] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    status: str = "collecting"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class Contact(SQLModel, table=True):
    __tablename__ = "contacts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_contacts"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contacts_tenant_id_tenants"),
        UniqueConstraint("tenant_id", "id", name="uq_contacts_tenant_id"),
        UniqueConstraint(
            "tenant_id", "id", "business_id", name="uq_contacts_tenant_id_business_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_contacts_tenant_business",
        ),
        Index("ix_contacts_tenant_business", "tenant_id", "business_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    tenant_id: str
    business_id: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    consent_status: str = "unknown"
    timezone: Optional[str] = None
    preferred_channel: Optional[str] = None
    language: Optional[str] = None
    last_visit_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class ContactChannelConsent(SQLModel, table=True):
    """Explicit channel-specific consent state, including durable revocation."""
    __tablename__ = "contact_channel_consents"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_contact_channel_consents"),
        UniqueConstraint("tenant_id", "business_id", "contact_id", "channel", name="uq_contact_channel_consent_scope"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contact_channel_consents_tenant"),
        ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_contact_channel_consents_contact"),
        CheckConstraint("channel IN ('email', 'sms', 'whatsapp')", name="ck_contact_channel_consents_channel"),
        CheckConstraint("status IN ('granted', 'revoked')", name="ck_contact_channel_consents_status"),
        Index("ix_contact_channel_consents_lookup", "tenant_id", "business_id", "contact_id", "channel"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    status: str = "granted"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class ConsentRecord(SQLModel, table=True):
    """Append-only channel consent lifecycle record from a trusted source."""
    __tablename__ = "consent_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_consent_records"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consent_records_tenant"),
        ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_consent_records_contact"),
        CheckConstraint("channel IN ('email', 'sms', 'whatsapp', 'call', 'chat', 'voicemail', 'phone')", name="ck_consent_records_channel"),
        CheckConstraint("status IN ('granted', 'revoked', 'expired', 'unknown')", name="ck_consent_records_status"),
        Index("ix_consent_records_current", "tenant_id", "business_id", "contact_id", "channel", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    status: str = "unknown"
    source: str = "trusted_admin"
    source_reference: Optional[str] = None
    expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class PolicyProfile(SQLModel, table=True):
    """Tenant/business policy controls; custom rules may only tighten defaults."""
    __tablename__ = "policy_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_policy_profiles"),
        UniqueConstraint("tenant_id", "business_id", name="uq_policy_profiles_business"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_policy_profiles_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_policy_profiles_business"),
        CheckConstraint("mode IN ('enforce', 'safe_mode')", name="ck_policy_profiles_mode"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    mode: str = "enforce"
    allow_manual_override: bool = False
    custom_rules: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CountryPolicy(SQLModel, table=True):
    """Data-driven jurisdiction rules, provisioned by deployment/admin path."""
    __tablename__ = "country_policies"
    __table_args__ = (
        PrimaryKeyConstraint("country_code", name="pk_country_policies"),
        CheckConstraint("region_group IN ('US', 'CA', 'EU', 'AFRICA', 'UNKNOWN')", name="ck_country_policies_region_group"),
    )
    country_code: str = Field(primary_key=True)
    region_group: str
    rules: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    active: bool = True
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class SuppressionRule(SQLModel, table=True):
    """Tenant-scoped suppression for one provisioned lead or contact."""
    __tablename__ = "suppression_rules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_suppression_rules"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_suppression_rules_tenant"),
        ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_suppression_rules_contact"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_suppression_rules_lead"),
        CheckConstraint("(contact_id IS NOT NULL AND lead_id IS NULL) OR (contact_id IS NULL AND lead_id IS NOT NULL)", name="ck_suppression_rules_single_subject"),
        CheckConstraint("channel IS NULL OR channel IN ('email', 'sms', 'whatsapp', 'call', 'chat', 'voicemail', 'phone')", name="ck_suppression_rules_channel"),
        Index("ix_suppression_rules_scope", "tenant_id", "business_id", "active", "channel"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str | None = None
    lead_id: UUID | None = None
    channel: str | None = None
    reason_code: str
    active: bool = True
    created_by: str = "trusted_admin"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class PolicyDecisionLog(SQLModel, table=True):
    """Audit record for each allow/block decision and any explicit override."""
    __tablename__ = "policy_decision_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_policy_decision_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_policy_decision_logs_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_policy_decision_logs_business"),
        CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_policy_decision_logs_direction"),
        Index("ix_policy_decision_logs_scope_created", "tenant_id", "business_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    direction: str
    channel: str
    country_code: str
    resource_type: str
    resource_id: str
    decision: str
    reason_code: str
    consent_status: str = "unknown"
    policy_source: str
    overridden: bool = False
    override_actor: str | None = None
    override_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_conversations"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_conversations_tenant_id_tenants"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id"),
        UniqueConstraint(
            "tenant_id", "id", "business_id", name="uq_conversations_tenant_id_business_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_conversations_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_conversations_tenant_contact_business",
        ),
        Index("ix_conversations_tenant_business", "tenant_id", "business_id"),
        Index("ix_conversations_tenant_contact", "tenant_id", "contact_id"),
        Index("ix_conversations_correlation_id", "correlation_id"),
        CheckConstraint("status IN ('open', 'pending', 'closed')", name="ck_conversations_status"),
        CheckConstraint("version >= 0", name="ck_conversations_version"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    workflow_state: str = WorkflowState.NEW.value
    status: str = Field(default="open", sa_column=Column(String(), nullable=False, server_default="open"))
    version: int = Field(default=0, sa_column=Column(Integer(), nullable=False, server_default="0"))
    correlation_id: Optional[str] = None
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class ConversationAssignment(SQLModel, table=True):
    __tablename__ = "conversation_assignments"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_conversation_assignments_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_conversation_assignments_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_conversation_assignments_staff",
        ),
    )

    conversation_id: UUID = Field(primary_key=True)
    tenant_id: str
    business_id: str
    staff_id: UUID | None = None
    assigned_by: str
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_messages"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_messages_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_messages_tenant_conversation",
        ),
        Index("ix_messages_tenant_conversation_created", "tenant_id", "conversation_id", "created_at"),
        CheckConstraint("delivery_status IN ('not_applicable', 'pending', 'sent', 'failed', 'blocked', 'dry_run')", name="ck_messages_delivery_status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    conversation_id: UUID
    sender: str
    channel: str
    content: str
    subject: str | None = None
    direction: str
    delivery_status: str = "not_applicable"
    provider_message_id: Optional[str] = None
    delivery_error_code: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class WorkflowStateRecord(SQLModel, table=True):
    __tablename__ = "workflow_state_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_state_records"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_workflow_state_records_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_workflow_state_records_tenant_conversation",
        ),
        Index(
            "ix_workflow_state_records_tenant_conversation_created",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    conversation_id: UUID
    state: str
    context: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(json_storage_type(), nullable=False, default=dict),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class WorkflowExecution(SQLModel, table=True):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_executions"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_workflow_executions_idempotency"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_workflow_executions_tenant"),
        ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_workflow_executions_conversation"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_workflow_executions_status"),
        CheckConstraint("attempts > 0", name="ck_workflow_executions_attempts"),
        Index("ix_workflow_executions_tenant_conversation_created", "tenant_id", "conversation_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    conversation_id: UUID
    workflow_name: str
    idempotency_key: str
    input_hash: str
    status: str = "running"
    attempts: int = 1
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    error_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class FollowUpTask(SQLModel, table=True):
    __tablename__ = "follow_up_tasks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_follow_up_tasks"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_follow_up_tasks_idempotency"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_follow_up_tasks_tenant"),
        ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_follow_up_tasks_conversation"),
        CheckConstraint("reason IN ('callback_requested', 'missed_response', 'nurture')", name="ck_follow_up_tasks_reason"),
        CheckConstraint("status IN ('scheduled', 'cancelled', 'completed')", name="ck_follow_up_tasks_status"),
        Index("ix_follow_up_tasks_tenant_business_due", "tenant_id", "business_id", "scheduled_at", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    conversation_id: UUID
    idempotency_key: str
    scheduled_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    reason: str
    workflow_state: str
    status: str = "scheduled"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class EventLog(SQLModel, table=True):
    __tablename__ = "event_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_event_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_event_logs_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_event_logs_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_event_logs_tenant_conversation_business",
        ),
        Index("ix_event_logs_tenant_conversation_created", "tenant_id", "conversation_id", "created_at"),
        Index("ix_event_logs_tenant_event_type_created", "tenant_id", "event_type", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    conversation_id: UUID
    event_type: str
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(json_storage_type(), nullable=False, default=dict),
    )
    source: str
    correlation_id: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Appointment(SQLModel, table=True):
    __tablename__ = "appointments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_appointments"),
        CheckConstraint("ends_at > starts_at", name="ck_appointments_end_after_start"),
        CheckConstraint("source IN ('inbound', 'outreach')", name="ck_appointments_source"),
        UniqueConstraint("tenant_id", "slot_id", name="uq_appointments_tenant_slot"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_appointments_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_appointments_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_appointments_tenant_contact_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_appointments_tenant_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_appointments_tenant_business_staff",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "service_id"],
            ["services.tenant_id", "services.business_id", "services.id"],
            name="fk_appointments_tenant_business_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "campaign_id"],
            ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"],
            name="fk_appointments_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "campaign_enrollment_id"],
            ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"],
            name="fk_appointments_campaign_enrollment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "slot_id"],
            ["appointment_slots.tenant_id", "appointment_slots.business_id", "appointment_slots.id"],
            name="fk_appointments_tenant_business_slot",
        ),
        Index("ix_appointments_tenant_business_start", "tenant_id", "business_id", "starts_at"),
        Index("ix_appointments_tenant_contact_status", "tenant_id", "contact_id", "status"),
        Index("uq_appointments_contact_action_token_hash", "contact_action_token_hash", unique=True),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    conversation_id: UUID | None = None
    staff_id: UUID | None = None
    service_id: UUID | None = None
    slot_id: UUID | None = None
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    status: str = "requested"
    source: str = "inbound"
    campaign_id: UUID | None = None
    campaign_enrollment_id: UUID | None = None
    proposal_slot_ids: list[str] = Field(
        default_factory=list, sa_column=Column(json_storage_type(), nullable=False, default=list)
    )
    contact_action_token_hash: str | None = None
    calendar_event_id: Optional[str] = None
    crm_record_id: Optional[str] = None
    correlation_id: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Staff(SQLModel, table=True):
    __tablename__ = "staff"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_staff"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_staff_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_staff_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_staff_tenant_business",
        ),
        Index("ix_staff_tenant_business_active", "tenant_id", "business_id", "active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    display_name: str
    email: Optional[str] = None
    active: bool = True
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Service(SQLModel, table=True):
    __tablename__ = "services"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_services"),
        CheckConstraint("duration_minutes > 0", name="ck_services_duration_positive"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_services_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_services_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_services_tenant_business",
        ),
        Index("ix_services_tenant_business_active", "tenant_id", "business_id", "active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    name: str
    duration_minutes: int
    active: bool = True
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AppointmentSlot(SQLModel, table=True):
    __tablename__ = "appointment_slots"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_appointment_slots"),
        CheckConstraint("ends_at > starts_at", name="ck_appointment_slots_end_after_start"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_slots_tenant_business_id"),
        UniqueConstraint("tenant_id", "staff_id", "starts_at", name="uq_slots_tenant_staff_start"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_appointment_slots_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_appointment_slots_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_appointment_slots_tenant_business_staff",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "service_id"],
            ["services.tenant_id", "services.business_id", "services.id"],
            name="fk_appointment_slots_tenant_business_service",
        ),
        Index(
            "ix_appointment_slots_tenant_business_status_start",
            "tenant_id", "business_id", "status", "starts_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    staff_id: UUID
    service_id: UUID
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    status: str = "available"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class EscalationTicket(SQLModel, table=True):
    __tablename__ = "escalation_tickets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_escalation_tickets"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_escalation_tickets_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_escalation_tickets_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_escalation_tickets_tenant_contact_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_escalation_tickets_tenant_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "assigned_staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_escalation_tickets_assigned_staff",
        ),
        Index("ix_escalation_tickets_tenant_business_status", "tenant_id", "business_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    conversation_id: UUID
    reason: str
    priority: str = "urgent"
    status: str = "open"
    assigned_staff_id: UUID | None = None
    resolved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    resolution: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class RoutingRule(SQLModel, table=True):
    __tablename__ = "routing_rules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_routing_rules"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_routing_rules_weekday_range"),
        CheckConstraint("closes_at != opens_at", name="ck_routing_rules_hours_not_equal"),
        UniqueConstraint("tenant_id", "business_id", "weekday", name="uq_routing_rules_tenant_business_day"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_routing_rules_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_routing_rules_tenant_business",
        ),
        Index("ix_routing_rules_tenant_business_active", "tenant_id", "business_id", "active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    weekday: int
    opens_at: time
    closes_at: time
    after_hours_action: str = "callback"
    active: bool = True
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class LeadSource(SQLModel, table=True):
    __tablename__ = "lead_sources"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_lead_sources"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_lead_sources_tenant_business_id"),
        UniqueConstraint("tenant_id", "business_id", "name", name="uq_lead_sources_tenant_business_name"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_sources_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_lead_sources_tenant_business"),
        Index("ix_lead_sources_tenant_business", "tenant_id", "business_id"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    name: str
    source_type: str = "manual"
    external_reference: Optional[str] = None
    source_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class Lead(SQLModel, table=True):
    __tablename__ = "leads"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_leads"),
        CheckConstraint(
            "status IN ('imported', 'enriched', 'scored', 'qualified', 'contacted', 'responded', 'booked', 'converted', 'opted_out', 'closed')",
            name="ck_leads_status",
        ),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_leads_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_leads_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_leads_tenant_business"),
        ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_leads_tenant_contact_business"),
        ForeignKeyConstraint(["tenant_id", "business_id", "source_id"], ["lead_sources.tenant_id", "lead_sources.business_id", "lead_sources.id"], name="fk_leads_tenant_business_source"),
        Index("ix_leads_tenant_business_created", "tenant_id", "business_id", "created_at"),
        Index("ix_leads_tenant_business_status", "tenant_id", "business_id", "status"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    source_id: UUID
    status: str = LeadLifecycle.IMPORTED.value
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class LeadEnrichment(SQLModel, table=True):
    __tablename__ = "lead_enrichments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_lead_enrichments"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_enrichments_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_enrichments_tenant_business_lead"),
        Index("ix_lead_enrichments_tenant_lead_created", "tenant_id", "lead_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    lead_id: UUID
    company_info: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    public_profile_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    website_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    custom_business_context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    lead_fit_signal: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    provider_name: str = "manual"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class LeadScore(SQLModel, table=True):
    __tablename__ = "lead_scores"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_lead_scores"),
        CheckConstraint("fit_score BETWEEN 0 AND 100", name="ck_lead_scores_fit_range"),
        CheckConstraint("urgency_score BETWEEN 0 AND 100", name="ck_lead_scores_urgency_range"),
        CheckConstraint("intent_score BETWEEN 0 AND 100", name="ck_lead_scores_intent_range"),
        CheckConstraint("priority_score BETWEEN 0 AND 100", name="ck_lead_scores_priority_range"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_scores_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_scores_tenant_business_lead"),
        Index("ix_lead_scores_tenant_lead_created", "tenant_id", "lead_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    lead_id: UUID
    fit_score: int
    urgency_score: int
    intent_score: int
    priority_score: int
    rationale: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class LeadStateTransition(SQLModel, table=True):
    __tablename__ = "lead_state_transitions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_lead_state_transitions"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_state_transitions_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_state_transitions_tenant_business_lead"),
        Index("ix_lead_state_transitions_tenant_lead_created", "tenant_id", "lead_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    lead_id: UUID
    from_state: Optional[str] = None
    to_state: str
    context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class LeadEventLog(SQLModel, table=True):
    __tablename__ = "lead_event_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_lead_event_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_event_logs_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_event_logs_tenant_business_lead"),
        Index("ix_lead_event_logs_tenant_lead_created", "tenant_id", "lead_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    lead_id: UUID
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    source: str = "lead_import_service"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class Campaign(SQLModel, table=True):
    __tablename__ = "campaigns"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaigns"),
        CheckConstraint("status IN ('draft', 'active', 'paused', 'completed', 'canceled', 'archived')", name="ck_campaigns_status"),
        CheckConstraint("channel IN ('email', 'sms', 'whatsapp')", name="ck_campaigns_channel"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaigns_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaigns_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_campaigns_tenant_business"),
        Index("ix_campaigns_tenant_business_status", "tenant_id", "business_id", "status"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    name: str
    channel: str
    audience_filters: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    status: str = "draft"
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CampaignSequence(SQLModel, table=True):
    __tablename__ = "campaign_sequences"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaign_sequences"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaign_sequences_tenant_business_id"),
        UniqueConstraint("tenant_id", "business_id", "campaign_id", "sequence_order", name="uq_campaign_sequences_order"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_sequences_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_sequences_tenant_campaign"),
        Index("ix_campaign_sequences_tenant_campaign_order", "tenant_id", "campaign_id", "sequence_order"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    campaign_id: UUID
    name: str = "default"
    sequence_order: int = 0
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CampaignStep(SQLModel, table=True):
    __tablename__ = "campaign_steps"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaign_steps"),
        CheckConstraint("step_order >= 0", name="ck_campaign_steps_order_nonnegative"),
        CheckConstraint("delay_seconds >= 0", name="ck_campaign_steps_delay_nonnegative"),
        UniqueConstraint("tenant_id", "business_id", "sequence_id", "step_order", name="uq_campaign_steps_order"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaign_steps_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_steps_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "sequence_id"], ["campaign_sequences.tenant_id", "campaign_sequences.business_id", "campaign_sequences.id"], name="fk_campaign_steps_tenant_sequence"),
        Index("ix_campaign_steps_tenant_sequence_order", "tenant_id", "sequence_id", "step_order"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    sequence_id: UUID
    step_order: int
    delay_seconds: int = 0
    template_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CampaignEnrollment(SQLModel, table=True):
    __tablename__ = "campaign_enrollments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaign_enrollments"),
        CheckConstraint("status IN ('active', 'completed', 'skipped', 'canceled')", name="ck_campaign_enrollments_status"),
        UniqueConstraint("tenant_id", "business_id", "campaign_id", "lead_id", name="uq_campaign_enrollments_lead"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaign_enrollments_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_enrollments_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_enrollments_tenant_campaign"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_campaign_enrollments_tenant_lead"),
        ForeignKeyConstraint(["tenant_id", "business_id", "sequence_id"], ["campaign_sequences.tenant_id", "campaign_sequences.business_id", "campaign_sequences.id"], name="fk_campaign_enrollments_tenant_sequence"),
        Index("ix_campaign_enrollments_tenant_campaign_status_due", "tenant_id", "campaign_id", "status", "next_step_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    campaign_id: UUID
    sequence_id: UUID
    lead_id: UUID
    experiment_group: str = "all"
    status: str = "active"
    current_step_order: int = -1
    next_step_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    enrolled_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CampaignStepRun(SQLModel, table=True):
    __tablename__ = "campaign_step_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaign_step_runs"),
        CheckConstraint("status IN ('queued', 'sent', 'dry_run', 'blocked', 'failed', 'skipped')", name="ck_campaign_step_runs_status"),
        UniqueConstraint("tenant_id", "id", name="uq_campaign_step_runs_tenant_id"),
        UniqueConstraint("tenant_id", "business_id", "enrollment_id", "step_id", name="uq_campaign_step_runs_enrollment_step"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_step_runs_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "enrollment_id"], ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"], name="fk_campaign_step_runs_tenant_enrollment"),
        ForeignKeyConstraint(["tenant_id", "business_id", "step_id"], ["campaign_steps.tenant_id", "campaign_steps.business_id", "campaign_steps.id"], name="fk_campaign_step_runs_tenant_step"),
        Index("ix_campaign_step_runs_tenant_enrollment", "tenant_id", "enrollment_id"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    enrollment_id: UUID
    step_id: UUID
    step_order: int
    status: str
    message_id: UUID | None = None
    rendered_content: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    skip_reason: Optional[str] = None
    generated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class CampaignEventLog(SQLModel, table=True):
    __tablename__ = "campaign_event_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaign_event_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_event_logs_tenant_id_tenants"),
        ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_event_logs_tenant_campaign"),
        ForeignKeyConstraint(["tenant_id", "business_id", "enrollment_id"], ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"], name="fk_campaign_event_logs_tenant_enrollment"),
        Index("ix_campaign_event_logs_tenant_campaign_created", "tenant_id", "campaign_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    campaign_id: UUID
    enrollment_id: UUID | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class OutreachMessage(SQLModel, table=True):
    """An idempotent, auditable outbound delivery to a provisioned contact."""
    __tablename__ = "outreach_messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_outreach_messages"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outreach_messages_idempotency"),
        UniqueConstraint("tenant_id", "business_id", "campaign_id", "contact_id", name="uq_outreach_messages_campaign_contact"),
        UniqueConstraint("tenant_id", "business_id", "provider_message_id", name="uq_outreach_messages_provider_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_outreach_messages_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_outreach_messages_campaign"),
        ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_outreach_messages_contact"),
        ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"], ["conversations.tenant_id", "conversations.id", "conversations.business_id"], name="fk_outreach_messages_conversation"),
        CheckConstraint("status IN ('scheduled', 'sending', 'sent', 'delivered', 'failed', 'dry_run')", name="ck_outreach_messages_status"),
        CheckConstraint("attempts >= 0", name="ck_outreach_messages_attempts"),
        Index("ix_outreach_messages_tenant_due", "tenant_id", "status", "scheduled_at"),
        Index("ix_outreach_messages_campaign_due", "tenant_id", "business_id", "campaign_id", "status", "scheduled_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    campaign_id: UUID
    contact_id: str
    conversation_id: UUID | None = None
    channel: str
    idempotency_key: str
    provider_message_id: str | None = None
    provider_status: str | None = None
    provider_error_code: str | None = None
    content: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    content_hash: str
    status: str = "scheduled"
    attempts: int = 0
    scheduled_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    delivered_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    failed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CRMSyncRecord(SQLModel, table=True):
    """Durable, tenant-scoped CRM sync idempotency and retry state."""
    __tablename__ = "crm_sync_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_crm_sync_records"),
        UniqueConstraint("tenant_id", "provider_key", "idempotency_key", name="uq_crm_sync_idempotency"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_crm_sync_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_crm_sync_business"),
        CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_crm_sync_direction"),
        CheckConstraint("status IN ('pending', 'synced', 'failed', 'conflict')", name="ck_crm_sync_status"),
        CheckConstraint("attempts >= 0", name="ck_crm_sync_attempts"),
        Index("ix_crm_sync_entity", "tenant_id", "provider_key", "entity_type", "entity_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    provider_key: str
    direction: str
    entity_type: str
    entity_id: str
    event_type: str
    idempotency_key: str
    status: str = "pending"
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    external_id: str | None = None
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class CampaignReply(SQLModel, table=True):
    """Reply classification and processing metadata; raw reply text is not retained."""
    __tablename__ = "campaign_replies"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_campaign_replies"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_replies_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_replies_campaign"),
        ForeignKeyConstraint(["tenant_id", "business_id", "enrollment_id"], ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"], name="fk_campaign_replies_enrollment"),
        ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_campaign_replies_lead"),
        UniqueConstraint("tenant_id", "business_id", "campaign_id", "external_message_id", name="uq_campaign_replies_external_message"),
        CheckConstraint("classification IN ('positive', 'negative', 'opt_out', 'book_appointment', 'callback', 'unknown')", name="ck_campaign_replies_classification"),
        Index("ix_campaign_replies_tenant_campaign_created", "tenant_id", "business_id", "campaign_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    campaign_id: UUID
    enrollment_id: UUID
    lead_id: UUID
    channel: str
    classification: str
    external_message_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class QueueTaskRecord(SQLModel, table=True):
    """Durable outbox, retry ledger, and dead-letter record for queued work."""
    __tablename__ = "queue_task_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_queue_task_records"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_queue_tasks_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_queue_tasks_business",
        ),
        UniqueConstraint("tenant_id", "business_id", "idempotency_key", name="uq_queue_tasks_idempotency"),
        CheckConstraint(
            "domain IN ('inbound', 'outreach', 'scheduling', 'escalation', 'analytics')",
            name="ck_queue_tasks_domain",
        ),
        CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'retrying', 'succeeded', 'dead_letter')",
            name="ck_queue_tasks_status",
        ),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="ck_queue_tasks_attempts"),
        Index("ix_queue_tasks_due", "status", "available_at", "domain"),
        Index("ix_queue_tasks_scope", "tenant_id", "business_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    region_id: str | None = None
    domain: str
    task_name: str
    idempotency_key: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False))
    correlation_id: str
    traceparent: str | None = None
    status: str = Field(default="pending", max_length=24)
    attempts: int = 0
    max_attempts: int = 5
    available_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    dispatched_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    redriven_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    error_code: str | None = Field(default=None, max_length=80)
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(json_storage_type()))


class ProviderWebhookEvent(SQLModel, table=True):
    """Idempotent, tenant-scoped record of a verified provider callback."""
    __tablename__ = "provider_webhook_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_provider_webhook_events"),
        UniqueConstraint("tenant_id", "business_id", "provider", "event_key",
                         name="uq_provider_webhook_event_key"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_provider_webhooks_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"],
                             ["businesses.tenant_id", "businesses.id"],
                             name="fk_provider_webhooks_business"),
        CheckConstraint("status IN ('received', 'processed', 'ignored', 'failed')",
                        name="ck_provider_webhook_event_status"),
        Index("ix_provider_webhooks_scope_created", "tenant_id", "business_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    provider: str
    event_key: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    status: str = "received"
    result: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))
    processed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class ProviderInteractionLog(SQLModel, table=True):
    """Secret and PII free provider attempt audit record."""
    __tablename__ = "provider_interaction_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_provider_interaction_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_provider_interactions_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_provider_interactions_business"),
        Index("ix_provider_interactions_scope_created", "tenant_id", "business_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    provider: str
    operation: str
    idempotency_key: str
    outcome: str
    error_code: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class FeedbackEvent(SQLModel, table=True):
    """PII-minimized, idempotent outcome linked to the originating decision."""
    __tablename__ = "feedback_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "event_key", name="uq_feedback_events_event_key"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_feedback_events_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_feedback_events_business"),
        Index("ix_feedback_events_scope_time", "tenant_id", "business_id", "occurred_at"),
        Index("ix_feedback_events_scope_type", "tenant_id", "business_id", "event_type", "occurred_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    event_key: str = Field(max_length=200)
    correlation_id: str = Field(max_length=100)
    lead_id: UUID | None = None
    contact_id: str | None = None
    campaign_id: UUID | None = None
    event_type: str = Field(max_length=64)
    decision: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    outcome: str = Field(max_length=64)
    event_metadata: dict[str, Any] = Field(default_factory=dict, sa_column=Column("metadata", json_storage_type(), nullable=False, default=dict))
    occurred_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class OptimizationMetric(SQLModel, table=True):
    __tablename__ = "optimization_metrics"
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "metric_key", "period_start", name="uq_optimization_metrics_period"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_metrics_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_metrics_business"),
        Index("ix_optimization_metrics_scope_type", "tenant_id", "business_id", "metric_type", "period_start"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    metric_key: str = Field(max_length=200)
    metric_type: str = Field(max_length=64)
    segment: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    sample_size: int = 0
    metric_values: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    period_start: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    period_end: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class OptimizationRecommendation(SQLModel, table=True):
    __tablename__ = "optimization_recommendations"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_optimization_recommendation_confidence"),
        CheckConstraint("sample_size >= 0", name="ck_optimization_recommendation_sample_size"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected', 'applied', 'rolled_back')", name="ck_optimization_recommendation_status"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_recommendations_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_recommendations_business"),
        Index("ix_optimization_recommendations_scope_status", "tenant_id", "business_id", "status", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    recommendation_type: str = Field(max_length=64)
    status: str = Field(default="pending", max_length=24)
    change: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    confidence: float
    expected_impact: float = 0.0
    sample_size: int = 0
    rationale: str = Field(max_length=2000)
    reviewed_by: str | None = None
    reviewed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class FeatureFlag(SQLModel, table=True):
    __tablename__ = "optimization_feature_flags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "flag_key", name="uq_optimization_feature_flag_scope"),
        CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="ck_optimization_feature_flag_rollout"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_feature_flags_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_feature_flags_business"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    flag_key: str = Field(max_length=100)
    enabled: bool = False
    rollout_percentage: int = 0
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    updated_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


class OptimizationAuditLog(SQLModel, table=True):
    __tablename__ = "optimization_audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_optimization_audit_tenant"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_optimization_audit_business"),
        Index("ix_optimization_audit_scope_created", "tenant_id", "business_id", "created_at"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    recommendation_id: UUID | None = None
    action: str = Field(max_length=64)
    actor_id: str = Field(max_length=160)
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(json_storage_type(), nullable=False, default=dict))
    created_at: datetime = Field(default_factory=utc_now, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()))


__all__ = [
    "Appointment",
    "AppointmentSlot",
    "Business",
    "BusinessVerticalProfile",
    "Campaign",
    "CampaignEnrollment",
    "CampaignEventLog",
    "OutreachMessage",
    "CRMSyncRecord",
    "CampaignReply",
    "QueueTaskRecord",
    "ProviderWebhookEvent",
    "ProviderInteractionLog",
    "CampaignSequence",
    "CampaignStep",
    "CampaignStepRun",
    "Channel",
    "Contact",
    "ContactChannelConsent",
    "ConsentRecord",
    "CountryPolicy",
    "Conversation",
    "EventLog",
    "FeedbackEvent",
    "FeatureFlag",
    "EscalationTicket",
    "Lead",
    "LeadEnrichment",
    "LeadEventLog",
    "LeadLifecycle",
    "LeadScore",
    "LeadSource",
    "LeadStateTransition",
    "Message",
    "PolicyDecisionLog",
    "PolicyProfile",
    "RegionAuditLog",
    "RegionFeatureFlag",
    "RegionPolicyOverride",
    "TenantRegionConfig",
    "OptimizationAuditLog",
    "OptimizationMetric",
    "OptimizationRecommendation",
    "RoutingRule",
    "Service",
    "Staff",
    "SuppressionRule",
    "Tenant",
    "WorkflowState",
    "WorkflowStateRecord",
    "VerticalIntakeSession",
]
