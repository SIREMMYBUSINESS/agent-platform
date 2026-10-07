"""Tenant-scoped persistence contracts for outbound lead qualification."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import update
from sqlmodel import Session, select

from app.persistence.models import (
    Lead, LeadEnrichment, LeadEventLog, LeadScore, LeadSource, LeadStateTransition,
)
from app.persistence.repositories import _database_call, _require_tenant
from app.models import LeadLifecycle


ALLOWED_LEAD_TRANSITIONS = {
    LeadLifecycle.IMPORTED.value: {LeadLifecycle.ENRICHED.value, LeadLifecycle.SCORED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.ENRICHED.value: {LeadLifecycle.SCORED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.SCORED.value: {LeadLifecycle.QUALIFIED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.QUALIFIED.value: {LeadLifecycle.CONTACTED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.CONTACTED.value: {LeadLifecycle.RESPONDED.value, LeadLifecycle.BOOKED.value, LeadLifecycle.CONVERTED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.RESPONDED.value: {LeadLifecycle.BOOKED.value, LeadLifecycle.CONVERTED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.BOOKED.value: {LeadLifecycle.CONVERTED.value, LeadLifecycle.OPTED_OUT.value, LeadLifecycle.CLOSED.value},
    LeadLifecycle.CONVERTED.value: {LeadLifecycle.CLOSED.value},
    LeadLifecycle.OPTED_OUT.value: {LeadLifecycle.CLOSED.value},
    LeadLifecycle.CLOSED.value: set(),
}


class LeadSourceRepository(Protocol):
    def get_or_create(self, *, tenant_id: str, business_id: str, name: str, source_type: str = "manual", external_reference: str | None = None, source_metadata: dict[str, Any] | None = None) -> LeadSource: ...
    def get(self, *, tenant_id: str, business_id: str, source_id: UUID) -> LeadSource | None: ...


class LeadRepository(Protocol):
    def create(self, *, tenant_id: str, business_id: str, contact_id: str, source_id: UUID) -> Lead: ...
    def get(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> Lead | None: ...
    def latest_for_contact(self, *, tenant_id: str, business_id: str,
                           contact_id: str) -> Lead | None: ...
    def list(self, *, tenant_id: str, business_id: str, offset: int, limit: int) -> list[Lead]: ...


class LeadEnrichmentRepository(Protocol):
    def add(self, *, tenant_id: str, business_id: str, lead_id: UUID, values: dict[str, Any], provider_name: str) -> LeadEnrichment: ...
    def latest(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> LeadEnrichment | None: ...


class LeadScoreRepository(Protocol):
    def add(self, *, tenant_id: str, business_id: str, lead_id: UUID, scores: dict[str, Any]) -> LeadScore: ...
    def latest(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> LeadScore | None: ...


class LeadTransitionRepository(Protocol):
    def record_imported(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> LeadStateTransition: ...
    def transition(self, *, tenant_id: str, business_id: str, lead_id: UUID, expected_state: str, new_state: str, context: dict[str, Any]) -> LeadStateTransition: ...
    def history(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> list[LeadStateTransition]: ...


class LeadEventRepository(Protocol):
    def add(self, *, tenant_id: str, business_id: str, lead_id: UUID, event_type: str, payload: dict[str, Any]) -> LeadEventLog: ...
    def list_for_lead(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> list[LeadEventLog]: ...


class SQLLeadSourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, *, tenant_id: str, business_id: str, name: str, source_type: str = "manual", external_reference: str | None = None, source_metadata: dict[str, Any] | None = None) -> LeadSource:
        _require_tenant(tenant_id)
        normalized = name.strip()
        statement = select(LeadSource).where(
            LeadSource.tenant_id == tenant_id,
            LeadSource.business_id == business_id,
            LeadSource.name == normalized,
        )
        def persist() -> LeadSource:
            source = self._session.exec(statement).first()
            if source is None:
                source = LeadSource(tenant_id=tenant_id, business_id=business_id, name=normalized, source_type=source_type, external_reference=external_reference, source_metadata=source_metadata or {})
                self._session.add(source)
                self._session.flush()
            return source
        return _database_call(persist)

    def get(self, *, tenant_id: str, business_id: str, source_id: UUID) -> LeadSource | None:
        _require_tenant(tenant_id)
        statement = select(LeadSource).where(
            LeadSource.tenant_id == tenant_id,
            LeadSource.business_id == business_id,
            LeadSource.id == source_id,
        )
        return _database_call(lambda: self._session.exec(statement).first())


class SQLLeadRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, tenant_id: str, business_id: str, contact_id: str, source_id: UUID) -> Lead:
        _require_tenant(tenant_id)
        lead = Lead(tenant_id=tenant_id, business_id=business_id, contact_id=contact_id, source_id=source_id)
        def persist() -> Lead:
            self._session.add(lead)
            self._session.flush()
            return lead
        return _database_call(persist)

    def get(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> Lead | None:
        _require_tenant(tenant_id)
        statement = select(Lead).where(Lead.tenant_id == tenant_id, Lead.business_id == business_id, Lead.id == lead_id)
        return _database_call(lambda: self._session.exec(statement).first())

    def latest_for_contact(self, *, tenant_id: str, business_id: str,
                           contact_id: str) -> Lead | None:
        _require_tenant(tenant_id)
        statement = select(Lead).where(
            Lead.tenant_id == tenant_id, Lead.business_id == business_id,
            Lead.contact_id == contact_id,
        ).order_by(Lead.created_at.desc(), Lead.id.desc())
        return _database_call(lambda: self._session.exec(statement).first())

    def list(self, *, tenant_id: str, business_id: str, offset: int = 0, limit: int = 50) -> list[Lead]:
        _require_tenant(tenant_id)
        statement = select(Lead).where(Lead.tenant_id == tenant_id, Lead.business_id == business_id).order_by(Lead.created_at.desc(), Lead.id.desc()).offset(offset).limit(limit)
        return _database_call(lambda: list(self._session.exec(statement).all()))


class SQLLeadEnrichmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, lead_id: UUID, values: dict[str, Any], provider_name: str) -> LeadEnrichment:
        _require_tenant(tenant_id)
        row = LeadEnrichment(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id, provider_name=provider_name, **values)
        def persist() -> LeadEnrichment:
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def latest(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> LeadEnrichment | None:
        _require_tenant(tenant_id)
        stmt = select(LeadEnrichment).where(LeadEnrichment.tenant_id == tenant_id, LeadEnrichment.business_id == business_id, LeadEnrichment.lead_id == lead_id).order_by(LeadEnrichment.created_at.desc(), LeadEnrichment.id.desc())
        return _database_call(lambda: self._session.exec(stmt).first())


class SQLLeadScoreRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, lead_id: UUID, scores: dict[str, Any]) -> LeadScore:
        _require_tenant(tenant_id)
        row = LeadScore(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id, **scores)
        def persist() -> LeadScore:
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def latest(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> LeadScore | None:
        _require_tenant(tenant_id)
        stmt = select(LeadScore).where(LeadScore.tenant_id == tenant_id, LeadScore.business_id == business_id, LeadScore.lead_id == lead_id).order_by(LeadScore.created_at.desc(), LeadScore.id.desc())
        return _database_call(lambda: self._session.exec(stmt).first())


class SQLLeadTransitionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_imported(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> LeadStateTransition:
        _require_tenant(tenant_id)
        row = LeadStateTransition(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id, from_state=None, to_state="imported", context={})
        def persist() -> LeadStateTransition:
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def transition(self, *, tenant_id: str, business_id: str, lead_id: UUID, expected_state: str, new_state: str, context: dict[str, Any]) -> LeadStateTransition:
        _require_tenant(tenant_id)
        if new_state not in ALLOWED_LEAD_TRANSITIONS.get(expected_state, set()):
            raise ValueError(f"invalid lead lifecycle transition: {expected_state} -> {new_state}")
        def persist() -> LeadStateTransition:
            result = self._session.exec(update(Lead).where(
                Lead.tenant_id == tenant_id, Lead.business_id == business_id,
                Lead.id == lead_id, Lead.status == expected_state,
            ).values(status=new_state, updated_at=datetime.now(UTC)))
            if result.rowcount != 1:
                raise ValueError("lead state changed or lead not found")
            row = LeadStateTransition(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id, from_state=expected_state, to_state=new_state, context=context)
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def history(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> list[LeadStateTransition]:
        _require_tenant(tenant_id)
        stmt = select(LeadStateTransition).where(LeadStateTransition.tenant_id == tenant_id, LeadStateTransition.business_id == business_id, LeadStateTransition.lead_id == lead_id).order_by(LeadStateTransition.created_at, LeadStateTransition.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))


class SQLLeadEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, lead_id: UUID, event_type: str, payload: dict[str, Any]) -> LeadEventLog:
        _require_tenant(tenant_id)
        row = LeadEventLog(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id, event_type=event_type, payload=payload)
        def persist() -> LeadEventLog:
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_for_lead(self, *, tenant_id: str, business_id: str, lead_id: UUID) -> list[LeadEventLog]:
        _require_tenant(tenant_id)
        stmt = select(LeadEventLog).where(LeadEventLog.tenant_id == tenant_id, LeadEventLog.business_id == business_id, LeadEventLog.lead_id == lead_id).order_by(LeadEventLog.created_at, LeadEventLog.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))
