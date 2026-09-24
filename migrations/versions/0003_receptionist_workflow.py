"""Add intake routing, staff scheduling, and escalations.

Revision ID: 0003_receptionist
Revises: 0002_appointments
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_receptionist"
down_revision: Union[str, None] = "0002_appointments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("timezone", sa.String(), server_default="UTC", nullable=False))
    op.add_column("contacts", sa.Column("timezone", sa.String(), nullable=True))

    op.create_table(
        "staff",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_staff_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_staff_tenant_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_staff"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_staff_tenant_business_id"),
    )
    op.create_index("ix_staff_tenant_business_active", "staff", ["tenant_id", "business_id", "active"])

    op.create_table(
        "services",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("duration_minutes > 0", name="ck_services_duration_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_services_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_services_tenant_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_services_tenant_business_id"),
    )
    op.create_index("ix_services_tenant_business_active", "services", ["tenant_id", "business_id", "active"])

    op.create_table(
        "appointment_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), server_default="available", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appointment_slots_end_after_start"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_appointment_slots_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_appointment_slots_tenant_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_appointment_slots_tenant_business_staff",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id", "service_id"],
            ["services.tenant_id", "services.business_id", "services.id"],
            name="fk_appointment_slots_tenant_business_service",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointment_slots"),
        sa.UniqueConstraint("tenant_id", "business_id", "id", name="uq_slots_tenant_business_id"),
        sa.UniqueConstraint("tenant_id", "staff_id", "starts_at", name="uq_slots_tenant_staff_start"),
    )
    op.create_index(
        "ix_appointment_slots_tenant_business_status_start", "appointment_slots",
        ["tenant_id", "business_id", "status", "starts_at"],
    )

    op.create_table(
        "routing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=False),
        sa.Column("closes_at", sa.Time(), nullable=False),
        sa.Column("after_hours_action", sa.String(), server_default="callback", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_routing_rules_weekday_range"),
        sa.CheckConstraint("closes_at != opens_at", name="ck_routing_rules_hours_not_equal"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_routing_rules_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_routing_rules_tenant_business",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_routing_rules"),
        sa.UniqueConstraint("tenant_id", "business_id", "weekday", name="uq_routing_rules_tenant_business_day"),
    )
    op.create_index(
        "ix_routing_rules_tenant_business_active", "routing_rules",
        ["tenant_id", "business_id", "active"],
    )

    op.create_table(
        "escalation_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), server_default="urgent", nullable=False),
        sa.Column("status", sa.String(), server_default="open", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_escalation_tickets_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id"], ["businesses.tenant_id", "businesses.id"],
            name="fk_escalation_tickets_tenant_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_escalation_tickets_tenant_contact_business",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_escalation_tickets_tenant_conversation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_escalation_tickets"),
    )
    op.create_index(
        "ix_escalation_tickets_tenant_business_status", "escalation_tickets",
        ["tenant_id", "business_id", "status"],
    )

    op.add_column("appointments", sa.Column("staff_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("appointments", sa.Column("service_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("appointments", sa.Column("slot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint(
        "uq_appointments_tenant_slot", "appointments", ["tenant_id", "slot_id"]
    )
    op.create_foreign_key(
        "fk_appointments_tenant_business_staff", "appointments", "staff",
        ["tenant_id", "business_id", "staff_id"], ["tenant_id", "business_id", "id"],
    )
    op.create_foreign_key(
        "fk_appointments_tenant_business_service", "appointments", "services",
        ["tenant_id", "business_id", "service_id"], ["tenant_id", "business_id", "id"],
    )
    op.create_foreign_key(
        "fk_appointments_tenant_business_slot", "appointments", "appointment_slots",
        ["tenant_id", "business_id", "slot_id"], ["tenant_id", "business_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_appointments_tenant_business_slot", "appointments", type_="foreignkey")
    op.drop_constraint("fk_appointments_tenant_business_service", "appointments", type_="foreignkey")
    op.drop_constraint("fk_appointments_tenant_business_staff", "appointments", type_="foreignkey")
    op.drop_constraint("uq_appointments_tenant_slot", "appointments", type_="unique")
    op.drop_column("appointments", "slot_id")
    op.drop_column("appointments", "service_id")
    op.drop_column("appointments", "staff_id")
    op.drop_index("ix_escalation_tickets_tenant_business_status", table_name="escalation_tickets")
    op.drop_table("escalation_tickets")
    op.drop_index("ix_routing_rules_tenant_business_active", table_name="routing_rules")
    op.drop_table("routing_rules")
    op.drop_index("ix_appointment_slots_tenant_business_status_start", table_name="appointment_slots")
    op.drop_table("appointment_slots")
    op.drop_index("ix_services_tenant_business_active", table_name="services")
    op.drop_table("services")
    op.drop_index("ix_staff_tenant_business_active", table_name="staff")
    op.drop_table("staff")
    op.drop_column("contacts", "timezone")
    op.drop_column("businesses", "timezone")
