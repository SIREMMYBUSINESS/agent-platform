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

from app.integrations.provider_support import (
    ProviderConfigurationError, safe_provider_configuration_reason_code,
)
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.metrics import metrics
from app.queue.contracts import QueueDomain
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
             recipient: str, content: Mapping[str, str], idempotency_key: str,
             message_id: UUID, attempt: int) -> str | None: ...


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
        provider_message_id = None
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
                    provider_message_id = self._sender.send(
                        tenant_id=row.tenant_id, business_id=row.business_id,
                        channel=row.channel, recipient=destination,
                        content=row.content, idempotency_key=row.idempotency_key,
                        message_id=row.id, attempt=claimed.attempts,
                    )
            except ProviderConfigurationError as exc:
                # Persist only a stable reason code. The raw exception message
                # stays out of records and logs, while operators can distinguish
                # a missing scope file from an invalid or ambiguous one.
                error = f"provider_config:{safe_provider_configuration_reason_code(exc)}"
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
            failure_reason = error
            receipt_status = getattr(current, "provider_status", None)
            if receipt_status in {"delivered", "read"}:
                status, failure_reason = "delivered", None
            elif receipt_status in {"failed", "undelivered"}:
                status = "failed"
                failure_reason = f"provider_receipt:{receipt_status}:{getattr(current, 'provider_error_code', None) or 'unknown'}"
            updated = uow.outreach_messages.set_state(
                tenant_id=principal.tenant_id, business_id=current.business_id,
                message_id=current.id, expected_status="sending", status=status,
                at=self._now_utc(), failure_reason=failure_reason,
                provider_message_id=provider_message_id,
            )
            self._event(uow, updated, principal,
                        "MessageDelivered" if status == "delivered" else "MessageFailed" if status == "failed" else "MessageSent",
                        {"message_id": str(updated.id), **({"reason": failure_reason} if failure_reason else {})})
            result = self._snapshot(updated)
        metrics.event("outreach", "send_attempt", "failed" if result.status == "failed" else "succeeded")
        if result.status == "failed":
            metrics.event("outreach", "failure", "provider")
        return result

    def enqueue_send(self, principal: AuthenticatedPrincipal, message_id: UUID):
        """Queue a scheduled delivery; HTTP callers never invoke providers."""
        now = self._now_utc()
        require_campaign_writer(principal, now)
        with self._uow_factory() as uow:
            row = uow.outreach_messages.get_for_tenant(
                tenant_id=principal.tenant_id, message_id=message_id,
            )
            if row is None:
                raise OutreachNotFound("outreach message not found")
            if row.status != "scheduled":
                if row.status in {"sent", "delivered"}:
                    return self._snapshot(row)
                raise OutreachConflict(f"message cannot be queued while {row.status}")
            if self._persisted_utc(row.scheduled_at) > now:
                raise OutreachConflict("message is not due")
            self._enqueue_delivery(uow, row, available_at=now, attempt=row.attempts + 1)
            return self._snapshot(row)

    @staticmethod
    def _enqueue_delivery(uow, row, *, available_at: datetime, attempt: int) -> None:
        uow.queue_tasks.enqueue(
            tenant_id=row.tenant_id, business_id=row.business_id,
            domain=QueueDomain.OUTREACH.value, task_name="outreach.execute_send",
            idempotency_key=f"outreach:{row.id}:attempt:{attempt}",
            aggregate_type="outreach_message", aggregate_id=str(row.id),
            payload={"message_id": str(row.id)},
            correlation_id=f"outreach:{row.id}", traceparent=None,
            available_at=available_at, max_attempts=6,
        )

    def retry_send(self, principal: AuthenticatedPrincipal, message_id: UUID,
                   *, scheduled_at: datetime | None = None, enqueue: bool = False):
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
                clear_provider_receipt=True,
            )
            updated = self._snapshot(updated)
            self._event(uow, row, principal, "MessageRetryScheduled", {
                "message_id": str(row.id), "scheduled_at": scheduled_at.isoformat(),
                "attempt": row.attempts + 1,
            })
            if enqueue:
                self._enqueue_delivery(
                    uow, updated, available_at=scheduled_at, attempt=row.attempts + 1,
                )
        metrics.event("outreach", "retry", "scheduled")
        return updated

    def mark_delivered(self, principal: AuthenticatedPrincipal, message_id: UUID):
        return self._mark_terminal(principal, message_id, status="delivered")

    def mark_failed(self, principal: AuthenticatedPrincipal, message_id: UUID,
                    *, failure_reason: str):
        reason = self._safe_failure_reason(failure_reason)
        return self._mark_terminal(principal, message_id, status="failed", failure_reason=reason)

    def apply_twilio_receipt(self, principal: AuthenticatedPrincipal, *, business_id: str,
                             message_id: UUID, provider_message_id: str,
                             attempt: int, provider_status: str,
                             error_code: str | None = None):
        """Apply a verified Twilio receipt under an internally authenticated principal."""
        now = self._now_utc()
        if (not isinstance(principal, AuthenticatedPrincipal)
                or "delivery_receipt_writer" not in principal.roles
                or principal.auth_source != "twilio_webhook"
                or principal.issued_at > now
                or (principal.expires_at is not None and principal.expires_at <= now)):
            from app.auth.principal_resolver import AuthorizationError
            raise AuthorizationError("Delivery receipt access denied")
        if provider_status not in {"accepted", "queued", "sending", "sent", "delivered", "read", "failed", "undelivered"}:
            raise ValueError("unsupported provider delivery status")
        if not re.fullmatch(r"[A-Z]{2}[A-Za-z0-9]{32}", provider_message_id):
            raise ValueError("provider message reference is invalid")
        if not isinstance(attempt, int) or attempt < 1:
            raise ValueError("provider delivery attempt is invalid")
        if error_code is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", error_code):
            raise ValueError("provider error code is invalid")
        rank = {"accepted": 0, "queued": 0, "sending": 1, "sent": 2,
                "delivered": 3, "read": 3, "failed": 3, "undelivered": 3}
        with self._uow_factory() as uow:
            row = uow.outreach_messages.get_for_update(
                tenant_id=principal.tenant_id, business_id=business_id, message_id=message_id,
            )
            if row is None or row.channel not in {"sms", "whatsapp"}:
                raise OutreachNotFound("provider delivery target not found")
            if row.attempts != attempt or row.status not in {"sending", "sent"}:
                return self._snapshot(row)
            if row.provider_message_id not in {None, provider_message_id}:
                return self._snapshot(row)
            previous = row.provider_status
            if row.status in {"delivered", "failed"}:
                return self._snapshot(row)
            if previous in {"delivered", "read", "failed", "undelivered"} and provider_status != previous:
                return self._snapshot(row)
            if previous is not None and rank[provider_status] < rank.get(previous, 0):
                return self._snapshot(row)
            if previous == provider_status and row.provider_message_id == provider_message_id:
                return self._snapshot(row)
            status = row.status
            reason = None
            if row.status == "sent" and provider_status in {"delivered", "read"}:
                status = "delivered"
            elif row.status == "sent" and provider_status in {"failed", "undelivered"}:
                status = "failed"
                reason = f"provider_receipt:{provider_status}:{error_code or 'unknown'}"
            updated = uow.outreach_messages.set_state(
                tenant_id=principal.tenant_id, business_id=business_id,
                message_id=message_id, expected_status=row.status, status=status,
                at=now, failure_reason=reason,
                provider_message_id=provider_message_id,
                provider_status=provider_status, provider_error_code=error_code,
            )
            event_type = ("MessageDelivered" if status == "delivered" else
                          "MessageFailed" if status == "failed" else "MessageDeliveryReceipt")
            self._event(uow, updated, principal, event_type, {
                "message_id": str(message_id), "provider": "twilio",
                "provider_status": provider_status,
                **({"error_code": error_code} if error_code else {}),
            })
            result = self._snapshot(updated)
        metrics.event("outreach", "delivery_receipt",
                      "failed" if result.status == "failed" else "succeeded")
        return result

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
