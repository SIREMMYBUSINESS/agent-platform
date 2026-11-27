"""Allow business-scoped SMS refusal audit events without a conversation."""

from alembic import op
import sqlalchemy as sa


revision = "0036_nullable_event_log_conversation"
down_revision = "0035_message_sent_by_staff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("event_logs") as batch:
        batch.alter_column(
            "conversation_id",
            existing_type=sa.Uuid(),
            nullable=True,
        )


def downgrade() -> None:
    # Only the new SMS composer writes business-scoped EventLog rows without a
    # conversation. Remove those rows before restoring the original NOT NULL contract.
    op.execute(
        sa.text(
            "DELETE FROM event_logs "
            "WHERE conversation_id IS NULL AND source = 'conversation_sms_composer'"
        )
    )
    with op.batch_alter_table("event_logs") as batch:
        batch.alter_column(
            "conversation_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
