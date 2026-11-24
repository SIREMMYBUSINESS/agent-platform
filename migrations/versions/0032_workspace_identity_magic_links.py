"""Add provisioned workspace identities and one-time passwordless sessions."""

from alembic import op
import sqlalchemy as sa


revision = "0032_workspace_identity_magic_links"
down_revision = "0031_voice_number_routes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="invited"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('invited', 'active', 'disabled')", name="ck_workspace_users_status"),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_users"),
        sa.UniqueConstraint("email", name="uq_workspace_users_email"),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("workspace_label", sa.String(length=200), nullable=False, server_default="Workspace"),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="invited"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('owner', 'admin', 'manager', 'agent', 'viewer')", name="ck_tenant_memberships_role"),
        sa.CheckConstraint("status IN ('invited', 'active', 'suspended')", name="ck_tenant_memberships_status"),
        sa.ForeignKeyConstraint(["user_id"], ["workspace_users.id"], name="fk_tenant_memberships_user"),
        sa.PrimaryKeyConstraint("id", name="pk_tenant_memberships"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_tenant_user"),
        sa.UniqueConstraint("tenant_id", "user_id", "id", name="uq_tenant_memberships_scope_id"),
    )
    op.create_index("ix_tenant_memberships_user_status", "tenant_memberships", ["user_id", "status"])
    op.create_index("ix_tenant_memberships_tenant_status", "tenant_memberships", ["tenant_id", "status"])

    op.create_table(
        "magic_link_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["workspace_users.id"], name="fk_magic_link_challenges_user"),
        sa.PrimaryKeyConstraint("id", name="pk_magic_link_challenges"),
        sa.UniqueConstraint("token_hash", name="uq_magic_link_challenges_token_hash"),
    )
    op.create_index("ix_magic_link_challenges_user_created", "magic_link_challenges", ["user_id", "created_at"])

    op.create_table(
        "auth_login_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["workspace_users.id"], name="fk_auth_login_tickets_user"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_login_tickets"),
        sa.UniqueConstraint("token_hash", name="uq_auth_login_tickets_token_hash"),
    )

    op.create_table(
        "workspace_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=160), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["workspace_users.id"], name="fk_workspace_sessions_user"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id", "membership_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id", "tenant_memberships.id"],
            name="fk_workspace_sessions_membership",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_workspace_sessions_token_hash"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_workspace_sessions_expiry"),
    )
    op.create_index("ix_workspace_sessions_user_tenant", "workspace_sessions", ["user_id", "tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_sessions_user_tenant", table_name="workspace_sessions")
    op.drop_table("workspace_sessions")
    op.drop_table("auth_login_tickets")
    op.drop_index("ix_magic_link_challenges_user_created", table_name="magic_link_challenges")
    op.drop_table("magic_link_challenges")
    op.drop_index("ix_tenant_memberships_tenant_status", table_name="tenant_memberships")
    op.drop_index("ix_tenant_memberships_user_status", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_table("workspace_users")
