"""Persist verified provider callbacks idempotently."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_provider_webhook_events"
down_revision = "0014_durable_task_queue"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade():
    op.create_table(
        "provider_webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("result", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_provider_webhook_events"),
        sa.UniqueConstraint("tenant_id", "business_id", "provider", "event_key", name="uq_provider_webhook_event_key"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_provider_webhooks_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_provider_webhooks_business"),
        sa.CheckConstraint("status IN ('received', 'processed', 'ignored', 'failed')", name="ck_provider_webhook_event_status"),
    )
    op.create_index("ix_provider_webhooks_scope_created", "provider_webhook_events", ["tenant_id", "business_id", "created_at"])
    op.create_table(
        "provider_interaction_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_provider_interaction_logs"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_provider_interactions_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_provider_interactions_business"),
    )
    op.create_index("ix_provider_interactions_scope_created", "provider_interaction_logs", ["tenant_id", "business_id", "created_at"])


def downgrade():
    op.drop_index("ix_provider_interactions_scope_created", table_name="provider_interaction_logs")
    op.drop_table("provider_interaction_logs")
    op.drop_index("ix_provider_webhooks_scope_created", table_name="provider_webhook_events")
    op.drop_table("provider_webhook_events")
