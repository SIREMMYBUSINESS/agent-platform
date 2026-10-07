"""Tenant-scoped, durable CRM synchronization with retries and conflict records."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID

from app.integrations.crm.base import CRMProvider
from app.integrations.crm.events import CRMEvent, CRMEventType
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.metrics import metrics
from app.regions.service import RegionService


class CRMSyncError(Exception):
    """Base CRM synchronization error."""


class CRMNotFound(CRMSyncError):
    pass


class CRMConflict(CRMSyncError):
    pass


class CRMProviderError(CRMSyncError):
    pass


class CRMSyncEngine:
    def __init__(self, *, provider: CRMProvider, uow_factory: Callable[[], UnitOfWork],
                 business_id: str, max_attempts: int = 5,
                 now_provider: Callable[[], datetime] | None = None) -> None:
        if not business_id.strip() or max_attempts < 1:
            raise ValueError("business_id and a positive max_attempts are required")
        provider_key = getattr(provider, "provider_key", None)
        if not isinstance(provider_key, str) or not provider_key.strip():
            raise ValueError("CRM provider_key must be a nonblank string")
        self.provider = provider
        self.provider_key = provider_key.strip()
        self._uow_factory = uow_factory
        self.business_id = business_id.strip()
        self.max_attempts = max_attempts
        self._now = now_provider or (lambda: datetime.now(UTC))

    def sync_outbound(self, principal: AuthenticatedPrincipal, event: CRMEvent):
        self._authorize(principal)
        key = self._validate_key(event.idempotency_key)
        entity_type = event.entity_type.strip().lower()
        if entity_type not in {"contact", "conversation", "campaign"}:
            raise ValueError("unsupported CRM entity_type")
        entity_id = str(event.entity_id).strip()
        if not entity_id:
            raise ValueError("entity_id must not be blank")
        with self._uow_factory() as uow:
            existing = uow.crm_sync.get_by_key(
                tenant_id=principal.tenant_id, provider_key=self.provider_key,
                idempotency_key=key,
            )
            if existing is not None:
                if (existing.entity_type != entity_type or existing.entity_id != entity_id
                        or existing.event_type != str(event.event_type)):
                    raise CRMConflict("idempotency key is already bound to another CRM event")
                if existing.status in {"synced", "conflict"}:
                    return self._snapshot(existing)
                record = existing
            else:
                payload = self._build_outbound_payload(uow, principal.tenant_id,
                                                       entity_type, entity_id,
                                                       str(event.event_type), event.payload or {})
                record = uow.crm_sync.create(
                    tenant_id=principal.tenant_id, business_id=self.business_id,
                    provider_key=self.provider_key, direction="outbound",
                    entity_type=entity_type, entity_id=entity_id,
                    event_type=str(event.event_type), idempotency_key=key,
                    payload=payload,
                )
                record = self._snapshot(record)
        result = self._deliver(principal, record)
        metrics.event("crm", "sync_request", "failed" if result.status == "failed" else "succeeded")
        return result

    def retry_sync(self, principal: AuthenticatedPrincipal, record_id: UUID):
        self._authorize(principal)
        with self._uow_factory() as uow:
            record = uow.crm_sync.get_for_tenant(
                tenant_id=principal.tenant_id, record_id=record_id,
            )
            if record is None:
                raise CRMNotFound("CRM sync record not found")
            if record.status == "synced":
                return self._snapshot(record)
            if record.status == "conflict":
                raise CRMConflict("conflicted CRM sync requires reconciliation")
            if record.attempts >= self.max_attempts:
                raise CRMProviderError("CRM sync retry limit exceeded")
            if record.direction != "outbound":
                raise CRMConflict("inbound CRM records cannot be retried outbound")
            record = self._snapshot(record)
        result = self._deliver(principal, record)
        metrics.event("crm", "retry", "failed" if result.status == "failed" else "succeeded")
        return result

    def sync_inbound(self, principal: AuthenticatedPrincipal, *,
                     payload: Mapping[str, Any], idempotency_key: str):
        """Apply a CRM contact update; tenant and business come only from trusted context."""
        self._authorize(principal)
        key = self._validate_key(idempotency_key)
        if not isinstance(payload, Mapping):
            raise ValueError("CRM payload must be an object")
        external_id = payload.get("id")
        if not isinstance(external_id, str) or not external_id.strip():
            raise ValueError("CRM contact id is required")
        fields = self._contact_fields(payload)
        source_version = self._timestamp(payload.get("updated_at"))
        # CRM supplied tenant_id/business_id are intentionally ignored.
        with self._uow_factory() as uow:
            existing = uow.crm_sync.get_by_key(
                tenant_id=principal.tenant_id, provider_key=self.provider_key,
                idempotency_key=key,
            )
            if existing is not None:
                if existing.direction != "inbound" or existing.external_id != external_id:
                    raise CRMConflict("idempotency key is already bound to another CRM event")
                return self._snapshot(existing)
            link = uow.crm_sync.get_contact_link_by_external_id(
                tenant_id=principal.tenant_id, business_id=self.business_id,
                provider_key=self.provider_key, external_id=external_id,
            )
            contact = (uow.contacts.get(tenant_id=principal.tenant_id,
                                        contact_id=link.entity_id) if link else None)
            record = uow.crm_sync.create(
                tenant_id=principal.tenant_id, business_id=self.business_id,
                provider_key=self.provider_key, direction="inbound",
                entity_type="contact", entity_id=contact.id if contact else external_id,
                event_type="contact.inbound_sync", idempotency_key=key,
                payload={"fields": fields, "remote_updated_at": source_version.isoformat() if source_version else None},
            )
            if contact is not None and link is not None:
                last_remote = self._timestamp(link.payload.get("remote_updated_at"))
                local_updated = self._persisted_timestamp(contact.updated_at)
                last_local = self._timestamp(link.payload.get("local_updated_at"))
                if (source_version is not None and last_remote is not None
                        and local_updated is not None and last_local is not None and local_updated > last_local
                        and source_version <= last_remote):
                    return self._snapshot(uow.crm_sync.set_result(
                        tenant_id=principal.tenant_id, record_id=record.id,
                        status="conflict", external_id=external_id,
                        error="local_contact_changed_since_last_sync",
                    ))
                contact = uow.crm_contacts.update(
                    tenant_id=principal.tenant_id, business_id=self.business_id,
                    contact_id=contact.id, fields=fields,
                )
            else:
                contact = uow.crm_contacts.create(
                    tenant_id=principal.tenant_id, business_id=self.business_id,
                    fields=fields,
                )
                record.entity_id = contact.id
            self._apply_contact_status(uow, principal.tenant_id, contact.id, payload, external_id)
            completed = uow.crm_sync.set_result(
                tenant_id=principal.tenant_id, record_id=record.id,
                status="synced", external_id=external_id,
                payload={"fields": fields,
                         "remote_updated_at": source_version.isoformat() if source_version else None,
                         "local_updated_at": self._persisted_timestamp(contact.updated_at).isoformat()},
            )
            return self._snapshot(completed)

    def _deliver(self, principal: AuthenticatedPrincipal, record):
        if record.attempts >= self.max_attempts:
            raise CRMProviderError("CRM sync retry limit exceeded")
        with self._uow_factory() as uow:
            uow.crm_sync.increment_attempt(tenant_id=principal.tenant_id, record_id=record.id)
        metrics.event("crm", "sync_attempt", "attempted")
        try:
            with self._uow_factory() as uow:
                business = uow.businesses.get(tenant_id=principal.tenant_id,
                                              business_id=record.business_id)
                if business is None:
                    raise CRMNotFound("business not found in regional scope")
                provider_region = RegionService(
                    uow_factory=self._uow_factory,
                ).authorize_provider_transfer(
                    uow=uow, tenant_id=principal.tenant_id, business=business,
                    provider_kind="crm", resource_type=record.entity_type,
                    resource_id=record.entity_id, actor_id=principal.principal_id,
                )
            result = self._call_provider(principal.tenant_id, record,
                                         provider_region=provider_region)
            if not isinstance(result, Mapping):
                result = {}
            external_id = result.get("id") or record.external_id
            with self._uow_factory() as uow:
                completed = uow.crm_sync.set_result(
                    tenant_id=principal.tenant_id, record_id=record.id,
                    status="synced", external_id=str(external_id) if external_id is not None else None,
                    payload={**record.payload,
                             "remote_updated_at": self._as_iso(result.get("updated_at")),
                             "local_updated_at": self._as_iso(record.payload.get("local_updated_at"))},
                )
                if completed is None:
                    raise CRMNotFound("CRM sync record not found")
                result = self._snapshot(completed)
            metrics.event("crm", "sync", "succeeded")
            return result
        except Exception as exc:
            # Store only the exception type. Provider messages may contain PII or secrets.
            with self._uow_factory() as uow:
                failed = uow.crm_sync.set_result(
                    tenant_id=principal.tenant_id, record_id=record.id,
                    status="failed", error=f"provider_error:{type(exc).__name__}",
                )
                if failed is None:
                    raise CRMNotFound("CRM sync record not found") from exc
                result = self._snapshot(failed)
            metrics.event("crm", "sync", "failed")
            metrics.event("crm", "failure", "provider")
            return result

    def _call_provider(self, tenant_id: str, record, *, provider_region: str | None = None):
        payload = record.payload
        key = record.idempotency_key
        region_kwargs = ({"region_id": provider_region}
                         if provider_region and getattr(self.provider, "regional_provider", False)
                         else {})
        if record.entity_type == "contact":
            if record.external_id:
                return self.provider.update_contact(
                    record.external_id, payload["contact"], tenant_id=tenant_id,
                    business_id=record.business_id, idempotency_key=key, **region_kwargs,
                )
            with self._uow_factory() as uow:
                link = uow.crm_sync.latest_contact_link(
                    tenant_id=tenant_id, business_id=record.business_id,
                    provider_key=self.provider_key, contact_id=record.entity_id,
                )
                linked_external_id = link.external_id if link is not None else None
            if linked_external_id is not None:
                return self.provider.update_contact(
                    linked_external_id, payload["contact"], tenant_id=tenant_id,
                    business_id=record.business_id, idempotency_key=key, **region_kwargs,
                )
            return self.provider.create_contact(
                payload["contact"], tenant_id=tenant_id,
                business_id=record.business_id, idempotency_key=key, **region_kwargs,
            )
        activity = payload["activity"]
        if record.entity_type == "conversation" and record.event_type in {
            CRMEventType.CONVERSATION_CREATED.value, CRMEventType.CONVERSATION_CLOSED.value,
        }:
            contact_link = payload.get("external_contact_id")
            return self.provider.create_conversation_note(
                contact_link, activity, tenant_id=tenant_id,
                business_id=record.business_id, idempotency_key=key, **region_kwargs,
            )
        return self.provider.create_activity(
            activity, tenant_id=tenant_id, business_id=record.business_id,
            idempotency_key=key, **region_kwargs,
        )

    def _build_outbound_payload(self, uow, tenant_id: str, entity_type: str,
                                entity_id: str, event_type: str,
                                extra: dict[str, Any]) -> dict[str, Any]:
        if entity_type == "contact":
            contact = uow.contacts.get(tenant_id=tenant_id, contact_id=entity_id)
            if contact is None or contact.business_id != self.business_id:
                raise CRMNotFound("contact not found in tenant business scope")
            consents = {}
            for channel in ("email", "sms", "whatsapp"):
                consent = uow.consent_records.current(
                    tenant_id=tenant_id, business_id=self.business_id,
                    contact_id=contact.id, channel=channel,
                )
                if consent is not None:
                    consents[channel] = consent.status
            suppressed = any(uow.suppressions.find_active(
                tenant_id=tenant_id, business_id=self.business_id,
                contact_id=contact.id, lead_id=None, channel=channel,
            ) is not None for channel in ("email", "sms", "whatsapp"))
            return {"local_updated_at": self._persisted_timestamp(contact.updated_at).isoformat(),
                    "contact": {"id": contact.id, "full_name": contact.full_name,
                                "email": contact.email, "phone": contact.phone,
                                "whatsapp": contact.whatsapp, "timezone": contact.timezone,
                                "consent_status": consents,
                                "suppressed": suppressed}}
        if entity_type == "conversation":
            conversation = uow.conversations.get(tenant_id=tenant_id, conversation_id=entity_id)
            if conversation is None or conversation.business_id != self.business_id:
                raise CRMNotFound("conversation not found in tenant business scope")
            contact_link = uow.crm_sync.latest_contact_link(
                tenant_id=tenant_id, business_id=self.business_id,
                provider_key=self.provider_key, contact_id=conversation.contact_id,
            )
            return {"external_contact_id": contact_link.external_id if contact_link else None,
                    "activity": {**self._safe_event_payload(extra),
                                 "type": event_type, "conversation_id": str(conversation.id),
                                 "contact_id": conversation.contact_id,
                                 "channel": conversation.channel, "status": conversation.status,
                                 "occurred_at": self._as_iso(self._now())}}
        try:
            campaign_id = UUID(entity_id)
        except (ValueError, TypeError) as exc:
            raise ValueError("campaign entity_id must be a UUID") from exc
        campaign = uow.campaigns.get(
            tenant_id=tenant_id, business_id=self.business_id, campaign_id=campaign_id,
        )
        if campaign is None:
            raise CRMNotFound("campaign not found in tenant business scope")
        return {"activity": {**self._safe_event_payload(extra),
                             "type": event_type, "campaign_id": str(campaign.id),
                             "campaign_name": campaign.name, "channel": campaign.channel,
                             "status": campaign.status, "occurred_at": self._as_iso(self._now()),
                             }}

    @staticmethod
    def _safe_event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        # Scope and identity fields always come from principal-scoped persistence.
        reserved = {"tenant_id", "business_id", "contact_id", "conversation_id", "campaign_id"}
        return {key: value for key, value in payload.items() if key not in reserved}

    def _apply_contact_status(self, uow, tenant_id: str, contact_id: str,
                              payload: Mapping[str, Any], external_id: str) -> None:
        consent = payload.get("consent_status", {})
        if consent is not None:
            if not isinstance(consent, Mapping):
                raise ValueError("consent_status must map channels to statuses")
            allowed = {"email", "sms", "whatsapp"}
            if set(consent) - allowed:
                raise ValueError("unsupported consent channel")
            for channel, status in consent.items():
                if status not in {"granted", "revoked", "expired", "unknown"}:
                    raise ValueError("unsupported consent status")
                uow.crm_contacts.update_consent(
                    tenant_id=tenant_id, business_id=self.business_id,
                    contact_id=contact_id, channel=channel, status=status,
                    source_reference=external_id,
                )
        suppressed = payload.get("suppressed")
        if suppressed is not None:
            if not isinstance(suppressed, bool):
                raise ValueError("suppressed must be a boolean")
            uow.crm_contacts.set_suppressed(
                tenant_id=tenant_id, business_id=self.business_id,
                contact_id=contact_id, suppressed=suppressed,
            )

    @staticmethod
    def _contact_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
        aliases = {"name": "full_name", "full_name": "full_name", "email": "email",
                   "phone": "phone", "whatsapp": "whatsapp", "timezone": "timezone"}
        fields: dict[str, Any] = {}
        for source, target in aliases.items():
            if source in payload:
                value = payload[source]
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{source} must be a string or null")
                fields[target] = value
        return fields

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("CRM updated_at must be an ISO timestamp") from exc
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("CRM updated_at must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _persisted_timestamp(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _as_iso(cls, value: Any) -> str | None:
        stamp = cls._timestamp(value)
        return stamp.isoformat() if stamp else None

    @staticmethod
    def _validate_key(value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("idempotency_key must be between 1 and 200 characters")
        return value.strip()

    def _authorize(self, principal: AuthenticatedPrincipal) -> None:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("CRM clock must be timezone-aware")
        if (not isinstance(principal, AuthenticatedPrincipal)
                or principal.issued_at > now
                or principal.expires_at is not None and principal.expires_at <= now
                or "crm_sync" not in principal.roles):
            raise PermissionError("valid crm_sync principal required")

    @staticmethod
    def _snapshot(record):
        return type(record)(**record.model_dump())


__all__ = ["CRMConflict", "CRMNotFound", "CRMProviderError", "CRMSyncEngine", "CRMSyncError"]
