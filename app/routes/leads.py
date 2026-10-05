"""Lead import and tenant/business-scoped read endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_authenticated_principal, require_role
from app.security import AuthenticatedPrincipal

from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import (
    LeadEnrichmentResponse,
    LeadEventResponse,
    LeadImportRequest,
    LeadImportResponse,
    LeadListResponse,
    LeadReadResponse,
    LeadScoresResponse,
    LeadSummaryResponse,
    LeadTransitionResponse,
)
from app.services.lead_import_service import LeadImportService


router = APIRouter(prefix="/leads", tags=["leads"])


def get_lead_import_service(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> LeadImportService:
    return LeadImportService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def _response(detail, *, include_audit: bool) -> LeadReadResponse:
    lead = detail.lead
    return LeadReadResponse(
        id=lead.id,
        tenant_id=lead.tenant_id,
        business_id=lead.business_id,
        contact_id=lead.contact_id,
        source_id=lead.source_id,
        status=lead.status,
        created_at=lead.created_at,
        scores=LeadScoresResponse.model_validate(detail.score) if detail.score else None,
        enrichment=LeadEnrichmentResponse.model_validate(detail.enrichment) if detail.enrichment else None,
        transitions=[LeadTransitionResponse.model_validate(row) for row in detail.transitions] if include_audit else [],
        events=[LeadEventResponse.model_validate(row) for row in detail.events] if include_audit else [],
    )


@router.post("/import", response_model=LeadImportResponse, status_code=201)
def import_lead(
    request: LeadImportRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("lead_writer")),
    service: LeadImportService = Depends(get_lead_import_service),
) -> LeadImportResponse:
    detail = service.import_lead(request)
    return LeadImportResponse(**_response(detail, include_audit=True).model_dump())


@router.get("/{lead_id}", response_model=LeadReadResponse)
def get_lead(
    lead_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("lead_reader")),
    service: LeadImportService = Depends(get_lead_import_service),
) -> LeadReadResponse:
    return _response(service.get(lead_id=lead_id), include_audit=True)


@router.get("", response_model=LeadListResponse)
def list_leads(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal = Depends(require_role("lead_reader")),
    service: LeadImportService = Depends(get_lead_import_service),
) -> LeadListResponse:
    items = [
        LeadSummaryResponse(
            id=item.lead.id,
            contact_id=item.lead.contact_id,
            status=item.lead.status,
            created_at=item.lead.created_at,
            scores=LeadScoresResponse.model_validate(item.score) if item.score else None,
        )
        for item in service.list(offset=offset, limit=limit)
    ]
    return LeadListResponse(items=items, offset=offset, limit=limit)


__all__ = ["get_lead_import_service", "router"]
