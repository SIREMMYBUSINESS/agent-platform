"""Authenticated outbound voice call requests and platform number lookup."""
from __future__ import annotations

import hmac
import hashlib
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import require_role, require_tenant_home_region
from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.persistence.models import VoiceNumberRoute
from sqlmodel import select
from app.queue.contracts import QueueTaskView
from app.queue.service import QueueTaskConflict, QueueAuthorizationError
from app.security import AuthenticatedPrincipal
from app.services.voice_delivery_service import VoiceDeliveryBlocked, VoiceDeliveryService

router = APIRouter(prefix="/voice", tags=["voice"])
_E164_PATTERN = r"^\+[1-9][0-9]{6,14}$"


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str = Field(min_length=1, max_length=160)
    contact_id: str = Field(min_length=1, max_length=160)


class VoiceNumberMappingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=160)
    business_id: str = Field(min_length=1, max_length=160)


def get_voice_delivery_service() -> VoiceDeliveryService:
    return VoiceDeliveryService(
        uow_factory=lambda: UnitOfWork(session_factory), provider=None,
    )


def _authorize_voice_gateway(authorization: str | None) -> None:
    expected = settings.voice_gateway_key_sha256
    scheme, sep, raw = (authorization or "").partition(" ")
    if not expected or len(expected) != 64 or not sep or scheme.casefold() != "bearer" or not raw:
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})
    supplied_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(supplied_digest, expected.lower()):
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})


@router.get("/resolve-number", response_model=VoiceNumberMappingResponse)
def resolve_inbound_number(
    to: str = Query(pattern=_E164_PATTERN, min_length=8, max_length=16),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> VoiceNumberMappingResponse:
    """Resolve an exact enabled E.164 route for the voice gateway."""
    _authorize_voice_gateway(authorization)
    try:
        with session_factory() as session:
            row = session.exec(select(VoiceNumberRoute).where(
                VoiceNumberRoute.phone_number == to, VoiceNumberRoute.enabled == True  # noqa: E712
            )).first()
            if row is None:
                raise HTTPException(404, "Voice number is not configured")
            result = {"tenant_id": row.tenant_id, "business_id": row.business_id}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Voice number lookup unavailable") from None
    require_tenant_home_region(result["tenant_id"])
    return VoiceNumberMappingResponse.model_validate(result)


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


__all__ = [
    "get_voice_delivery_service",
    "get_voice_number_lookup_service",
    "router",
]
