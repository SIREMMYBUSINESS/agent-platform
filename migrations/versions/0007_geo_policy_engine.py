"""Add durable consent, suppression, and data-driven geo policy tables.

Revision ID: 0007_geo_policy_engine
Revises: 0006_reply_optout_analytics
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_geo_policy_engine"
down_revision: Union[str, None] = "0006_reply_optout_analytics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rules(*, inbound: dict, outbound: dict, safe_mode: bool = False) -> dict:
    result = {"inbound": inbound, "outbound": outbound}
    if safe_mode:
        result["safe_mode"] = True
    return result


def _gate(required: bool = True) -> dict:
    return {"allowed": True, "consent_required": required}


def upgrade() -> None:
    op.add_column("businesses", sa.Column("country_code", sa.String(), nullable=True))

    op.create_table(
        "policy_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), server_default="enforce", nullable=False),
        sa.Column("allow_manual_override", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("custom_rules", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_policy_profiles"),
        sa.UniqueConstraint("tenant_id", "business_id", name="uq_policy_profiles_business"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_policy_profiles_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_policy_profiles_business"),
        sa.CheckConstraint("mode IN ('enforce', 'safe_mode')", name="ck_policy_profiles_mode"),
    )

    op.create_table(
        "country_policies",
        sa.Column("country_code", sa.String(), nullable=False),
        sa.Column("region_group", sa.String(), nullable=False),
        sa.Column("rules", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("country_code", name="pk_country_policies"),
        sa.CheckConstraint("region_group IN ('US', 'CA', 'EU', 'AFRICA', 'UNKNOWN')", name="ck_country_policies_region_group"),
    )

    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default="unknown", nullable=False),
        sa.Column("source", sa.String(), server_default="trusted_admin", nullable=False),
        sa.Column("source_reference", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_consent_records"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consent_records_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_consent_records_contact"),
        sa.CheckConstraint("channel IN ('email', 'sms', 'whatsapp', 'call', 'chat', 'voicemail', 'phone')", name="ck_consent_records_channel"),
        sa.CheckConstraint("status IN ('granted', 'revoked', 'expired', 'unknown')", name="ck_consent_records_status"),
    )
    op.create_index("ix_consent_records_current", "consent_records", ["tenant_id", "business_id", "contact_id", "channel", "created_at"])

    op.create_table(
        "suppression_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=True),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.String(), server_default="trusted_admin", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppression_rules"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_suppression_rules_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_suppression_rules_contact"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_suppression_rules_lead"),
        sa.CheckConstraint("(contact_id IS NOT NULL AND lead_id IS NULL) OR (contact_id IS NULL AND lead_id IS NOT NULL)", name="ck_suppression_rules_single_subject"),
        sa.CheckConstraint("channel IS NULL OR channel IN ('email', 'sms', 'whatsapp', 'call', 'chat', 'voicemail', 'phone')", name="ck_suppression_rules_channel"),
    )
    op.create_index("ix_suppression_rules_scope", "suppression_rules", ["tenant_id", "business_id", "active", "channel"])

    op.create_table(
        "policy_decision_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("consent_status", sa.String(), server_default="unknown", nullable=False),
        sa.Column("policy_source", sa.String(), nullable=False),
        sa.Column("overridden", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("override_actor", sa.String(), nullable=True),
        sa.Column("override_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_policy_decision_logs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_policy_decision_logs_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_policy_decision_logs_business"),
        sa.CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_policy_decision_logs_direction"),
    )
    op.create_index("ix_policy_decision_logs_scope_created", "policy_decision_logs", ["tenant_id", "business_id", "created_at"])

    profiles = {
        "US": _rules(
            inbound={"chat": _gate(False), "email": _gate(), "sms": _gate(), "call": _gate(), "phone": _gate(), "voicemail": _gate()},
            outbound={"email": _gate(), "sms": _gate(), "whatsapp": _gate(), "call": _gate()},
        ),
        "CA": _rules(
            inbound={"email": _gate(), "sms": _gate(), "chat": _gate(False), "call": _gate()},
            outbound={"email": _gate(), "sms": _gate(), "whatsapp": _gate()},
        ),
        "EU": _rules(
            inbound={"email": _gate(), "sms": _gate(), "chat": _gate(), "call": _gate()},
            outbound={"email": _gate(), "sms": _gate(), "whatsapp": _gate()},
        ),
        "AFRICA": _rules(inbound={}, outbound={}, safe_mode=True),
    }
    # JSONB dictionaries cannot be rendered by Alembic's offline literal
    # renderer. These fixed deployment defaults are emitted as explicit SQL.
    for code, rules in profiles.items():
        encoded_rules = json.dumps(rules, separators=(",", ":"))
        # ``sa.text`` treats JSON's ``:true`` and ``:false`` tokens as bind
        # parameters; escape colons so online execution and offline SQL both
        # preserve the literal JSON document.
        encoded_rules = encoded_rules.replace(":", r"\:")
        op.execute(sa.text(
            "INSERT INTO country_policies (country_code, region_group, rules) "
            f"VALUES ('{code}', '{code}', '{encoded_rules}'::jsonb)"
        ))


def downgrade() -> None:
    op.drop_index("ix_policy_decision_logs_scope_created", table_name="policy_decision_logs")
    op.drop_table("policy_decision_logs")
    op.drop_index("ix_suppression_rules_scope", table_name="suppression_rules")
    op.drop_table("suppression_rules")
    op.drop_index("ix_consent_records_current", table_name="consent_records")
    op.drop_table("consent_records")
    op.drop_table("country_policies")
    op.drop_table("policy_profiles")
    op.drop_column("businesses", "country_code")
