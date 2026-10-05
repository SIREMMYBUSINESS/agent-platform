"""Deterministic escalation decisions and tenant-scoped escalation sweep."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from app.auth.principal_resolver import AuthorizationError
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.workflows.receptionist import ReceptionistActions, WorkflowResult


@dataclass(frozen=True)
class EscalationDecision:
    reason: str
    priority: Literal["normal", "high", "urgent"]
    idempotency_suffix: str


@dataclass(frozen=True)
class EscalationRules:
    timeout_after: timedelta = timedelta(minutes=30)
    unassigned_after: timedelta = timedelta(minutes=5)

    def __post_init__(self):
        if self.timeout_after <= timedelta(0) or self.unassigned_after <= timedelta(0):
            raise ValueError("escalation thresholds must be positive")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def decide(self, *, conversation, assignment, events, now: datetime) -> EscalationDecision | None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        now = now.astimezone(UTC)
        failed_events = [event for event in events if event.event_type == "outbound.delivery.failed"]
        if failed_events:
            failure = failed_events[-1]
            return EscalationDecision("outbound delivery failed", "high", str(failure.id))
        age = now - self._utc(conversation.updated_at)
        if assignment is None and age >= self.unassigned_after:
            return EscalationDecision("conversation remained unassigned", "normal",
                                      f"unassigned:{conversation.started_at.isoformat()}")
        if age >= self.timeout_after:
            return EscalationDecision("conversation response timed out", "high",
                                      f"timeout:{conversation.started_at.isoformat()}")
        return None


class EscalationService:
    """Evaluate only persisted business conversations and trusted delivery events."""

    def __init__(self, *, uow_factory, actions: ReceptionistActions,
                 rules: EscalationRules | None = None, now_provider=None):
        self._uow_factory = uow_factory
        self._actions = actions
        self._rules = rules or EscalationRules()
        self._now = now_provider or (lambda: datetime.now(UTC))

    def run_due(self, principal: AuthenticatedPrincipal, *, business_id: str,
                limit: int = 200) -> list[WorkflowResult]:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("escalation clock must be timezone-aware")
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "workflow_executor" not in principal.roles
                or principal.issued_at > now
                or (principal.expires_at is not None and principal.expires_at <= now)):
            raise AuthorizationError("Escalation access denied")
        # Take a tenant/business scoped snapshot; each escalation owns a separate
        # workflow Unit of Work so ticket and dedupe record commit atomically.
        with self._uow_factory() as uow:
            business = uow.businesses.get(tenant_id=principal.tenant_id, business_id=business_id)
            if business is None:
                raise AuthorizationError("Escalation access denied")
            conversations = uow.conversations.list_for_business(
                tenant_id=principal.tenant_id, business_id=business.id, limit=limit,
            )
            candidates = []
            for conversation in conversations:
                assignment = uow.conversation_assignments.get(
                    tenant_id=principal.tenant_id, conversation_id=conversation.id,
                )
                events = uow.events.list_for_conversation(
                    tenant_id=principal.tenant_id, conversation_id=conversation.id,
                )
                decision = self._rules.decide(conversation=conversation,
                    assignment=assignment, events=events, now=now)
                if decision is not None:
                    candidates.append((conversation.id, decision))
        results = []
        for conversation_id, decision in candidates:
            key = f"escalation:{conversation_id}:{decision.idempotency_suffix}"
            result = self._actions.execute(principal, conversation_id, action="escalate",
                idempotency_key=key,
                input_data={"reason": decision.reason, "priority": decision.priority})
            if not result.replayed:
                results.append(result)
        return results
