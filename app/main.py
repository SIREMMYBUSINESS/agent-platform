from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

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

app = FastAPI(title=settings.app_name)
app.include_router(lead_router)
app.include_router(campaign_router)


def get_inbound_service() -> InboundService:
    """Build a service with trusted tenant context and lazy persistence access."""
    return InboundService(
        trusted_tenant_id=settings.inbound_tenant_id,
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

@app.post("/inbound", response_model=InboundResponse)
def inbound(
    request: InboundRequest,
    service: InboundService = Depends(get_inbound_service),
) -> InboundResponse:
    return InboundResponse.model_validate(service.handle(request))
