"""Principal-authorized conversation lifecycle and staff assignment."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.auth.principal_resolver import AuthorizationError
from app.conversation_schemas import ConversationReadResponse
from app.models import Channel, ConversationStatus
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal


class ConversationConflictError(Exception):
    """An invalid transition or stale version prevents an update."""


TRANSITIONS = {"open": {"pending", "closed"}, "pending": {"open", "closed"}, "closed": {"open"}}


class ConversationService:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork],
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self._uow_factory = uow_factory
        self._now = now_provider or (lambda: datetime.now(UTC))

    def _authorize(self, principal: AuthenticatedPrincipal, role: str) -> str:
        if not isinstance(principal, AuthenticatedPrincipal):
            raise AuthorizationError("Conversation access denied")
        now = self._now()
        if (role not in principal.roles or principal.issued_at > now
                or (principal.expires_at is not None and principal.expires_at <= now)):
            raise AuthorizationError("Conversation access denied")
        return principal.tenant_id

    @staticmethod
    def _get(uow, tenant_id, conversation_id):
        row = uow.conversations.get(tenant_id=tenant_id, conversation_id=conversation_id)
        if row is None:
            raise AuthorizationError("Conversation access denied")
        return row

    @staticmethod
    def _response(uow, row):
        assignment = uow.conversation_assignments.get(tenant_id=row.tenant_id, conversation_id=row.id)
        return ConversationReadResponse(
            id=row.id, tenant_id=row.tenant_id, business_id=row.business_id,
            contact_id=row.contact_id, channel=row.channel, status=row.status,
            version=row.version, workflow_state=row.workflow_state,
            assigned_staff_id=assignment.staff_id if assignment else None,
            started_at=row.started_at, updated_at=row.updated_at,
        )

    @staticmethod
    def _event(uow, principal, row, event_type, **details):
        uow.events.add(
            tenant_id=principal.tenant_id, business_id=row.business_id, conversation_id=row.id,
            event_type=event_type, payload={"actor_id": principal.principal_id, **details},
            source="conversation_service", correlation_id=str(row.id),
        )

    def create(self, principal: AuthenticatedPrincipal, *, contact_id: str,
               channel: Channel) -> ConversationReadResponse:
        tenant_id = self._authorize(principal, "conversation_writer")
        channel = Channel(channel)
        with self._uow_factory() as uow:
            contact = uow.contacts.get(tenant_id=tenant_id, contact_id=contact_id)
            if contact is None or uow.businesses.get(tenant_id=tenant_id, business_id=contact.business_id) is None:
                raise AuthorizationError("Conversation access denied")
            row = uow.conversations.create(tenant_id=tenant_id, business_id=contact.business_id,
                                           contact_id=contact.id, channel=channel.value)
            self._event(uow, principal, row, "conversation.created")
            return self._response(uow, row)

    def get(self, principal: AuthenticatedPrincipal, conversation_id: UUID) -> ConversationReadResponse:
        tenant_id = self._authorize(principal, "conversation_reader")
        with self._uow_factory() as uow:
            return self._response(uow, self._get(uow, tenant_id, conversation_id))

    def list(self, principal: AuthenticatedPrincipal, *, offset: int = 0,
             limit: int = 50) -> list[ConversationReadResponse]:
        tenant_id = self._authorize(principal, "conversation_reader")
        with self._uow_factory() as uow:
            return [self._response(uow, row) for row in uow.conversations.list(
                tenant_id=tenant_id, offset=offset, limit=limit,
            )]

    def transition(self, principal: AuthenticatedPrincipal, conversation_id: UUID,
                   *, status: ConversationStatus, expected_version: int) -> ConversationReadResponse:
        tenant_id = self._authorize(principal, "conversation_writer")
        status = ConversationStatus(status).value
        with self._uow_factory() as uow:
            row = self._get(uow, tenant_id, conversation_id)
            if row.status == status:
                return self._response(uow, row)
            if status not in TRANSITIONS[row.status]:
                raise ConversationConflictError("Invalid conversation transition")
            if row.version != expected_version:
                raise ConversationConflictError("Conversation version is stale")
            previous = row.status
            row = uow.conversations.change(tenant_id=tenant_id, conversation_id=row.id,
                                           expected_version=expected_version, status=status)
            if row is None:
                raise ConversationConflictError("Conversation version is stale")
            self._event(uow, principal, row, "conversation.status.changed", previous=previous, status=status)
            return self._response(uow, row)

    def assign(self, principal: AuthenticatedPrincipal, conversation_id: UUID,
               *, staff_id: UUID | None, expected_version: int) -> ConversationReadResponse:
        tenant_id = self._authorize(principal, "conversation_writer")
        with self._uow_factory() as uow:
            row = self._get(uow, tenant_id, conversation_id)
            if row.status == "closed":
                raise ConversationConflictError("Reopen conversation before changing assignment")
            if staff_id is not None:
                eligible = uow.staff.list_active(tenant_id=tenant_id, business_id=row.business_id)
                if not any(staff.id == staff_id for staff in eligible):
                    raise AuthorizationError("Conversation access denied")
            current = uow.conversation_assignments.get(tenant_id=tenant_id, conversation_id=row.id)
            if (current.staff_id if current else None) == staff_id:
                return self._response(uow, row)
            if row.version != expected_version:
                raise ConversationConflictError("Conversation version is stale")
            row = uow.conversations.change(tenant_id=tenant_id, conversation_id=row.id,
                                           expected_version=expected_version, status=row.status)
            if row is None:
                raise ConversationConflictError("Conversation version is stale")
            uow.conversation_assignments.set(tenant_id=tenant_id, business_id=row.business_id,
                conversation_id=row.id, staff_id=staff_id, assigned_by=principal.principal_id)
            self._event(uow, principal, row, "conversation.assignment.changed",
                        staff_id=str(staff_id) if staff_id else None)
            return self._response(uow, row)
