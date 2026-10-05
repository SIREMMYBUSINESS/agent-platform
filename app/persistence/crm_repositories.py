"""Tenant-scoped repositories for durable CRM sync state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlmodel import Session, select

from app.persistence.models import CRMSyncRecord, Contact, ConsentRecord, SuppressionRule
from app.persistence.repositories import _database_call, _require_tenant


class SQLCRMSyncRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_key(self, *, tenant_id: str, provider_key: str, idempotency_key: str) -> CRMSyncRecord | None:
        _require_tenant(tenant_id)
        stmt = select(CRMSyncRecord).where(
            CRMSyncRecord.tenant_id == tenant_id,
            CRMSyncRecord.provider_key == provider_key,
            CRMSyncRecord.idempotency_key == idempotency_key,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_for_tenant(self, *, tenant_id: str, record_id: UUID) -> CRMSyncRecord | None:
        _require_tenant(tenant_id)
        stmt = select(CRMSyncRecord).where(
            CRMSyncRecord.tenant_id == tenant_id, CRMSyncRecord.id == record_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def latest_contact_link(self, *, tenant_id: str, business_id: str,
                            provider_key: str, contact_id: str) -> CRMSyncRecord | None:
        _require_tenant(tenant_id)
        stmt = select(CRMSyncRecord).where(
            CRMSyncRecord.tenant_id == tenant_id,
            CRMSyncRecord.business_id == business_id,
            CRMSyncRecord.provider_key == provider_key,
            CRMSyncRecord.entity_type == "contact",
            CRMSyncRecord.entity_id == contact_id,
            CRMSyncRecord.status == "synced",
            CRMSyncRecord.external_id.is_not(None),
        ).order_by(CRMSyncRecord.updated_at.desc(), CRMSyncRecord.id.desc())
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_contact_link_by_external_id(self, *, tenant_id: str, business_id: str,
                                        provider_key: str, external_id: str) -> CRMSyncRecord | None:
        _require_tenant(tenant_id)
        stmt = select(CRMSyncRecord).where(
            CRMSyncRecord.tenant_id == tenant_id,
            CRMSyncRecord.business_id == business_id,
            CRMSyncRecord.provider_key == provider_key,
            CRMSyncRecord.entity_type == "contact",
            CRMSyncRecord.status == "synced",
            CRMSyncRecord.external_id == external_id,
        ).order_by(CRMSyncRecord.updated_at.desc(), CRMSyncRecord.id.desc())
        return _database_call(lambda: self._session.exec(stmt).first())

    def create(self, *, tenant_id: str, business_id: str, provider_key: str,
               direction: str, entity_type: str, entity_id: str, event_type: str,
               idempotency_key: str, payload: dict[str, Any]) -> CRMSyncRecord:
        _require_tenant(tenant_id)
        row = CRMSyncRecord(
            tenant_id=tenant_id, business_id=business_id, provider_key=provider_key,
            direction=direction, entity_type=entity_type, entity_id=entity_id,
            event_type=event_type, idempotency_key=idempotency_key, payload=payload,
        )
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def set_result(self, *, tenant_id: str, record_id: UUID, status: str,
                   external_id: str | None = None, payload: dict[str, Any] | None = None,
                   error: str | None = None) -> CRMSyncRecord | None:
        _require_tenant(tenant_id)
        values: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
        if external_id is not None:
            values["external_id"] = external_id
        if payload is not None:
            values["payload"] = payload
        values["last_error"] = error
        stmt = update(CRMSyncRecord).where(
            CRMSyncRecord.tenant_id == tenant_id, CRMSyncRecord.id == record_id,
        ).values(**values)
        def persist():
            result = self._session.exec(stmt)
            if result.rowcount != 1:
                return None
            self._session.flush()
            return self._session.exec(select(CRMSyncRecord).where(
                CRMSyncRecord.tenant_id == tenant_id, CRMSyncRecord.id == record_id,
            )).first()
        return _database_call(persist)

    def increment_attempt(self, *, tenant_id: str, record_id: UUID) -> None:
        _require_tenant(tenant_id)
        stmt = update(CRMSyncRecord).where(
            CRMSyncRecord.tenant_id == tenant_id, CRMSyncRecord.id == record_id,
        ).values(attempts=CRMSyncRecord.attempts + 1, updated_at=datetime.now(UTC))
        _database_call(lambda: self._session.exec(stmt))


class SQLCRMContactRepository:
    """CRM-specific contact updates and trusted consent/suppression state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, tenant_id: str, business_id: str,
               fields: dict[str, Any]) -> Contact:
        _require_tenant(tenant_id)
        allowed = {"full_name", "phone", "email", "whatsapp", "timezone"}
        if set(fields) - allowed:
            raise ValueError("unsupported CRM contact fields")
        row = Contact(tenant_id=tenant_id, business_id=business_id, **fields)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def update(self, *, tenant_id: str, business_id: str, contact_id: str,
               fields: dict[str, Any]) -> Contact | None:
        _require_tenant(tenant_id)
        allowed = {"full_name", "phone", "email", "whatsapp", "timezone"}
        if set(fields) - allowed:
            raise ValueError("unsupported CRM contact fields")
        values = {**fields, "updated_at": datetime.now(UTC)}
        stmt = update(Contact).where(
            Contact.tenant_id == tenant_id, Contact.business_id == business_id,
            Contact.id == contact_id,
        ).values(**values)
        def persist():
            result = self._session.exec(stmt)
            if result.rowcount != 1:
                return None
            self._session.flush()
            return self._session.exec(select(Contact).where(
                Contact.tenant_id == tenant_id, Contact.business_id == business_id,
                Contact.id == contact_id,
            )).first()
        return _database_call(persist)

    def update_consent(self, *, tenant_id: str, business_id: str, contact_id: str,
                       channel: str, status: str, source_reference: str | None = None) -> None:
        _require_tenant(tenant_id)
        row = ConsentRecord(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
            channel=channel, status=status, source="crm_sync",
            source_reference=source_reference,
        )
        def persist():
            self._session.add(row)
            self._session.flush()
        _database_call(persist)

    def set_suppressed(self, *, tenant_id: str, business_id: str, contact_id: str,
                       suppressed: bool, reason_code: str = "crm_suppression") -> None:
        _require_tenant(tenant_id)
        def persist():
            rows = self._session.exec(select(SuppressionRule).where(
                SuppressionRule.tenant_id == tenant_id,
                SuppressionRule.business_id == business_id,
                SuppressionRule.contact_id == contact_id,
                SuppressionRule.reason_code == reason_code,
            )).all()
            if suppressed and not any(row.active for row in rows):
                self._session.add(SuppressionRule(
                    tenant_id=tenant_id, business_id=business_id,
                    contact_id=contact_id, reason_code=reason_code,
                    created_by="crm_sync",
                ))
            elif not suppressed:
                for row in rows:
                    row.active = False
            self._session.flush()
        _database_call(persist)
