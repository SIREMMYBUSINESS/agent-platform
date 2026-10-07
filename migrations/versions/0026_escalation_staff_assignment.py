"""Persist staff assignment on escalation tickets."""
from alembic import op
import sqlalchemy as sa


revision = "0026_escalation_staff_assignment"
down_revision = "0025_optimization_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("escalation_tickets") as batch_op:
        batch_op.add_column(sa.Column("assigned_staff_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_escalation_tickets_assigned_staff", "staff",
            ["tenant_id", "business_id", "assigned_staff_id"],
            ["tenant_id", "business_id", "id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("escalation_tickets") as batch_op:
        batch_op.drop_constraint("fk_escalation_tickets_assigned_staff", type_="foreignkey")
        batch_op.drop_column("assigned_staff_id")
