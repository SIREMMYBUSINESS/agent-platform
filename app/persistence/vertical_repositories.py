"""Tenant scoped repositories for reusable vertical configurations and intakes."""
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlmodel import Session, select

from app.persistence.models import BusinessVerticalProfile, VerticalIntakeSession
from app.persistence.repositories import _database_call, _require_tenant


class SQLVerticalRepository:
    def __init__(self, session: Session):
        self._session = session

    def profile(self, *, tenant_id: str, business_id: str):
        _require_tenant(tenant_id)
        return _database_call(lambda: self._session.get(
            BusinessVerticalProfile, (tenant_id, business_id),
        ))

    def put_profile(self, *, tenant_id: str, business_id: str, vertical_id: str,
                    config: dict, created_by: str):
        _require_tenant(tenant_id)
        row = self.profile(tenant_id=tenant_id, business_id=business_id)
        if row is None:
            row = BusinessVerticalProfile(tenant_id=tenant_id, business_id=business_id,
                vertical_id=vertical_id, config=config, created_by=created_by)
            self._session.add(row)
        else:
            row.vertical_id, row.config = vertical_id, config
            row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row

    def set_status(self, *, tenant_id: str, business_id: str, status: str):
        _require_tenant(tenant_id)
        row = self.profile(tenant_id=tenant_id, business_id=business_id)
        if row is None:
            return None
        row.status = status
        row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row

    def intake_by_key(self, *, tenant_id: str, business_id: str,
                      idempotency_key: str, lock: bool = False):
        _require_tenant(tenant_id)
        stmt = select(VerticalIntakeSession).where(
            VerticalIntakeSession.tenant_id == tenant_id,
            VerticalIntakeSession.business_id == business_id,
            VerticalIntakeSession.idempotency_key == idempotency_key,
        )
        if lock:
            stmt = stmt.with_for_update()
        return _database_call(lambda: self._session.exec(stmt).first())

    def intake(self, *, tenant_id: str, business_id: str, session_id: UUID,
               lock: bool = False):
        _require_tenant(tenant_id)
        stmt = select(VerticalIntakeSession).where(
            VerticalIntakeSession.tenant_id == tenant_id,
            VerticalIntakeSession.business_id == business_id,
            VerticalIntakeSession.id == session_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        return _database_call(lambda: self._session.exec(stmt).first())

    def create_intake(self, *, tenant_id: str, business_id: str, contact_id: str,
                      conversation_id: UUID, idempotency_key: str):
        _require_tenant(tenant_id)
        row = VerticalIntakeSession(tenant_id=tenant_id, business_id=business_id,
            contact_id=contact_id, conversation_id=conversation_id,
            idempotency_key=idempotency_key)
        self._session.add(row)
        self._session.flush()
        return row

    def save_answer(self, *, tenant_id: str, business_id: str, session_id: UUID,
                    answer_key: str, question_index: int, answer: str,
                    status: str, idempotency_key: str):
        _require_tenant(tenant_id)
        row = self.intake(tenant_id=tenant_id, business_id=business_id,
                          session_id=session_id, lock=True)
        if row is None:
            return None
        answer_digest = sha256(answer.encode("utf-8")).hexdigest()
        previous = row.answer_idempotency.get(idempotency_key)
        if previous is not None:
            if previous != answer_digest:
                raise ValueError("answer idempotency key was reused with different content")
            return row
        row.answers = {**row.answers, answer_key: answer}
        row.answer_idempotency = {**row.answer_idempotency, idempotency_key: answer_digest}
        row.question_index = question_index
        row.status = status
        row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row
