from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse

from app.auth.dependencies import get_authenticated_principal, require_role
from app.security import AuthenticatedPrincipal
from app.auth.principal_resolver import AuthorizationError
from app.routes.conversations import router as conversation_router
from app.services.conversation_service import ConversationConflictError
from app.services.outreach_execution import (
    OutreachConflict, OutreachNotEligible, OutreachNotFound, RateLimitExceeded,
)
from app.services.recipient_selection import RecipientSelectionError

from app.config import settings
from app.schemas import InboundRequest, InboundResponse
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.services.inbound import (
    InboundProvisioningError,
    InboundService,
    InboundTenantMismatchError,
)
from app.routes.leads import router as lead_router
from app.services.lead_import_service import (
    LeadConfigurationError,
    LeadProvisioningError,
    LeadTenantMismatchError,
)
from app.routes.campaigns import router as campaign_router
from app.services.campaign_service import (
    CampaignConfigurationError,
    CampaignNotFoundError,
    CampaignProvisioningError,
    CampaignStateError,
    CampaignTenantMismatchError,
)
from app.services.enrollment_service import CampaignEligibilityError
from app.services.reply_service import CampaignReplyNotFoundError, CampaignReplyTenantMismatchError
from app.observability.logging import configure_structured_logging
from app.observability.middleware import ObservabilityMiddleware
from app.observability.metrics import metrics

configure_structured_logging(settings.log_level)
app = FastAPI(title=settings.app_name)
app.add_middleware(ObservabilityMiddleware)
app.include_router(lead_router)
app.include_router(campaign_router)
app.include_router(conversation_router)


@app.exception_handler(AuthorizationError)
async def authorization_error_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Access denied"})


@app.exception_handler(ConversationConflictError)
async def conversation_conflict_handler(request: Request, exc: ConversationConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OutreachConflict)
async def outreach_conflict_handler(request: Request, exc: OutreachConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(OutreachNotFound)
async def outreach_not_found_handler(request: Request, exc: OutreachNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Outreach resource not found"})


@app.exception_handler(OutreachNotEligible)
async def outreach_not_eligible_handler(request: Request, exc: OutreachNotEligible) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "Recipient is not eligible for outreach"})


@app.exception_handler(RateLimitExceeded)
async def outreach_rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(RecipientSelectionError)
async def recipient_selection_handler(request: Request, exc: RecipientSelectionError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Campaign not found or unavailable"})


def get_inbound_service(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> InboundService:
    """Build a service with trusted tenant context and lazy persistence access."""
    return InboundService(
        trusted_tenant_id=principal.tenant_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


@app.exception_handler(InboundTenantMismatchError)
async def inbound_tenant_mismatch_handler(
    request: Request, exc: InboundTenantMismatchError
) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(InboundProvisioningError)
async def inbound_provisioning_handler(
    request: Request, exc: InboundProvisioningError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(LeadTenantMismatchError)
async def lead_tenant_mismatch_handler(request: Request, exc: LeadTenantMismatchError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(LeadProvisioningError)
async def lead_provisioning_handler(request: Request, exc: LeadProvisioningError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(LeadConfigurationError)
async def lead_configuration_handler(request: Request, exc: LeadConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(CampaignTenantMismatchError)
async def campaign_tenant_mismatch_handler(request: Request, exc: CampaignTenantMismatchError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(CampaignProvisioningError)
async def campaign_provisioning_handler(request: Request, exc: CampaignProvisioningError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(CampaignNotFoundError)
async def campaign_not_found_handler(request: Request, exc: CampaignNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(CampaignConfigurationError)
async def campaign_configuration_handler(request: Request, exc: CampaignConfigurationError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(CampaignStateError)
async def campaign_state_handler(request: Request, exc: CampaignStateError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(CampaignEligibilityError)
async def campaign_eligibility_handler(request: Request, exc: CampaignEligibilityError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(CampaignReplyTenantMismatchError)
async def campaign_reply_tenant_mismatch_handler(request: Request, exc: CampaignReplyTenantMismatchError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(CampaignReplyNotFoundError)
async def campaign_reply_not_found_handler(request: Request, exc: CampaignReplyNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics_endpoint(
    principal: AuthenticatedPrincipal = Depends(require_role("metrics_reader")),
) -> PlainTextResponse:
    """Prometheus scrape output; protected and free of tenant/contact labels."""
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

@app.post("/inbound", response_model=InboundResponse)
def inbound(
    request: InboundRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("inbound_writer")),
    service: InboundService = Depends(get_inbound_service),
) -> InboundResponse:
    return InboundResponse.model_validate(service.handle(request))
