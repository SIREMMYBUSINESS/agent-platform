"""Idempotent campaign message scheduling and provider execution."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.metrics import metrics
from app.services.policy_service import PolicyService
from app.services.recipient_selection import require_campaign_writer


class OutreachError(Exception):
    """Invalid or unavailable outreach operation."""


class OutreachNotFound(OutreachError):
    pass


class OutreachConflict(OutreachError):
    pass


class OutreachNotEligible(OutreachError):
    pass


class RateLimitExceeded(OutreachError):
    pass


class MessageSender(Protocol):
    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str) -> None: ...


@dataclass(frozen=True)
class RateLimitPolicy:
    tenant_messages_per_window: int = 100
    campaign_messages_per_window: int = 25
    window_seconds: int = 3600
    max_attempts: int = 4

    def __post_init__(self) -> None:
        if min(self.tenant_messages_per_window, self.campaign_messages_per_window,
               self.window_seconds, self.max_attempts) < 1:
            raise ValueError("rate limits and retry count must be positive")


class OutreachExecutionService:
    def __init__(self, *, uow_factory: Callable[[], UnitOfWork],
                 sender: MessageSender | None = None,
                 policy_service: PolicyService | None = None,
                 rate_limits: RateLimitPolicy | None = None,
                 now_provider: Callable[[], datetime] | None = None) -> None:
        self._uow_factory = uow_factory
        self._sender = sender
        self._policy = policy_service or PolicyService()
        self._limits = rate_limits or RateLimitPolicy()
        self._now = now_provider or (lambda: datetime.now(UTC))

    def schedule_send(self, principal: AuthenticatedPrincipal, *, campaign_id: UUID,
                      contact_id: str, idempotency_key: str,
                      content: Mapping[str, str], scheduled_at: datetime,
                      conversation_id: UUID | None = None):
        now = self._now_utc()
        require_campaign_writer(principal, now)
        key = self._key(idempotency_key)
        canonical_content = self._content(content)
        scheduled_at = self._aware(scheduled_at, "scheduled_at").astimezone(UTC)
        if scheduled_at < now:
            raise ValueError("scheduled_at must not be in the past")
        content_hash = self._fingerprint(campaign_id, contact_id, conversation_id,
                                         canonical_content, scheduled_at)
        blocked_reason = None
        result = None
        with self._uow_factory() as uow:
            existing = uow.outreach_messages.get_by_key(
                tenant_id=principal.tenant_id, idempotency_key=key,
            )
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise OutreachConflict("idempotency key was already used for different input")
                return self._snapshot(existing)
            campaign = uow.campaigns.get_for_tenant(
                tenant_id=principal.tenant_id, campaign_id=campaign_id,
            )
            if campaign is None:
                raise OutreachNotFound("campaign not found")
            if campaign.status != "active":
                raise OutreachConflict("campaign must be active to schedule sends")
            contact = uow.contacts.get(tenant_id=principal.tenant_id, contact_id=contact_id)
            if contact is None or contact.business_id != campaign.business_id:
                raise OutreachNotFound("contact not found in campaign scope")
            conversation = None
            if conversation_id is not None:
                conversation = uow.conversations.get(
                    tenant_id=principal.tenant_id, conversation_id=conversation_id,
                )
                if (conversation is None or conversation.business_id != campaign.business_id
                        or conversation.contact_id != contact.id):
                    raise OutreachNotFound("conversation not found in campaign scope")
            if uow.outreach_messages.get_for_contact(
                tenant_id=principal.tenant_id, business_id=campaign.business_id,
                campaign_id=campaign.id, contact_id=contact.id,
            ) is not None:
                raise OutreachConflict("contact is already selected for this campaign")
            decision = self._policy_decision(uow, principal.tenant_id,
                                             campaign.business_id, campaign.channel, contact)
            if not decision.allowed:
                blocked_reason = decision.reason_code
            else:
                self._enforce_scheduling_limits(uow, principal.tenant_id, campaign.id, now)
                row = uow.outreach_messages.create(
                    tenant_id=principal.tenant_id, business_id=campaign.business_id,
                    campaign_id=campaign.id, contact_id=contact.id,
                    conversation_id=conversation.id if conversation else None,
                    channel=campaign.channel, idempotency_key=key,
                    content=canonical_content, content_hash=content_hash,
                    scheduled_at=scheduled_at, created_at=now,
                )
                self._event(uow, row, principal, "MessageScheduled", {
                    "message_id": str(row.id), "scheduled_at": scheduled_at.isoformat(),
                })
                result = self._snapshot(row)
        if blocked_reason:
            raise OutreachNotEligible(blocked_reason)
        return result

    def execute_send(self, principal: AuthenticatedPrincipal, message_id: UUID):
        now = self._now_utc()
        require_campaign_writer(principal, now)
        blocked_reason = None
        claimed = row = contact = None
        with self._uow_factory() as uow:
            row = uow.outreach_messages.get_for_tenant(
                tenant_id=principal.tenant_id, message_id=message_id,
            )
            if row is None:
                raise OutreachNotFound("outreach message not found")
            if row.status in {"sent", "delivered"}:
                return self._snapshot(row)
            if row.status != "scheduled":
                raise OutreachConflict(f"message cannot execute while {row.status}")
            scheduled_at = self._persisted_utc(row.scheduled_at)
            if scheduled_at > now:
                raise OutreachConflict("message is not due")
            campaign = uow.campaigns.get(
                tenant_id=principal.tenant_id, business_id=row.business_id,
                campaign_id=row.campaign_id,
            )
            contact = uow.contacts.get(tenant_id=principal.tenant_id, contact_id=row.contact_id)
            if campaign is None or campaign.status != "active" or contact is None:
                raise OutreachNotEligible("campaign or contact is no longer eligible")
            decision = self._policy_decision(uow, principal.tenant_id,
                                             row.business_id, row.channel, contact)
            if not decision.allowed:
                blocked_reason = decision.reason_code
            else:
                since = now - timedelta(seconds=self._limits.window_seconds)
                if uow.outreach_messages.attempts_since(tenant_id=principal.tenant_id, since=since) >= self._limits.tenant_messages_per_window:
                    raise RateLimitExceeded("tenant outreach rate limit exceeded")
                if uow.outreach_messages.attempts_since(tenant_id=principal.tenant_id, since=since,
                                                       campaign_id=row.campaign_id) >= self._limits.campaign_messages_per_window:
                    raise RateLimitExceeded("campaign outreach rate limit exceeded")
                claimed = uow.outreach_messages.set_state(
                    tenant_id=principal.tenant_id, business_id=row.business_id,
                    message_id=row.id, expected_status="scheduled", status="sending",
                    at=now, increment_attempts=True,
                )
                claimed = self._snapshot(claimed)
                row = self._snapshot(row)
                contact = self._snapshot(contact)
        if blocked_reason:
            raise OutreachNotEligible(blocked_reason)
        # The provider receives the same key for retry deduplication. It is
        # called outside the DB transaction; status/audit updates are atomic.
        error = None
        if self._sender is None:
            error = "sender_not_configured"
        else:
            try:
                destination = getattr(contact, row.channel if row.channel != "whatsapp" else "whatsapp", None)
                if not destination and row.channel == "sms":
                    destination = contact.phone
                if not destination:
                    error = "recipient_address_missing"
                else:
                    self._sender.send(
                        tenant_id=row.tenant_id, business_id=row.business_id,
                        channel=row.channel, recipient=destination,
                        content=row.content, idempotency_key=row.idempotency_key,
                    )
            except Exception as exc:  # provider details may contain secrets or PII
                error = f"provider_error:{type(exc).__name__}"
        with self._uow_factory() as uow:
            current = uow.outreach_messages.get(
                tenant_id=principal.tenant_id, business_id=claimed.business_id,
                message_id=claimed.id,
            )
            if current is None or current.status != "sending":
                raise OutreachConflict("outreach message state changed during delivery")
            status = "failed" if error else "sent"
            updated = uow.outreach_messages.set_state(
                tenant_id=principal.tenant_id, business_id=current.business_id,
                message_id=current.id, expected_status="sending", status=status,
                at=self._now_utc(), failure_reason=error,
            )
            self._event(uow, updated, principal,
                        "MessageFailed" if error else "MessageSent",
                        {"message_id": str(updated.id), **({"reason": error} if error else {})})
            result = self._snapshot(updated)
        metrics.event("outreach", "send_attempt", "failed" if error else "succeeded")
        if error:
            metrics.event("outreach", "failure", "provider")
        return result

    def retry_send(self, principal: AuthenticatedPrincipal, message_id: UUID,
                   *, scheduled_at: datetime | None = None):
        now = self._now_utc()
        require_campaign_writer(principal, now)
        scheduled_at = self._aware(scheduled_at or now, "scheduled_at").astimezone(UTC)
        if scheduled_at < now:
            raise ValueError("scheduled_at must not be in the past")
        with self._uow_factory() as uow:
            row = uow.outreach_messages.get_for_tenant(
                tenant_id=principal.tenant_id, message_id=message_id,
            )
            if row is None:
                raise OutreachNotFound("outreach message not found")
            if row.status != "failed":
                if row.status == "scheduled":
                    return self._snapshot(row)
                raise OutreachConflict("only failed messages can be retried")
            if row.attempts >= self._limits.max_attempts:
                raise RateLimitExceeded("message retry limit exceeded")
            self._enforce_scheduling_limits(uow, principal.tenant_id, row.campaign_id, now)
            updated = uow.outreach_messages.set_state(
                tenant_id=principal.tenant_id, business_id=row.business_id,
                message_id=row.id, expected_status="failed", status="scheduled",
                at=now, scheduled_at=scheduled_at,
            )
            updated = self._snapshot(updated)
            self._event(uow, row, principal, "MessageRetryScheduled", {
                "message_id": str(row.id), "scheduled_at": scheduled_at.isoformat(),
                "attempt": row.attempts + 1,
            })
        metrics.event("outreach", "retry", "scheduled")
        return updated

    def mark_delivered(self, principal: AuthenticatedPrincipal, message_id: UUID):
        return self._mark_terminal(principal, message_id, status="delivered")

    def mark_failed(self, principal: AuthenticatedPrincipal, message_id: UUID,
                    *, failure_reason: str):
        reason = self._safe_failure_reason(failure_reason)
        return self._mark_terminal(principal, message_id, status="failed", failure_reason=reason)

    def _mark_terminal(self, principal, message_id, *, status, failure_reason=None):
        now = self._now_utc()
        require_campaign_writer(principal, now)
        with self._uow_factory() as uow:
            row = uow.outreach_messages.get_for_tenant(
                tenant_id=principal.tenant_id, message_id=message_id,
            )
            if row is None:
                raise OutreachNotFound("outreach message not found")
            if row.status == status:
                return self._snapshot(row)
            if row.status != "sent":
                raise OutreachConflict(f"message cannot become {status} from {row.status}")
            updated = uow.outreach_messages.set_state(
                tenant_id=principal.tenant_id, business_id=row.business_id,
                message_id=row.id, expected_status="sent", status=status,
                at=now, failure_reason=failure_reason,
            )
            self._event(uow, updated, principal,
                        "MessageDelivered" if status == "delivered" else "MessageFailed",
                        {"message_id": str(updated.id), **({"reason": failure_reason} if failure_reason else {})})
            return self._snapshot(updated)

    def _policy_decision(self, uow, tenant_id, business_id, channel, contact):
        tenant = uow.tenants.get(tenant_id=tenant_id)
        business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
        decision = self._policy.evaluate(
            uow, tenant=tenant, business=business, contact=contact,
            channel=channel, direction="outbound",
        )
        return decision

    def _enforce_scheduling_limits(self, uow, tenant_id, campaign_id, now):
        since = now - timedelta(seconds=self._limits.window_seconds)
        if uow.outreach_messages.count_since(tenant_id=tenant_id, since=since) >= self._limits.tenant_messages_per_window:
            raise RateLimitExceeded("tenant outreach rate limit exceeded")
        if uow.outreach_messages.count_since(tenant_id=tenant_id, since=since,
                                             campaign_id=campaign_id) >= self._limits.campaign_messages_per_window:
            raise RateLimitExceeded("campaign outreach rate limit exceeded")

    @staticmethod
    def _event(uow, row, principal, event_type, details):
        uow.campaign_events.add(
            tenant_id=row.tenant_id, business_id=row.business_id,
            campaign_id=row.campaign_id, event_type=event_type,
            payload={**details, "actor_id": principal.principal_id},
        )

    def _now_utc(self):
        now = self._now()
        return self._aware(now, "clock").astimezone(UTC)

    @staticmethod
    def _aware(value, field):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        return value

    @staticmethod
    def _key(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("idempotency_key must be between 1 and 200 characters")
        return value.strip()

    @staticmethod
    def _content(value):
        if not isinstance(value, Mapping) or not value or set(value) - {"subject", "body"}:
            raise ValueError("content must contain subject and/or body only")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 20000 for v in value.values()):
            raise ValueError("content fields must be non-empty strings up to 20000 characters")
        return dict(sorted(value.items()))

    @staticmethod
    def _fingerprint(campaign_id, contact_id, conversation_id, content, scheduled_at):
        raw = json.dumps({"campaign_id": str(campaign_id), "contact_id": contact_id,
                          "conversation_id": str(conversation_id) if conversation_id else None,
                          "content": content, "scheduled_at": scheduled_at.isoformat()},
                         sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _safe_failure_reason(reason):
        if not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", reason.strip()):
            raise ValueError("failure_reason must be a short provider-safe code")
        return reason.strip()

    @staticmethod
    def _snapshot(row):
        values = row.model_dump()
        for field in ("scheduled_at", "sent_at", "delivered_at", "failed_at", "created_at", "updated_at"):
            value = values.get(field)
            if isinstance(value, datetime):
                values[field] = OutreachExecutionService._persisted_utc(value)
        return type(row)(**values)

    @staticmethod
    def _persisted_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = [
    "MessageSender", "OutreachConflict", "OutreachError", "OutreachExecutionService",
    "OutreachNotEligible", "OutreachNotFound", "RateLimitExceeded", "RateLimitPolicy",
]
