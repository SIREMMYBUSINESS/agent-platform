"""Add durable queue outbox, retry state, and dead-letter ledger."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_durable_task_queue"
down_revision = "0013_provider_delivery_receipts"
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "queue_task_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("task_name", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("correlation_id", sa.String(), nullable=False),
        sa.Column("traceparent", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redriven_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("result", json_type, nullable=True),
        sa.CheckConstraint(
            "domain IN ('inbound', 'outreach', 'scheduling', 'escalation', 'analytics')",
            name="ck_queue_tasks_domain",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'running', 'retrying', 'succeeded', 'dead_letter')",
            name="ck_queue_tasks_status",
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="ck_queue_tasks_attempts"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_queue_tasks_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_queue_tasks_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_queue_task_records"),
        sa.UniqueConstraint(
            "tenant_id", "business_id", "idempotency_key", name="uq_queue_tasks_idempotency"
        ),
    )
    op.create_index(
        "ix_queue_tasks_due", "queue_task_records", ["status", "available_at", "domain"]
    )
    op.create_index(
        "ix_queue_tasks_scope", "queue_task_records", ["tenant_id", "business_id", "created_at"]
    )


def downgrade():
    op.drop_index("ix_queue_tasks_scope", table_name="queue_task_records")
    op.drop_index("ix_queue_tasks_due", table_name="queue_task_records")
    op.drop_table("queue_task_records")
