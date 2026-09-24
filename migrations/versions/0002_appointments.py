"""Add tenant-scoped appointment persistence.

Revision ID: 0002_appointments
Revises: 0001_initial
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0002_appointments"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), server_default="requested", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appointments_end_after_start"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_appointments_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_appointments_tenant_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_appointments_tenant_contact_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_appointments_tenant_conversation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
    )
    op.create_index(
        "ix_appointments_tenant_business_start",
        "appointments",
        ["tenant_id", "business_id", "starts_at"],
    )
    op.create_index(
        "ix_appointments_tenant_contact_status",
        "appointments",
        ["tenant_id", "contact_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_tenant_contact_status", table_name="appointments")
    op.drop_index("ix_appointments_tenant_business_start", table_name="appointments")
    op.drop_table("appointments")
