"""Resolve a trusted principal without accepting caller-selected tenant scope.

Lookup adapters are server-controlled dependencies. They must share the
application-owned read transaction where backed by repositories; this module
does not create sessions, commit transactions, or provision entities.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.security import AuthenticatedPrincipal


class AuthenticationError(Exception):
    """The supplied credential cannot establish a valid identity."""


class AuthorizationError(Exception):
    """The identity has no valid grant for its provisioned tenant."""


@dataclass(frozen=True)
class VerifiedCredential:
    """Trusted adapter result after verification, containing no credential secret."""

    principal_id: str
    tenant_id: str
    auth_source: str
    issued_at: datetime
    expires_at: datetime | None = None
    revoked: bool = False


@dataclass(frozen=True)
class PrincipalGrant:
    """Administratively provisioned grant, never constructed from public input."""

    principal_id: str
    tenant_id: str
    roles: tuple[str, ...]
    enabled: bool = True
    expires_at: datetime | None = None


class CredentialLookup(Protocol):
    def lookup_verified(self, credential: str) -> VerifiedCredential | None:
        """Verify the full credential and return its trusted mapping or None.

        A public key ID lookup alone is insufficient. API-key adapters must
        verify the secret; JWT adapters must verify signature and claims.
        Never log or persist the supplied raw credential.
        """
        ...


class TenantLookup(Protocol):
    def exists(self, *, tenant_id: str) -> bool: ...


class GrantLookup(Protocol):
    def get(self, *, principal_id: str, tenant_id: str) -> PrincipalGrant | None: ...


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


class PrincipalResolver:
    """Authenticate and resolve tenant membership before application services.

    No request body or resource ID participates in resolution. Roles are
    sourced only from the grant lookup. Resource-level authorization remains
    the service's responsibility, scoped by the returned principal. This
    class does not itself implement API-key cryptography or HTTP handling.
    """

    def __init__(
        self,
        *,
        credentials: CredentialLookup,
        tenants: TenantLookup,
        grants: GrantLookup,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._credentials = credentials
        self._tenants = tenants
        self._grants = grants
        self._now = now_provider or (lambda: datetime.now(UTC))

    def resolve(self, credential: str) -> AuthenticatedPrincipal:
        if not isinstance(credential, str) or not credential.strip():
            raise AuthenticationError("Invalid credential")
        now = self._now()
        if not _aware(now):
            raise ValueError("Resolver clock must return a timezone-aware datetime")
        identity = self._credentials.lookup_verified(credential)
        if identity is None or identity.revoked:
            raise AuthenticationError("Invalid credential")
        if any(not isinstance(value, str) or not value.strip() for value in (
            identity.principal_id, identity.tenant_id, identity.auth_source,
        )):
            raise AuthenticationError("Invalid credential")
        if not _aware(identity.issued_at) or identity.issued_at > now:
            raise AuthenticationError("Invalid credential")
        if identity.expires_at is not None and (
            not _aware(identity.expires_at) or identity.expires_at <= now
        ):
            raise AuthenticationError("Invalid credential")

        if not self._tenants.exists(tenant_id=identity.tenant_id):
            raise AuthorizationError("Tenant access denied")
        grant = self._grants.get(
            principal_id=identity.principal_id, tenant_id=identity.tenant_id,
        )
        if (
            grant is None or not grant.enabled
            or grant.principal_id != identity.principal_id
            or grant.tenant_id != identity.tenant_id
            or not isinstance(grant.roles, tuple) or not grant.roles
            or any(not isinstance(role, str) or not role.strip() for role in grant.roles)
        ):
            raise AuthorizationError("Tenant access denied")
        if grant.expires_at is not None and (
            not _aware(grant.expires_at) or grant.expires_at <= now
        ):
            raise AuthorizationError("Tenant access denied")
        expirations = [value for value in (identity.expires_at, grant.expires_at)
                       if value is not None]
        return AuthenticatedPrincipal(
            principal_id=identity.principal_id,
            tenant_id=identity.tenant_id,
            roles=grant.roles,
            auth_source=identity.auth_source,
            issued_at=now,
            expires_at=min(expirations) if expirations else None,
        )
