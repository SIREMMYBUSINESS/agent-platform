"""Provider contracts independent of concrete vendor SDKs."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class MessageProvider(Protocol):
    provider_key: str
    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str,
             message_id=None, attempt: int | None = None) -> str | None: ...


class SMSProvider(MessageProvider, Protocol):
    """SMS/WhatsApp sender contract."""


class EmailProvider(MessageProvider, Protocol):
    """Email sender contract."""


class VoiceProvider(Protocol):
    provider_key: str
    def initiate_call(self, *, tenant_id: str, business_id: str, recipient: str,
                      callback_url: str, idempotency_key: str) -> str: ...


class CalendarProvider(Protocol):
    provider_key: str
    def availability(self, *, tenant_id: str, business_id: str,
                     time_min: str, time_max: str) -> list[Mapping[str, Any]]: ...
    def create_event(self, *, tenant_id: str, business_id: str, event: Mapping[str, Any],
                     idempotency_key: str) -> Mapping[str, Any]: ...
    def cancel_event(self, *, tenant_id: str, business_id: str, event_id: str,
                     idempotency_key: str) -> None: ...


__all__ = ["CalendarProvider", "EmailProvider", "MessageProvider", "SMSProvider", "VoiceProvider"]
