"""HTTP credential extraction and principal resolution before service execution."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.api_keys import ApiKeyVerifier
from app.auth.principal_resolver import AuthenticationError, AuthorizationError, PrincipalResolver
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.request_context import current_request_context
from dataclasses import replace
from app.config import settings
from app.regions.service import RegionAccessDenied, RegionService


bearer = HTTPBearer(auto_error=False, scheme_name="ApiKey", description="Provisioned API key")


def get_auth_uow_factory():
    return lambda: UnitOfWork(session_factory)


def get_authenticated_principal(
    request: Request,
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    uow_factory=Depends(get_auth_uow_factory),
) -> AuthenticatedPrincipal:
    if credential is None:
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
    try:
        with uow_factory() as uow:
            principal = PrincipalResolver(
                credentials=ApiKeyVerifier(uow.api_keys),
                tenants=uow.tenants, grants=uow.api_key_grants,
            ).resolve(credential.credentials)
            RegionService(uow_factory=uow_factory).assert_runtime_data_access(
                uow=uow, tenant_id=principal.tenant_id,
                runtime_region=settings.database_region_id or settings.deployment_region_id,
                actor_id=principal.principal_id,
            )
        context = current_request_context.get()
        if context is not None:
            current_request_context.set(replace(context, tenant_id=principal.tenant_id))
        request.state.principal_tenant_id = principal.tenant_id
        return principal
    except AuthenticationError:
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"}) from None
    except AuthorizationError:
        raise HTTPException(403, "Tenant access denied") from None
    except RegionAccessDenied:
        raise HTTPException(403, "Tenant data residency access denied") from None


def get_optional_authenticated_principal(
    request: Request,
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    uow_factory=Depends(get_auth_uow_factory),
) -> AuthenticatedPrincipal | None:
    """Resolve credentials when supplied; capability-token routes may be public."""
    if credential is None:
        return None
    return get_authenticated_principal(request=request, credential=credential, uow_factory=uow_factory)


def require_role(role: str):
    """Require an explicit role; resource lookups still enforce tenant scope."""
    def dependency(
        principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    ) -> AuthenticatedPrincipal:
        if role not in principal.roles:
            raise HTTPException(403, "Operation not permitted")
        return principal
    return dependency
