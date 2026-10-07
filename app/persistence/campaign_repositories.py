"""Tenant-scoped campaign persistence contracts and SQLModel repositories."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, update
from sqlmodel import Session, select

from app.persistence.models import (
    Campaign, CampaignEnrollment, CampaignEventLog, Lead, OutreachMessage, CampaignSequence,
    CampaignStep, CampaignStepRun,
)
from app.persistence.repositories import _database_call, _require_tenant


class SQLCampaignRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, tenant_id: str, business_id: str, name: str,
               channel: str, audience_filters: dict[str, Any]) -> Campaign:
        _require_tenant(tenant_id)
        row = Campaign(tenant_id=tenant_id, business_id=business_id, name=name,
                       channel=channel, audience_filters=audience_filters)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def get(self, *, tenant_id: str, business_id: str, campaign_id: UUID) -> Campaign | None:
        _require_tenant(tenant_id)
        stmt = select(Campaign).where(Campaign.tenant_id == tenant_id,
                                      Campaign.business_id == business_id,
                                      Campaign.id == campaign_id)
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_for_tenant(self, *, tenant_id: str, campaign_id: UUID) -> Campaign | None:
        """Resolve a campaign's business from the trusted tenant-scoped row."""
        _require_tenant(tenant_id)
        stmt = select(Campaign).where(
            Campaign.tenant_id == tenant_id, Campaign.id == campaign_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def set_status(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
                   expected_status: str, new_status: str) -> Campaign:
        _require_tenant(tenant_id)
        def persist():
            result = self._session.exec(update(Campaign).where(
                Campaign.tenant_id == tenant_id, Campaign.business_id == business_id,
                Campaign.id == campaign_id, Campaign.status == expected_status,
            ).values(status=new_status, updated_at=datetime.now(UTC)))
            if result.rowcount != 1:
                raise ValueError("campaign state changed or campaign not found")
            return self.get(tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id)
        return _database_call(persist)


class SQLCampaignSequenceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
               name: str = "default", sequence_order: int = 0) -> CampaignSequence:
        _require_tenant(tenant_id)
        row = CampaignSequence(tenant_id=tenant_id, business_id=business_id,
                               campaign_id=campaign_id, name=name, sequence_order=sequence_order)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_for_campaign(self, *, tenant_id: str, business_id: str,
                          campaign_id: UUID) -> list[CampaignSequence]:
        _require_tenant(tenant_id)
        stmt = select(CampaignSequence).where(
            CampaignSequence.tenant_id == tenant_id,
            CampaignSequence.business_id == business_id,
            CampaignSequence.campaign_id == campaign_id,
        ).order_by(CampaignSequence.sequence_order, CampaignSequence.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))


class SQLCampaignStepRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, tenant_id: str, business_id: str, sequence_id: UUID,
               step_order: int, delay_seconds: int,
               template_data: dict[str, Any]) -> CampaignStep:
        _require_tenant(tenant_id)
        row = CampaignStep(tenant_id=tenant_id, business_id=business_id,
                           sequence_id=sequence_id, step_order=step_order,
                           delay_seconds=delay_seconds, template_data=template_data)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_for_sequence(self, *, tenant_id: str, business_id: str,
                          sequence_id: UUID) -> list[CampaignStep]:
        _require_tenant(tenant_id)
        stmt = select(CampaignStep).where(
            CampaignStep.tenant_id == tenant_id,
            CampaignStep.business_id == business_id,
            CampaignStep.sequence_id == sequence_id,
        ).order_by(CampaignStep.step_order, CampaignStep.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))


class SQLCampaignEnrollmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_or_create(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
                      sequence_id: UUID, lead_id: UUID, next_step_at: datetime) -> CampaignEnrollment:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.campaign_id == campaign_id,
            CampaignEnrollment.lead_id == lead_id,
        )
        def persist():
            existing = self._session.exec(stmt).first()
            if existing is not None:
                return existing
            row = CampaignEnrollment(tenant_id=tenant_id, business_id=business_id,
                                     campaign_id=campaign_id, sequence_id=sequence_id,
                                     lead_id=lead_id, next_step_at=next_step_at)
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def get(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
            enrollment_id: UUID) -> CampaignEnrollment | None:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.campaign_id == campaign_id,
            CampaignEnrollment.id == enrollment_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_by_id(self, *, tenant_id: str, business_id: str,
                  enrollment_id: UUID) -> CampaignEnrollment | None:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.id == enrollment_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_for_lead(self, *, tenant_id: str, business_id: str,
                     campaign_id: UUID, lead_id: UUID) -> CampaignEnrollment | None:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.campaign_id == campaign_id,
            CampaignEnrollment.lead_id == lead_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def list_all(self, *, tenant_id: str, business_id: str,
                 campaign_id: UUID) -> list[CampaignEnrollment]:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.campaign_id == campaign_id,
        ).order_by(CampaignEnrollment.enrolled_at, CampaignEnrollment.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))

    def list_active(self, *, tenant_id: str, business_id: str,
                    campaign_id: UUID) -> list[CampaignEnrollment]:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.campaign_id == campaign_id,
            CampaignEnrollment.status == "active",
        ).order_by(CampaignEnrollment.enrolled_at, CampaignEnrollment.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))

    def list_active_for_contact(self, *, tenant_id: str, business_id: str,
                                contact_id: str) -> list[CampaignEnrollment]:
        """List active outreach sequences for a scoped contact."""
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).join(
            Lead,
            (Lead.tenant_id == CampaignEnrollment.tenant_id)
            & (Lead.business_id == CampaignEnrollment.business_id)
            & (Lead.id == CampaignEnrollment.lead_id),
        ).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.status == "active",
            Lead.contact_id == contact_id,
        ).order_by(CampaignEnrollment.updated_at.desc(), CampaignEnrollment.id.desc())
        return _database_call(lambda: list(self._session.exec(stmt).all()))

    def latest_reply_enrollment_for_contact(self, *, tenant_id: str, business_id: str,
                                            contact_id: str) -> CampaignEnrollment | None:
        _require_tenant(tenant_id)
        stmt = select(CampaignEnrollment).join(
            Lead,
            (Lead.tenant_id == CampaignEnrollment.tenant_id)
            & (Lead.business_id == CampaignEnrollment.business_id)
            & (Lead.id == CampaignEnrollment.lead_id),
        ).where(
            CampaignEnrollment.tenant_id == tenant_id,
            CampaignEnrollment.business_id == business_id,
            CampaignEnrollment.status.in_(("active", "completed")),
            CampaignEnrollment.current_step_order >= 0,
            Lead.contact_id == contact_id,
        ).order_by(CampaignEnrollment.updated_at.desc(), CampaignEnrollment.id.desc()).limit(1)
        return _database_call(lambda: self._session.exec(stmt).first())

    def latest_active_for_contact(self, *, tenant_id: str, business_id: str,
                                  contact_id: str) -> CampaignEnrollment | None:
        return next((row for row in self.list_active_for_contact(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
        ) if row.current_step_order >= 0), None)

    def advance(self, *, tenant_id: str, business_id: str, enrollment_id: UUID,
                expected_status: str, current_step_order: int, status: str,
                next_step_at: datetime | None) -> CampaignEnrollment:
        _require_tenant(tenant_id)
        def persist():
            result = self._session.exec(update(CampaignEnrollment).where(
                CampaignEnrollment.tenant_id == tenant_id,
                CampaignEnrollment.business_id == business_id,
                CampaignEnrollment.id == enrollment_id,
                CampaignEnrollment.status == expected_status,
            ).values(current_step_order=current_step_order, status=status,
                     next_step_at=next_step_at, updated_at=datetime.now(UTC)))
            if result.rowcount != 1:
                raise ValueError("campaign enrollment changed or not found")
            stmt = select(CampaignEnrollment).where(
                CampaignEnrollment.tenant_id == tenant_id,
                CampaignEnrollment.business_id == business_id,
                CampaignEnrollment.id == enrollment_id,
            )
            return self._session.exec(stmt).one()
        return _database_call(persist)


class SQLCampaignStepRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def exists(self, *, tenant_id: str, business_id: str,
               enrollment_id: UUID, step_id: UUID) -> bool:
        _require_tenant(tenant_id)
        stmt = select(CampaignStepRun.id).where(
            CampaignStepRun.tenant_id == tenant_id,
            CampaignStepRun.business_id == business_id,
            CampaignStepRun.enrollment_id == enrollment_id,
            CampaignStepRun.step_id == step_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first() is not None)

    def get(self, *, tenant_id: str, business_id: str,
            run_id: UUID) -> CampaignStepRun | None:
        _require_tenant(tenant_id)
        stmt = select(CampaignStepRun).where(
            CampaignStepRun.tenant_id == tenant_id,
            CampaignStepRun.business_id == business_id,
            CampaignStepRun.id == run_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def list_for_campaign(self, *, tenant_id: str, business_id: str,
                          campaign_id: UUID) -> list[CampaignStepRun]:
        _require_tenant(tenant_id)
        stmt = select(CampaignStepRun).join(
            CampaignEnrollment,
            (CampaignEnrollment.tenant_id == CampaignStepRun.tenant_id)
            & (CampaignEnrollment.business_id == CampaignStepRun.business_id)
            & (CampaignEnrollment.id == CampaignStepRun.enrollment_id),
        ).where(
            CampaignStepRun.tenant_id == tenant_id,
            CampaignStepRun.business_id == business_id,
            CampaignEnrollment.campaign_id == campaign_id,
        ).order_by(CampaignStepRun.generated_at, CampaignStepRun.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))

    def add(self, *, tenant_id: str, business_id: str, enrollment_id: UUID,
            step_id: UUID, step_order: int, status: str,
            rendered_content: dict[str, Any], skip_reason: str | None,
            generated_at: datetime, sent_at: datetime | None,
            message_id: UUID | None = None) -> CampaignStepRun:
        _require_tenant(tenant_id)
        row = CampaignStepRun(tenant_id=tenant_id, business_id=business_id,
                              enrollment_id=enrollment_id, step_id=step_id,
                              step_order=step_order, status=status,
                              message_id=message_id,
                              rendered_content=rendered_content, skip_reason=skip_reason,
                              generated_at=generated_at, sent_at=sent_at)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def set_status(self, *, tenant_id: str, business_id: str, run_id: UUID,
                   status: str, sent_at: datetime | None = None,
                   skip_reason: str | None = None,
                   message_id: UUID | None = None) -> CampaignStepRun | None:
        _require_tenant(tenant_id)
        values: dict[str, Any] = {"status": status, "skip_reason": skip_reason}
        if sent_at is not None:
            values["sent_at"] = sent_at
        if message_id is not None:
            values["message_id"] = message_id
        stmt = update(CampaignStepRun).where(
            CampaignStepRun.tenant_id == tenant_id,
            CampaignStepRun.business_id == business_id,
            CampaignStepRun.id == run_id,
        ).values(**values)
        def persist():
            result = self._session.exec(stmt)
            if result.rowcount != 1:
                return None
            self._session.flush()
            return self._session.exec(select(CampaignStepRun).where(
                CampaignStepRun.tenant_id == tenant_id,
                CampaignStepRun.business_id == business_id,
                CampaignStepRun.id == run_id,
            )).first()
        return _database_call(persist)


class SQLCampaignEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
            event_type: str, payload: dict[str, Any],
            enrollment_id: UUID | None = None) -> CampaignEventLog:
        _require_tenant(tenant_id)
        row = CampaignEventLog(tenant_id=tenant_id, business_id=business_id,
                               campaign_id=campaign_id, enrollment_id=enrollment_id,
                               event_type=event_type, payload=payload)
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def list_for_campaign(self, *, tenant_id: str, business_id: str,
                          campaign_id: UUID) -> list[CampaignEventLog]:
        _require_tenant(tenant_id)
        stmt = select(CampaignEventLog).where(
            CampaignEventLog.tenant_id == tenant_id,
            CampaignEventLog.business_id == business_id,
            CampaignEventLog.campaign_id == campaign_id,
        ).order_by(CampaignEventLog.created_at, CampaignEventLog.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))


class SQLOutreachMessageRepository:
    """Tenant-scoped scheduled-send persistence; transaction ownership stays in UoW."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, *, tenant_id: str, business_id: str, message_id: UUID) -> OutreachMessage | None:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.business_id == business_id,
            OutreachMessage.id == message_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_for_tenant(self, *, tenant_id: str, message_id: UUID) -> OutreachMessage | None:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id, OutreachMessage.id == message_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_by_key(self, *, tenant_id: str, idempotency_key: str) -> OutreachMessage | None:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.idempotency_key == idempotency_key,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_by_provider_message_id(self, *, tenant_id: str, business_id: str,
                                   provider_message_id: str) -> OutreachMessage | None:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.business_id == business_id,
            OutreachMessage.provider_message_id == provider_message_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_for_update(self, *, tenant_id: str, business_id: str,
                       message_id: UUID) -> OutreachMessage | None:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.business_id == business_id,
            OutreachMessage.id == message_id,
        ).with_for_update()
        return _database_call(lambda: self._session.exec(stmt).first())

    def get_for_contact(self, *, tenant_id: str, business_id: str,
                        campaign_id: UUID, contact_id: str) -> OutreachMessage | None:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.business_id == business_id,
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.contact_id == contact_id,
        )
        return _database_call(lambda: self._session.exec(stmt).first())

    def list_for_campaign(self, *, tenant_id: str, business_id: str,
                          campaign_id: UUID) -> list[OutreachMessage]:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.business_id == business_id,
            OutreachMessage.campaign_id == campaign_id,
        ).order_by(OutreachMessage.scheduled_at, OutreachMessage.id)
        return _database_call(lambda: list(self._session.exec(stmt).all()))

    def list_contact_ids(self, *, tenant_id: str, business_id: str,
                         campaign_id: UUID) -> set[str]:
        _require_tenant(tenant_id)
        stmt = select(OutreachMessage.contact_id).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.business_id == business_id,
            OutreachMessage.campaign_id == campaign_id,
        )
        return _database_call(lambda: set(self._session.exec(stmt).all()))

    def count_since(self, *, tenant_id: str, since: datetime,
                    campaign_id: UUID | None = None) -> int:
        _require_tenant(tenant_id)
        stmt = select(func.count(OutreachMessage.id)).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.created_at >= since,
        )
        if campaign_id is not None:
            stmt = stmt.where(OutreachMessage.campaign_id == campaign_id)
        return _database_call(lambda: int(self._session.exec(stmt).one()))

    def attempts_since(self, *, tenant_id: str, since: datetime,
                       campaign_id: UUID | None = None) -> int:
        _require_tenant(tenant_id)
        stmt = select(func.coalesce(func.sum(OutreachMessage.attempts), 0)).where(
            OutreachMessage.tenant_id == tenant_id,
            OutreachMessage.updated_at >= since,
        )
        if campaign_id is not None:
            stmt = stmt.where(OutreachMessage.campaign_id == campaign_id)
        return _database_call(lambda: int(self._session.exec(stmt).one()))

    def create(self, *, tenant_id: str, business_id: str, campaign_id: UUID,
               contact_id: str, conversation_id: UUID | None, channel: str,
               idempotency_key: str, content: dict[str, Any], content_hash: str,
               scheduled_at: datetime, created_at: datetime) -> OutreachMessage:
        _require_tenant(tenant_id)
        row = OutreachMessage(
            tenant_id=tenant_id, business_id=business_id, campaign_id=campaign_id,
            contact_id=contact_id, conversation_id=conversation_id, channel=channel,
            idempotency_key=idempotency_key, content=content, content_hash=content_hash,
            scheduled_at=scheduled_at, created_at=created_at, updated_at=created_at,
        )
        def persist():
            self._session.add(row)
            self._session.flush()
            return row
        return _database_call(persist)

    def set_state(self, *, tenant_id: str, business_id: str, message_id: UUID,
                  expected_status: str, status: str, at: datetime,
                  failure_reason: str | None = None, increment_attempts: bool = False,
                  scheduled_at: datetime | None = None,
                  provider_message_id: str | None = None,
                  provider_status: str | None = None,
                  provider_error_code: str | None = None,
                  clear_provider_receipt: bool = False) -> OutreachMessage:
        _require_tenant(tenant_id)
        values: dict[str, Any] = {"status": status, "updated_at": at}
        if scheduled_at is not None:
            values["scheduled_at"] = scheduled_at
        if provider_message_id is not None:
            values["provider_message_id"] = provider_message_id
        if provider_status is not None:
            values["provider_status"] = provider_status
        if provider_error_code is not None:
            values["provider_error_code"] = provider_error_code
        if clear_provider_receipt:
            values.update(provider_message_id=None, provider_status=None,
                          provider_error_code=None)
        if status == "sent":
            values["sent_at"] = at
        elif status == "delivered":
            values["delivered_at"] = at
        elif status == "failed":
            values["failed_at"] = at
            values["failure_reason"] = failure_reason
        if increment_attempts:
            values["attempts"] = OutreachMessage.attempts + 1
        def persist():
            result = self._session.exec(update(OutreachMessage).where(
                OutreachMessage.tenant_id == tenant_id,
                OutreachMessage.business_id == business_id,
                OutreachMessage.id == message_id,
                OutreachMessage.status == expected_status,
            ).values(**values))
            if result.rowcount != 1:
                raise ValueError("outreach message state changed or not found")
            return self.get(tenant_id=tenant_id, business_id=business_id, message_id=message_id)
        return _database_call(persist)
