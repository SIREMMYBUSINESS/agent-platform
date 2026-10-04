"""Tenant-scoped persistence contracts for compliance policy decisions."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import or_
from sqlmodel import Session, select

from app.persistence.models import (
    ConsentRecord, CountryPolicy, PolicyDecisionLog, PolicyProfile, SuppressionRule,
)
from app.persistence.repositories import _database_call, _require_tenant


class SQLConsentRecordRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, contact_id: str,
            channel: str, status: str, source: str,
            source_reference: str | None = None,
            expires_at: datetime | None = None) -> ConsentRecord:
        _require_tenant(tenant_id)
        row = ConsentRecord(tenant_id=tenant_id, business_id=business_id,
                            contact_id=contact_id, channel=channel, status=status,
                            source=source, source_reference=source_reference,
                            expires_at=expires_at)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def current(self, *, tenant_id: str, business_id: str, contact_id: str,
                channel: str) -> ConsentRecord | None:
        _require_tenant(tenant_id)
        stmt = select(ConsentRecord).where(
            ConsentRecord.tenant_id == tenant_id,
            ConsentRecord.business_id == business_id,
            ConsentRecord.contact_id == contact_id,
            ConsentRecord.channel == channel,
        ).order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
        return _database_call(lambda: self._session.exec(stmt).first())


class SQLPolicyProfileRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, business_id: str) -> PolicyProfile | None:
        _require_tenant(tenant_id)
        stmt = select(PolicyProfile).where(
            PolicyProfile.tenant_id == tenant_id,
            PolicyProfile.business_id == business_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())


class SQLCountryPolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, country_code: str) -> CountryPolicy | None:
        stmt = select(CountryPolicy).where(
            CountryPolicy.country_code == country_code.upper(),
        )
        return _database_call(lambda: self._session.exec(stmt).first())


class SQLSuppressionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, reason_code: str,
            contact_id: str | None = None, lead_id: UUID | None = None,
            channel: str | None = None, created_by: str = "trusted_admin") -> SuppressionRule:
        _require_tenant(tenant_id)
        row = SuppressionRule(tenant_id=tenant_id, business_id=business_id,
                              reason_code=reason_code, contact_id=contact_id,
                              lead_id=lead_id, channel=channel, created_by=created_by)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def find_active(self, *, tenant_id: str, business_id: str,
                    contact_id: str, lead_id: UUID | None,
                    channel: str) -> SuppressionRule | None:
        _require_tenant(tenant_id)
        subject = [SuppressionRule.contact_id == contact_id]
        if lead_id is not None:
            subject.append(SuppressionRule.lead_id == lead_id)
        stmt = select(SuppressionRule).where(
            SuppressionRule.tenant_id == tenant_id,
            SuppressionRule.business_id == business_id,
            SuppressionRule.active.is_(True),
            or_(SuppressionRule.channel.is_(None), SuppressionRule.channel == channel),
            or_(*subject),
        ).order_by(SuppressionRule.created_at, SuppressionRule.id)
        return _database_call(lambda: self._session.exec(stmt).first())


class SQLPolicyDecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, direction: str,
            channel: str, country_code: str, resource_type: str,
            resource_id: str, decision: str, reason_code: str,
            consent_status: str, policy_source: str,
            overridden: bool = False, override_actor: str | None = None,
            override_reason: str | None = None) -> PolicyDecisionLog:
        _require_tenant(tenant_id)
        row = PolicyDecisionLog(
            tenant_id=tenant_id, business_id=business_id, direction=direction,
            channel=channel, country_code=country_code, resource_type=resource_type,
            resource_id=resource_id, decision=decision, reason_code=reason_code,
            consent_status=consent_status, policy_source=policy_source,
            overridden=overridden, override_actor=override_actor,
            override_reason=override_reason,
        )
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_for_business(self, *, tenant_id: str, business_id: str) -> list[PolicyDecisionLog]:
        _require_tenant(tenant_id)
        stmt = select(PolicyDecisionLog).where(
            PolicyDecisionLog.tenant_id == tenant_id,
            PolicyDecisionLog.business_id == business_id,
        ).order_by(PolicyDecisionLog.created_at, PolicyDecisionLog.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))
