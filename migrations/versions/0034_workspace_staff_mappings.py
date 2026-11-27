"""Add business-scoped workspace user to staff mappings and immutable audit events."""

from alembic import op
import sqlalchemy as sa


revision = "0034_workspace_staff_mappings"
down_revision = "0033_business_voice_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_staff_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("business_id", sa.String(length=160), nullable=False),
        sa.Column("workspace_user_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_workspace_staff_mappings_staff",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_staff_mappings"),
        sa.UniqueConstraint(
            "tenant_id", "business_id", "workspace_user_id",
            name="uq_workspace_staff_mappings_user",
        ),
        sa.UniqueConstraint(
            "tenant_id", "business_id", "staff_id",
            name="uq_workspace_staff_mappings_staff",
        ),
    )
    op.create_index(
        "ix_workspace_staff_mappings_scope",
        "workspace_staff_mappings", ["tenant_id", "business_id"],
    )

    op.create_table(
        "workspace_staff_mapping_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("business_id", sa.String(length=160), nullable=False),
        sa.Column("mapping_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_user_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("actor_workspace_user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'removed')",
            name="ck_workspace_staff_mapping_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_workspace_staff_mapping_events_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_staff_mapping_events"),
    )
    op.create_index(
        "ix_workspace_staff_mapping_events_scope_created",
        "workspace_staff_mapping_events",
        ["tenant_id", "business_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_staff_mapping_events_scope_created",
        table_name="workspace_staff_mapping_events",
    )
    op.drop_table("workspace_staff_mapping_events")
    op.drop_index(
        "ix_workspace_staff_mappings_scope",
        table_name="workspace_staff_mappings",
    )
    op.drop_table("workspace_staff_mappings")
