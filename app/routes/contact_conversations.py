"""Tenant- and business-scoped read-only contact conversation endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import require_role
from app.config import settings
from app.contact_timeline_schemas import ContactConversationsResponse, ContactTimelineResponse
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.contact_timeline_service import (
    ContactTimelineConfigurationError,
    ContactTimelineNotFoundError,
    ContactTimelineService,
)


router = APIRouter(prefix="/contacts", tags=["contact-conversations"])


def get_contact_timeline_service() -> ContactTimelineService:
    return ContactTimelineService(
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def _translate_error(exc: Exception):
    if isinstance(exc, ContactTimelineNotFoundError):
        raise HTTPException(status_code=404, detail="Contact not found") from None
    if isinstance(exc, ContactTimelineConfigurationError):
        raise HTTPException(status_code=503, detail="Contact conversation reads are not configured") from None
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail="Invalid timeline cursor or pagination") from None
    raise exc


@router.get("/{contact_id}/conversations", response_model=ContactConversationsResponse)
def list_contact_conversations(
    contact_id: str,
    principal: AuthenticatedPrincipal = Depends(require_role("conversation_reader")),
    service: ContactTimelineService = Depends(get_contact_timeline_service),
) -> ContactConversationsResponse:
    try:
        return service.list_conversations(principal, contact_id=contact_id)
    except (ContactTimelineNotFoundError, ContactTimelineConfigurationError) as exc:
        _translate_error(exc)


@router.get("/{contact_id}/timeline", response_model=ContactTimelineResponse)
def get_contact_timeline(
    contact_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    principal: AuthenticatedPrincipal = Depends(require_role("conversation_reader")),
    service: ContactTimelineService = Depends(get_contact_timeline_service),
) -> ContactTimelineResponse:
    try:
        return service.timeline(principal, contact_id=contact_id, limit=limit, cursor=cursor)
    except (ContactTimelineNotFoundError, ContactTimelineConfigurationError, ValueError) as exc:
        _translate_error(exc)


__all__ = ["get_contact_timeline_service", "router"]
