"""HTTP credential extraction and principal resolution before service execution."""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.api_keys import ApiKeyVerifier
from app.auth.principal_resolver import AuthenticationError, AuthorizationError, PrincipalResolver
from app.persistence.session import control_session_factory, session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.request_context import current_request_context
from dataclasses import replace
from app.config import settings
from app.regions.service import RegionAccessDenied, RegionService
from app.regions.catalog import get_region


bearer = HTTPBearer(auto_error=False, scheme_name="ApiKey", description="Provisioned API key")


def get_auth_uow_factory():
    if settings.tenant_region_routing_enabled and not settings.control_database_url:
        raise HTTPException(503, "Tenant directory is unavailable")
    factory = control_session_factory if settings.tenant_region_routing_enabled else session_factory
    return lambda: UnitOfWork(factory)


def require_tenant_home_region(tenant_id: str) -> None:
    """Reject public integration requests that would read a non-local tenant DB."""
    if not settings.tenant_region_routing_enabled:
        return
    if not settings.control_database_url:
        raise HTTPException(503, "Tenant directory is unavailable")
    try:
        with UnitOfWork(control_session_factory) as uow:
            assignment = uow.tenant_database_assignments.get(tenant_id=tenant_id)
    except Exception:
        raise HTTPException(503, "Tenant directory is unavailable") from None
    if assignment is None or assignment.status == "disabled":
        raise HTTPException(403, "Tenant access denied")
    if assignment.status == "migrating":
        raise HTTPException(503, "Tenant data is temporarily unavailable")
    if assignment.status != "active":
        raise HTTPException(403, "Tenant access denied")
    runtime_region = settings.database_region_id or settings.deployment_region_id
    if assignment.home_region_id != runtime_region:
        raise HTTPException(
            421, "Tenant request must be served in its home region",
            headers={"X-Tenant-Home-Region": assignment.home_region_id},
        )


def get_authenticated_principal(
    request: Request,
    credential: HTTPAuthorizationCredentials | None = Depends(bearer),
    uow_factory=Depends(get_auth_uow_factory),
) -> AuthenticatedPrincipal:
    if credential is None:
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
    try:
        with uow_factory() as uow:
            tenant_lookup = (
                uow.tenant_database_assignments
                if settings.tenant_region_routing_enabled else uow.tenants
            )
            principal = PrincipalResolver(
                credentials=ApiKeyVerifier(uow.api_keys),
                tenants=tenant_lookup, grants=uow.api_key_grants,
            ).resolve(credential.credentials)
            if settings.tenant_region_routing_enabled:
                assignment = tenant_lookup.get(tenant_id=principal.tenant_id)
                if assignment is None:
                    raise AuthorizationError("Tenant access denied")
                if assignment.status == "migrating":
                    raise HTTPException(503, "Tenant data is temporarily unavailable")
                if assignment.status != "active":
                    raise AuthorizationError("Tenant access denied")
                try:
                    get_region(assignment.home_region_id)
                except ValueError:
                    raise HTTPException(503, "Tenant data is temporarily unavailable") from None
                runtime_region = settings.database_region_id or settings.deployment_region_id
                if assignment.home_region_id != runtime_region:
                    raise HTTPException(
                        421, "Tenant request must be served in its home region",
                        headers={"X-Tenant-Home-Region": assignment.home_region_id},
                    )
            else:
                RegionService(uow_factory=uow_factory).assert_runtime_data_access(
                    uow=uow, tenant_id=principal.tenant_id,
                    runtime_region=settings.database_region_id or settings.deployment_region_id,
                    actor_id=principal.principal_id,
                )
        if settings.tenant_region_routing_enabled:
            regional_uow_factory = lambda: UnitOfWork(session_factory)
            with regional_uow_factory() as regional_uow:
                RegionService(uow_factory=regional_uow_factory).assert_runtime_data_access(
                    uow=regional_uow, tenant_id=principal.tenant_id,
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
