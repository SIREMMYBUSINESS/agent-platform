"""Tenant-scoped repository contracts and SQLModel implementations.

Repositories share the Session owned by a UnitOfWork. They may flush to obtain
database-generated values and constraint feedback, but transaction completion
belongs exclusively to the UnitOfWork.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.persistence.models import (
    Business,
    Contact,
    Conversation,
    Appointment,
    AppointmentSlot,
    EventLog,
    EscalationTicket,
    Message,
    RoutingRule,
    Service,
    Staff,
    Tenant,
    WorkflowStateRecord,
)


class PersistenceError(Exception):
    """Base application-level persistence failure."""


class EntityNotFoundError(PersistenceError):
    """An operation requiring an entity could not find it in the tenant scope."""


class ConstraintViolationError(PersistenceError):
    """A database constraint rejected a persistence operation."""


class DuplicateEntityError(ConstraintViolationError):
    """A uniqueness constraint rejected a duplicate entity."""


class InvalidTenantRelationshipError(ConstraintViolationError):
    """A foreign key rejected a tenant or parent relationship."""


class TenantRepository(Protocol):
    def get(self, *, tenant_id: str) -> Tenant | None: ...
    def exists(self, *, tenant_id: str) -> bool: ...


class BusinessRepository(Protocol):
    def get(self, *, tenant_id: str, business_id: str) -> Business | None: ...


class ContactRepository(Protocol):
    def get(self, *, tenant_id: str, contact_id: str) -> Contact | None: ...


class ConversationRepository(Protocol):
    def get(self, *, tenant_id: str, conversation_id: UUID | str) -> Conversation | None: ...
    def get_for_update(self, *, tenant_id: str, conversation_id: UUID | str) -> Conversation | None: ...
    def create(
        self, *, tenant_id: str, business_id: str, contact_id: str, channel: str,
        correlation_id: str | None = None,
    ) -> Conversation: ...
    def list_for_contact(self, *, tenant_id: str, contact_id: str) -> list[Conversation]: ...
    def list(self, *, tenant_id: str, offset: int, limit: int) -> list[Conversation]: ...
    def list_for_business(self, *, tenant_id: str, business_id: str, limit: int = 500) -> list[Conversation]: ...
    def change(self, *, tenant_id: str, conversation_id: UUID, expected_version: int,
               status: str) -> Conversation | None: ...


class MessageRepository(Protocol):
    def add(
        self, *, tenant_id: str, conversation_id: UUID | str, sender: str,
        channel: str, content: str, direction: str,
    ) -> Message: ...
    def list_for_conversation(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> list[Message]: ...


class WorkflowRepository(Protocol):
    def get_current(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> Conversation | None: ...
    def history(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> list[WorkflowStateRecord]: ...
    def append(
        self, *, tenant_id: str, conversation_id: UUID | str, state: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowStateRecord: ...


class EventRepository(Protocol):
    def add(
        self, *, tenant_id: str, business_id: str, conversation_id: UUID | str,
        event_type: str, payload: dict[str, Any], source: str,
        correlation_id: str | None = None,
    ) -> EventLog: ...
    def list_for_conversation(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> list[EventLog]: ...


class RoutingRuleRepository(Protocol):
    def list_for_business(self, *, tenant_id: str, business_id: str) -> list[RoutingRule]: ...


class StaffRepository(Protocol):
    def list_active(self, *, tenant_id: str, business_id: str) -> list[Staff]: ...


class ServiceRepository(Protocol):
    def list_active(self, *, tenant_id: str, business_id: str) -> list[Service]: ...


class AppointmentSlotRepository(Protocol):
    def list_available(
        self, *, tenant_id: str, business_id: str, after: datetime,
    ) -> list[AppointmentSlot]: ...
    def reserve(self, *, tenant_id: str, slot_id: UUID | str) -> AppointmentSlot | None: ...


class AppointmentRepository(Protocol):
    def create(
        self, *, tenant_id: str, business_id: str, contact_id: str,
        conversation_id: UUID, staff_id: UUID, service_id: UUID, slot_id: UUID,
        starts_at: datetime, ends_at: datetime, status: str = "proposed",
    ) -> Appointment: ...
    def latest_proposed(
        self, *, tenant_id: str, business_id: str, contact_id: str,
    ) -> Appointment | None: ...
    def confirm(self, *, tenant_id: str, appointment_id: UUID | str) -> Appointment | None: ...


class EscalationRepository(Protocol):
    def create(
        self, *, tenant_id: str, business_id: str, contact_id: str,
        conversation_id: UUID, reason: str, priority: str,
    ) -> EscalationTicket: ...


def _require_tenant(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id is required")


def _uuid(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("conversation_id must be a valid UUID") from exc


def _translate(exc: SQLAlchemyError) -> PersistenceError:
    if isinstance(exc, IntegrityError):
        original = exc.orig
        sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
        diagnostic = getattr(original, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        text = str(original).lower()
        if sqlstate == "23505" or "unique constraint failed" in text or "is not unique" in text:
            return DuplicateEntityError("database uniqueness constraint rejected the operation")
        if sqlstate == "23503" or "foreign key constraint failed" in text:
            return InvalidTenantRelationshipError(
                "tenant or parent relationship is invalid"
                + (f" ({constraint_name})" if constraint_name else "")
            )
        return ConstraintViolationError("database constraint rejected the operation")
    return PersistenceError("database operation failed")


T = TypeVar("T")


def _database_call(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except SQLAlchemyError as exc:
        raise _translate(exc) from exc


class SQLTenantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str) -> Tenant | None:
        _require_tenant(tenant_id)
        return _database_call(lambda: self._session.get(Tenant, tenant_id))

    def exists(self, *, tenant_id: str) -> bool:
        return self.get(tenant_id=tenant_id) is not None


class SQLBusinessRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, business_id: str) -> Business | None:
        _require_tenant(tenant_id)
        statement = select(Business).where(
            Business.tenant_id == tenant_id, Business.id == business_id
        )
        return _database_call(lambda: self._session.exec(statement).first())


class SQLContactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, contact_id: str) -> Contact | None:
        _require_tenant(tenant_id)
        statement = select(Contact).where(Contact.tenant_id == tenant_id, Contact.id == contact_id)
        return _database_call(lambda: self._session.exec(statement).first())

    def list_for_business(self, *, tenant_id: str, business_id: str,
                          limit: int = 500) -> list[Contact]:
        _require_tenant(tenant_id)
        if not 1 <= limit <= 2000:
            raise ValueError("limit must be between 1 and 2000")
        statement = (select(Contact).where(
            Contact.tenant_id == tenant_id, Contact.business_id == business_id,
        ).order_by(Contact.id).limit(limit))
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLConversationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, conversation_id: UUID | str) -> Conversation | None:
        _require_tenant(tenant_id)
        conversation_uuid = _uuid(conversation_id)
        statement = select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.id == conversation_uuid
        )
        return _database_call(lambda: self._session.exec(statement).first())

    def get_for_update(self, *, tenant_id: str,
                       conversation_id: UUID | str) -> Conversation | None:
        _require_tenant(tenant_id)
        statement = select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.id == _uuid(conversation_id)
        ).with_for_update()
        return _database_call(lambda: self._session.exec(statement).first())

    def list(self, *, tenant_id: str, offset: int = 0, limit: int = 50) -> list[Conversation]:
        _require_tenant(tenant_id)
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid pagination")
        statement = (select(Conversation).where(Conversation.tenant_id == tenant_id)
                     .order_by(Conversation.started_at, Conversation.id).offset(offset).limit(limit))
        return _database_call(lambda: list(self._session.exec(statement).all()))

    def list_for_business(self, *, tenant_id: str, business_id: str,
                          limit: int = 500) -> list[Conversation]:
        _require_tenant(tenant_id)
        if not 1 <= limit <= 2000:
            raise ValueError("limit must be between 1 and 2000")
        statement = select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.business_id == business_id,
            Conversation.status != "closed",
        ).order_by(Conversation.updated_at, Conversation.id).limit(limit)
        return _database_call(lambda: list(self._session.exec(statement).all()))

    def change(self, *, tenant_id: str, conversation_id: UUID, expected_version: int,
               status: str) -> Conversation | None:
        """Atomically claim a management update; None means absent or stale scope."""
        _require_tenant(tenant_id)
        def persist():
            result = self._session.execute(update(Conversation).where(
                Conversation.tenant_id == tenant_id, Conversation.id == _uuid(conversation_id),
                Conversation.version == expected_version,
            ).values(status=status, version=expected_version + 1, updated_at=datetime.now(UTC)))
            if result.rowcount != 1:
                return None
            self._session.flush()
            row = self.get(tenant_id=tenant_id, conversation_id=conversation_id)
            self._session.refresh(row)
            return row
        return _database_call(persist)

    def create(
        self, *, tenant_id: str, business_id: str, contact_id: str, channel: str,
        correlation_id: str | None = None,
    ) -> Conversation:
        _require_tenant(tenant_id)
        conversation = Conversation(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            channel=channel,
            correlation_id=correlation_id,
        )
        def persist() -> Conversation:
            self._session.add(conversation)
            self._session.flush()
            return conversation
        return _database_call(persist)

    def list_for_contact(self, *, tenant_id: str, contact_id: str) -> list[Conversation]:
        _require_tenant(tenant_id)
        statement = (
            select(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.contact_id == contact_id)
            .order_by(Conversation.started_at, Conversation.id)
        )
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLMessageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, *, tenant_id: str, conversation_id: UUID | str, sender: str,
        channel: str, content: str, direction: str,
    ) -> Message:
        _require_tenant(tenant_id)
        message = Message(
            tenant_id=tenant_id, conversation_id=_uuid(conversation_id), sender=sender,
            channel=channel, content=content, direction=direction,
        )
        def persist() -> Message:
            self._session.add(message)
            self._session.flush()
            return message
        return _database_call(persist)

    def list_for_conversation(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> list[Message]:
        _require_tenant(tenant_id)
        statement = (
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.conversation_id == _uuid(conversation_id))
            .order_by(Message.created_at, Message.id)
        )
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLWorkflowRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_current(self, *, tenant_id: str, conversation_id: UUID | str) -> Conversation | None:
        _require_tenant(tenant_id)
        statement = select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.id == _uuid(conversation_id)
        )
        return _database_call(lambda: self._session.exec(statement).first())

    def history(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> list[WorkflowStateRecord]:
        _require_tenant(tenant_id)
        statement = (
            select(WorkflowStateRecord)
            .where(
                WorkflowStateRecord.tenant_id == tenant_id,
                WorkflowStateRecord.conversation_id == _uuid(conversation_id),
            )
            .order_by(WorkflowStateRecord.created_at, WorkflowStateRecord.id)
        )
        return _database_call(lambda: list(self._session.exec(statement).all()))

    def append(
        self, *, tenant_id: str, conversation_id: UUID | str, state: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowStateRecord:
        _require_tenant(tenant_id)
        conversation_uuid = _uuid(conversation_id)
        def persist() -> WorkflowStateRecord:
            conversation = self._session.exec(
                select(Conversation).where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.id == conversation_uuid,
                )
            ).first()
            if conversation is None:
                raise EntityNotFoundError("conversation not found for tenant")
            conversation.workflow_state = state
            conversation.updated_at = datetime.now(UTC)
            record = WorkflowStateRecord(
                tenant_id=tenant_id, conversation_id=conversation_uuid,
                state=state, context=context or {},
            )
            self._session.add(record)
            self._session.flush()
            return record
        return _database_call(persist)


class SQLEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, *, tenant_id: str, business_id: str, conversation_id: UUID | str,
        event_type: str, payload: dict[str, Any], source: str,
        correlation_id: str | None = None,
    ) -> EventLog:
        _require_tenant(tenant_id)
        event = EventLog(
            tenant_id=tenant_id, business_id=business_id,
            conversation_id=_uuid(conversation_id), event_type=event_type,
            payload=payload, source=source, correlation_id=correlation_id,
        )
        def persist() -> EventLog:
            self._session.add(event)
            self._session.flush()
            return event
        return _database_call(persist)

    def list_for_conversation(
        self, *, tenant_id: str, conversation_id: UUID | str,
    ) -> list[EventLog]:
        _require_tenant(tenant_id)
        statement = (
            select(EventLog)
            .where(EventLog.tenant_id == tenant_id, EventLog.conversation_id == _uuid(conversation_id))
            .order_by(EventLog.created_at, EventLog.id)
        )
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLRoutingRuleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_business(self, *, tenant_id: str, business_id: str) -> list[RoutingRule]:
        _require_tenant(tenant_id)
        statement = (
            select(RoutingRule)
            .where(RoutingRule.tenant_id == tenant_id, RoutingRule.business_id == business_id,
                   RoutingRule.active.is_(True))
            .order_by(RoutingRule.weekday)
        )
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLStaffRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self, *, tenant_id: str, business_id: str) -> list[Staff]:
        _require_tenant(tenant_id)
        statement = select(Staff).where(
            Staff.tenant_id == tenant_id, Staff.business_id == business_id, Staff.active.is_(True)
        ).order_by(Staff.display_name, Staff.id)
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLServiceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self, *, tenant_id: str, business_id: str) -> list[Service]:
        _require_tenant(tenant_id)
        statement = select(Service).where(
            Service.tenant_id == tenant_id,
            Service.business_id == business_id,
            Service.active.is_(True),
        ).order_by(Service.name, Service.id)
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLAppointmentSlotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_available(
        self, *, tenant_id: str, business_id: str, after: datetime,
    ) -> list[AppointmentSlot]:
        _require_tenant(tenant_id)
        statement = (
            select(AppointmentSlot)
            .join(Staff, (Staff.tenant_id == AppointmentSlot.tenant_id)
                  & (Staff.business_id == AppointmentSlot.business_id)
                  & (Staff.id == AppointmentSlot.staff_id))
            .join(Service, (Service.tenant_id == AppointmentSlot.tenant_id)
                  & (Service.business_id == AppointmentSlot.business_id)
                  & (Service.id == AppointmentSlot.service_id))
            .where(
                AppointmentSlot.tenant_id == tenant_id,
                AppointmentSlot.business_id == business_id,
                AppointmentSlot.status == "available",
                AppointmentSlot.starts_at >= after,
                Staff.active.is_(True),
                Service.active.is_(True),
            )
            .order_by(AppointmentSlot.starts_at, AppointmentSlot.staff_id, AppointmentSlot.id)
        )
        return _database_call(lambda: list(self._session.exec(statement).all()))

    def reserve(self, *, tenant_id: str, slot_id: UUID | str) -> AppointmentSlot | None:
        _require_tenant(tenant_id)
        slot_uuid = _uuid(slot_id)

        def persist() -> AppointmentSlot | None:
            result = self._session.exec(
                update(AppointmentSlot)
                .where(
                    AppointmentSlot.tenant_id == tenant_id,
                    AppointmentSlot.id == slot_uuid,
                    AppointmentSlot.status == "available",
                )
                .values(status="reserved")
            )
            if result.rowcount != 1:
                return None
            self._session.flush()
            return self._session.exec(select(AppointmentSlot).where(
                AppointmentSlot.tenant_id == tenant_id,
                AppointmentSlot.id == slot_uuid,
            )).first()

        return _database_call(persist)


class SQLAppointmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, *, tenant_id: str, business_id: str, contact_id: str,
        conversation_id: UUID, staff_id: UUID, service_id: UUID, slot_id: UUID,
        starts_at: datetime, ends_at: datetime, status: str = "proposed",
    ) -> Appointment:
        _require_tenant(tenant_id)
        appointment = Appointment(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            staff_id=staff_id,
            service_id=service_id,
            slot_id=slot_id,
            starts_at=starts_at,
            ends_at=ends_at,
            status=status,
        )

        def persist() -> Appointment:
            self._session.add(appointment)
            self._session.flush()
            return appointment

        return _database_call(persist)

    def latest_proposed(
        self, *, tenant_id: str, business_id: str, contact_id: str,
    ) -> Appointment | None:
        _require_tenant(tenant_id)
        statement = (
            select(Appointment)
            .where(
                Appointment.tenant_id == tenant_id,
                Appointment.business_id == business_id,
                Appointment.contact_id == contact_id,
                Appointment.status == "proposed",
            )
            .order_by(Appointment.created_at.desc(), Appointment.id.desc())
        )
        return _database_call(lambda: self._session.exec(statement).first())

    def confirm(self, *, tenant_id: str, appointment_id: UUID | str) -> Appointment | None:
        _require_tenant(tenant_id)
        appointment_uuid = _uuid(appointment_id)

        def persist() -> Appointment | None:
            appointment = self._session.exec(select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.id == appointment_uuid,
                Appointment.status == "proposed",
            )).first()
            if appointment is None:
                return None
            if appointment.slot_id is not None:
                result = self._session.exec(
                    update(AppointmentSlot)
                    .where(
                        AppointmentSlot.tenant_id == tenant_id,
                        AppointmentSlot.id == appointment.slot_id,
                        AppointmentSlot.status == "reserved",
                    )
                    .values(status="booked")
                )
                if result.rowcount != 1:
                    return None
            appointment.status = "confirmed"
            self._session.flush()
            return appointment

        return _database_call(persist)


class SQLEscalationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, *, tenant_id: str, business_id: str, contact_id: str,
        conversation_id: UUID, reason: str, priority: str,
    ) -> EscalationTicket:
        _require_tenant(tenant_id)
        ticket = EscalationTicket(
            tenant_id=tenant_id,
            business_id=business_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            reason=reason,
            priority=priority,
        )

        def persist() -> EscalationTicket:
            self._session.add(ticket)
            self._session.flush()
            return ticket

        return _database_call(persist)


__all__ = [
    "BusinessRepository", "ConstraintViolationError", "ContactRepository",
    "ConversationRepository", "DuplicateEntityError", "EventRepository",
    "EntityNotFoundError", "InvalidTenantRelationshipError", "MessageRepository", "PersistenceError",
    "SQLBusinessRepository", "SQLContactRepository", "SQLConversationRepository",
    "SQLEventRepository", "SQLMessageRepository", "SQLTenantRepository",
    "SQLWorkflowRepository", "TenantRepository", "WorkflowRepository",
    "AppointmentRepository", "AppointmentSlotRepository", "EscalationRepository",
    "RoutingRuleRepository", "ServiceRepository", "StaffRepository",
    "SQLAppointmentRepository", "SQLAppointmentSlotRepository", "SQLEscalationRepository",
    "SQLRoutingRuleRepository", "SQLServiceRepository", "SQLStaffRepository",
]
