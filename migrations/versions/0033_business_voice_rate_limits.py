"""Add configurable per-business inbound voice admission budgets."""

from alembic import op
import sqlalchemy as sa


revision = "0033_business_voice_rate_limits"
down_revision = "0032_workspace_identity_magic_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode supports SQLite-based migration tests and PostgreSQL production.
    with op.batch_alter_table("businesses") as batch:
        batch.add_column(
            sa.Column("voice_calls_per_minute", sa.Integer(), nullable=False, server_default="10"),
        )
        batch.create_check_constraint(
            "ck_businesses_voice_calls_per_minute",
            "voice_calls_per_minute >= 1 AND voice_calls_per_minute <= 60",
        )


def downgrade() -> None:
    with op.batch_alter_table("businesses") as batch:
        batch.drop_constraint("ck_businesses_voice_calls_per_minute", type_="check")
        batch.drop_column("voice_calls_per_minute")
