"""HTTP credential extraction and principal resolution before service execution."""

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.api_keys import ApiKeyVerifier
from app.auth.principal_resolver import AuthenticationError, AuthorizationError, PrincipalResolver
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal


bearer = HTTPBearer(auto_error=False, scheme_name="ApiKey", description="Provisioned API key")


def get_auth_uow_factory():
    return lambda: UnitOfWork(session_factory)


def get_authenticated_principal(
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    uow_factory=Depends(get_auth_uow_factory),
) -> AuthenticatedPrincipal:
    if credential is None:
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
    try:
        with uow_factory() as uow:
            return PrincipalResolver(
                credentials=ApiKeyVerifier(uow.api_keys),
                tenants=uow.tenants, grants=uow.api_key_grants,
            ).resolve(credential.credentials)
    except AuthenticationError:
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"}) from None
    except AuthorizationError:
        raise HTTPException(403, "Tenant access denied") from None


def get_optional_authenticated_principal(
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    uow_factory=Depends(get_auth_uow_factory),
) -> AuthenticatedPrincipal | None:
    """Resolve credentials when supplied; capability-token routes may be public."""
    if credential is None:
        return None
    return get_authenticated_principal(credential=credential, uow_factory=uow_factory)


def require_role(role: str):
    """Require an explicit role; resource lookups still enforce tenant scope."""
    def dependency(
        principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        if role not in principal.roles:
            raise HTTPException(403, "Operation not permitted")
        return principal
    return dependency
