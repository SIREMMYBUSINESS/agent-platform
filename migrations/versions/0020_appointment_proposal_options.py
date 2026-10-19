"""Persist the exact appointment slots offered for customer selection."""
from alembic import op
import sqlalchemy as sa


revision = "0020_appointment_proposal_options"
down_revision = "0019_appointment_campaign_links"
branch_labels = None
depends_on = None


def upgrade():
    # Alembic creates alembic_version.version_num as VARCHAR(32), but this
    # revision ID (and later descriptive IDs) is longer. Widen the version
    # column before Alembic records this revision at the end of the upgrade.
    with op.batch_alter_table("alembic_version") as batch:
        batch.alter_column(
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=128),
            existing_nullable=False,
        )

    with op.batch_alter_table("appointments") as batch:
        batch.add_column(sa.Column(
            "proposal_slot_ids", sa.JSON(), nullable=False,
            server_default=sa.text("'[]'"),
        ))
    with op.batch_alter_table("appointments") as batch:
        batch.alter_column("proposal_slot_ids", server_default=None)


def downgrade():
    with op.batch_alter_table("appointments") as batch:
        batch.drop_column("proposal_slot_ids")
