from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Column,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    JSON,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models import Channel, WorkflowState


def utc_now() -> datetime:
    return datetime.now(UTC)


def json_storage_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    __table_args__ = (PrimaryKeyConstraint("id", name="pk_tenants"),)

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    region: str = "US"
    default_timezone: str = "UTC"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Business(SQLModel, table=True):
    __tablename__ = "businesses"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_businesses"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_businesses_tenant_id_tenants"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_businesses_tenant_id"),
        Index("ix_businesses_tenant_id", "tenant_id", "id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    tenant_id: str
    name: str
    vertical: str
    region: str = "US"
    timezone: str = "UTC"
    default_language: str = "en"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Contact(SQLModel, table=True):
    __tablename__ = "contacts"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_contacts"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_contacts_tenant_id_tenants"),
        UniqueConstraint("tenant_id", "id", name="uq_contacts_tenant_id"),
        UniqueConstraint(
            "tenant_id", "id", "business_id", name="uq_contacts_tenant_id_business_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_contacts_tenant_business",
        ),
        Index("ix_contacts_tenant_business", "tenant_id", "business_id"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    tenant_id: str
    business_id: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    consent_status: str = "unknown"
    timezone: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_conversations"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_conversations_tenant_id_tenants"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id"),
        UniqueConstraint(
            "tenant_id", "id", "business_id", name="uq_conversations_tenant_id_business_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_conversations_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_conversations_tenant_contact_business",
        ),
        Index("ix_conversations_tenant_business", "tenant_id", "business_id"),
        Index("ix_conversations_tenant_contact", "tenant_id", "contact_id"),
        Index("ix_conversations_correlation_id", "correlation_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    channel: str
    workflow_state: str = WorkflowState.NEW.value
    correlation_id: Optional[str] = None
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_messages"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_messages_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_messages_tenant_conversation",
        ),
        Index("ix_messages_tenant_conversation_created", "tenant_id", "conversation_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    conversation_id: UUID
    sender: str
    channel: str
    content: str
    direction: str
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class WorkflowStateRecord(SQLModel, table=True):
    __tablename__ = "workflow_state_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_workflow_state_records"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_workflow_state_records_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_workflow_state_records_tenant_conversation",
        ),
        Index(
            "ix_workflow_state_records_tenant_conversation_created",
            "tenant_id",
            "conversation_id",
            "created_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    conversation_id: UUID
    state: str
    context: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(json_storage_type(), nullable=False, default=dict),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class EventLog(SQLModel, table=True):
    __tablename__ = "event_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_event_logs"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_event_logs_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_event_logs_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id", "business_id"],
            ["conversations.tenant_id", "conversations.id", "conversations.business_id"],
            name="fk_event_logs_tenant_conversation_business",
        ),
        Index("ix_event_logs_tenant_conversation_created", "tenant_id", "conversation_id", "created_at"),
        Index("ix_event_logs_tenant_event_type_created", "tenant_id", "event_type", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    conversation_id: UUID
    event_type: str
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(json_storage_type(), nullable=False, default=dict),
    )
    source: str
    correlation_id: Optional[str] = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Appointment(SQLModel, table=True):
    __tablename__ = "appointments"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_appointments"),
        CheckConstraint("ends_at > starts_at", name="ck_appointments_end_after_start"),
        UniqueConstraint("tenant_id", "slot_id", name="uq_appointments_tenant_slot"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_appointments_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_appointments_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_appointments_tenant_contact_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_appointments_tenant_conversation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_appointments_tenant_business_staff",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "service_id"],
            ["services.tenant_id", "services.business_id", "services.id"],
            name="fk_appointments_tenant_business_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "slot_id"],
            ["appointment_slots.tenant_id", "appointment_slots.business_id", "appointment_slots.id"],
            name="fk_appointments_tenant_business_slot",
        ),
        Index("ix_appointments_tenant_business_start", "tenant_id", "business_id", "starts_at"),
        Index("ix_appointments_tenant_contact_status", "tenant_id", "contact_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    conversation_id: UUID | None = None
    staff_id: UUID | None = None
    service_id: UUID | None = None
    slot_id: UUID | None = None
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    status: str = "requested"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Staff(SQLModel, table=True):
    __tablename__ = "staff"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_staff"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_staff_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_staff_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_staff_tenant_business",
        ),
        Index("ix_staff_tenant_business_active", "tenant_id", "business_id", "active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    display_name: str
    active: bool = True
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Service(SQLModel, table=True):
    __tablename__ = "services"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_services"),
        CheckConstraint("duration_minutes > 0", name="ck_services_duration_positive"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_services_tenant_business_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_services_tenant_id_tenants"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_services_tenant_business",
        ),
        Index("ix_services_tenant_business_active", "tenant_id", "business_id", "active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    name: str
    duration_minutes: int
    active: bool = True
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AppointmentSlot(SQLModel, table=True):
    __tablename__ = "appointment_slots"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_appointment_slots"),
        CheckConstraint("ends_at > starts_at", name="ck_appointment_slots_end_after_start"),
        UniqueConstraint("tenant_id", "business_id", "id", name="uq_slots_tenant_business_id"),
        UniqueConstraint("tenant_id", "staff_id", "starts_at", name="uq_slots_tenant_staff_start"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_appointment_slots_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_appointment_slots_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "staff_id"],
            ["staff.tenant_id", "staff.business_id", "staff.id"],
            name="fk_appointment_slots_tenant_business_staff",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "service_id"],
            ["services.tenant_id", "services.business_id", "services.id"],
            name="fk_appointment_slots_tenant_business_service",
        ),
        Index(
            "ix_appointment_slots_tenant_business_status_start",
            "tenant_id", "business_id", "status", "starts_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    staff_id: UUID
    service_id: UUID
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    status: str = "available"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class EscalationTicket(SQLModel, table=True):
    __tablename__ = "escalation_tickets"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_escalation_tickets"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_escalation_tickets_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_escalation_tickets_tenant_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id", "business_id"],
            ["contacts.tenant_id", "contacts.id", "contacts.business_id"],
            name="fk_escalation_tickets_tenant_contact_business",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            name="fk_escalation_tickets_tenant_conversation",
        ),
        Index("ix_escalation_tickets_tenant_business_status", "tenant_id", "business_id", "status"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    contact_id: str
    conversation_id: UUID
    reason: str
    priority: str = "urgent"
    status: str = "open"
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class RoutingRule(SQLModel, table=True):
    __tablename__ = "routing_rules"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_routing_rules"),
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_routing_rules_weekday_range"),
        CheckConstraint("closes_at != opens_at", name="ck_routing_rules_hours_not_equal"),
        UniqueConstraint("tenant_id", "business_id", "weekday", name="uq_routing_rules_tenant_business_day"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_routing_rules_tenant_id_tenants"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id"],
            ["businesses.tenant_id", "businesses.id"],
            name="fk_routing_rules_tenant_business",
        ),
        Index("ix_routing_rules_tenant_business_active", "tenant_id", "business_id", "active"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    tenant_id: str
    business_id: str
    weekday: int
    opens_at: time
    closes_at: time
    after_hours_action: str = "callback"
    active: bool = True
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


__all__ = [
    "Appointment",
    "AppointmentSlot",
    "Business",
    "Channel",
    "Contact",
    "Conversation",
    "EventLog",
    "EscalationTicket",
    "Message",
    "RoutingRule",
    "Service",
    "Staff",
    "Tenant",
    "WorkflowState",
    "WorkflowStateRecord",
]
