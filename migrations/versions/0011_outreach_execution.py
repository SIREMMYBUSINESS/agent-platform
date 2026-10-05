"""Persist tenant-scoped idempotent outbound message attempts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0011_outreach_execution"
down_revision = "0010_receptionist_workflows"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_status", "campaigns",
        "status IN ('draft', 'active', 'paused', 'completed', 'canceled', 'archived')",
    )
    op.create_table(
        "outreach_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="scheduled"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_outreach_messages"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_outreach_messages_idempotency"),
        sa.UniqueConstraint("tenant_id", "business_id", "campaign_id", "contact_id", name="uq_outreach_messages_campaign_contact"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_outreach_messages_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "campaign_id"], ["campaigns.tenant_id", "campaigns.business_id", "campaigns.id"], name="fk_outreach_messages_campaign"),
        sa.ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"], ["contacts.tenant_id", "contacts.id", "contacts.business_id"], name="fk_outreach_messages_contact"),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"], ["conversations.tenant_id", "conversations.id", "conversations.business_id"], name="fk_outreach_messages_conversation"),
        sa.CheckConstraint("status IN ('scheduled', 'sending', 'sent', 'delivered', 'failed')", name="ck_outreach_messages_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_outreach_messages_attempts"),
    )
    op.create_index("ix_outreach_messages_tenant_due", "outreach_messages", ["tenant_id", "status", "scheduled_at"])
    op.create_index("ix_outreach_messages_campaign_due", "outreach_messages", ["tenant_id", "business_id", "campaign_id", "status", "scheduled_at"])


def downgrade():
    op.drop_index("ix_outreach_messages_campaign_due", table_name="outreach_messages")
    op.drop_index("ix_outreach_messages_tenant_due", table_name="outreach_messages")
    op.drop_table("outreach_messages")
    op.execute("UPDATE campaigns SET status = 'canceled' WHERE status = 'archived'")
    op.drop_constraint("ck_campaigns_status", "campaigns", type_="check")
    op.create_check_constraint(
        "ck_campaigns_status", "campaigns",
        "status IN ('draft', 'active', 'paused', 'completed', 'canceled')",
    )
