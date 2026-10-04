"""Tenant and business-scoped campaign orchestration endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends

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
)
from app.services.campaign_service import CampaignService
from app.services.enrollment_service import EnrollmentService
from app.services.reply_service import ReplyService
from app.services.campaign_analytics_service import CampaignAnalyticsService


router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def get_campaign_service() -> CampaignService:
    return CampaignService(
        trusted_tenant_id=settings.inbound_tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_enrollment_service() -> EnrollmentService:
    return EnrollmentService(
        trusted_tenant_id=settings.inbound_tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_reply_service() -> ReplyService:
    return ReplyService(
        trusted_tenant_id=settings.inbound_tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


def get_campaign_analytics_service() -> CampaignAnalyticsService:
    return CampaignAnalyticsService(
        trusted_tenant_id=settings.inbound_tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
    )


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
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignResponse:
    return _campaign_response(service.create(request))


@router.post("/{campaign_id}/enroll", response_model=CampaignEnrollResponse)
def enroll_campaign(
    campaign_id: UUID,
    request: CampaignEnrollRequest,
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
    service: EnrollmentService = Depends(get_enrollment_service),
) -> CampaignTriggerResponse:
    return CampaignTriggerResponse(**service.trigger(campaign_id, request))


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(
    campaign_id: UUID,
    service: CampaignService = Depends(get_campaign_service),
) -> CampaignResponse:
    return _campaign_response(service.get(campaign_id=campaign_id))


@router.post("/{campaign_id}/replies", response_model=CampaignReplyResponse, status_code=201)
def process_campaign_reply(
    campaign_id: UUID,
    request: CampaignReplyRequest,
    service: ReplyService = Depends(get_reply_service),
) -> CampaignReplyResponse:
    return CampaignReplyResponse(**service.process(campaign_id, request))


@router.get("/{campaign_id}/analytics", response_model=CampaignAnalyticsResponse)
def get_campaign_analytics(
    campaign_id: UUID,
    service: CampaignAnalyticsService = Depends(get_campaign_analytics_service),
) -> CampaignAnalyticsResponse:
    return CampaignAnalyticsResponse(**service.get(campaign_id))


__all__ = ["get_campaign_service", "get_enrollment_service", "get_reply_service", "get_campaign_analytics_service", "router"]
