"""Resolve swappable provider adapters from trusted tenant/business secrets."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.integrations.calendar.google import GoogleCalendarProvider
from app.integrations.email.smtp import SMTPEmailSender
from app.integrations.email.sendgrid import SendGridEmailSender
from app.integrations.provider_support import (
    ProviderAuditSink, ProviderConfigurationError, TenantConfigResolver,
)
from app.integrations.sms.twilio import TwilioMessageSender
from app.integrations.voice.twilio import TwilioVoiceProvider
from app.integrations.crm.hubspot import HubSpotCRMProvider
from app.integrations.crm.salesforce import SalesforceCRMProvider
from app.observability.metrics import metrics
from app.observability.telemetry import start_span
import time
import logging


logger = logging.getLogger("app.observability")


class ProviderAdapterFactory:
    """Select provider type only from tenant/business-scoped configuration."""
    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport=None):
        self.configs, self.audit_sink, self.transport = configs, audit_sink, transport

    def build(self, kind: str, *, tenant_id: str, business_id: str,
              region_id: str | None = None) -> Any:
        config = self.configs.resolve(tenant_id=tenant_id, business_id=business_id)
        if region_id:
            suffix = "__" + region_id.replace("-", "_").upper()
            for key, value in tuple(config.items()):
                if key.upper().endswith(suffix):
                    config[key[:-len(suffix)]] = value
        selectors = {"sms": "sms_provider", "email": "email_provider",
                     "voice": "voice_provider", "calendar": "calendar_provider",
                     "crm": "crm_provider"}
        try:
            selector = selectors[kind]
        except KeyError:
            raise ValueError("unsupported provider type") from None
        default = {"sms": "twilio", "email": "smtp", "voice": "twilio",
                   "calendar": "google", "crm": "hubspot"}[kind]
        provider = config.get(selector, default).strip().lower()
        kwargs = {"configs": self.configs, "audit_sink": self.audit_sink}
        choices = {
            ("sms", "twilio"): TwilioMessageSender,
            ("email", "smtp"): SMTPEmailSender,
            ("email", "sendgrid"): SendGridEmailSender,
            ("voice", "twilio"): TwilioVoiceProvider,
            ("calendar", "google"): GoogleCalendarProvider,
            ("crm", "hubspot"): HubSpotCRMProvider,
            ("crm", "salesforce"): SalesforceCRMProvider,
        }
        adapter = choices.get((kind, provider))
        if adapter is None:
            raise ProviderConfigurationError(f"unsupported {kind} provider")
        if not (kind == "email" and adapter is SMTPEmailSender) and self.transport is not None:
            kwargs["transport"] = self.transport
        return adapter(**kwargs)


class TenantScopedMessageSender:
    """MessageSender contract which resolves adapter from trusted scope on every send."""
    provider_key = "tenant_scoped"

    def __init__(self, factory: ProviderAdapterFactory):
        self._factory = factory

    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str,
             message_id=None, attempt: int | None = None,
             region_id: str | None = None) -> str | None:
        kind = "email" if channel == "email" else "sms" if channel in {"sms", "whatsapp"} else None
        if kind is None:
            raise ValueError("unsupported outbound channel")
        adapter = self._factory.build(kind, tenant_id=tenant_id, business_id=business_id,
                                      region_id=region_id)
        provider = getattr(adapter, "provider_key", "unknown")
        if provider not in {"twilio", "sendgrid", "smtp"}:
            provider = "unknown"
        started = time.perf_counter()
        outcome = "failure"
        try:
            with start_span("provider.message.send", attributes={
                "provider.type": provider, "messaging.channel": channel,
                "tenant.id": tenant_id, "business.id": business_id,
                "message.id": str(message_id or ""),
            }):
                result = adapter.send(tenant_id=tenant_id, business_id=business_id, channel=channel,
                                      recipient=recipient, content=content,
                                      idempotency_key=idempotency_key, message_id=message_id,
                                      attempt=attempt)
            outcome = "success"
            return result
        finally:
            normalized_channel = channel if channel in {
                "sms", "email", "voice", "call", "chat", "voicemail", "whatsapp",
            } else "unknown"
            metrics.provider_call(provider=provider, operation="provider_call", outcome=outcome,
                                  duration_seconds=time.perf_counter() - started,
                                  tenant_id=tenant_id, business_id=business_id)
            metrics.outreach_delivery(channel=normalized_channel, outcome=outcome,
                                      tenant_id=tenant_id, business_id=business_id)
            logger.info("provider call completed", extra={
                "tenant_id": tenant_id, "business_id": business_id,
                "provider": provider, "channel": normalized_channel,
                "component": "outreach", "operation": "send_attempt",
                "outcome": outcome,
            })


__all__ = ["ProviderAdapterFactory", "TenantScopedMessageSender"]
