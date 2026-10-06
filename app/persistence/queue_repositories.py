"""Persistence port for durable queued work; transactions belong to UnitOfWork."""

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import update
from sqlmodel import Session, select

from app.persistence.models import QueueTaskRecord
from app.persistence.repositories import _database_call, _require_tenant


class QueueTaskRepository(Protocol):
    def enqueue(self, **values) -> tuple[QueueTaskRecord, bool]: ...
    def get(self, *, tenant_id: str, business_id: str, task_id: UUID, lock: bool = False) -> QueueTaskRecord | None: ...
    def get_by_key(self, *, tenant_id: str, business_id: str, idempotency_key: str) -> QueueTaskRecord | None: ...
    def list_due(self, *, now: datetime, stale_before: datetime, limit: int) -> list[QueueTaskRecord]: ...
    def claim(self, *, task_id: UUID, now: datetime, lease: timedelta) -> QueueTaskRecord | None: ...
    def mark_dispatched(self, *, task_id: UUID, now: datetime, stale_before: datetime) -> bool: ...
    def succeed(self, *, task_id: UUID, now: datetime, result: dict) -> QueueTaskRecord | None: ...
    def fail_or_retry(self, *, task_id: UUID, now: datetime, error_code: str, retry_at: datetime) -> QueueTaskRecord | None: ...
    def redrive(self, *, tenant_id: str, business_id: str, task_id: UUID, now: datetime) -> QueueTaskRecord | None: ...


class SQLQueueTaskRepository:
    def __init__(self, session: Session):
        self._session = session

    def get(self, *, tenant_id: str, business_id: str, task_id: UUID,
            lock: bool = False) -> QueueTaskRecord | None:
        _require_tenant(tenant_id)
        statement = select(QueueTaskRecord).where(
            QueueTaskRecord.tenant_id == tenant_id,
            QueueTaskRecord.business_id == business_id,
            QueueTaskRecord.id == task_id,
        )
        if lock:
            statement = statement.with_for_update()
        return _database_call(lambda: self._session.exec(statement).first())

    def get_by_key(self, *, tenant_id: str, business_id: str,
                   idempotency_key: str) -> QueueTaskRecord | None:
        _require_tenant(tenant_id)
        statement = select(QueueTaskRecord).where(
            QueueTaskRecord.tenant_id == tenant_id,
            QueueTaskRecord.business_id == business_id,
            QueueTaskRecord.idempotency_key == idempotency_key,
        )
        return _database_call(lambda: self._session.exec(statement).first())

    def enqueue(self, *, tenant_id: str, business_id: str, task_name: str,
                domain: str, idempotency_key: str, aggregate_type: str,
                aggregate_id: str, payload: dict, correlation_id: str,
                traceparent: str | None, available_at: datetime,
                max_attempts: int) -> tuple[QueueTaskRecord, bool]:
        _require_tenant(tenant_id)
        def persist():
            existing = self._session.exec(select(QueueTaskRecord).where(
                QueueTaskRecord.tenant_id == tenant_id,
                QueueTaskRecord.business_id == business_id,
                QueueTaskRecord.idempotency_key == idempotency_key,
            ).with_for_update()).first()
            if existing is not None:
                same = (existing.domain == domain and existing.task_name == task_name
                        and existing.aggregate_type == aggregate_type
                        and existing.aggregate_id == aggregate_id and existing.payload == payload
                        and existing.max_attempts == max_attempts)
                if not same:
                    raise ValueError("idempotency key conflicts with different queued work")
                return existing, False
            row = QueueTaskRecord(
                tenant_id=tenant_id, business_id=business_id, domain=domain,
                task_name=task_name, idempotency_key=idempotency_key,
                aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                payload=payload, correlation_id=correlation_id,
                traceparent=traceparent, available_at=available_at,
                max_attempts=max_attempts,
            )
            self._session.add(row)
            self._session.flush()
            return row, True
        return _database_call(persist)

    def list_due(self, *, now: datetime, stale_before: datetime,
                 limit: int) -> list[QueueTaskRecord]:
        statement = select(QueueTaskRecord).where(
            (
                QueueTaskRecord.status.in_(("pending", "retrying"))
                & (QueueTaskRecord.available_at <= now)
            )
            | (
                (QueueTaskRecord.status == "running")
                & (QueueTaskRecord.started_at <= stale_before)
            ),
        ).order_by(QueueTaskRecord.available_at, QueueTaskRecord.created_at).limit(limit)
        return _database_call(lambda: list(self._session.exec(statement).all()))

    def claim(self, *, task_id: UUID, now: datetime, lease: timedelta) -> QueueTaskRecord | None:
        def persist():
            row = self._session.exec(select(QueueTaskRecord).where(
                QueueTaskRecord.id == task_id,
            ).with_for_update()).first()
            if row is None or row.status in {"succeeded", "dead_letter"}:
                return None
            started_at = _aware_utc(row.started_at)
            available_at = _aware_utc(row.available_at)
            if row.status == "running" and started_at is not None and started_at > now - lease:
                return None
            if (row.status in {"pending", "queued", "retrying"}
                    and available_at is not None and available_at > now):
                return None
            row.status = "running"
            row.attempts += 1
            row.started_at = now
            row.error_code = None
            self._session.flush()
            return row
        return _database_call(persist)

    def mark_dispatched(self, *, task_id: UUID, now: datetime, stale_before: datetime) -> bool:
        statement = update(QueueTaskRecord).where(
            QueueTaskRecord.id == task_id,
            (QueueTaskRecord.status.in_(("pending", "retrying")))
            | ((QueueTaskRecord.status == "running")
               & (QueueTaskRecord.started_at <= stale_before)),
        ).values(status="queued", dispatched_at=now)
        return _database_call(lambda: self._session.exec(statement).rowcount == 1)

    def succeed(self, *, task_id: UUID, now: datetime, result: dict) -> QueueTaskRecord | None:
        row = self._session.get(QueueTaskRecord, task_id)
        if row is None or row.status != "running":
            return None
        row.status = "succeeded"
        row.completed_at = now
        row.result = result
        row.error_code = None
        self._session.flush()
        return row

    def fail_or_retry(self, *, task_id: UUID, now: datetime, error_code: str,
                      retry_at: datetime) -> QueueTaskRecord | None:
        row = self._session.get(QueueTaskRecord, task_id)
        if row is None or row.status != "running":
            return None
        row.error_code = error_code[:80]
        row.status = "dead_letter" if row.attempts >= row.max_attempts else "retrying"
        row.available_at = retry_at
        row.completed_at = now if row.status == "dead_letter" else None
        self._session.flush()
        return row

    def redrive(self, *, tenant_id: str, business_id: str, task_id: UUID,
                now: datetime) -> QueueTaskRecord | None:
        row = self.get(tenant_id=tenant_id, business_id=business_id, task_id=task_id, lock=True)
        if row is None or row.status != "dead_letter":
            return None
        row.status = "pending"
        row.attempts = 0
        row.available_at = now
        row.dispatched_at = row.started_at = row.completed_at = None
        row.redriven_at = now
        row.error_code = None
        self._session.flush()
        return row


__all__ = ["QueueTaskRepository", "SQLQueueTaskRepository"]


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
