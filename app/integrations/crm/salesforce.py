"""Salesforce REST CRMProvider adapter with external-ID upserts."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from urllib.parse import quote

from app.integrations.provider_support import (
    JSONHTTPTransport, ProviderAuditSink, ProviderConfigurationError,
    ProviderHTTPError, TenantConfigResolver, UrllibJSONTransport, emit_audit,
    json_request,
)


class SalesforceCRMProvider:
    provider_key = "salesforce"

    def __init__(self, *, configs: TenantConfigResolver, audit_sink: ProviderAuditSink,
                 transport: JSONHTTPTransport | None = None, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._configs = configs
        self._audit = audit_sink
        self._transport = transport or UrllibJSONTransport()
        self._timeout = timeout

    def create_contact(self, contact: Mapping[str, object], *, tenant_id: str,
                       business_id: str, idempotency_key: str) -> Mapping[str, object]:
        return self._run(tenant_id, business_id, "create_contact", idempotency_key,
                         lambda cfg: self._upsert_contact(cfg, contact, idempotency_key))

    def update_contact(self, external_id: str, contact: Mapping[str, object], *,
                       tenant_id: str, business_id: str,
                       idempotency_key: str) -> Mapping[str, object]:
        return self._run(tenant_id, business_id, "update_contact", idempotency_key,
                         lambda cfg: self._update_contact(cfg, external_id, contact, idempotency_key))

    def get_contact(self, external_id: str, *, tenant_id: str,
                    business_id: str) -> Mapping[str, object] | None:
        return self._run(tenant_id, business_id, "get_contact", "read",
                         lambda cfg: self._get_record(cfg, "Contact", external_id))

    def create_activity(self, activity: Mapping[str, object], *, tenant_id: str,
                        business_id: str, idempotency_key: str) -> Mapping[str, object]:
        def call(cfg):
            external_field = self._required(cfg, "activity_external_id_field")
            fields = {
                "Subject": str(activity.get("type", "Platform activity"))[:255],
                "Status": "Completed",
                "Description": self._activity_text(activity),
                external_field: idempotency_key,
            }
            if activity.get("external_contact_id"):
                fields["WhoId"] = activity["external_contact_id"]
            return self._upsert(cfg, "Task", external_field, idempotency_key, fields)
        return self._run(tenant_id, business_id, "create_activity", idempotency_key, call)

    def create_conversation_note(self, external_contact_id: str | None,
                                 note: Mapping[str, object], *, tenant_id: str,
                                 business_id: str, idempotency_key: str) -> Mapping[str, object]:
        def call(cfg):
            external_field = self._required(cfg, "activity_external_id_field")
            fields = {
                "Subject": "Receptionist conversation note",
                "Status": "Completed",
                "Description": self._activity_text(note),
                external_field: idempotency_key,
            }
            if external_contact_id:
                fields["WhoId"] = external_contact_id
            return self._upsert(cfg, "Task", external_field, idempotency_key, fields)
        return self._run(tenant_id, business_id, "create_conversation_note", idempotency_key, call)

    def _upsert_contact(self, cfg, contact, key):
        external_field = self._required(cfg, "contact_external_id_field")
        fields = {**self._contact_fields(contact), external_field: key}
        return self._upsert(cfg, "Contact", external_field, key, fields)

    def _update_contact(self, cfg, external_id, contact, idempotency_key):
        version = self._version(cfg)
        url = f"{self._root(cfg, version)}/sobjects/Contact/{quote(external_id, safe='')}"
        json_request(self._transport, "PATCH", url,
                     headers=self._headers(cfg, idempotency_key),
                     payload=self._contact_fields(contact), timeout=self._timeout)
        return self._get_record(cfg, "Contact", external_id)

    def _upsert(self, cfg, object_name, external_field, external_value, fields):
        version = self._version(cfg)
        url = (f"{self._root(cfg, version)}/sobjects/{object_name}/"
               f"{quote(external_field, safe='')}/{quote(str(external_value), safe='')}")
        json_request(self._transport, "PATCH", url,
                     headers=self._headers(cfg, str(external_value)), payload=fields,
                     timeout=self._timeout)
        response = json_request(
            self._transport, "GET", url,
            headers=self._headers(cfg), timeout=self._timeout,
        )
        return response

    def _get_record(self, cfg, object_name, record_id):
        version = self._version(cfg)
        url = f"{self._root(cfg, version)}/sobjects/{object_name}/{quote(record_id, safe='')}"
        return json_request(self._transport, "GET", url,
                            headers=self._headers(cfg), timeout=self._timeout)

    @staticmethod
    def _contact_fields(contact):
        fields = {}
        for source, target in (("email", "Email"), ("phone", "Phone"),
                               ("whatsapp", "MobilePhone"), ("timezone", "TimeZoneSidKey")):
            value = contact.get(source)
            if value is not None:
                fields[target] = value
        full_name = str(contact.get("full_name") or "").strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            if len(parts) == 1:
                fields["LastName"] = parts[0]
            else:
                fields["FirstName"], fields["LastName"] = parts
        # These fields must be provisioned as custom fields in each Salesforce org.
        if contact.get("consent_status") is not None:
            fields["Platform_Consent_Status__c"] = str(contact["consent_status"])
        if contact.get("suppressed") is not None:
            fields["Platform_Suppressed__c"] = bool(contact["suppressed"])
        return fields

    def _run(self, tenant_id, business_id, operation, idempotency_key, call):
        try:
            config = self._configs.resolve(tenant_id=tenant_id, business_id=business_id)
            response = call(config)
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation=operation,
                       idempotency_key=idempotency_key, outcome="succeeded")
            return response
        except Exception as exc:
            code = (f"provider_http_error:{exc.status_code}" if isinstance(exc, ProviderHTTPError)
                    else f"salesforce_error:{type(exc).__name__}")
            emit_audit(self._audit, tenant_id=tenant_id, business_id=business_id,
                       provider=self.provider_key, operation=operation,
                       idempotency_key=idempotency_key, outcome="failed", error_code=code)
            raise RuntimeError(code) from None

    @classmethod
    def _root(cls, config, version):
        instance_url = cls._required(config, "instance_url").rstrip("/")
        if not instance_url.startswith("https://"):
            raise ProviderConfigurationError("Salesforce instance_url must use HTTPS")
        return f"{instance_url}/services/data/{version}"

    @staticmethod
    def _version(config):
        version = config.get("api_version", "v66.0")
        if not version.startswith("v") or not version[1:].replace(".", "").isdigit():
            raise ProviderConfigurationError("Salesforce api_version is invalid")
        return version

    @staticmethod
    def _headers(config, idempotency_key=None):
        token = SalesforceCRMProvider._required(config, "access_token")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Sforce-Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _required(config, key):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ProviderConfigurationError(f"Salesforce {key} is not configured")
        return value.strip()

    @staticmethod
    def _activity_text(activity):
        import json
        return json.dumps(dict(activity), sort_keys=True, default=str)


__all__ = ["SalesforceCRMProvider"]
