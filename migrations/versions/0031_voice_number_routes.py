"""Create explicit inbound voice number routes using the control-directory schema."""
from alembic import op
import sqlalchemy as sa

revision = "0031_voice_number_routes"
down_revision = "0030_tenant_db_route"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_number_routes",
        sa.Column("e164", sa.String(16), nullable=False),
        sa.Column("tenant_id", sa.String(160), nullable=False),
        sa.Column("business_id", sa.String(160), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("e164", name="pk_voice_number_routes"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_voice_number_routes_status"),
    )
    op.create_index("ix_voice_number_routes_scope", "voice_number_routes", ["tenant_id", "business_id"])


def downgrade():
    op.drop_index("ix_voice_number_routes_scope", table_name="voice_number_routes")
    op.drop_table("voice_number_routes")
