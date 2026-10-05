"""Tenant scoped workflow executions and follow up task repositories."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlmodel import Session, select

from app.persistence.models import FollowUpTask, WorkflowExecution
from app.persistence.repositories import _database_call, _require_tenant


class WorkflowExecutionRepository(Protocol):
    def get_by_key(self, *, tenant_id: str, idempotency_key: str, lock: bool = False) -> WorkflowExecution | None: ...
    def start(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
              workflow_name: str, idempotency_key: str, input_hash: str) -> WorkflowExecution: ...
    def finish(self, *, tenant_id: str, idempotency_key: str,
               result: dict) -> WorkflowExecution | None: ...
    def fail(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
             workflow_name: str, idempotency_key: str, input_hash: str,
             error_code: str) -> WorkflowExecution: ...


class SQLWorkflowExecutionRepository:
    def __init__(self, session: Session): self._session = session

    def get_by_key(self, *, tenant_id: str, idempotency_key: str,
                   lock: bool = False) -> WorkflowExecution | None:
        _require_tenant(tenant_id)
        statement = select(WorkflowExecution).where(
            WorkflowExecution.tenant_id == tenant_id,
            WorkflowExecution.idempotency_key == idempotency_key,
        )
        if lock:
            statement = statement.with_for_update()
        return _database_call(lambda: self._session.exec(statement).first())

    def start(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
              workflow_name: str, idempotency_key: str, input_hash: str) -> WorkflowExecution:
        _require_tenant(tenant_id)
        def persist():
            row = self.get_by_key(tenant_id=tenant_id, idempotency_key=idempotency_key, lock=True)
            if row is None:
                row = WorkflowExecution(tenant_id=tenant_id, business_id=business_id,
                    conversation_id=conversation_id, workflow_name=workflow_name,
                    idempotency_key=idempotency_key, input_hash=input_hash)
                self._session.add(row)
            else:
                row.status = "running"
                row.attempts += 1
                row.error_code = None
                row.updated_at = datetime.now(UTC)
            self._session.flush()
            return row
        return _database_call(persist)

    def finish(self, *, tenant_id: str, idempotency_key: str,
               result: dict) -> WorkflowExecution | None:
        _require_tenant(tenant_id)
        row = self.get_by_key(tenant_id=tenant_id, idempotency_key=idempotency_key, lock=True)
        if row is None:
            return None
        row.status = "succeeded"
        row.result = result
        row.error_code = None
        row.updated_at = row.completed_at = datetime.now(UTC)
        self._session.flush()
        return row

    def fail(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
             workflow_name: str, idempotency_key: str, input_hash: str,
             error_code: str) -> WorkflowExecution:
        _require_tenant(tenant_id)
        row = self.get_by_key(tenant_id=tenant_id, idempotency_key=idempotency_key)
        if row is None:
            row = WorkflowExecution(tenant_id=tenant_id, business_id=business_id,
                conversation_id=conversation_id, workflow_name=workflow_name,
                idempotency_key=idempotency_key, input_hash=input_hash,
                status="failed", attempts=1, error_code=error_code)
            self._session.add(row)
        else:
            row.status = "failed"
            row.attempts += 1
            row.error_code = error_code
            row.updated_at = datetime.now(UTC)
        self._session.flush()
        return row


class FollowUpRepository(Protocol):
    def get_by_key(self, *, tenant_id: str, idempotency_key: str) -> FollowUpTask | None: ...
    def create(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
               scheduled_at: datetime, reason: str, workflow_state: str,
               idempotency_key: str) -> FollowUpTask: ...
    def list_due(self, *, tenant_id: str, business_id: str, now: datetime, limit: int) -> list[FollowUpTask]: ...


class SQLFollowUpRepository:
    def __init__(self, session: Session): self._session = session

    def get_by_key(self, *, tenant_id: str, idempotency_key: str) -> FollowUpTask | None:
        _require_tenant(tenant_id)
        return _database_call(lambda: self._session.exec(select(FollowUpTask).where(
            FollowUpTask.tenant_id == tenant_id,
            FollowUpTask.idempotency_key == idempotency_key,
        )).first())

    def create(self, *, tenant_id: str, business_id: str, conversation_id: UUID,
               scheduled_at: datetime, reason: str, workflow_state: str,
               idempotency_key: str) -> FollowUpTask:
        _require_tenant(tenant_id)
        def persist():
            existing = self.get_by_key(tenant_id=tenant_id, idempotency_key=idempotency_key)
            if existing is not None:
                return existing
            row = FollowUpTask(tenant_id=tenant_id, business_id=business_id,
                conversation_id=conversation_id, scheduled_at=scheduled_at, reason=reason,
                workflow_state=workflow_state, idempotency_key=idempotency_key)
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_due(self, *, tenant_id: str, business_id: str,
                 now: datetime, limit: int = 100) -> list[FollowUpTask]:
        _require_tenant(tenant_id)
        statement = select(FollowUpTask).where(
            FollowUpTask.tenant_id == tenant_id, FollowUpTask.business_id == business_id,
            FollowUpTask.status == "scheduled", FollowUpTask.scheduled_at <= now,
        ).order_by(FollowUpTask.scheduled_at, FollowUpTask.id).limit(limit)
        return _database_call(lambda: list(self._session.exec(statement).all()))
