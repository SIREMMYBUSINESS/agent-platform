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


class ProviderAdapterFactory:
    """Select provider type only from tenant/business-scoped configuration."""
    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport=None):
        self.configs, self.audit_sink, self.transport = configs, audit_sink, transport

    def build(self, kind: str, *, tenant_id: str, business_id: str) -> Any:
        config = self.configs.resolve(tenant_id=tenant_id, business_id=business_id)
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
             message_id=None, attempt: int | None = None) -> str | None:
        kind = "email" if channel == "email" else "sms" if channel in {"sms", "whatsapp"} else None
        if kind is None:
            raise ValueError("unsupported outbound channel")
        adapter = self._factory.build(kind, tenant_id=tenant_id, business_id=business_id)
        return adapter.send(tenant_id=tenant_id, business_id=business_id, channel=channel,
                            recipient=recipient, content=content,
                            idempotency_key=idempotency_key, message_id=message_id,
                            attempt=attempt)


__all__ = ["ProviderAdapterFactory", "TenantScopedMessageSender"]
