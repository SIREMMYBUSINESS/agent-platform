"""Persist provider-free outreach plans as terminal dry-run deliveries."""
from alembic import op

revision = "0024_outreach_dry_run_status"
down_revision = "0023_campaign_step_delivery_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_outreach_messages_status", "outreach_messages", type_="check")
    op.create_check_constraint(
        "ck_outreach_messages_status", "outreach_messages",
        "status IN ('scheduled', 'sending', 'sent', 'delivered', 'failed', 'dry_run')",
    )


def downgrade() -> None:
    op.execute("UPDATE outreach_messages SET status = 'scheduled' WHERE status = 'dry_run'")
    op.drop_constraint("ck_outreach_messages_status", "outreach_messages", type_="check")
    op.create_check_constraint(
        "ck_outreach_messages_status", "outreach_messages",
        "status IN ('scheduled', 'sending', 'sent', 'delivered', 'failed')",
    )
