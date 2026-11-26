"""Authenticated, tenant-scoped outbound voice call requests."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import get_auth_uow_factory, require_role, require_tenant_home_region
from app.auth.voice_gateway import require_voice_gateway
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueTaskView
from app.queue.service import QueueTaskConflict, QueueAuthorizationError
from app.security import AuthenticatedPrincipal
from app.services.voice_delivery_service import VoiceDeliveryBlocked, VoiceDeliveryService
from app.services.voice_number_resolver import VoiceNumberNotFound, VoiceNumberResolver


router = APIRouter(prefix="/voice", tags=["voice"])


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str = Field(min_length=1, max_length=160)
    contact_id: str = Field(min_length=1, max_length=160)


class ResolveNumberResponse(BaseModel):
    """Server-resolved scope and inbound budget for a dialed number."""
    model_config = ConfigDict(extra="forbid")
    tenant_id: str
    business_id: str
    voice_calls_per_minute: int = Field(ge=1, le=60)


class VoiceRateLimitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calls_per_minute: int = Field(ge=1, le=60)


def get_voice_number_resolver(
    directory_uow_factory=Depends(get_auth_uow_factory),
) -> VoiceNumberResolver:
    return VoiceNumberResolver(
        directory_uow_factory=directory_uow_factory,
        business_uow_factory=lambda: UnitOfWork(session_factory),
        region_guard=lambda tenant_id: require_tenant_home_region(tenant_id),
    )


def get_voice_delivery_service() -> VoiceDeliveryService:
    return VoiceDeliveryService(
        uow_factory=lambda: UnitOfWork(session_factory), provider=None,
    )


@router.post("/calls", response_model=QueueTaskView, status_code=202)
def request_outbound_call(
    request: OutboundCallRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=160),
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: VoiceDeliveryService = Depends(get_voice_delivery_service),
) -> QueueTaskView:
    try:
        return service.enqueue(
            principal, business_id=request.business_id, contact_id=request.contact_id,
            idempotency_key=idempotency_key,
        )
    except VoiceDeliveryBlocked as exc:
        raise HTTPException(403, "Call is not permitted for this contact and business scope") from exc
    except QueueTaskConflict as exc:
        raise HTTPException(409, "Idempotency key was already used for different call data") from exc
    except QueueAuthorizationError as exc:
        raise HTTPException(403, "Call is not permitted for this tenant and business scope") from exc


@router.get(
    "/resolve-number", response_model=ResolveNumberResponse,
    responses={404: {"description": "Number is not provisioned for voice"}},
)
def resolve_number(
    response: Response,
    to: str = Query(pattern=r"^\+[1-9][0-9]{6,14}$", description="Dialed number in E.164 format"),
    _gateway: None = Depends(require_voice_gateway),
    resolver: VoiceNumberResolver = Depends(get_voice_number_resolver),
) -> ResolveNumberResponse:
    """Resolve a dialed number to its tenant and business for the voice gateway."""
    response.headers["Cache-Control"] = "no-store"
    try:
        resolved = resolver.resolve(to)
    except VoiceNumberNotFound:
        raise HTTPException(404, "Voice number is not provisioned") from None
    return ResolveNumberResponse(
        tenant_id=resolved.tenant_id, business_id=resolved.business_id,
        voice_calls_per_minute=resolved.voice_calls_per_minute,
    )


@router.put("/businesses/{business_id}/voice-rate-limit")
def set_business_voice_rate_limit(
    business_id: str,
    request: VoiceRateLimitRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("business_onboarder")),
) -> dict[str, int | str]:
    """Set the tenant-scoped per-business inbound call budget."""
    with UnitOfWork(session_factory) as uow:
        business = uow.businesses.get(
            tenant_id=principal.tenant_id, business_id=business_id,
        )
        if business is None:
            raise HTTPException(404, "Business not found")
        business.voice_calls_per_minute = request.calls_per_minute
    return {
        "business_id": business_id,
        "voice_calls_per_minute": request.calls_per_minute,
    }


__all__ = ["get_voice_delivery_service", "get_voice_number_resolver", "router"]
