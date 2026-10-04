"""Add tenant and business scoped campaign orchestration records.

Revision ID: 0005_campaign_orchestration
Revises: 0004_lead_import
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0005_campaign_orchestration"
down_revision: Union[str, None] = "0004_lead_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def _json(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"))


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "campaigns",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        _json("audience_filters"),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft', 'active', 'paused', 'completed', 'canceled')", name="ck_campaigns_status"),
        sa.CheckConstraint("channel IN ('email', 'sms', 'whatsapp')", name="ck_campaigns_channel"),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaigns_tenant_business_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaigns_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_campaigns_tenant_business"),
    )
    op.create_index("ix_campaigns_tenant_business_status", "campaigns", ["tenant_id", "business_id", "status"])

    op.create_table(
        "campaign_sequences",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), server_default="default", nullable=False),
        sa.Column("sequence_order", sa.Integer(), server_default="0", nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_sequences"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaign_sequences_tenant_business_id"),
        sa.UniqueConstraint("tenant_id", "business_id", "campaign_id", "sequence_order", name="uq_campaign_sequences_order"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_sequences_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_sequences_tenant_campaign"),
    )
    op.create_index("ix_campaign_sequences_tenant_campaign_order", "campaign_sequences", ["tenant_id", "campaign_id", "sequence_order"])

    op.create_table(
        "campaign_steps",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("sequence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("delay_seconds", sa.Integer(), server_default="0", nullable=False),
        _json("template_data"),
        _created(),
        sa.CheckConstraint("step_order >= 0", name="ck_campaign_steps_order_nonnegative"),
        sa.CheckConstraint("delay_seconds >= 0", name="ck_campaign_steps_delay_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_steps"),
        sa.UniqueConstraint("tenant_id", "business_id", "sequence_id", "step_order", name="uq_campaign_steps_order"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaign_steps_tenant_business_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_steps_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "sequence_id"], ["campaign_sequences.tenant_id", "campaign_sequences.business_id", "campaign_sequences.id"], name="fk_campaign_steps_tenant_sequence"),
    )
    op.create_index("ix_campaign_steps_tenant_sequence_order", "campaign_steps", ["tenant_id", "sequence_id", "step_order"])

    op.create_table(
        "campaign_enrollments",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(), server_default="active", nullable=False),
        sa.Column("current_step_order", sa.Integer(), server_default="-1", nullable=False),
        sa.Column("next_step_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('active', 'completed', 'skipped', 'canceled')", name="ck_campaign_enrollments_status"),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_enrollments"),
        sa.UniqueConstraint("tenant_id", "business_id", "campaign_id", "lead_id", name="uq_campaign_enrollments_lead"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_campaign_enrollments_tenant_business_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_enrollments_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_enrollments_tenant_campaign"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_campaign_enrollments_tenant_lead"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "sequence_id"], ["campaign_sequences.tenant_id", "campaign_sequences.business_id", "campaign_sequences.id"], name="fk_campaign_enrollments_tenant_sequence"),
    )
    op.create_index("ix_campaign_enrollments_tenant_campaign_status_due", "campaign_enrollments", ["tenant_id", "campaign_id", "status", "next_step_at"])

    op.create_table(
        "campaign_step_runs",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        _json("rendered_content"),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('sent', 'skipped')", name="ck_campaign_step_runs_status"),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_step_runs"),
        sa.UniqueConstraint("tenant_id", "business_id", "enrollment_id", "step_id", name="uq_campaign_step_runs_enrollment_step"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_step_runs_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "enrollment_id"], ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"], name="fk_campaign_step_runs_tenant_enrollment"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "step_id"], ["campaign_steps.tenant_id", "campaign_steps.business_id", "campaign_steps.id"], name="fk_campaign_step_runs_tenant_step"),
    )
    op.create_index("ix_campaign_step_runs_tenant_enrollment", "campaign_step_runs", ["tenant_id", "enrollment_id"])

    op.create_table(
        "campaign_event_logs",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        _json("payload"),
        _created(),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_event_logs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_event_logs_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_event_logs_tenant_campaign"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "enrollment_id"], ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"], name="fk_campaign_event_logs_tenant_enrollment"),
    )
    op.create_index("ix_campaign_event_logs_tenant_campaign_created", "campaign_event_logs", ["tenant_id", "campaign_id", "created_at"])


def downgrade() -> None:
    for table, index in (
        ("campaign_event_logs", "ix_campaign_event_logs_tenant_campaign_created"),
        ("campaign_step_runs", "ix_campaign_step_runs_tenant_enrollment"),
        ("campaign_enrollments", "ix_campaign_enrollments_tenant_campaign_status_due"),
        ("campaign_steps", "ix_campaign_steps_tenant_sequence_order"),
        ("campaign_sequences", "ix_campaign_sequences_tenant_campaign_order"),
        ("campaigns", "ix_campaigns_tenant_business_status"),
    ):
        op.drop_index(index, table_name=table)
    for table in ("campaign_event_logs", "campaign_step_runs", "campaign_enrollments", "campaign_steps", "campaign_sequences", "campaigns"):
        op.drop_table(table)
