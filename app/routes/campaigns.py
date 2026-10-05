"""Tenant and business-scoped campaign orchestration endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import get_authenticated_principal, require_role
from app.security import AuthenticatedPrincipal

from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import (
    CampaignCreateRequest,
    CampaignEnrollRequest,
    CampaignEnrollResponse,
    CampaignEnrollmentResponse,
    CampaignEventResponse,
    CampaignResponse,
    CampaignStepResponse,
    CampaignSequenceResponse,
    CampaignStepRunResponse,
    CampaignTriggerRequest,
    CampaignTriggerResponse,
    CampaignReplyRequest,
    CampaignReplyResponse,
    CampaignAnalyticsResponse,
    CampaignStatusResponse,
    RecipientSelectionRequest, RecipientSelectionResponse,
    OutreachScheduleRequest, OutreachMessageResponse, OutreachFailureRequest,
)
from app.services.campaign_service import CampaignService
from app.services.enrollment_service import EnrollmentService
from app.services.reply_service import ReplyService
from app.services.campaign_analytics_service import CampaignAnalyticsService
from app.services.recipient_selection import RecipientSelectionService
from app.services.outreach_execution import OutreachExecutionService


router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def get_campaign_service(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> CampaignService:
    return CampaignService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_enrollment_service(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> EnrollmentService:
    return EnrollmentService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_reply_service(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> ReplyService:
    return ReplyService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_campaign_analytics_service(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> CampaignAnalyticsService:
    return CampaignAnalyticsService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_recipient_selection_service() -> RecipientSelectionService:
    return RecipientSelectionService(uow_factory=lambda: UnitOfWork(session_factory))


def get_outreach_execution_service() -> OutreachExecutionService:
    # Provider adapters are injected by deployment wiring. With no provider
    # configured execution records a failed attempt safely and remains retryable.
    return OutreachExecutionService(uow_factory=lambda: UnitOfWork(session_factory))


def _campaign_response(details) -> CampaignResponse:
    campaign = details.campaign
    return CampaignResponse(
        id=campaign.id,
        tenant_id=campaign.tenant_id,
        business_id=campaign.business_id,
        name=campaign.name,
        channel=campaign.channel,
        audience_filters=campaign.audience_filters,
        status=campaign.status,
        sequences=[
            CampaignSequenceResponse(
                id=sequence.id,
                name=sequence.name,
                steps=[CampaignStepResponse(
                    id=step.id,
                    step_order=step.step_order,
                    delay_seconds=step.delay_seconds,
                    template=step.template_data,
                ) for step in steps],
            ) for sequence, steps in details.sequences
        ],
        enrollments=[CampaignEnrollmentResponse.model_validate(row) for row in details.enrollments],
        events=[CampaignEventResponse.model_validate(row) for row in details.events],
        step_runs=[CampaignStepRunResponse.model_validate(row) for row in details.step_runs],
    )


@router.post("", response_model=CampaignResponse, status_code=201)
def create_campaign(
    request: CampaignCreateRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignResponse:
    return _campaign_response(service.create(request, principal=principal))


@router.post("/{campaign_id}/activate", response_model=CampaignStatusResponse)
def activate_campaign(
    campaign_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignStatusResponse:
    campaign = service.activate(principal, campaign_id)
    return CampaignStatusResponse(id=campaign.id, status=campaign.status)


@router.post("/{campaign_id}/pause", response_model=CampaignStatusResponse)
def pause_campaign(
    campaign_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignStatusResponse:
    campaign = service.pause(principal, campaign_id)
    return CampaignStatusResponse(id=campaign.id, status=campaign.status)


@router.post("/{campaign_id}/archive", response_model=CampaignStatusResponse)
def archive_campaign(
    campaign_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignStatusResponse:
    campaign = service.archive(principal, campaign_id)
    return CampaignStatusResponse(id=campaign.id, status=campaign.status)


@router.post("/{campaign_id}/recipients/select", response_model=RecipientSelectionResponse)
def select_campaign_recipients(
    campaign_id: UUID, request: RecipientSelectionRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: RecipientSelectionService = Depends(get_recipient_selection_service),
) -> RecipientSelectionResponse:
    selection = service.select(principal, campaign_id,
                               contact_ids=request.contact_ids, limit=request.limit)
    return RecipientSelectionResponse(campaign_id=selection.campaign_id,
                                      contact_ids=list(selection.contact_ids),
                                      excluded_counts=selection.excluded_counts)


@router.post("/{campaign_id}/messages", response_model=OutreachMessageResponse, status_code=201)
def schedule_campaign_message(
    campaign_id: UUID, request: OutreachScheduleRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: OutreachExecutionService = Depends(get_outreach_execution_service),
) -> OutreachMessageResponse:
    try:
        row = service.schedule_send(
            principal, campaign_id=campaign_id, contact_id=request.contact_id,
            conversation_id=request.conversation_id, idempotency_key=request.idempotency_key,
            content=request.content.model_dump(exclude_none=True), scheduled_at=request.scheduled_at,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return OutreachMessageResponse.model_validate(row, from_attributes=True)


@router.post("/messages/{message_id}/execute", response_model=OutreachMessageResponse)
def execute_campaign_message(
    message_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: OutreachExecutionService = Depends(get_outreach_execution_service),
) -> OutreachMessageResponse:
    return OutreachMessageResponse.model_validate(service.execute_send(principal, message_id), from_attributes=True)


@router.post("/messages/{message_id}/retry", response_model=OutreachMessageResponse)
def retry_campaign_message(
    message_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: OutreachExecutionService = Depends(get_outreach_execution_service),
) -> OutreachMessageResponse:
    return OutreachMessageResponse.model_validate(service.retry_send(principal, message_id), from_attributes=True)


@router.post("/messages/{message_id}/delivered", response_model=OutreachMessageResponse)
def mark_campaign_message_delivered(
    message_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: OutreachExecutionService = Depends(get_outreach_execution_service),
) -> OutreachMessageResponse:
    return OutreachMessageResponse.model_validate(service.mark_delivered(principal, message_id), from_attributes=True)


@router.post("/messages/{message_id}/failed", response_model=OutreachMessageResponse)
def mark_campaign_message_failed(
    message_id: UUID, request: OutreachFailureRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: OutreachExecutionService = Depends(get_outreach_execution_service),
) -> OutreachMessageResponse:
    return OutreachMessageResponse.model_validate(
        service.mark_failed(principal, message_id, failure_reason=request.failure_reason),
        from_attributes=True,
    )


@router.post("/{campaign_id}/enroll", response_model=CampaignEnrollResponse)
def enroll_campaign(
    campaign_id: UUID,
    request: CampaignEnrollRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: EnrollmentService = Depends(get_enrollment_service),
) -> CampaignEnrollResponse:
    enrollments = service.enroll(campaign_id, request)
    return CampaignEnrollResponse(
        campaign_id=campaign_id,
        enrollments=[CampaignEnrollmentResponse.model_validate(row) for row in enrollments],
    )


@router.post("/{campaign_id}/trigger", response_model=CampaignTriggerResponse)
def trigger_campaign(
    campaign_id: UUID,
    request: CampaignTriggerRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: EnrollmentService = Depends(get_enrollment_service),
) -> CampaignTriggerResponse:
    return CampaignTriggerResponse(**service.trigger(campaign_id, request))


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_reader")),
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignResponse:
    return _campaign_response(service.get(principal, campaign_id=campaign_id))


@router.post("/{campaign_id}/replies", response_model=CampaignReplyResponse, status_code=201)
def process_campaign_reply(
    campaign_id: UUID,
    request: CampaignReplyRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
    service: ReplyService = Depends(get_reply_service),
) -> CampaignReplyResponse:
    return CampaignReplyResponse(**service.process(campaign_id, request))


@router.get("/{campaign_id}/analytics", response_model=CampaignAnalyticsResponse)
def get_campaign_analytics(
    campaign_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("campaign_reader")),
    service: CampaignAnalyticsService = Depends(get_campaign_analytics_service),
) -> CampaignAnalyticsResponse:
    return CampaignAnalyticsResponse(**service.get(campaign_id))


__all__ = ["get_campaign_service", "get_enrollment_service", "get_reply_service", "get_campaign_analytics_service", "router"]
