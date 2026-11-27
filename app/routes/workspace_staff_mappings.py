"""Owner/admin-only management of business-scoped workspace staff mappings."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.auth.dependencies import get_auth_uow_factory, get_authenticated_principal
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.workspace_staff_mapping_service import (
    WorkspaceStaffMappingConflict,
    WorkspaceStaffMappingNotFound,
    WorkspaceStaffMappingService,
    workspace_user_id_from_principal,
)
from app.workspace_staff_mapping_schemas import (
    WorkspaceStaffMappingCreateRequest,
    WorkspaceStaffMappingListResponse,
    WorkspaceStaffMappingResponse,
)


router = APIRouter(tags=["workspace-staff-mappings"])


def get_workspace_staff_mapping_service() -> WorkspaceStaffMappingService:
    # Staff and mapping rows live with tenant/business data in the regional DB.
    return WorkspaceStaffMappingService(uow_factory=lambda: UnitOfWork(session_factory))


def _authorize_owner_or_admin(
    principal: AuthenticatedPrincipal,
    identity_uow_factory,
    *,
    target_workspace_user_id: UUID | None = None,
) -> UUID:
    actor_id = workspace_user_id_from_principal(principal)
    if actor_id is None:
        raise HTTPException(403, "Workspace owner or admin access is required")

    with identity_uow_factory() as identity_uow:
        actor = identity_uow.workspace_identity.get_user(actor_id)
        membership = identity_uow.workspace_identity.get_membership_for_user_tenant(
            user_id=actor_id, tenant_id=principal.tenant_id,
        )
        if (
            actor is None or actor.status != "active" or actor.email_verified_at is None
            or membership is None or membership.role not in {"owner", "admin"}
        ):
            raise HTTPException(403, "Workspace owner or admin access is required")

        if target_workspace_user_id is not None:
            target = identity_uow.workspace_identity.get_user(target_workspace_user_id)
            target_membership = identity_uow.workspace_identity.get_membership_for_user_tenant(
                user_id=target_workspace_user_id, tenant_id=principal.tenant_id,
            )
            if (
                target is None or target.status != "active" or target.email_verified_at is None
                or target_membership is None
            ):
                raise HTTPException(404, "Workspace user not found")

    return actor_id


@router.get(
    "/businesses/{business_id}/workspace-staff-mappings",
    response_model=WorkspaceStaffMappingListResponse,
)
def list_workspace_staff_mappings(
    business_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    identity_uow_factory=Depends(get_auth_uow_factory),
    service: WorkspaceStaffMappingService = Depends(get_workspace_staff_mapping_service),
):
    _authorize_owner_or_admin(principal, identity_uow_factory)
    try:
        return {"items": service.list_for_business(
            tenant_id=principal.tenant_id, business_id=business_id,
        )}
    except WorkspaceStaffMappingNotFound:
        raise HTTPException(404, "Business not found") from None


@router.post(
    "/businesses/{business_id}/workspace-staff-mappings",
    response_model=WorkspaceStaffMappingResponse,
    status_code=201,
)
def create_workspace_staff_mapping(
    business_id: str,
    request: WorkspaceStaffMappingCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    identity_uow_factory=Depends(get_auth_uow_factory),
    service: WorkspaceStaffMappingService = Depends(get_workspace_staff_mapping_service),
):
    actor_id = _authorize_owner_or_admin(
        principal, identity_uow_factory,
        target_workspace_user_id=request.workspace_user_id,
    )
    try:
        return service.create(
            tenant_id=principal.tenant_id,
            business_id=business_id,
            workspace_user_id=request.workspace_user_id,
            staff_id=request.staff_id,
            actor_workspace_user_id=actor_id,
        )
    except WorkspaceStaffMappingNotFound:
        raise HTTPException(404, "Business or active staff member not found") from None
    except WorkspaceStaffMappingConflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete(
    "/businesses/{business_id}/workspace-staff-mappings/{mapping_id}",
    status_code=204,
)
def remove_workspace_staff_mapping(
    business_id: str,
    mapping_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    identity_uow_factory=Depends(get_auth_uow_factory),
    service: WorkspaceStaffMappingService = Depends(get_workspace_staff_mapping_service),
):
    actor_id = _authorize_owner_or_admin(principal, identity_uow_factory)
    try:
        service.remove(
            tenant_id=principal.tenant_id,
            business_id=business_id,
            mapping_id=mapping_id,
            actor_workspace_user_id=actor_id,
        )
    except WorkspaceStaffMappingNotFound:
        raise HTTPException(404, "Mapping not found") from None
    return Response(status_code=204)
