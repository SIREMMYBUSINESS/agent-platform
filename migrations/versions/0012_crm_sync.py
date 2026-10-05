"""Persist tenant-scoped CRM synchronization and retry state."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_crm_sync"
down_revision = "0011_outreach_execution"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "crm_sync_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("provider_key", sa.String(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_crm_sync_records"),
        sa.UniqueConstraint("tenant_id", "provider_key", "idempotency_key", name="uq_crm_sync_idempotency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_crm_sync_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"], name="fk_crm_sync_business"),
        sa.CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_crm_sync_direction"),
        sa.CheckConstraint("status IN ('pending', 'synced', 'failed', 'conflict')", name="ck_crm_sync_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_crm_sync_attempts"),
    )
    op.create_index("ix_crm_sync_entity", "crm_sync_records",
                    ["tenant_id", "provider_key", "entity_type", "entity_id", "created_at"])


def downgrade():
    op.drop_index("ix_crm_sync_entity", table_name="crm_sync_records")
    op.drop_table("crm_sync_records")
