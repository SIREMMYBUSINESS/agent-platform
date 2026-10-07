"""Track whether appointments originated from inbound or outreach."""
from alembic import op
import sqlalchemy as sa

revision = "0018_appointment_origin"
down_revision = "0017_message_delivery_state"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("appointments") as batch:
        batch.add_column(sa.Column("source", sa.String(), nullable=False,
                                   server_default="inbound"))
        batch.create_check_constraint(
            "ck_appointments_source", "source IN ('inbound', 'outreach')",
        )


def downgrade():
    with op.batch_alter_table("appointments") as batch:
        batch.drop_constraint("ck_appointments_source", type_="check")
        batch.drop_column("source")
