"""Authenticated outbound voice call requests and platform number lookup."""
from __future__ import annotations

import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import require_role, require_tenant_home_region
from app.config import settings
from app.integrations.provider_support import DirectoryTenantConfigResolver
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueTaskView
from app.queue.service import QueueTaskConflict, QueueAuthorizationError
from app.security import AuthenticatedPrincipal
from app.services.voice_delivery_service import VoiceDeliveryBlocked, VoiceDeliveryService
from app.services.voice_number_lookup_service import (
    VoiceNumberLookupAmbiguous,
    VoiceNumberLookupService,
)

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


def get_voice_number_lookup_service() -> VoiceNumberLookupService:
    if settings.provider_secrets_dir is None:
        raise HTTPException(503, "Voice number lookup is not configured")
    return VoiceNumberLookupService(
        uow_factory=lambda: UnitOfWork(session_factory),
        config_resolver=DirectoryTenantConfigResolver(settings.provider_secrets_dir),
    )


def _authorize_voice_number_lookup(authorization: str | None) -> None:
    """Require a dedicated platform-wide secret; tenant API keys are not global."""
    expected = settings.voice_number_lookup_token
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        not expected
        or not separator
        or scheme.casefold() != "bearer"
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(401, "Invalid credential", headers={"WWW-Authenticate": "Bearer"})


@router.get("/resolve-number", response_model=VoiceNumberMappingResponse)
def resolve_inbound_number(
    to: str = Query(pattern=_E164_PATTERN, min_length=8, max_length=16),
    authorization: str | None = Header(default=None, alias="Authorization"),
    service: VoiceNumberLookupService = Depends(get_voice_number_lookup_service),
) -> VoiceNumberMappingResponse:
    """Resolve an exact Twilio E.164 destination from trusted business config.

    This endpoint is for the Cloudflare voice Worker only. Its dedicated
    platform-wide token is configured as VOICE_NUMBER_LOOKUP_TOKEN; a normal
    tenant API key cannot safely perform cross-tenant number resolution.
    """
    _authorize_voice_number_lookup(authorization)
    try:
        mapping = service.resolve(dialed_number=to)
    except VoiceNumberLookupAmbiguous:
        raise HTTPException(503, "Voice number mapping is unavailable") from None
    except ValueError:
        raise HTTPException(422, "Invalid dialed number") from None
    except Exception:
        # Do not expose database, secret-file, or tenant details to the caller.
        raise HTTPException(503, "Voice number lookup is unavailable") from None

    if mapping is None:
        raise HTTPException(404, "Voice number is not configured")

    # Honor the platform's tenant home-region guard before exposing the mapping.
    require_tenant_home_region(mapping["tenant_id"])
    return VoiceNumberMappingResponse.model_validate(mapping)


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
