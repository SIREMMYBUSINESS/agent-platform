"""Authenticated region provisioning, residency policy, and audit APIs."""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import require_role
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.regions.schemas import (
    RegionEnableRequest, RegionFeatureFlagView, RegionInfoView, RegionListResponse,
    RegionPolicyOverrideRequest, RegionPolicyOverrideView, RegionPolicyResponse,
    TenantRegionConfigView, TenantRegionsResponse, RegionFlagRequest,
)
from app.regions.service import RegionService
from app.security import AuthenticatedPrincipal


router = APIRouter(tags=["regions"])


def get_region_service() -> RegionService:
    return RegionService(uow_factory=lambda: UnitOfWork(session_factory))


@router.get("/regions", response_model=RegionListResponse)
def list_regions(principal: AuthenticatedPrincipal = Depends(require_role("region_reader")),
                 service: RegionService = Depends(get_region_service)):
    return {"items": service.list_regions(principal)}


@router.get("/regions/{region_id}/policy", response_model=RegionPolicyResponse)
def get_region_policy(region_id: str,
                      principal: AuthenticatedPrincipal = Depends(require_role("region_reader")),
                      service: RegionService = Depends(get_region_service)):
    try:
        return service.policy(principal, region_id)
    except LookupError:
        raise HTTPException(404, "Tenant region not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/regions/{region_id}/policy-override", response_model=RegionPolicyOverrideView,
             status_code=201)
def add_region_policy_override(
    region_id: str, request: RegionPolicyOverrideRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("region_policy_approver")),
    service: RegionService = Depends(get_region_service),
):
    try:
        return service.add_policy_override(principal, region_id, request)
    except LookupError:
        raise HTTPException(404, "Tenant region not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/tenants/{tenant_id}/regions", response_model=TenantRegionsResponse)
def get_tenant_regions(
    tenant_id: str,
    principal: AuthenticatedPrincipal = Depends(require_role("region_reader")),
    service: RegionService = Depends(get_region_service),
):
    if tenant_id != principal.tenant_id:
        raise HTTPException(403, "Tenant access denied")
    try:
        return service.tenant_regions(principal)
    except LookupError:
        raise HTTPException(404, "Tenant not found") from None


@router.post("/tenants/{tenant_id}/regions/enable", response_model=TenantRegionConfigView)
def enable_tenant_region(
    tenant_id: str, request: RegionEnableRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("region_admin")),
    service: RegionService = Depends(get_region_service),
):
    if tenant_id != principal.tenant_id:
        raise HTTPException(403, "Tenant access denied")
    try:
        return service.enable_region(principal, request)
    except PermissionError:
        raise HTTPException(403, "Region operation denied") from None
    except LookupError:
        raise HTTPException(404, "Tenant not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.put("/regions/{region_id}/feature-flags", response_model=RegionFeatureFlagView)
def set_region_feature_flag(
    region_id: str, request: RegionFlagRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("region_release_manager")),
    service: RegionService = Depends(get_region_service),
):
    try:
        return service.set_feature_flag(principal, region_id=region_id, request=request)
    except PermissionError:
        raise HTTPException(403, "Region operation denied") from None
    except LookupError:
        raise HTTPException(404, "Tenant region not found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/regions/{region_id}/audit")
def get_region_audit(
    region_id: str, limit: int = Query(default=100, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(require_role("region_reader")),
    service: RegionService = Depends(get_region_service),
):
    try:
        return {"items": service.audit(principal, region_id=region_id, limit=limit)}
    except LookupError:
        raise HTTPException(404, "Tenant region not found") from None
