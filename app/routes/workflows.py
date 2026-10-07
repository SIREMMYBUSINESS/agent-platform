"""Tenant-scoped appointment workflow actions."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Query

from app.auth.dependencies import (
    get_authenticated_principal, get_optional_authenticated_principal, require_role,
)
from app.config import settings
from app.integrations.bootstrap_example import build_calendar_provider
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import (
    AppointmentCancelRequest, AppointmentWorkflowRequest, AppointmentWorkflowResponse,
    ContactAppointmentActionRequest, ContactAppointmentCancelRequest,
    EscalationResolveRequest,
    EscalationReadResponse,
)
from app.security import AuthenticatedPrincipal
from app.services.appointment_workflow_service import (
    AppointmentWorkflowError, AppointmentWorkflowService,
)
from app.services.escalation_workflow_service import EscalationWorkflowService
from app.services.campaign_analytics_service import CampaignAnalyticsService
from app.schemas import BusinessFunnelResponse
from app.services.reply_service import CampaignReplyNotFoundError

router = APIRouter(tags=["workflows"])


@router.get("/businesses/{business_id}/funnel", response_model=BusinessFunnelResponse)
def business_funnel(
    business_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
):
    service = CampaignAnalyticsService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=None,
        uow_factory=lambda: UnitOfWork(session_factory),
    )
    try:
        return service.business_funnel(business_id=business_id, roles=principal.roles)
    except CampaignReplyNotFoundError as exc:
        raise HTTPException(404, "Business not found") from exc


@router.get("/escalations", response_model=list[EscalationReadResponse])
def list_escalations(
    status: str | None = Query(default="open", pattern="^(open|in_progress|resolved|escalated_to_human)$"),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(require_role("workflow_executor")),
):
    return EscalationWorkflowService(
        uow_factory=lambda: UnitOfWork(session_factory),
    ).list_tickets(principal, status=status, offset=offset, limit=limit)


def service_for(principal: AuthenticatedPrincipal):
    calendar_provider = build_calendar_provider() if settings.provider_secrets_dir else None
    return AppointmentWorkflowService(
        trusted_tenant_id=principal.tenant_id,
        uow_factory=lambda: UnitOfWork(session_factory),
        calendar_provider=calendar_provider,
    )


@router.post("/workflows/confirm-appointment", response_model=AppointmentWorkflowResponse)
def confirm_appointment(
    request: AppointmentWorkflowRequest,
    principal: AuthenticatedPrincipal | None = Depends(get_optional_authenticated_principal),
):
    try:
        if request.token is not None:
            return AppointmentWorkflowService(
                trusted_tenant_id="contact-token",
                uow_factory=lambda: UnitOfWork(session_factory),
                calendar_provider=build_calendar_provider() if settings.provider_secrets_dir else None,
            ).confirm_contact_token(request.token, request.slot_number)
        if principal is None:
            raise HTTPException(401, "Invalid credential")
        if "workflow_executor" not in principal.roles:
            raise HTTPException(403, "Operation not permitted")
        return service_for(principal).confirm(
            principal, request.appointment_id, slot_number=request.slot_number,
        )
    except LookupError as exc:
        raise HTTPException(404, "Appointment not found") from exc
    except AppointmentWorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/appointments/{appointment_id}", response_model=AppointmentWorkflowResponse)
def get_appointment(
    appointment_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("workflow_executor")),
):
    try:
        return service_for(principal).get(principal, appointment_id)
    except LookupError as exc:
        raise HTTPException(404, "Appointment not found") from exc


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentWorkflowResponse)
def cancel_appointment(
    appointment_id: UUID, request: AppointmentCancelRequest,
    principal: AuthenticatedPrincipal | None = Depends(get_optional_authenticated_principal),
):
    try:
        if request.token is not None:
            result = AppointmentWorkflowService(
                trusted_tenant_id="contact-token",
                uow_factory=lambda: UnitOfWork(session_factory),
                calendar_provider=build_calendar_provider() if settings.provider_secrets_dir else None,
            ).cancel_contact_token(request.token, request.reason)
            if result.id != appointment_id:
                raise LookupError("appointment not found")
            return result
        if principal is None:
            raise HTTPException(401, "Invalid credential")
        if "workflow_executor" not in principal.roles:
            raise HTTPException(403, "Operation not permitted")
        return service_for(principal).cancel(principal, appointment_id, request.reason)
    except LookupError as exc:
        raise HTTPException(404, "Appointment not found") from exc
    except AppointmentWorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/contact/appointments/confirm", response_model=AppointmentWorkflowResponse)
def confirm_contact_appointment(request: ContactAppointmentActionRequest):
    service = AppointmentWorkflowService(
        trusted_tenant_id="contact-token", uow_factory=lambda: UnitOfWork(session_factory),
        calendar_provider=build_calendar_provider() if settings.provider_secrets_dir else None,
    )
    try:
        return service.confirm_contact_token(request.token, request.slot_number)
    except LookupError as exc:
        raise HTTPException(404, "Appointment action not found") from exc
    except AppointmentWorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/contact/appointments/cancel", response_model=AppointmentWorkflowResponse)
def cancel_contact_appointment(request: ContactAppointmentCancelRequest):
    service = AppointmentWorkflowService(
        trusted_tenant_id="contact-token", uow_factory=lambda: UnitOfWork(session_factory),
        calendar_provider=build_calendar_provider() if settings.provider_secrets_dir else None,
    )
    try:
        return service.cancel_contact_token(request.token, request.reason)
    except LookupError as exc:
        raise HTTPException(404, "Appointment action not found") from exc
    except AppointmentWorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/escalations/{ticket_id}/resolve")
def resolve_escalation(
    ticket_id: UUID, request: EscalationResolveRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("workflow_executor")),
):
    try:
        service = EscalationWorkflowService(
            uow_factory=lambda: UnitOfWork(session_factory),
        )
        return service.resolve(
            principal, ticket_id, resolution=request.resolution,
            response_to_contact=request.response_to_contact,
        )
    except LookupError as exc:
        raise HTTPException(404, "Escalation not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


__all__ = ["router"]
