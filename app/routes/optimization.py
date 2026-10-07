"""Authenticated optimization metrics, feedback, and human review endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_authenticated_principal, require_role
from app.config import settings
from app.optimization.schemas import (
    OptimizationAuditResponse, OptimizationFeedbackRequest,
    OptimizationFeedbackResponse, OptimizationMetricsResponse,
    OptimizationRecommendationView, OptimizationRecommendationsResponse,
    OptimizationRejectRequest, NextBestActionsResponse,
)
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.optimization_service import OptimizationService


router = APIRouter(prefix="/optimization", tags=["optimization"])


def get_optimization_service(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> OptimizationService:
    if not settings.lead_business_id:
        raise HTTPException(status_code=503, detail="Optimization business scope is not configured")
    return OptimizationService(
        trusted_tenant_id=principal.tenant_id,
        trusted_business_id=settings.lead_business_id,
        uow_factory=lambda: UnitOfWork(session_factory),
        minimum_sample=settings.optimization_min_sample_size,
        confidence_threshold=settings.optimization_confidence_threshold,
        dry_run=settings.optimization_dry_run,
    )


@router.get("/metrics", response_model=OptimizationMetricsResponse)
def optimization_metrics(limit: int = Query(default=100, ge=1, le=500),
                         principal: AuthenticatedPrincipal = Depends(require_role("optimization_reader")),
                         service: OptimizationService = Depends(get_optimization_service)):
    return {"items": service.metrics(limit=limit)}


@router.get("/next-best-actions/{lead_id}", response_model=NextBestActionsResponse)
def next_best_actions(
    lead_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("workflow_executor")),
    service: OptimizationService = Depends(get_optimization_service),
):
    try:
        return service.recommend_next_actions(lead_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/recommendations", response_model=OptimizationRecommendationsResponse)
def optimization_recommendations(
    status: str = Query(default="pending", pattern="^(pending|approved|rejected|applied|rolled_back|all)$"),
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(require_role("optimization_reader")),
    service: OptimizationService = Depends(get_optimization_service),
):
    return {"items": service.recommendations(status=None if status == "all" else status,
                                               limit=limit)}


@router.put("/recommendations/{recommendation_id}/approve",
            response_model=OptimizationRecommendationView)
def approve_recommendation(
    recommendation_id: UUID,
    principal: AuthenticatedPrincipal = Depends(require_role("optimization_approver")),
    service: OptimizationService = Depends(get_optimization_service),
):
    try:
        return service.approve(principal, recommendation_id)["recommendation"]
    except LookupError:
        raise HTTPException(status_code=404, detail="Pending recommendation not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.put("/recommendations/{recommendation_id}/reject",
            response_model=OptimizationRecommendationView)
def reject_recommendation(
    recommendation_id: UUID, request: OptimizationRejectRequest,
    principal: AuthenticatedPrincipal = Depends(require_role("optimization_approver")),
    service: OptimizationService = Depends(get_optimization_service),
):
    try:
        return service.reject(principal, recommendation_id, request.rationale)
    except LookupError:
        raise HTTPException(status_code=404, detail="Pending recommendation not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/audit", response_model=OptimizationAuditResponse)
def optimization_audit(limit: int = Query(default=100, ge=1, le=500),
                       principal: AuthenticatedPrincipal = Depends(require_role("optimization_reader")),
                       service: OptimizationService = Depends(get_optimization_service)):
    return {"items": service.audit(limit=limit)}


@router.post("/feedback", response_model=OptimizationFeedbackResponse, status_code=201)
def submit_feedback(
    request: OptimizationFeedbackRequest,
    service: OptimizationService = Depends(get_optimization_service),
    principal: AuthenticatedPrincipal = Depends(require_role("optimization_feedback_writer")),
):
    decision = {key: value for key, value in {
        "variant": request.variant, "action": request.action, "state": request.state,
        "test_group": request.test_group, "channel": request.from_channel,
        "reason_code": request.reason_code,
    }.items() if value is not None}
    event_metadata = {key: value for key, value in {
        "sentiment": request.sentiment, "sentiment_score": request.sentiment_score,
        "response_time_seconds": request.response_time_seconds,
        "resolution_time_seconds": request.resolution_time_seconds,
        "customer_satisfaction": request.customer_satisfaction,
    }.items() if value is not None}
    try:
        row = service.capture_manual_feedback(
            event_key=request.event_key, correlation_id=request.correlation_id,
            event_type=request.event_type, outcome=request.outcome,
            decision=decision, event_metadata=event_metadata,
            lead_id=request.lead_id, campaign_id=request.campaign_id,
            appointment_id=request.appointment_id,
            principal=principal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return OptimizationFeedbackResponse(id=row.id, event_type=row.event_type,
                                       outcome=row.outcome)
