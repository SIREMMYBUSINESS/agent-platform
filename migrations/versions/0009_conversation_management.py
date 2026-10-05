"""Conversation lifecycle and tenant-scoped staff assignment."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_conversation_management"
down_revision = "0008_api_key_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("status", sa.String(), nullable=False, server_default="open"))
    op.add_column("conversations", sa.Column("version", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_conversations_status", "conversations", "status IN ('open', 'pending', 'closed')")
    op.create_check_constraint("ck_conversations_version", "conversations", "version >= 0")
    op.create_table(
        "conversation_assignments",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True)),
        sa.Column("assigned_by", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_conversation_assignments_tenant"),
        sa.ForeignKeyConstraint(["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_conversation_assignments_conversation"),
        sa.ForeignKeyConstraint(["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"], name="fk_conversation_assignments_staff"),
    )


def downgrade():
    op.drop_table("conversation_assignments")
    op.drop_constraint("ck_conversations_version", "conversations", type_="check")
    op.drop_constraint("ck_conversations_status", "conversations", type_="check")
    op.drop_column("conversations", "version")
    op.drop_column("conversations", "status")
