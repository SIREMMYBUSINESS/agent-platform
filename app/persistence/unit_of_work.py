"""Application-owned transaction and session lifecycle."""

from collections.abc import Callable
from types import TracebackType
from typing import Self

from sqlmodel import Session

from app.persistence.repositories import (
    SQLAppointmentRepository,
    SQLAppointmentSlotRepository,
    SQLBusinessRepository,
    SQLContactRepository,
    SQLConversationRepository,
    SQLEventRepository,
    SQLMessageRepository,
    SQLRoutingRuleRepository,
    SQLEscalationRepository,
    SQLServiceRepository,
    SQLStaffRepository,
    SQLTenantRepository,
    SQLWorkflowRepository,
)


class UnitOfWork:
    """Own one session and transaction for one application operation.

    A normal context exit commits. An exceptional exit rolls back. The session
    is closed after either path. Repositories added later must use ``session``
    and leave commit/rollback to this unit of work.
    """

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self._entered = False
        self._finished = False
        self._closed = False
        self.tenants: SQLTenantRepository | None = None
        self.businesses: SQLBusinessRepository | None = None
        self.contacts: SQLContactRepository | None = None
        self.conversations: SQLConversationRepository | None = None
        self.messages: SQLMessageRepository | None = None
        self.workflows: SQLWorkflowRepository | None = None
        self.events: SQLEventRepository | None = None
        self.appointments: SQLAppointmentRepository | None = None
        self.appointment_slots: SQLAppointmentSlotRepository | None = None
        self.escalations: SQLEscalationRepository | None = None
        self.routing_rules: SQLRoutingRuleRepository | None = None
        self.services: SQLServiceRepository | None = None
        self.staff: SQLStaffRepository | None = None

    @property
    def session(self) -> Session:
        if self._session is None or self._closed:
            raise RuntimeError("UnitOfWork session is only available inside its context")
        return self._session

    def __enter__(self) -> Self:
        if self._entered:
            raise RuntimeError("UnitOfWork cannot be entered more than once")
        self._entered = True
        self._session = self._session_factory()
        try:
            self._session.begin()
            self.tenants = SQLTenantRepository(self._session)
            self.businesses = SQLBusinessRepository(self._session)
            self.contacts = SQLContactRepository(self._session)
            self.conversations = SQLConversationRepository(self._session)
            self.messages = SQLMessageRepository(self._session)
            self.workflows = SQLWorkflowRepository(self._session)
            self.events = SQLEventRepository(self._session)
            self.appointments = SQLAppointmentRepository(self._session)
            self.appointment_slots = SQLAppointmentSlotRepository(self._session)
            self.escalations = SQLEscalationRepository(self._session)
            self.routing_rules = SQLRoutingRuleRepository(self._session)
            self.services = SQLServiceRepository(self._session)
            self.staff = SQLStaffRepository(self._session)
        except BaseException:
            self.close()
            raise
        return self

    def commit(self) -> None:
        """Commit the active transaction; repositories must not call this."""
        if self._finished:
            return
        session = self.session
        try:
            session.commit()
        except BaseException:
            try:
                session.rollback()
            finally:
                self._finished = True
            raise
        self._finished = True

    def rollback(self) -> None:
        """Roll back the active transaction."""
        if self._finished:
            return
        try:
            self.session.rollback()
        finally:
            self._finished = True

    def close(self) -> None:
        """Close this unit's session exactly once."""
        if self._closed:
            return
        self._closed = True
        if self._session is not None:
            self._session.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            if not self._finished:
                if exc_type is None:
                    self.commit()
                else:
                    self.rollback()
        finally:
            self.close()
        return False
