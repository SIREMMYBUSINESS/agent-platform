"""Deterministic, explainable lead scoring for the Phase 2A foundation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


def _bounded_score(value: Any, fallback: int) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return fallback


@dataclass(frozen=True)
class LeadScoreResult:
    fit_score: int
    urgency_score: int
    intent_score: int
    priority_score: int
    rationale: dict[str, Any]

    def persistence_values(self) -> dict[str, Any]:
        return asdict(self)


class LeadScoringService:
    """Scores enrichment signals without requiring an LLM or remote service."""

    qualification_threshold = 65

    def score(self, enrichment: dict[str, Any] | None,
              weights: dict[str, float] | None = None) -> LeadScoreResult:
        enrichment = enrichment or {}
        fit_signal = enrichment.get("lead_fit_signal") or {}
        company = enrichment.get("company_info") or {}
        profile = enrichment.get("public_profile_data") or {}
        website = enrichment.get("website_metadata") or {}
        fit_fallback = 70 if company or profile or website else 35
        fit = _bounded_score(fit_signal.get("fit_score"), fit_fallback)
        urgency = _bounded_score(fit_signal.get("urgency_score"), 10)
        intent = _bounded_score(fit_signal.get("intent_score"), 20)
        normalized_weights = {"fit_score": 0.4, "urgency_score": 0.3, "intent_score": 0.3}
        if weights is not None:
            candidate = {key: float(weights.get(key, 0)) for key in normalized_weights}
            total = sum(candidate.values())
            if total <= 0 or any(value < 0 for value in candidate.values()):
                raise ValueError("lead score weights must be nonnegative with a positive sum")
            normalized_weights = {key: value / total for key, value in candidate.items()}
        priority = round(fit * normalized_weights["fit_score"]
                         + urgency * normalized_weights["urgency_score"]
                         + intent * normalized_weights["intent_score"])
        engagement = fit_signal.get("engagement_score")
        recency = fit_signal.get("recency_score")
        if engagement is not None or recency is not None:
            engagement_score = _bounded_score(engagement, 0)
            recency_score = _bounded_score(recency, 0)
            priority = round(priority * 0.8 + engagement_score * 0.1 + recency_score * 0.1)
            formula = "base_priority*0.8 + engagement*0.1 + recency*0.1"
        else:
            formula = ("weighted_fit + weighted_urgency + weighted_intent" if weights is not None
                       else "fit*0.4 + urgency*0.3 + intent*0.3")
        return LeadScoreResult(
            fit_score=fit,
            urgency_score=urgency,
            intent_score=intent,
            priority_score=priority,
            rationale={
                "formula": formula,
                "weights": normalized_weights,
                "qualification_threshold": self.qualification_threshold,
            },
        )


__all__ = ["LeadScoreResult", "LeadScoringService"]
