"""Add tenant and business scoped lead import, enrichment, scores, and audit.

Revision ID: 0004_lead_import
Revises: 0003_receptionist
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0004_lead_import"
down_revision: Union[str, None] = "0003_receptionist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def _json(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(), nullable=nullable, server_default=sa.text("'{}'::jsonb") if not nullable else None)


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "lead_sources",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), server_default="manual", nullable=False),
        sa.Column("external_reference", sa.String(), nullable=True),
        _json("source_metadata"),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_lead_sources"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_lead_sources_tenant_business_id"),
        sa.UniqueConstraint("tenant_id", "business_id", "name", name="uq_lead_sources_tenant_business_name"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_sources_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_lead_sources_tenant_business"),
    )
    op.create_index("ix_lead_sources_tenant_business", "lead_sources", ["tenant_id", "business_id"])

    op.create_table(
        "leads",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(), server_default="imported", nullable=False),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('imported', 'enriched', 'scored', 'qualified', 'contacted', 'responded', 'booked', 'converted', 'opted_out', 'closed')",
            name="ck_leads_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_leads"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_leads_tenant_business_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_leads_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_leads_tenant_business"),
        sa.ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_leads_tenant_contact_business"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "source_id"], ["lead_sources.tenant_id", "lead_sources.business_id", "lead_sources.id"], name="fk_leads_tenant_business_source"),
    )
    op.create_index("ix_leads_tenant_business_created", "leads", ["tenant_id", "business_id", "created_at"])
    op.create_index("ix_leads_tenant_business_status", "leads", ["tenant_id", "business_id", "status"])

    op.create_table(
        "lead_enrichments",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        _json("company_info"), _json("public_profile_data"), _json("website_metadata"),
        _json("custom_business_context"), _json("lead_fit_signal"),
        sa.Column("provider_name", sa.String(), server_default="manual", nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_lead_enrichments"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_enrichments_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_enrichments_tenant_business_lead"),
    )
    op.create_index("ix_lead_enrichments_tenant_lead_created", "lead_enrichments", ["tenant_id", "lead_id", "created_at"])

    op.create_table(
        "lead_scores",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fit_score", sa.Integer(), nullable=False),
        sa.Column("urgency_score", sa.Integer(), nullable=False),
        sa.Column("intent_score", sa.Integer(), nullable=False),
        sa.Column("priority_score", sa.Integer(), nullable=False),
        _json("rationale"),
        _created_at(),
        sa.CheckConstraint("fit_score BETWEEN 0 AND 100", name="ck_lead_scores_fit_range"),
        sa.CheckConstraint("urgency_score BETWEEN 0 AND 100", name="ck_lead_scores_urgency_range"),
        sa.CheckConstraint("intent_score BETWEEN 0 AND 100", name="ck_lead_scores_intent_range"),
        sa.CheckConstraint("priority_score BETWEEN 0 AND 100", name="ck_lead_scores_priority_range"),
        sa.PrimaryKeyConstraint("id", name="pk_lead_scores"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_scores_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_scores_tenant_business_lead"),
    )
    op.create_index("ix_lead_scores_tenant_lead_created", "lead_scores", ["tenant_id", "lead_id", "created_at"])

    op.create_table(
        "lead_state_transitions",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", sa.String(), nullable=True),
        sa.Column("to_state", sa.String(), nullable=False),
        _json("context"),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_lead_state_transitions"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_state_transitions_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_state_transitions_tenant_business_lead"),
    )
    op.create_index("ix_lead_state_transitions_tenant_lead_created", "lead_state_transitions", ["tenant_id", "lead_id", "created_at"])

    op.create_table(
        "lead_event_logs",
        _id(),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        _json("payload"),
        sa.Column("source", sa.String(), server_default="lead_import_service", nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_lead_event_logs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_lead_event_logs_tenant_id_tenants"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_lead_event_logs_tenant_business_lead"),
    )
    op.create_index("ix_lead_event_logs_tenant_lead_created", "lead_event_logs", ["tenant_id", "lead_id", "created_at"])


def downgrade() -> None:
    for table, index in (
        ("lead_event_logs", "ix_lead_event_logs_tenant_lead_created"),
        ("lead_state_transitions", "ix_lead_state_transitions_tenant_lead_created"),
        ("lead_scores", "ix_lead_scores_tenant_lead_created"),
        ("lead_enrichments", "ix_lead_enrichments_tenant_lead_created"),
        ("leads", "ix_leads_tenant_business_status"),
        ("leads", "ix_leads_tenant_business_created"),
        ("lead_sources", "ix_lead_sources_tenant_business"),
    ):
        op.drop_index(index, table_name=table)
    for table in ("lead_event_logs", "lead_state_transitions", "lead_scores", "lead_enrichments", "leads", "lead_sources"):
        op.drop_table(table)
