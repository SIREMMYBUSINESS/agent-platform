"""Application boundary for recording optimization inputs and outcomes."""
from __future__ import annotations

from uuid import UUID

from app.optimization.feedback import FeedbackEventCapture
from app.optimization.segments import normalized_segment


class OptimizationTracker:
    def __init__(self, capture: FeedbackEventCapture | None = None) -> None:
        self._capture = capture or FeedbackEventCapture()

    def record_lead_score(self, *, uow, tenant_id: str, business_id: str,
                          lead_id: UUID, score_id: UUID, correlation_id: str,
                          fit_score: float, urgency_score: float, intent_score: float,
                          source: str, industry: str, country: str = "unknown",
                          weights: dict | None = None, rollout_percentage: int = 0,
                          in_rollout: bool = False):
        segment = normalized_segment(source=source, industry=industry, country=country)
        safe_values = dict(part.split("=", 1) for part in segment.split(";"))
        return self._capture.capture(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=f"lead-scored:{lead_id}:{score_id}",
            correlation_id=correlation_id, event_type="lead_scored", outcome="scored",
            lead_id=lead_id,
            decision={"fit_score": fit_score, "urgency_score": urgency_score,
                      "intent_score": intent_score, **safe_values},
            event_metadata={"weights": weights or {},
                            "rollout_percentage": rollout_percentage,
                            "in_rollout": in_rollout},
        )

    def record_outcome(self, *, uow, tenant_id: str, business_id: str,
                       event_key: str, correlation_id: str, event_type: str,
                       outcome: str, lead_id: UUID | None = None,
                       campaign_id: UUID | None = None,
                       decision: dict | None = None,
                       event_metadata: dict | None = None,
                       occurred_at=None):
        return self._capture.capture(
            uow=uow, tenant_id=tenant_id, business_id=business_id,
            event_key=event_key, correlation_id=correlation_id,
            event_type=event_type, outcome=outcome, lead_id=lead_id,
            campaign_id=campaign_id, decision=decision or {},
            event_metadata=event_metadata or {}, occurred_at=occurred_at,
        )
