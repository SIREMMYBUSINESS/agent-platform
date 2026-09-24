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

app = FastAPI(title=settings.app_name)


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

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/inbound", response_model=InboundResponse)
def inbound(
    request: InboundRequest,
    service: InboundService = Depends(get_inbound_service),
) -> InboundResponse:
    return InboundResponse.model_validate(service.handle(request))
