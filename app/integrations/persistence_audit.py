"""Persist provider attempt metadata without secrets, addresses, or content."""
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork


class SQLProviderAuditSink:
    def __init__(self, uow_factory=lambda: UnitOfWork(session_factory)):
        self._uow_factory = uow_factory

    def emit(self, event):
        with self._uow_factory() as uow:
            uow.provider_interactions.add(
                tenant_id=event.tenant_id, business_id=event.business_id,
                provider=event.provider, operation=event.operation,
                idempotency_key=event.idempotency_key, outcome=event.outcome,
                error_code=event.error_code,
            )


__all__ = ["SQLProviderAuditSink"]
