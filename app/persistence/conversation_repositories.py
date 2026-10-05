"""Tenant-scoped current assignment storage; transaction ownership stays in UoW."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlmodel import Session, select

from app.persistence.models import ConversationAssignment
from app.persistence.repositories import _database_call, _require_tenant


class ConversationAssignmentRepository(Protocol):
    def get(self, *, tenant_id: str, conversation_id: UUID) -> ConversationAssignment | None: ...
    def set(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
            staff_id: UUID | None, assigned_by: str) -> ConversationAssignment: ...


class SQLConversationAssignmentRepository:
    def __init__(self, session: Session):
        self._session = session

    def get(self, *, tenant_id: str, conversation_id: UUID) -> ConversationAssignment | None:
        _require_tenant(tenant_id)
        return _database_call(lambda: self._session.exec(select(ConversationAssignment).where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.conversation_id == conversation_id,
        )).first())

    def set(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
            staff_id: UUID | None, assigned_by: str) -> ConversationAssignment:
        """Caller must first claim the conversation version in the same transaction."""
        _require_tenant(tenant_id)
        def persist():
            row = self.get(tenant_id=tenant_id, conversation_id=conversation_id)
            if row is None:
                row = ConversationAssignment(
                    tenant_id=tenant_id, business_id=business_id,
                    conversation_id=conversation_id, staff_id=staff_id, assigned_by=assigned_by,
                )
                self._session.add(row)
            else:
                row.staff_id = staff_id
                row.assigned_by = assigned_by
                row.updated_at = datetime.now(UTC)
            self._session.flush()
            return row
        return _database_call(persist)
