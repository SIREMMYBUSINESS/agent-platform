"""Twilio SMS and WhatsApp adapter implementing the outreach sender contract."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from urllib.parse import urlencode
from uuid import UUID

from app.integrations.provider_support import (
    JSONHTTPTransport, ProviderAuditSink, ProviderConfigurationError,
    ProviderHTTPError, TenantConfigResolver, UrllibJSONTransport, emit_audit,
)
from app.integrations.sms.twilio_webhooks import build_status_callback_url


class TwilioDeliveryError(RuntimeError):
    """Sanitized Twilio request failure."""


class TwilioMessageSender:
    provider_key = "twilio"
    endpoint = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport: JSONHTTPTransport | None = None, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._configs = configs
        self._audit = audit_sink
        self._transport = transport or UrllibJSONTransport()
        self._timeout = timeout

    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str,
             message_id: UUID | None = None, attempt: int | None = None) -> str | None:
        if channel not in {"sms", "whatsapp"}:
            raise ValueError("TwilioMessageSender supports sms and whatsapp")
        key = self._key(idempotency_key)
        try:
            config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
            account_sid = self._required(config, "account_sid")
            api_key_sid = config.get("api_key_sid", "").strip()
            api_key_secret = config.get("api_key_secret", "").strip()
            auth_token = config.get("auth_token", "").strip()
            if bool(api_key_sid) != bool(api_key_secret):
                raise ProviderConfigurationError("Twilio API key SID and secret must be configured together")
            if api_key_sid:
                auth_username, auth_password = api_key_sid, api_key_secret
            elif auth_token:
                # Backward compatible for existing configurations. Prefer an
                # API key for application credentials because it can be revoked
                # independently from the Twilio account Auth Token.
                auth_username, auth_password = account_sid, auth_token
            else:
                raise ProviderConfigurationError("Twilio API key credentials are required")
            source_key = "from_whatsapp" if channel == "whatsapp" else "from_sms"
            source = config.get(source_key, "").strip()
            service_sid = config.get("messaging_service_sid", "").strip()
            if not source and not service_sid:
                raise ProviderConfigurationError("Twilio sender or Messaging Service SID is required")
            if not isinstance(recipient, str) or not recipient.strip():
                raise ValueError("recipient address is required")
            body = content.get("body")
            if not isinstance(body, str) or not body.strip():
                raise ValueError("message body is required")
            if channel == "whatsapp":
                if source:
                    source = self._whatsapp_address(source)
                recipient = self._whatsapp_address(recipient)
            form = {"To": recipient, "Body": body}
            callback_base_url = config.get("status_callback_base_url", "").strip()
            if (callback_base_url and auth_token
                    and message_id is not None and attempt is not None):
                callback_url = build_status_callback_url(
                    callback_base_url, tenant_id=tenant_id, business_id=business_id,
                    message_id=message_id, attempt=attempt,
                )
                form["StatusCallback"] = callback_url
            if source:
                form["From"] = source
            else:
                form["MessagingServiceSid"] = service_sid
            data = urlencode(form).encode("utf-8")
            auth = base64.b64encode(f"{auth_username}:{auth_password}".encode()).decode("ascii")
            status, response = self._transport.request(
                "POST", self.endpoint.format(account_sid=account_sid),
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type": "application/x-www-form-urlencoded",
                         "Idempotency-Key": key},
                body=data, timeout=self._timeout,
            )
            if not 200 <= status < 300:
                raise ProviderHTTPError(status, retryable=status == 429 or status >= 500)
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation=f"send_{channel}",
                       idempotency_key=key, outcome="succeeded")
            message_sid = response.get("sid")
            return message_sid if isinstance(message_sid, str) and message_sid else None
        except Exception as exc:
            code = (f"provider_http_error:{exc.status_code}" if isinstance(exc, ProviderHTTPError)
                    else "invalid_message_request" if isinstance(exc, ValueError)
                    else f"twilio_error:{type(exc).__name__}")
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation=f"send_{channel}",
                       idempotency_key=key, outcome="failed", error_code=code)
            if isinstance(exc, ValueError):
                raise
            raise TwilioDeliveryError(code) from None

    @staticmethod
    def _whatsapp_address(value: str) -> str:
        value = value.strip()
        return value if value.lower().startswith("whatsapp:") else f"whatsapp:{value}"

    @staticmethod
    def _required(config, key):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(f"Twilio {key} is not configured")
        return value.strip()

    @staticmethod
    def _key(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("idempotency_key must be between 1 and 200 characters")
        return value.strip()


__all__ = ["TwilioDeliveryError", "TwilioMessageSender"]
