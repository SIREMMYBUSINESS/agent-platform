"""HubSpot CRMProvider adapter using tenant-specific private-app/OAuth tokens."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import quote, urlencode

from app.integrations.provider_support import (
    JSONHTTPTransport, ProviderAuditSink, ProviderConfigurationError,
    ProviderHTTPError, TenantConfigResolver, UrllibJSONTransport, emit_audit,
    json_request,
)


class HubSpotCRMProvider:
    provider_key = "hubspot"
    base_url = "https://api.hubapi.com"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport: JSONHTTPTransport | None = None, timeout: float = 10.0,
                 api_version: str = "2026-03") -> None:
        if timeout <= 0 or not api_version.replace("-", "").isdigit():
            raise ValueError("valid HubSpot API version and timeout are required")
        self._configs = configs
        self._audit = audit_sink
        self._transport = transport or UrllibJSONTransport()
        self._timeout = timeout
        self._version = api_version

    def create_contact(self, contact: Mapping[str, object], *, tenant_id: str,
                       business_id: str, idempotency_key: str) -> Mapping[str, object]:
        return self._run(tenant_id, business_id, "create_contact", idempotency_key,
                         lambda token: self._contact_write("POST", "", contact, token, idempotency_key))

    def update_contact(self, external_id: str, contact: Mapping[str, object], *,
                       tenant_id: str, business_id: str,
                       idempotency_key: str) -> Mapping[str, object]:
        return self._run(tenant_id, business_id, "update_contact", idempotency_key,
                         lambda token: self._contact_write("PATCH", f"/{quote(external_id, safe='')}",
                                                           contact, token, idempotency_key))

    def get_contact(self, external_id: str, *, tenant_id: str,
                    business_id: str) -> Mapping[str, object] | None:
        return self._run(tenant_id, business_id, "get_contact", "read",
                         lambda token: self._contact_read(external_id, token))

    def create_activity(self, activity: Mapping[str, object], *, tenant_id: str,
                        business_id: str, idempotency_key: str) -> Mapping[str, object]:
        def call(token):
            content = self._activity_text(activity)
            payload = {"properties": {
                "hs_task_subject": str(activity.get("type", "Platform activity"))[:255],
                "hs_task_body": content,
                "hs_timestamp": str(activity.get("occurred_at", "")),
                "hs_task_status": "NOT_STARTED",
            }}
            response = json_request(self._transport, "POST", self._url("tasks"),
                                    headers=self._headers(token, idempotency_key),
                                    payload=payload, timeout=self._timeout)
            return self._normalize(response)
        return self._run(tenant_id, business_id, "create_activity", idempotency_key, call)

    def create_conversation_note(self, external_contact_id: str | None,
                                 note: Mapping[str, object], *, tenant_id: str,
                                 business_id: str, idempotency_key: str) -> Mapping[str, object]:
        def call(token):
            payload: dict[str, object] = {
                "properties": {"hs_note_body": self._activity_text(note),
                               "hs_timestamp": str(note.get("occurred_at", ""))},
            }
            if external_contact_id:
                payload["associations"] = [{
                    "to": {"id": external_contact_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED",
                               "associationTypeId": 202}],
                }]
            response = json_request(self._transport, "POST", self._url("notes"),
                                    headers=self._headers(token, idempotency_key),
                                    payload=payload, timeout=self._timeout)
            return self._normalize(response)
        return self._run(tenant_id, business_id, "create_conversation_note", idempotency_key, call)

    def _contact_write(self, method, suffix, contact, token, idempotency_key):
        properties = self._contact_properties(contact)
        response = json_request(
            self._transport, method, self._url(f"contacts{suffix}"),
            headers=self._headers(token, idempotency_key),
            payload={"properties": properties}, timeout=self._timeout,
        )
        return self._normalize(response)

    def _contact_read(self, external_id, token):
        props = "firstname,lastname,email,phone,whatsapp,platform_consent_status,platform_suppressed"
        query = urlencode({"properties": props})
        object_url = self._url(f"contacts/{quote(external_id, safe='')}")
        response = json_request(
            self._transport, "GET", f"{object_url}?{query}",
            headers=self._headers(token), timeout=self._timeout,
        )
        return self._normalize(response)

    @staticmethod
    def _contact_properties(contact: Mapping[str, object]) -> dict[str, object]:
        properties: dict[str, object] = {}
        full_name = str(contact.get("full_name") or "").strip()
        if full_name:
            pieces = full_name.split(maxsplit=1)
            properties["firstname"] = pieces[0]
            if len(pieces) > 1:
                properties["lastname"] = pieces[1]
        for source, target in (("email", "email"), ("phone", "phone"),
                               ("whatsapp", "whatsapp"), ("timezone", "hs_timezone")):
            if contact.get(source) is not None:
                properties[target] = contact[source]
        if contact.get("consent_status") is not None:
            properties["platform_consent_status"] = json.dumps(contact["consent_status"], sort_keys=True)
        if contact.get("suppressed") is not None:
            properties["platform_suppressed"] = str(bool(contact["suppressed"])).lower()
        return properties

    def _run(self, tenant_id, business_id, operation, idempotency_key, call):
        try:
            config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
            token = config.get("access_token")
            if not token or not token.strip():
                raise ProviderConfigurationError("HubSpot access token is not configured")
            response = call(token.strip())
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation=operation,
                       idempotency_key=idempotency_key, outcome="succeeded")
            return response
        except Exception as exc:
            code = (f"provider_http_error:{exc.status_code}" if isinstance(exc, ProviderHTTPError)
                    else f"hubspot_error:{type(exc).__name__}")
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation=operation,
                       idempotency_key=idempotency_key, outcome="failed", error_code=code)
            raise RuntimeError(code) from None

    def _url(self, object_type):
        return f"{self.base_url}/crm/objects/{self._version}/{object_type}"

    @staticmethod
    def _headers(token, idempotency_key=None):
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _activity_text(activity):
        return json.dumps(dict(activity), sort_keys=True, default=str)

    @staticmethod
    def _normalize(response):
        return {"id": response.get("id"), "updated_at": response.get("updatedAt"), **response}


__all__ = ["HubSpotCRMProvider"]
