"""Link campaign step executions to durable outbound delivery."""
from alembic import op
import sqlalchemy as sa

revision = "0023_campaign_step_delivery_outbox"
down_revision = "0022_contact_appointment_action_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_campaign_step_runs_status", "campaign_step_runs", type_="check")
    op.create_check_constraint(
        "ck_campaign_step_runs_status", "campaign_step_runs",
        "status IN ('queued', 'sent', 'dry_run', 'blocked', 'failed', 'skipped')",
    )
    op.create_unique_constraint(
        "uq_campaign_step_runs_tenant_id", "campaign_step_runs", ["tenant_id", "id"]
    )
    op.add_column("campaign_step_runs", sa.Column("message_id", sa.Uuid(), nullable=True))
    op.add_column("messages", sa.Column("subject", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "subject")
    op.drop_column("campaign_step_runs", "message_id")
    op.drop_constraint("uq_campaign_step_runs_tenant_id", "campaign_step_runs", type_="unique")
    op.drop_constraint("ck_campaign_step_runs_status", "campaign_step_runs", type_="check")
    op.create_check_constraint(
        "ck_campaign_step_runs_status", "campaign_step_runs", "status IN ('sent', 'skipped')"
    )
