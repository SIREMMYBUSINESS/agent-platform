"""Add tenant-scoped deployment regions, residency, audit, and regional flags."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import json


revision = "0027_regional_residency"
down_revision = "0026_escalation_staff_assignment"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("tenants", sa.Column(
        "default_region_id", sa.String(64), nullable=False, server_default="us-east-1",
    ))
    op.add_column("tenants", sa.Column(
        "supported_region_ids", _json_type(), nullable=False, server_default=sa.text("'[]'"),
    ))
    op.add_column("businesses", sa.Column("region_id", sa.String(64), nullable=True))
    op.add_column("businesses", sa.Column("jurisdiction_code", sa.String(8), nullable=True))
    op.add_column("queue_task_records", sa.Column("region_id", sa.String(64), nullable=True))

    op.create_table(
        "tenant_region_configs",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("region_id", sa.String(64), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("data_residency_region", sa.String(64), nullable=False),
        sa.Column("retention_policy", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("allowed_storage_regions", _json_type(), nullable=False),
        sa.Column("provider_preferences", _json_type(), nullable=False),
        sa.Column("compliance_profile", _json_type(), nullable=False),
        sa.Column("allowed_channels", _json_type(), nullable=False),
        sa.Column("local_phone_route", sa.String(40), nullable=True),
        sa.Column("contact_preferences", _json_type(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approval_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.String(160), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "region_id", name="pk_tenant_region_configs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_region_configs_tenant"),
        sa.CheckConstraint("approval_status IN ('pending', 'approved', 'rejected')", name="ck_tenant_region_configs_approval"),
        sa.CheckConstraint("length(country_code) = 2", name="ck_tenant_region_configs_country"),
    )
    op.create_index("ix_tenant_region_configs_scope_active", "tenant_region_configs", ["tenant_id", "active"])
    op.create_table(
        "region_policy_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("region_id", sa.String(64), nullable=False),
        sa.Column("override_rules", _json_type(), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approved_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_region_policy_overrides"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_region_policy_overrides_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "region_id"], ["tenant_region_configs.tenant_id", "tenant_region_configs.region_id"], name="fk_region_policy_overrides_region"),
    )
    op.create_index("ix_region_policy_overrides_scope", "region_policy_overrides", ["tenant_id", "region_id", "active"])
    op.create_table(
        "region_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("region_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(160), nullable=True),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("details", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_region_audit_logs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_region_audit_logs_tenant"),
    )
    op.create_index("ix_region_audit_logs_scope_created", "region_audit_logs", ["tenant_id", "region_id", "created_at"])
    op.create_table(
        "region_feature_flags",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("region_id", sa.String(64), nullable=False),
        sa.Column("flag_key", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "region_id", "flag_key", name="pk_region_feature_flags"),
        sa.ForeignKeyConstraint(["tenant_id", "region_id"], ["tenant_region_configs.tenant_id", "tenant_region_configs.region_id"], name="fk_region_feature_flags_region"),
        sa.CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="ck_region_feature_flags_rollout"),
    )

    # Map legacy tenants to one colocated region before regional routing is enabled.
    mappings = [
        ("US", "us-east-1", "US", "America/New_York", "en", ["sms", "email", "whatsapp", "call", "chat"]),
        ("CA", "ca-central-1", "CA", "America/Toronto", "en-CA", ["sms", "email", "whatsapp", "call", "chat"]),
        ("EU", "eu-west-1", "DE", "Europe/Berlin", "de", ["sms", "email", "whatsapp", "call", "chat"]),
        ("AFRICA", "af-south-1", "ZA", "Africa/Johannesburg", "en-ZA", []),
    ]
    for legacy_group, region_id, country, timezone, language, channels in mappings:
        tenant_predicate = "UPPER(region) = '" + legacy_group + "'"
        op.execute(sa.text(
            f"UPDATE tenants SET default_region_id = '{region_id}', "
            f"supported_region_ids = '[\"{region_id}\"]' WHERE {tenant_predicate}"
        ))
        compliance = json.dumps({"region_group": legacy_group,
                                 "safe_mode": legacy_group == "AFRICA"})
        op.execute(sa.text(
            "INSERT INTO tenant_region_configs "
            "(tenant_id, region_id, country_code, timezone, language, data_residency_region, "
            "allowed_storage_regions, provider_preferences, compliance_profile, allowed_channels, "
            "contact_preferences, active, approval_status, approved_by, approved_at) "
            f"SELECT id, '{region_id}', '{country}', '{timezone}', '{language}', '{region_id}', "
            f"'[\"{region_id}\"]', '{{}}', '{compliance}', '{json.dumps(channels)}', "
            f"'{{}}', true, 'approved', 'migration:0027', CURRENT_TIMESTAMP FROM tenants WHERE {tenant_predicate}"
        ))

    eu_countries = "AT BE BG HR CY CZ DK EE FI FR DE GR HU IS IE IT LV LI LT LU MT NL NO PL PT RO SK SI ES SE CH".split()
    for code in eu_countries:
        op.execute(sa.text(
            "INSERT INTO country_policies (country_code, region_group, rules, active) "
            "SELECT '" + code + "', 'EU', rules, active FROM country_policies "
            "WHERE country_code = 'EU' AND NOT EXISTS "
            "(SELECT 1 FROM country_policies WHERE country_code = '" + code + "')"
        ))
    african_countries = "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG EH ZM ZW".split()
    safe_rules = '{"inbound":{},"outbound":{},"safe_mode":true}'.replace(":", r"\:")
    for code in african_countries:
        op.execute(sa.text(
            "INSERT INTO country_policies (country_code, region_group, rules, active) "
            "SELECT '" + code + "', 'AFRICA', '" + safe_rules + "'::jsonb, true "
            "WHERE NOT EXISTS (SELECT 1 FROM country_policies WHERE country_code = '" + code + "')"
        ))

    op.execute(sa.text(
        "UPDATE businesses SET region_id = (SELECT default_region_id FROM tenants "
        "WHERE tenants.id = businesses.tenant_id)"
    ))
    op.execute(sa.text(
        "UPDATE queue_task_records SET region_id = (SELECT default_region_id FROM tenants "
        "WHERE tenants.id = queue_task_records.tenant_id)"
    ))


def downgrade() -> None:
    op.drop_table("region_feature_flags")
    op.drop_index("ix_region_audit_logs_scope_created", table_name="region_audit_logs")
    op.drop_table("region_audit_logs")
    op.drop_index("ix_region_policy_overrides_scope", table_name="region_policy_overrides")
    op.drop_table("region_policy_overrides")
    op.drop_index("ix_tenant_region_configs_scope_active", table_name="tenant_region_configs")
    op.drop_table("tenant_region_configs")
    op.drop_column("queue_task_records", "region_id")
    op.drop_column("businesses", "region_id")
    op.drop_column("businesses", "jurisdiction_code")
    op.drop_column("tenants", "supported_region_ids")
    op.drop_column("tenants", "default_region_id")
