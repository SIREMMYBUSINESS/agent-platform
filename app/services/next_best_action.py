"""Lead routing facade that consumes a persisted policy decision."""

from dataclasses import dataclass

from app.services.lead_scoring_service import LeadScoreResult, LeadScoringService
from app.services.policy_service import NextBestActionDecision, PolicyDecision, PolicyService


@dataclass(frozen=True)
class NextBestActionResult:
    score: LeadScoreResult
    decision: NextBestActionDecision


class NextBestActionService:
    def __init__(self, *, scoring: LeadScoringService | None = None,
                 policy: PolicyService | None = None) -> None:
        self._scoring = scoring or LeadScoringService()
        self._policy = policy or PolicyService()

    def select(self, *, enrichment: dict | None, policy_decision: PolicyDecision,
               contactable: bool, business_open: bool,
               appointment_available: bool, campaign_capacity: bool,
               allowed_channels: tuple[str, ...]) -> NextBestActionResult:
        score = self._scoring.score(enrichment)
        decision = self._policy.next_best_action(
            priority_score=score.priority_score, urgency_score=score.urgency_score,
            outreach_allowed=policy_decision.allowed, contactable=contactable,
            business_open=business_open, appointment_available=appointment_available,
            campaign_capacity=campaign_capacity, allowed_channels=allowed_channels,
        )
        return NextBestActionResult(score, decision)


__all__ = ["NextBestActionResult", "NextBestActionService"]
