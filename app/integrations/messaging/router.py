"""Route authorized outreach sends to their configured channel adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.integrations.provider_support import ProviderAuditSink, emit_audit


class MultiChannelMessageSender:
    """MessageSender implementation that selects only by the persisted channel."""

    provider_key = "multi_channel"

    def __init__(self, *, email_sender: Any, twilio_sender: Any,
                 audit_sink: ProviderAuditSink) -> None:
        self._senders = {"email": email_sender, "sms": twilio_sender,
                         "whatsapp": twilio_sender}
        self._audit = audit_sink

    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str,
             message_id: UUID | None = None, attempt: int | None = None) -> str | None:
        sender = self._senders.get(channel)
        if sender is None:
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation="route_message",
                       idempotency_key=idempotency_key, outcome="failed",
                       error_code="unsupported_channel")
            raise ValueError("unsupported outbound channel")
        return sender.send(tenant_id=tenant_id, business_id=business_id, channel=channel,
                           recipient=recipient, content=content, idempotency_key=idempotency_key,
                           message_id=message_id, attempt=attempt)


__all__ = ["MultiChannelMessageSender"]
