"""Persist the provider message reference used for verified delivery receipts."""

from alembic import op
import sqlalchemy as sa


revision = "0013_provider_delivery_receipts"
down_revision = "0012_crm_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("outreach_messages", sa.Column("provider_message_id", sa.String(), nullable=True))
    op.add_column("outreach_messages", sa.Column("provider_status", sa.String(), nullable=True))
    op.add_column("outreach_messages", sa.Column("provider_error_code", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_outreach_messages_provider_id", "outreach_messages",
        ["tenant_id", "business_id", "provider_message_id"],
    )


def downgrade():
    op.drop_constraint("uq_outreach_messages_provider_id", "outreach_messages", type_="unique")
    op.drop_column("outreach_messages", "provider_error_code")
    op.drop_column("outreach_messages", "provider_status")
    op.drop_column("outreach_messages", "provider_message_id")
