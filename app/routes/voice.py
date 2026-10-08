"""Authenticated, tenant-scoped outbound voice call requests."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import require_role
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueTaskView
from app.queue.service import QueueTaskConflict, QueueAuthorizationError
from app.security import AuthenticatedPrincipal
from app.services.voice_delivery_service import VoiceDeliveryBlocked, VoiceDeliveryService


router = APIRouter(prefix="/voice", tags=["voice"])


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str = Field(min_length=1, max_length=160)
    contact_id: str = Field(min_length=1, max_length=160)


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


__all__ = ["get_voice_delivery_service", "router"]
