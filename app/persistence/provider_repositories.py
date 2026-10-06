"""Durable provider callback and interaction repositories."""
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.persistence.models import ProviderInteractionLog, ProviderWebhookEvent
from app.persistence.repositories import _database_call, _require_tenant


class SQLProviderWebhookRepository:
    def __init__(self, session: Session):
        self._session = session

    def get_by_key(self, *, tenant_id: str, business_id: str, provider: str, event_key: str):
        _require_tenant(tenant_id)
        stmt = select(ProviderWebhookEvent).where(
            ProviderWebhookEvent.tenant_id == tenant_id,
            ProviderWebhookEvent.business_id == business_id,
            ProviderWebhookEvent.provider == provider,
            ProviderWebhookEvent.event_key == event_key,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def record(self, *, tenant_id: str, business_id: str, provider: str,
               event_key: str, event_type: str, payload: dict,
               status: str = "received", result: dict | None = None):
        _require_tenant(tenant_id)
        existing = self.get_by_key(tenant_id=tenant_id, business_id=business_id,
                                   provider=provider, event_key=event_key)
        if existing is not None:
            return existing, False
        row = ProviderWebhookEvent(
            tenant_id=tenant_id, business_id=business_id, provider=provider,
            event_key=event_key, event_type=event_type, payload=payload,
            status=status, result=result or {},
            processed_at=datetime.now(UTC) if status != "received" else None,
        )
        def persist():
            self._session.add(row)
            self._session.flush()
            return row, True
        return _database_call(persist)

    def complete(self, *, tenant_id: str, event_id, status: str, result: dict):
        _require_tenant(tenant_id)
        row = self._session.exec(select(ProviderWebhookEvent).where(
            ProviderWebhookEvent.tenant_id == tenant_id,
            ProviderWebhookEvent.id == event_id,
        )).first()
        if row is None:
            return None
        row.status, row.result, row.processed_at = status, result, datetime.now(UTC)
        self._session.flush()
        return row


class SQLProviderInteractionRepository:
    def __init__(self, session: Session):
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, provider: str,
            operation: str, idempotency_key: str, outcome: str,
            error_code: str | None = None):
        _require_tenant(tenant_id)
        row = ProviderInteractionLog(
            tenant_id=tenant_id, business_id=business_id, provider=provider,
            operation=operation, idempotency_key=idempotency_key,
            outcome=outcome, error_code=error_code,
        )
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)


__all__ = ["SQLProviderInteractionRepository", "SQLProviderWebhookRepository"]
