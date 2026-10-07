"""Persist vertical onboarding profiles, intake sessions, and visit dates."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0028_dental_vertical_onboarding"
down_revision = "0027_regional_residency"
branch_labels = None
depends_on = None


def _json_type():
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("contacts", sa.Column("last_visit_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "business_vertical_profiles",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("vertical_id", sa.String(64), nullable=False),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tenant_id", "business_id", name="pk_business_vertical_profiles"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
                                name="fk_business_vertical_profiles_business"),
        sa.CheckConstraint("status IN ('draft', 'ready', 'live')", name="ck_business_vertical_profiles_status"),
    )
    op.create_table(
        "vertical_intake_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("question_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answers", _json_type(), nullable=False),
        sa.Column("answer_idempotency", _json_type(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="collecting"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_vertical_intake_sessions"),
        sa.UniqueConstraint("tenant_id", "business_id", "idempotency_key",
                            name="uq_vertical_intake_session_idempotency"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_vertical_intake_sessions_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
                                name="fk_vertical_intake_sessions_business"),
        sa.ForeignKeyConstraint(["tenant_id", "contact_id", "business_id"],
                                ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
                                name="fk_vertical_intake_sessions_contact"),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id"],
                                ["conversations.tenant_id", "conversations.id"],
                                name="fk_vertical_intake_sessions_conversation"),
    )
    op.create_index("ix_vertical_intake_sessions_scope_status", "vertical_intake_sessions",
                    ["tenant_id", "business_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_vertical_intake_sessions_scope_status", table_name="vertical_intake_sessions")
    op.drop_table("vertical_intake_sessions")
    op.drop_table("business_vertical_profiles")
    op.drop_column("contacts", "last_visit_at")
