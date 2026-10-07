"""Persist receptionist message delivery state and provider references."""
from alembic import op
import sqlalchemy as sa

revision = "0017_message_delivery_state"
down_revision = "0016_phase6_workflow_fields"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("messages") as batch:
        batch.add_column(sa.Column("delivery_status", sa.String(), nullable=False,
                                   server_default="not_applicable"))
        batch.add_column(sa.Column("provider_message_id", sa.String(), nullable=True))
        batch.add_column(sa.Column("delivery_error_code", sa.String(), nullable=True))
        batch.create_check_constraint(
            "ck_messages_delivery_status",
            "delivery_status IN ('not_applicable', 'pending', 'sent', 'failed', 'blocked', 'dry_run')",
        )
    op.execute("UPDATE messages SET delivery_status = 'pending' WHERE direction = 'outbound'")


def downgrade():
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("ck_messages_delivery_status", type_="check")
        batch.drop_column("delivery_error_code")
        batch.drop_column("provider_message_id")
        batch.drop_column("delivery_status")
