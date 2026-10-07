"""Authenticated vertical template, onboarding, intake, and recall endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from uuid import UUID

from app.auth.dependencies import require_role
from app.config import settings
from app.integrations.provider_support import DirectoryTenantConfigResolver
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.verticals.outreach import DentalOutreachService
from app.verticals.schemas import (
    DentalOnboardingRequest, DentalOverdueOutreachRequest, HealthCheckResponse,
    OnboardingResponse, VerticalIntakeAnswerRequest, VerticalIntakeResponse,
    VerticalIntakeStartRequest,
)
from app.verticals.service import (
    VerticalAuthorizationError, VerticalOnboardingService, VerticalProvisioningError,
)


router = APIRouter(tags=["vertical-onboarding"])


def _uow_factory():
    return UnitOfWork(session_factory)


def get_vertical_service():
    resolver = None
    if settings.provider_secrets_dir is not None:
        try:
            resolver = DirectoryTenantConfigResolver(settings.provider_secrets_dir)
        except Exception:
            resolver = None
    return VerticalOnboardingService(uow_factory=_uow_factory,
                                     provider_config_resolver=resolver)


@router.get("/verticals")
def list_verticals(principal: AuthenticatedPrincipal = Depends(require_role("business_onboarder")),
                   service: VerticalOnboardingService = Depends(get_vertical_service)):
    return {"items": service.catalog(principal)}


@router.get("/verticals/{vertical_id}/template")
def get_vertical_template(vertical_id: str,
                          principal: AuthenticatedPrincipal = Depends(require_role("business_onboarder")),
                          service: VerticalOnboardingService = Depends(get_vertical_service)):
    try:
        return service.template(principal, vertical_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/onboarding/businesses", response_model=OnboardingResponse, status_code=201)
def onboard_business(request: DentalOnboardingRequest,
                     principal: AuthenticatedPrincipal = Depends(require_role("business_onboarder")),
                     service: VerticalOnboardingService = Depends(get_vertical_service)):
    try:
        return service.onboard(principal, request)
    except VerticalAuthorizationError:
        raise HTTPException(403, "Vertical onboarding denied") from None
    except VerticalProvisioningError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/businesses/{business_id}/onboarding/health", response_model=HealthCheckResponse)
def onboarding_health(business_id: str,
                      principal: AuthenticatedPrincipal = Depends(require_role("business_onboarder")),
                      service: VerticalOnboardingService = Depends(get_vertical_service)):
    try:
        return service.health_check(principal, business_id=business_id)
    except VerticalAuthorizationError:
        raise HTTPException(403, "Vertical onboarding denied") from None
    except VerticalProvisioningError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/businesses/{business_id}/onboarding/go-live")
def go_live(business_id: str,
            principal: AuthenticatedPrincipal = Depends(require_role("business_onboarder")),
            service: VerticalOnboardingService = Depends(get_vertical_service)):
    try:
        return service.activate(principal, business_id=business_id)
    except VerticalAuthorizationError:
        raise HTTPException(403, "Vertical onboarding denied") from None
    except VerticalProvisioningError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/businesses/{business_id}/vertical-intakes",
             response_model=VerticalIntakeResponse, status_code=201)
def start_vertical_intake(business_id: str, request: VerticalIntakeStartRequest,
                           principal: AuthenticatedPrincipal = Depends(require_role("workflow_executor")),
                           service: VerticalOnboardingService = Depends(get_vertical_service)):
    try:
        return service.start_intake(principal, business_id=business_id,
            contact_id=request.contact_id, channel=request.channel,
            idempotency_key=request.idempotency_key)
    except VerticalAuthorizationError:
        raise HTTPException(403, "Vertical intake denied") from None
    except VerticalProvisioningError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/businesses/{business_id}/vertical-intakes/{session_id}/answers",
             response_model=VerticalIntakeResponse)
def answer_vertical_intake(business_id: str, session_id: UUID,
                           request: VerticalIntakeAnswerRequest,
                           principal: AuthenticatedPrincipal = Depends(require_role("workflow_executor")),
                           service: VerticalOnboardingService = Depends(get_vertical_service)):
    try:
        return service.answer_intake(principal, business_id=business_id,
            session_id=session_id, answer=request.answer,
            idempotency_key=request.idempotency_key)
    except VerticalAuthorizationError:
        raise HTTPException(403, "Vertical intake denied") from None
    except VerticalProvisioningError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/businesses/{business_id}/vertical-outreach/enroll-overdue")
def enroll_overdue_patients(business_id: str, request: DentalOverdueOutreachRequest,
                            principal: AuthenticatedPrincipal = Depends(require_role("campaign_writer")),
                            service: DentalOutreachService = Depends(
                                lambda: DentalOutreachService(uow_factory=_uow_factory))):
    try:
        return service.enroll_overdue(principal, business_id=business_id,
            campaign_id=request.campaign_id,
            months_since_visit=request.months_since_visit, limit=request.limit)
    except PermissionError:
        raise HTTPException(403, "Vertical campaign operation denied") from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
