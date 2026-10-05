"""Persist idempotent receptionist workflow executions and follow ups."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_receptionist_workflows"
down_revision = "0009_conversation_management"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_name", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_executions"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_workflow_executions_idempotency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_workflow_executions_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_workflow_executions_conversation"),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_workflow_executions_status"),
        sa.CheckConstraint("attempts > 0", name="ck_workflow_executions_attempts"),
    )
    op.create_index("ix_workflow_executions_tenant_conversation_created", "workflow_executions",
                    ["tenant_id", "conversation_id", "created_at"])
    op.create_table(
        "follow_up_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("workflow_state", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_follow_up_tasks"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_follow_up_tasks_idempotency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_follow_up_tasks_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_follow_up_tasks_conversation"),
        sa.CheckConstraint("reason IN ('callback_requested', 'missed_response', 'nurture')", name="ck_follow_up_tasks_reason"),
        sa.CheckConstraint("status IN ('scheduled', 'cancelled', 'completed')", name="ck_follow_up_tasks_status"),
    )
    op.create_index("ix_follow_up_tasks_tenant_business_due", "follow_up_tasks",
                    ["tenant_id", "business_id", "scheduled_at", "status"])


def downgrade():
    op.drop_index("ix_follow_up_tasks_tenant_business_due", table_name="follow_up_tasks")
    op.drop_table("follow_up_tasks")
    op.drop_index("ix_workflow_executions_tenant_conversation_created", table_name="workflow_executions")
    op.drop_table("workflow_executions")
