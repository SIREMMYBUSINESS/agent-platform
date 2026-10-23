"""Create the control-directory tenant-to-region assignment."""

from alembic import op
import sqlalchemy as sa


revision = "0030_tenant_db_route"
down_revision = "0029_single_worker_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_database_assignments",
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("home_region_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('active', 'migrating', 'disabled')",
            name="ck_tenant_database_assignments_status",
        ),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_database_assignments"),
    )
    op.create_index(
        "ix_tenant_database_assignments_region_status",
        "tenant_database_assignments", ["home_region_id", "status"],
    )
    op.execute(sa.text(
        "INSERT INTO tenant_database_assignments (tenant_id, home_region_id) "
        "SELECT id, default_region_id FROM tenants"
    ))


def downgrade() -> None:
    op.drop_index(
        "ix_tenant_database_assignments_region_status",
        table_name="tenant_database_assignments",
    )
    op.drop_table("tenant_database_assignments")
