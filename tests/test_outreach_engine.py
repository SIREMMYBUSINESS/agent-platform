from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

from app.persistence.campaign_repositories import SQLCampaignEventRepository
from app.persistence.models import Campaign, CampaignEventLog, Contact, ConsentRecord, OutreachMessage, QueueTaskRecord, SuppressionRule
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.campaign_service import CampaignNotFoundError, CampaignService, CampaignTenantMismatchError
from app.services.outreach_execution import (
    OutreachConflict, OutreachExecutionService, OutreachNotFound, RateLimitExceeded, RateLimitPolicy,
)
from app.services.recipient_selection import RecipientSelectionService
from test_campaigns import campaign_payload

pytest_plugins = ["test_campaigns"]


NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


class Sender:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("provider request leaked data")


@pytest.fixture
def outreach(campaign_client):
    client, engine, _ = campaign_client
    created = client.post("/campaigns", json=campaign_payload())
    assert created.status_code == 201, created.text
    campaign_id = UUID(created.json()["id"])
    activated = client.post(f"/campaigns/{campaign_id}/activate")
    assert activated.status_code == 200, activated.text
    with Session(engine) as session:
        primary_contact = session.get(Contact, "contact-a")
        primary_contact.email = "jordan@example.test"
        session.add(primary_contact)
        session.commit()
    uow_factory = lambda: UnitOfWork(lambda: Session(engine))
    principal = AuthenticatedPrincipal(
        "outreach-actor", "tenant-a", ("campaign_writer", "campaign_reader"),
        "test", NOW - timedelta(minutes=1), NOW + timedelta(days=1),
    )
    sender = Sender()
    service = OutreachExecutionService(uow_factory=uow_factory, sender=sender,
                                      now_provider=lambda: NOW)
    selection = RecipientSelectionService(uow_factory=uow_factory,
                                          now_provider=lambda: NOW)
    yield engine, client, principal, campaign_id, service, selection, sender, uow_factory


def provision_contact(engine, contact_id="contact-b", *, consent="granted", suppressed=False):
    with Session(engine) as session:
        contact = Contact(id=contact_id, tenant_id="tenant-a", business_id="business-a",
                          full_name="Taylor", email="taylor@example.test")
        session.add(contact)
        session.flush()
        session.add(ConsentRecord(tenant_id="tenant-a", business_id="business-a",
                                  contact_id=contact_id, channel="email", status=consent,
                                  source="test_admin"))
        if suppressed:
            session.add(SuppressionRule(tenant_id="tenant-a", business_id="business-a",
                                        contact_id=contact_id, reason_code="test_suppression",
                                        channel="email", created_by="test_admin"))
        session.commit()


def schedule(service, principal, campaign_id, contact_id="contact-a", *, key="send-1", at=NOW):
    return service.schedule_send(
        principal, campaign_id=campaign_id, contact_id=contact_id,
        idempotency_key=key, content={"subject": "Hello", "body": "A reminder"},
        scheduled_at=at,
    )


def test_campaign_lifecycle_is_principal_scoped_and_audited(outreach):
    engine, client, _, campaign_id, *_ = outreach
    assert client.post(f"/campaigns/{campaign_id}/pause").json()["status"] == "paused"
    assert client.post(f"/campaigns/{campaign_id}/archive").json()["status"] == "archived"
    assert client.post(f"/campaigns/{campaign_id}/activate").status_code == 409
    service = CampaignService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                               uow_factory=outreach[-1])
    with pytest.raises(CampaignNotFoundError):
        service.activate(AuthenticatedPrincipal("other", "tenant-b", ("campaign_writer",),
                                                "test", NOW - timedelta(days=365)), campaign_id)
    with Session(engine) as session:
        assert session.exec(select(Campaign).where(Campaign.id == campaign_id)).one().status == "archived"
        rows = session.exec(select(CampaignEventLog)).all()
        names = {row.event_type for row in rows}
        assert "CampaignActivated" in names
        assert sum(row.event_type == "campaign.state.changed" for row in rows) >= 2


def test_campaign_reads_use_principal_tenant_and_reader_role(outreach):
    _, _, _, campaign_id, _, _, _, uow_factory = outreach
    service = CampaignService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                              uow_factory=uow_factory)
    issued_at = datetime.now(UTC) - timedelta(minutes=1)
    expires_at = datetime.now(UTC) + timedelta(days=1)
    reader = AuthenticatedPrincipal("reader", "tenant-a", ("campaign_reader",), "test",
                                    issued_at, expires_at)
    assert service.get(reader, campaign_id=campaign_id).campaign.id == campaign_id
    other_tenant = AuthenticatedPrincipal("other", "tenant-b", ("campaign_reader",), "test",
                                          issued_at, expires_at)
    with pytest.raises(CampaignNotFoundError):
        service.get(other_tenant, campaign_id=campaign_id)
    with pytest.raises(CampaignTenantMismatchError, match="campaign reader principal required"):
        service.get(AuthenticatedPrincipal("writer", "tenant-a", ("campaign_writer",), "test",
                                            issued_at), campaign_id=campaign_id)


def test_recipient_selection_is_tenant_scoped_deduplicated_and_suppression_aware(outreach):
    engine, _, principal, campaign_id, service, selection, _, _ = outreach
    provision_contact(engine, suppressed=True)
    result = selection.select(principal, campaign_id)
    assert result.contact_ids == ("contact-a",)
    assert result.excluded_counts["suppressed"] == 1
    scheduled = schedule(service, principal, campaign_id)
    assert scheduled.status == "scheduled"
    again = selection.select(principal, campaign_id)
    assert again.contact_ids == ()
    assert again.excluded_counts["already_selected"] == 1


def test_scheduling_is_idempotent_and_campaign_contact_unique(outreach):
    _, _, principal, campaign_id, service, *_ = outreach
    first = schedule(service, principal, campaign_id)
    replay = schedule(service, principal, campaign_id)
    assert first.id == replay.id
    with pytest.raises(OutreachConflict, match="different input"):
        service.schedule_send(principal, campaign_id=campaign_id, contact_id="contact-a",
                              idempotency_key="send-1", content={"body": "Different"},
                              scheduled_at=NOW)
    with pytest.raises(OutreachConflict, match="already selected"):
        schedule(service, principal, campaign_id, key="send-2")


def test_http_delivery_command_enqueues_without_calling_provider(outreach):
    engine, _, principal, campaign_id, service, _, sender, _ = outreach
    row = schedule(service, principal, campaign_id)

    queued = service.enqueue_send(principal, row.id)

    assert queued.id == row.id and queued.status == "scheduled"
    assert sender.calls == []
    with Session(engine) as session:
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "outreach.execute_send",
            QueueTaskRecord.aggregate_id == str(row.id),
        )).one()
        assert task.idempotency_key == f"outreach:{row.id}:attempt:1"
        assert task.payload == {"message_id": str(row.id)}
        assert task.tenant_id == "tenant-a" and task.business_id == "business-a"


def test_legacy_outreach_worker_dry_run_never_calls_provider(outreach, monkeypatch):
    from app.config import settings
    from app.queue.runtime import QueueTaskRunner
    from app.workers import handlers

    engine, _, principal, campaign_id, service, _, sender, uow_factory = outreach
    row = schedule(service, principal, campaign_id)
    service.enqueue_send(principal, row.id)
    with Session(engine) as session:
        task_id = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "outreach.execute_send",
            QueueTaskRecord.aggregate_id == str(row.id),
        )).one().id
    monkeypatch.setattr(settings, "workflow_dry_run", True)
    monkeypatch.setattr(handlers, "session_factory", lambda: Session(engine))
    runner = QueueTaskRunner(uow_factory=uow_factory,
                             handlers=handlers.build_default_handlers(message_sender=sender))

    result = runner.execute(task_id, expected_domain="outreach")

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == "dry_run"
    assert sender.calls == []
    with Session(engine) as session:
        persisted = session.get(OutreachMessage, row.id)
        assert persisted.status == "dry_run"


def test_execution_retry_delivery_tracking_and_events(outreach):
    engine, _, principal, campaign_id, service, _, sender, _ = outreach
    row = schedule(service, principal, campaign_id)
    sent = service.execute_send(principal, row.id)
    replay = service.execute_send(principal, row.id)
    delivered = service.mark_delivered(principal, row.id)
    assert sent.status == replay.status == "sent"
    assert sent.sent_at == NOW and sent.attempts == 1
    assert delivered.status == "delivered" and delivered.delivered_at == NOW
    assert len(sender.calls) == 1
    assert sender.calls[0]["idempotency_key"] == "send-1"
    with Session(engine) as session:
        message = session.exec(select(OutreachMessage)).one()
        event_types = {row.event_type for row in session.exec(select(CampaignEventLog)).all()}
    assert tuple(value.replace(tzinfo=UTC) if value.tzinfo is None else value
                 for value in (message.scheduled_at, message.sent_at, message.delivered_at)) == (NOW, NOW, NOW)
    assert {"MessageScheduled", "MessageSent", "MessageDelivered"} <= event_types


def test_verified_twilio_receipt_is_idempotent_and_survives_callback_during_send(outreach):
    engine, _, principal, campaign_id, service, _, _, uow_factory = outreach
    provider_principal = AuthenticatedPrincipal(
        "twilio:account", "tenant-a", ("delivery_receipt_writer",),
        "twilio_webhook", NOW - timedelta(minutes=1),
    )
    sid = "SM" + "a" * 32
    with Session(engine) as session:
        campaign = session.get(Campaign, campaign_id)
        campaign.channel = "sms"
        contact = session.get(Contact, "contact-a")
        contact.phone = "+15559990001"
        session.add(ConsentRecord(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
            channel="sms", status="granted", source="test_admin",
        ))
        session.commit()

    class CallbackDuringSend:
        def send(self, **kwargs):
            service.apply_twilio_receipt(
                provider_principal, business_id="business-a", message_id=kwargs["message_id"],
                provider_message_id=sid, attempt=kwargs["attempt"], provider_status="delivered",
            )
            return sid

    service._sender = CallbackDuringSend()
    scheduled = schedule(service, principal, campaign_id, key="twilio-receipt")
    delivered = service.execute_send(principal, scheduled.id)
    assert delivered.status == "delivered"
    assert delivered.provider_message_id == sid
    assert delivered.provider_status == "delivered"
    replay = service.apply_twilio_receipt(
        provider_principal, business_id="business-a", message_id=scheduled.id,
        provider_message_id=sid, attempt=1, provider_status="sent",
    )
    assert replay.status == "delivered"
    with Session(engine) as session:
        events = session.exec(select(CampaignEventLog).where(
            CampaignEventLog.event_type == "MessageDelivered",
        )).all()
        assert len(events) == 1


def test_retry_scopes_receipts_to_attempt_and_ignores_late_prior_attempt(outreach):
    engine, _, principal, campaign_id, service, _, _, _ = outreach
    receipt_principal = AuthenticatedPrincipal(
        "twilio:account", "tenant-a", ("delivery_receipt_writer",),
        "twilio_webhook", NOW - timedelta(minutes=1),
    )
    with Session(engine) as session:
        session.get(Campaign, campaign_id).channel = "sms"
        contact = session.get(Contact, "contact-a")
        contact.phone = "+15559990001"
        session.add(ConsentRecord(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
            channel="sms", status="granted", source="test_admin",
        ))
        session.commit()

    class ReturningSender:
        sid = "SM" + "a" * 32

        def send(self, **_kwargs):
            return self.sid

    sender = ReturningSender()
    service._sender = sender
    scheduled = schedule(service, principal, campaign_id, key="twilio-retry-receipt")
    sent = service.execute_send(principal, scheduled.id)
    assert sent.status == "sent" and sent.provider_message_id == sender.sid
    failed = service.apply_twilio_receipt(
        receipt_principal, business_id="business-a", message_id=scheduled.id,
        provider_message_id=sender.sid, attempt=1, provider_status="undelivered",
        error_code="30007",
    )
    assert failed.status == "failed"

    service.retry_send(principal, scheduled.id)
    stale_during_schedule = service.apply_twilio_receipt(
        receipt_principal, business_id="business-a", message_id=scheduled.id,
        provider_message_id=sender.sid, attempt=1, provider_status="delivered",
    )
    assert stale_during_schedule.status == "scheduled"

    sender.sid = "SM" + "c" * 32
    retried = service.execute_send(principal, scheduled.id)
    assert retried.status == "sent" and retried.provider_message_id == sender.sid
    stale_after_retry = service.apply_twilio_receipt(
        receipt_principal, business_id="business-a", message_id=scheduled.id,
        provider_message_id="SM" + "a" * 32, attempt=1, provider_status="delivered",
    )
    assert stale_after_retry.status == "sent"
    delivered = service.apply_twilio_receipt(
        receipt_principal, business_id="business-a", message_id=scheduled.id,
        provider_message_id=sender.sid, attempt=2, provider_status="delivered",
    )
    assert delivered.status == "delivered" and delivered.provider_status == "delivered"


def test_failed_send_is_retryable_and_retry_limit_is_deterministic(outreach):
    engine, _, principal, campaign_id, _, _, sender, uow_factory = outreach
    sender.fail = True
    service = OutreachExecutionService(
        uow_factory=uow_factory, sender=sender,
        rate_limits=RateLimitPolicy(max_attempts=2), now_provider=lambda: NOW,
    )
    row = schedule(service, principal, campaign_id)
    failed = service.execute_send(principal, row.id)
    assert failed.status == "failed" and failed.failed_at == NOW
    assert failed.failure_reason == "provider_error:RuntimeError"
    retried = service.retry_send(principal, row.id)
    assert retried.status == "scheduled"
    second = service.execute_send(principal, row.id)
    assert second.status == "failed" and second.attempts == 2
    with pytest.raises(RateLimitExceeded, match="retry limit"):
        service.retry_send(principal, row.id)
    with Session(engine) as session:
        assert session.exec(select(OutreachMessage)).one().attempts == 2


def test_success_after_retry_preserves_last_failure_tracking(outreach):
    engine, _, principal, campaign_id, _, _, sender, uow_factory = outreach
    sender.fail = True
    service = OutreachExecutionService(uow_factory=uow_factory, sender=sender,
                                       now_provider=lambda: NOW)
    row = schedule(service, principal, campaign_id)
    first_failure = service.execute_send(principal, row.id)
    service.retry_send(principal, row.id)
    sender.fail = False
    success = service.execute_send(principal, row.id)
    assert first_failure.failure_reason == "provider_error:RuntimeError"
    assert success.status == "sent" and success.sent_at == NOW
    assert success.failed_at == NOW
    assert success.failure_reason == "provider_error:RuntimeError"
    with Session(engine) as session:
        persisted = session.exec(select(OutreachMessage)).one()
        assert persisted.failed_at is not None and persisted.failure_reason == "provider_error:RuntimeError"


def test_tenant_and_campaign_rate_limits_and_cross_tenant_protection(outreach):
    engine, _, principal, campaign_id, _, _, _, uow_factory = outreach
    provision_contact(engine)
    service = OutreachExecutionService(
        uow_factory=uow_factory,
        rate_limits=RateLimitPolicy(tenant_messages_per_window=1,
                                    campaign_messages_per_window=1),
        now_provider=lambda: NOW,
    )
    schedule(service, principal, campaign_id)
    with pytest.raises(RateLimitExceeded):
        service.schedule_send(principal, campaign_id=campaign_id, contact_id="contact-b",
                              idempotency_key="send-b", content={"body": "Follow-up"},
                              scheduled_at=NOW)
    other_tenant = AuthenticatedPrincipal("other", "tenant-b", ("campaign_writer",),
                                          "test", NOW - timedelta(seconds=1))
    with pytest.raises(OutreachNotFound):
        service.schedule_send(other_tenant, campaign_id=campaign_id,
                              contact_id="contact-a", idempotency_key="cross-tenant",
                              content={"body": "Do not send"}, scheduled_at=NOW)


def test_scheduling_audit_failure_rolls_back_the_message(outreach, monkeypatch):
    engine, _, principal, campaign_id, service, _, _, _ = outreach
    original = SQLCampaignEventRepository.add

    def fail_schedule(self, **kwargs):
        if kwargs["event_type"] == "MessageScheduled":
            raise RuntimeError("audit persistence unavailable")
        return original(self, **kwargs)

    monkeypatch.setattr(SQLCampaignEventRepository, "add", fail_schedule)
    with pytest.raises(RuntimeError, match="audit persistence"):
        schedule(service, principal, campaign_id)
    with Session(engine) as session:
        assert session.exec(select(OutreachMessage)).first() is None
