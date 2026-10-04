"""Provider-independent identity contracts for application services."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Trusted identity resolved before calling an application service.

    Only authentication infrastructure may construct this from verified
    credentials and trusted provisioning records, never from request fields.
    API-key, JWT, worker, and integration adapters share this contract.
    ``auth_source`` describes provenance; it does not confer permissions.

    Construction validates shape, not authenticity or current validity.
    Authentication must check credential revocation and expiration; services
    must authorize the operation and resource using this principal. Request
    tenant IDs are assertions only, never an authorization source. Roles must
    come from trusted grants (or verified, explicitly trusted issuer claims).
    ``issued_at`` records when this identity context was issued; ``expires_at``
    is its validity bound, if any. Workers must establish their own trusted
    context rather than accepting a serialized principal as proof of identity.
    """

    principal_id: str
    tenant_id: str
    roles: tuple[str, ...]
    auth_source: str
    issued_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("principal_id", "tenant_id", "auth_source"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonblank string")
        if not isinstance(self.roles, tuple) or any(
            not isinstance(role, str) or not role.strip() for role in self.roles
        ):
            raise ValueError("roles must be a tuple of nonblank strings")
        for name in ("issued_at", "expires_at"):
            value = getattr(self, name)
            if name == "expires_at" and value is None:
                continue
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
