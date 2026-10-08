"""Twilio outbound call initiation using tenant-scoped mounted secrets."""
from __future__ import annotations

import base64
from urllib.parse import urlencode

from app.integrations.provider_support import (
    JSONHTTPTransport, ProviderAuditSink, ProviderConfigurationError,
    ProviderHTTPError, TenantConfigResolver, UrllibJSONTransport, emit_audit,
)


class TwilioVoiceProvider:
    provider_key = "twilio"
    endpoint = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport: JSONHTTPTransport | None = None, timeout: float = 10.0):
        self._configs, self._audit = configs, audit_sink
        self._transport, self._timeout = transport or UrllibJSONTransport(), timeout

    def initiate_call(self, *, tenant_id: str, business_id: str, recipient: str,
                       callback_url: str, idempotency_key: str,
                       status_callback_url: str | None = None) -> str:
        config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
        account = config.get("account_sid", "").strip()
        token = config.get("auth_token", "").strip()
        source = config.get("from_call", config.get("from_sms", "")).strip()
        if not account or not token or not source:
            raise ProviderConfigurationError("Twilio voice credentials and caller ID are required")
        if not recipient.strip() or not callback_url.startswith("https://"):
            raise ValueError("valid recipient and HTTPS callback URL are required")
        if status_callback_url is not None and not status_callback_url.startswith("https://"):
            raise ValueError("status callback URL must use HTTPS")
        auth = base64.b64encode(f"{account}:{token}".encode()).decode("ascii")
        status, response = self._transport.request(
            "POST", self.endpoint.format(account_sid=account),
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Idempotency-Key": idempotency_key},
            body=urlencode({
                "To": recipient, "From": source, "Url": callback_url,
                **({"StatusCallback": status_callback_url,
                   "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"]}
                  if status_callback_url else {}),
            }, doseq=True).encode(),
            timeout=self._timeout,
        )
        if not 200 <= status < 300:
            error = ProviderHTTPError(status, retryable=status == 429 or status >= 500)
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation="initiate_call",
                       idempotency_key=idempotency_key, outcome="failed",
                       error_code=f"provider_http_error:{status}")
            raise error
        call_id = response.get("sid")
        if not isinstance(call_id, str) or not call_id:
            raise RuntimeError("twilio_response_missing_call_id")
        emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                   provider=self.provider_key, operation="initiate_call",
                   idempotency_key=idempotency_key, outcome="succeeded")
        return call_id


__all__ = ["TwilioVoiceProvider"]
