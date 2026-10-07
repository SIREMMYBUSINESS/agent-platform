"""Store hashes for public appointment action capabilities."""
from alembic import op
import sqlalchemy as sa

revision = "0022_contact_appointment_action_tokens"
down_revision = "0021_staff_notification_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("contact_action_token_hash", sa.String(), nullable=True))
    op.create_index(
        "uq_appointments_contact_action_token_hash", "appointments", ["contact_action_token_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_appointments_contact_action_token_hash", table_name="appointments")
    op.drop_column("appointments", "contact_action_token_hash")
