"""Add optional staff attribution to outbound messages."""

from alembic import op
import sqlalchemy as sa


revision = "0035_message_sent_by_staff"
down_revision = "0034_workspace_staff_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode supports SQLite migration tests and PostgreSQL production.
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("sent_by_staff_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_messages_sent_by_staff",
            "staff",
            ["sent_by_staff_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_messages_sent_by_outbound_only",
            "sent_by_staff_id IS NULL OR direction = 'outbound'",
        )


def downgrade() -> None:
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("ck_messages_sent_by_outbound_only", type_="check")
        batch.drop_constraint("fk_messages_sent_by_staff", type_="foreignkey")
        batch.drop_column("sent_by_staff_id")
