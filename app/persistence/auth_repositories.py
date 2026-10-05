"""Read-only authentication repositories using the owning Unit of Work session."""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.auth.principal_resolver import PrincipalGrant, VerifiedCredential
from app.persistence.models import ApiKey, ApiKeyGrant


def _utc(value: datetime | None) -> datetime | None:
    # SQLite drops timezone information; persisted authentication timestamps are UTC.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class SQLApiKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_hash(self, key_hash: str) -> tuple[str, VerifiedCredential] | None:
        row = self._session.exec(select(ApiKey).where(ApiKey.key_hash == key_hash)).first()
        if row is None:
            return None
        return row.key_hash, VerifiedCredential(
            principal_id=row.id, tenant_id=row.tenant_id, auth_source="api_key",
            issued_at=_utc(row.issued_at), expires_at=_utc(row.expires_at), revoked=row.revoked,
        )


class SQLApiKeyGrantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, principal_id: str, tenant_id: str) -> PrincipalGrant | None:
        row = self._session.exec(select(ApiKeyGrant).where(
            ApiKeyGrant.principal_id == principal_id, ApiKeyGrant.tenant_id == tenant_id,
        )).first()
        if row is None:
            return None
        roles = tuple(row.roles) if isinstance(row.roles, list) else ()
        return PrincipalGrant(row.principal_id, row.tenant_id, roles, row.enabled, _utc(row.expires_at))
