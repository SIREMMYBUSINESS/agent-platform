"""Link appointment bookings to the outreach campaign enrollment that led to them."""
from alembic import op
import sqlalchemy as sa

revision = "0019_appointment_campaign_links"
down_revision = "0018_appointment_origin"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("appointments") as batch:
        batch.add_column(sa.Column("campaign_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("campaign_enrollment_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_appointments_campaign", "campaigns",
            ["tenant_id", "business_id", "campaign_id"],
            ["tenant_id", "business_id", "id"],
        )
        batch.create_foreign_key(
            "fk_appointments_campaign_enrollment", "campaign_enrollments",
            ["tenant_id", "business_id", "campaign_enrollment_id"],
            ["tenant_id", "business_id", "id"],
        )


def downgrade():
    with op.batch_alter_table("appointments") as batch:
        batch.drop_constraint("fk_appointments_campaign_enrollment", type_="foreignkey")
        batch.drop_constraint("fk_appointments_campaign", type_="foreignkey")
        batch.drop_column("campaign_enrollment_id")
        batch.drop_column("campaign_id")
