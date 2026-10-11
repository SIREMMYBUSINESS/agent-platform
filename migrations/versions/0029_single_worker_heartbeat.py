"""Record liveness for the PostgreSQL-backed single worker."""

from alembic import op
import sqlalchemy as sa


revision = "0029_single_worker_heartbeat"
down_revision = "0028_dental_vertical_onboarding"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(length=120), nullable=False),
        sa.Column("region_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('running', 'stopped')", name="ck_worker_heartbeats_status"),
        sa.PrimaryKeyConstraint("worker_id", name="pk_worker_heartbeats"),
    )
    op.create_index(
        "ix_worker_heartbeats_seen", "worker_heartbeats", ["status", "heartbeat_at"]
    )


def downgrade():
    op.drop_index("ix_worker_heartbeats_seen", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
