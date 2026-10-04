"""Provision API-key identities and tenant grants."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0008_api_key_auth"
down_revision = "0007_geo_policy_engine"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_api_keys_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_api_keys_tenant_id"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_hash"),
    )
    op.create_table(
        "api_key_grants",
        sa.Column("principal_id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), primary_key=True),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_api_key_grants_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "principal_id"], ["api_keys.tenant_id", "api_keys.id"], name="fk_api_key_grants_key"),
    )


def downgrade():
    op.drop_table("api_key_grants")
    op.drop_table("api_keys")
