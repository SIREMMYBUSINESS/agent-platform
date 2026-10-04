"""Persist channel consent revocations and campaign reply classifications.

Revision ID: 0006_reply_optout_analytics
Revises: 0005_campaign_orchestration
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_reply_optout_analytics"
down_revision: Union[str, None] = "0005_campaign_orchestration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_channel_consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_contact_channel_consents"),
        sa.UniqueConstraint("tenant_id", "business_id", "contact_id", "channel", name="uq_contact_channel_consent_scope"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contact_channel_consents_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_contact_channel_consents_contact"),
        sa.CheckConstraint("channel IN ('email', 'sms', 'whatsapp')", name="ck_contact_channel_consents_channel"),
        sa.CheckConstraint("status IN ('granted', 'revoked')", name="ck_contact_channel_consents_status"),
    )
    op.create_index("ix_contact_channel_consents_lookup", "contact_channel_consents", ["tenant_id", "business_id", "contact_id", "channel"])

    op.create_table(
        "campaign_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("external_message_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_replies"),
        sa.UniqueConstraint("tenant_id", "business_id", "campaign_id", "external_message_id", name="uq_campaign_replies_external_message"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_campaign_replies_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_campaign_replies_campaign"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "enrollment_id"], ["campaign_enrollments.tenant_id", "campaign_enrollments.business_id", "campaign_enrollments.id"], name="fk_campaign_replies_enrollment"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "lead_id"], ["leads.tenant_id", "leads.business_id", "leads.id"], name="fk_campaign_replies_lead"),
        sa.CheckConstraint("classification IN ('positive', 'negative', 'opt_out', 'book_appointment', 'callback', 'unknown')", name="ck_campaign_replies_classification"),
    )
    op.create_index("ix_campaign_replies_tenant_campaign_created", "campaign_replies", ["tenant_id", "business_id", "campaign_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_campaign_replies_tenant_campaign_created", table_name="campaign_replies")
    op.drop_table("campaign_replies")
    op.drop_index("ix_contact_channel_consents_lookup", table_name="contact_channel_consents")
    op.drop_table("contact_channel_consents")
