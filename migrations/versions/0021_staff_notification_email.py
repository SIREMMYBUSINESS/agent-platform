"""Allow configured active staff to receive escalation email alerts."""
from alembic import op
import sqlalchemy as sa


revision = "0021_staff_notification_email"
down_revision = "0020_appointment_proposal_options"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("staff", sa.Column("email", sa.String(), nullable=True))


def downgrade():
    op.drop_column("staff", "email")
