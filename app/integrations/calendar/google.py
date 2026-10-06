"""Google Calendar REST adapter with tenant-scoped OAuth access tokens."""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, urlencode
from typing import Any

from app.integrations.provider_support import (
    JSONHTTPTransport, ProviderAuditSink, ProviderConfigurationError,
    TenantConfigResolver, UrllibJSONTransport, emit_audit, json_request,
)


class GoogleCalendarProvider:
    provider_key = "google_calendar"
    base_url = "https://www.googleapis.com/calendar/v3/calendars"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport: JSONHTTPTransport | None = None, timeout: float = 10.0):
        self._configs, self._audit = configs, audit_sink
        self._transport, self._timeout = transport or UrllibJSONTransport(), timeout

    def _config(self, tenant_id, business_id):
        config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
        token, calendar_id = config.get("access_token", "").strip(), config.get("calendar_id", "").strip()
        if not token or not calendar_id:
            raise ProviderConfigurationError("Google Calendar token and calendar_id are required")
        return token, calendar_id

    def availability(self, *, tenant_id: str, business_id: str,
                      time_min: str, time_max: str) -> list[Mapping[str, Any]]:
        token, calendar = self._config(tenant_id, business_id)
        query = urlencode({"timeMin": time_min, "timeMax": time_max,
                           "singleEvents": "true", "orderBy": "startTime"})
        data = json_request(self._transport, "GET",
            f"{self.base_url}/{quote(calendar, safe='')}/events?{query}",
            headers={"Authorization": f"Bearer {token}"}, timeout=self._timeout)
        return [item for item in data.get("items", []) if isinstance(item, Mapping)]

    def create_event(self, *, tenant_id: str, business_id: str, event: Mapping[str, Any],
                     idempotency_key: str) -> Mapping[str, Any]:
        token, calendar = self._config(tenant_id, business_id)
        result = json_request(self._transport, "POST",
            f"{self.base_url}/{quote(calendar, safe='')}/events",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idempotency_key},
            payload=event, timeout=self._timeout)
        emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                   provider=self.provider_key, operation="create_event",
                   idempotency_key=idempotency_key, outcome="succeeded")
        return result

    def cancel_event(self, *, tenant_id: str, business_id: str, event_id: str,
                     idempotency_key: str) -> None:
        token, calendar = self._config(tenant_id, business_id)
        json_request(self._transport, "DELETE",
            f"{self.base_url}/{quote(calendar, safe='')}/events/{quote(event_id, safe='')}",
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": idempotency_key},
            timeout=self._timeout)
        emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                   provider=self.provider_key, operation="cancel_event",
                   idempotency_key=idempotency_key, outcome="succeeded")


__all__ = ["GoogleCalendarProvider"]
