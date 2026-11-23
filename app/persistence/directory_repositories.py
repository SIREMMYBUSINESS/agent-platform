"""Read-only access to tenant routing assignments in the control directory."""

from sqlmodel import Session, select

from app.persistence.models import TenantDatabaseAssignment
from app.persistence.repositories import _database_call


class SQLTenantDatabaseAssignmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str) -> TenantDatabaseAssignment | None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return None
        return _database_call(lambda: self._session.exec(select(TenantDatabaseAssignment).where(
            TenantDatabaseAssignment.tenant_id == tenant_id,
        )).first())

    def exists(self, *, tenant_id: str) -> bool:
        return self.get(tenant_id=tenant_id) is not None


__all__ = ["SQLTenantDatabaseAssignmentRepository"]
