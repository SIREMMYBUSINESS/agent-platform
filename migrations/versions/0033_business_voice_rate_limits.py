"""Add configurable per-business inbound voice admission budgets."""

from alembic import op
import sqlalchemy as sa


revision = "0033_business_voice_rate_limits"
down_revision = "0032_workspace_identity_magic_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "businesses",
        sa.Column("voice_calls_per_minute", sa.Integer(), nullable=False, server_default="10"),
    )
    op.create_check_constraint(
        "ck_businesses_voice_calls_per_minute",
        "businesses",
        "voice_calls_per_minute >= 1 AND voice_calls_per_minute <= 60",
    )


def downgrade() -> None:
    op.drop_constraint("ck_businesses_voice_calls_per_minute", "businesses", type_="check")
    op.drop_column("businesses", "voice_calls_per_minute")
