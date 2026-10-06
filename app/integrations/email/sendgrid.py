"""SendGrid email adapter with stable message metadata and open/click tracking."""
from __future__ import annotations

import json
from collections.abc import Mapping
from email.utils import parseaddr

from app.integrations.provider_support import (
    JSONHTTPTransport, ProviderAuditSink, ProviderConfigurationError,
    ProviderHTTPError, TenantConfigResolver, UrllibJSONTransport, emit_audit,
)


class SendGridEmailSender:
    provider_key = "sendgrid"
    endpoint = "https://api.sendgrid.com/v3/mail/send"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport: JSONHTTPTransport | None = None, timeout: float = 10.0):
        self._configs, self._audit = configs, audit_sink
        self._transport, self._timeout = transport or UrllibJSONTransport(), timeout

    def send(self, *, tenant_id: str, business_id: str, channel: str,
             recipient: str, content: Mapping[str, str], idempotency_key: str,
             message_id=None, attempt: int | None = None) -> str | None:
        if channel != "email":
            raise ValueError("SendGridEmailSender only supports email")
        try:
            config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
            token, sender = config.get("api_key", "").strip(), config.get("from_address", "").strip()
            if not token or not sender:
                raise ProviderConfigurationError("SendGrid API key and sender are required")
            if parseaddr(recipient)[1] != recipient or "@" not in recipient:
                raise ValueError("valid email address is required")
            if parseaddr(sender)[1] != sender or "@" not in sender:
                raise ProviderConfigurationError("SendGrid sender address is invalid")
            body = content.get("body", "")
            if not body.strip():
                raise ValueError("email body is required")
            payload = {
                "personalizations": [{"to": [{"email": recipient}],
                    "custom_args": {"idempotency_key": idempotency_key,
                                    **({"message_id": str(message_id)} if message_id else {})}}],
                "from": {"email": sender},
                "subject": content.get("subject") or "Message from your business",
                "content": [{"type": "text/plain", "value": body}],
                "tracking_settings": {"click_tracking": {"enable": True},
                                       "open_tracking": {"enable": True}},
            }
            status, response = self._transport.request(
                "POST", self.endpoint,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json", "Idempotency-Key": idempotency_key},
                body=json.dumps(payload, separators=(",", ":")).encode(), timeout=self._timeout,
            )
            if not 200 <= status < 300:
                raise ProviderHTTPError(status, retryable=status == 429 or status >= 500)
            response_headers = response.get("provider_response_headers", {})
            provider_id = (response.get("message_id") or response.get("id")
                           or (response_headers.get("x-message-id")
                               if isinstance(response_headers, Mapping) else None))
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation="send_email",
                       idempotency_key=idempotency_key, outcome="succeeded")
            return str(provider_id) if provider_id else None
        except Exception as exc:
            code = (f"provider_http_error:{exc.status_code}" if isinstance(exc, ProviderHTTPError)
                    else "invalid_email_request" if isinstance(exc, ValueError)
                    else f"sendgrid_error:{type(exc).__name__}")
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation="send_email",
                       idempotency_key=idempotency_key, outcome="failed", error_code=code)
            if isinstance(exc, ValueError):
                raise
            raise RuntimeError(code) from None


__all__ = ["SendGridEmailSender"]
