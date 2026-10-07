"""Add workflow correlation, provider references, preferences, and resolution data."""
from alembic import op
import sqlalchemy as sa

revision = "0016_phase6_workflow_fields"
down_revision = "0015_provider_webhook_events"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("appointments", sa.Column("calendar_event_id", sa.String(), nullable=True))
    op.add_column("appointments", sa.Column("crm_record_id", sa.String(), nullable=True))
    op.add_column("appointments", sa.Column("correlation_id", sa.String(), nullable=True))
    op.add_column("contacts", sa.Column("preferred_channel", sa.String(), nullable=True))
    op.add_column("contacts", sa.Column("language", sa.String(), nullable=True))
    op.add_column("escalation_tickets", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("escalation_tickets", sa.Column("resolution", sa.String(), nullable=True))


def downgrade():
    op.drop_column("escalation_tickets", "resolution")
    op.drop_column("escalation_tickets", "resolved_at")
    op.drop_column("contacts", "language")
    op.drop_column("contacts", "preferred_channel")
    op.drop_column("appointments", "correlation_id")
    op.drop_column("appointments", "crm_record_id")
    op.drop_column("appointments", "calendar_event_id")
