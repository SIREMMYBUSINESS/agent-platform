"""Create explicit inbound voice number routes."""
from alembic import op
import sqlalchemy as sa
revision = "0031_voice_number_routes"
down_revision = "0030_tenant_db_route"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("voice_number_routes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("phone_number", sa.String(16), nullable=False),
        sa.Column("tenant_id", sa.String(160), nullable=False),
        sa.Column("business_id", sa.String(160), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_voice_number_routes_tenant"),
        sa.ForeignKeyConstraint(["tenant_id","business_id"], ["businesses.tenant_id","businesses.id"], name="fk_voice_number_routes_business"),
        sa.PrimaryKeyConstraint("id", name="pk_voice_number_routes"),
        sa.UniqueConstraint("phone_number", name="uq_voice_number_routes_phone"))
    op.create_index("ix_voice_number_routes_scope", "voice_number_routes", ["tenant_id","business_id","enabled"])

def downgrade():
    op.drop_index("ix_voice_number_routes_scope", table_name="voice_number_routes")
    op.drop_table("voice_number_routes")
