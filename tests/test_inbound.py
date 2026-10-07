from app.auth.dependencies import get_authenticated_principal
from app.security import AuthenticatedPrincipal
from datetime import datetime as AuthDatetime, UTC as AUTH_UTC
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, delete, select

from app.main import app, get_inbound_service, get_queue_task_service
from app.persistence.models import (
    Appointment,
    AppointmentSlot,
    Business,
    Contact,
    Conversation,
    EscalationTicket,
    EventLog,
    Message,
    ConsentRecord,
    QueueTaskRecord,
    PolicyDecisionLog,
    RoutingRule,
    Service,
    Staff,
    Tenant,
    WorkflowStateRecord,
)
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import InboundResponse
from app.services.inbound import InboundService


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    service_id = uuid4()
    staff_id = uuid4()
    trusted_now = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)  # Thursday, 11:00 in Los Angeles.
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Test tenant"))
        session.flush()
        session.add(Business(
            id="biz-1", tenant_id="tenant-1", name="Test business", vertical="general",
            timezone="America/Los_Angeles",
        ))
        session.flush()
        session.add(Contact(
            id="contact-1", tenant_id="tenant-1", business_id="biz-1", full_name="Alex",
            consent_status="granted", timezone="America/Los_Angeles",
        ))
        session.add_all([
            Staff(id=staff_id, tenant_id="tenant-1", business_id="biz-1", display_name="Taylor"),
            Service(
                id=service_id, tenant_id="tenant-1", business_id="biz-1",
                name="Consultation", duration_minutes=30,
            ),
            RoutingRule(
                tenant_id="tenant-1", business_id="biz-1", weekday=3,
                opens_at=time(9), closes_at=time(17), after_hours_action="voicemail",
            ),
        ])
        session.flush()
        session.add_all([
            AppointmentSlot(
                tenant_id="tenant-1", business_id="biz-1", staff_id=staff_id,
                service_id=service_id, starts_at=trusted_now + timedelta(days=1),
                ends_at=trusted_now + timedelta(days=1, minutes=30),
            ),
            AppointmentSlot(
                tenant_id="tenant-1", business_id="biz-1", staff_id=staff_id,
                service_id=service_id, starts_at=trusted_now + timedelta(days=2),
                ends_at=trusted_now + timedelta(days=2, minutes=30),
            ),
            AppointmentSlot(
                tenant_id="tenant-1", business_id="biz-1", staff_id=staff_id,
                service_id=service_id, starts_at=trusted_now + timedelta(days=3),
                ends_at=trusted_now + timedelta(days=3, minutes=30),
            ),
        ])
        session.commit()

    service = InboundService(
        trusted_tenant_id="tenant-1",
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        now_provider=lambda: trusted_now,
    )
    app.dependency_overrides[get_inbound_service] = lambda: service
    app.state.inbound_test_engine = engine
    app.state.inbound_test_service = service
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        "test-identity", "tenant-1",
        ("inbound_writer", "lead_reader", "lead_writer", "campaign_reader", "campaign_writer"),
        "test", AuthDatetime.now(AUTH_UTC),
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        del app.state.inbound_test_engine
        del app.state.inbound_test_service
        engine.dispose()


def valid_inbound_payload():
    return {
        "tenant_id": "tenant-1",
        "business_id": "biz-1",
        "contact_id": "contact-1",
        "channel": "chat",
        "text": "I need to schedule an appointment",
        "contact_name": "Alex",
        "consent_status": "granted",
        "metadata": {"region": "US"},
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_inbound_ok(client):
    response = client.post("/inbound", json=valid_inbound_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "schedule" in body["agent_response"].casefold()


def test_contact_web_action_confirms_proposed_appointment_without_tenant_ids(client, monkeypatch):
    import re
    from sqlmodel import Session

    monkeypatch.setattr(
        "app.routes.workflows.session_factory",
        lambda: Session(app.state.inbound_test_engine, expire_on_commit=False),
    )
    response = client.post("/inbound", json=valid_inbound_payload())
    assert response.status_code == 200
    token_match = re.search(r"token=([A-Za-z0-9_-]+)", response.json()["agent_response"])
    assert token_match is not None
    with Session(app.state.inbound_test_engine) as session:
        appointment = session.exec(select(Appointment)).one()
        slots = session.exec(select(AppointmentSlot).order_by(AppointmentSlot.starts_at)).all()
        for offset, slot in enumerate(slots, start=1):
            slot.starts_at = datetime.now(UTC) + timedelta(days=offset)
            slot.ends_at = slot.starts_at + timedelta(minutes=30)
            session.add(slot)
        appointment.starts_at = slots[0].starts_at
        appointment.ends_at = slots[0].ends_at
        session.add(appointment)
        session.commit()

    confirmed = client.post("/workflows/confirm-appointment", json={
        "token": token_match.group(1), "slot_number": 2,
    })

    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"


def test_contact_web_action_cancels_proposed_appointment(client, monkeypatch):
    import re
    from sqlmodel import Session

    monkeypatch.setattr(
        "app.routes.workflows.session_factory",
        lambda: Session(app.state.inbound_test_engine, expire_on_commit=False),
    )
    response = client.post("/inbound", json=valid_inbound_payload())
    token = re.search(r"token=([A-Za-z0-9_-]+)", response.json()["agent_response"]).group(1)
    with Session(app.state.inbound_test_engine) as session:
        appointment = session.exec(select(Appointment)).one()
        slots = session.exec(select(AppointmentSlot).order_by(AppointmentSlot.starts_at)).all()
        for offset, slot in enumerate(slots, start=1):
            slot.starts_at = datetime.now(UTC) + timedelta(days=offset)
            slot.ends_at = slot.starts_at + timedelta(minutes=30)
            session.add(slot)
        appointment.starts_at = slots[0].starts_at
        appointment.ends_at = slots[0].ends_at
        appointment_id = appointment.id
        session.add(appointment)
        session.commit()

    cancelled = client.post(f"/appointments/{appointment_id}/cancel", json={
        "token": token, "reason": "Plans changed",
    })

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_workflow_dry_run_plans_calendar_and_crm_without_provider_calls(client, monkeypatch):
    import re
    from sqlmodel import Session
    from app.queue.runtime import QueueTaskRunner
    from app.workers import handlers as worker_handlers
    from app.config import settings
    from app.persistence.models import ProviderInteractionLog

    engine = app.state.inbound_test_engine
    monkeypatch.setattr("app.routes.workflows.session_factory",
                        lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr(worker_handlers, "session_factory",
                        lambda: Session(engine, expire_on_commit=False))
    monkeypatch.setattr(settings, "workflow_dry_run", True)
    intake = client.post("/inbound", json=valid_inbound_payload())
    token = re.search(r"token=([A-Za-z0-9_-]+)", intake.json()["agent_response"]).group(1)
    with Session(engine) as session:
        appointment = session.exec(select(Appointment)).one()
        slots = session.exec(select(AppointmentSlot).order_by(AppointmentSlot.starts_at)).all()
        for offset, slot in enumerate(slots, start=3):
            slot.starts_at = datetime.now(UTC) + timedelta(days=offset)
            slot.ends_at = slot.starts_at + timedelta(minutes=30)
            session.add(slot)
        appointment.starts_at, appointment.ends_at = slots[0].starts_at, slots[0].ends_at
        session.add(appointment)
        session.commit()
    assert client.post("/workflows/confirm-appointment", json={"token": token}).status_code == 200

    with Session(engine) as session:
        tasks = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name.in_(("scheduling.calendar_sync", "crm.sync_activity")),
        )).all()
        task_ids = [(row.id, row.domain) for row in tasks]

    class NeverCall:
        provider_key = "must_not_be_called"
        def create_event(self, **kwargs):
            raise AssertionError("calendar provider called during dry run")

    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine, expire_on_commit=False)),
        handlers=worker_handlers.build_default_handlers(
            calendar_provider=NeverCall(), crm_provider=NeverCall(),
        ),
    )
    for task_id, domain in task_ids:
        result = runner.execute(task_id, expected_domain=domain)
        assert result["status"] == "succeeded"

    with Session(engine) as session:
        interactions = session.exec(select(ProviderInteractionLog)).all()
    assert {row.operation for row in interactions} >= {"create_event", "sync_activity"}
    assert all(row.outcome == "dry_run" for row in interactions)


def test_normal_chat_intake_routes_business_hours_question(client):
    payload = valid_inbound_payload()
    payload["text"] = "What are your business hours?"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "business_hours_checked"
    assert "9:00 AM" in response.json()["agent_response"]


def test_general_question_uses_safe_faq_stub_and_persists_answer(client):
    from app.persistence.models import EventLog, Message

    payload = valid_inbound_payload()
    payload["text"] = "Can you tell me about parking?"
    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_state"] == "faq_answered"
    assert "team will follow up" in body["agent_response"]
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        outbound = session.exec(select(Message).where(Message.direction == "outbound")).one()
        event = session.exec(select(EventLog).where(
            EventLog.event_type == "faq.answer.generated",
        )).one()
    assert outbound.content == body["agent_response"]
    assert outbound.delivery_status == "sent"  # chat response is returned synchronously
    assert event.payload["source"] == "fallback"


def test_inbound_uses_injected_tenant_scoped_knowledge_adapter(client):
    from app.services.faq_service import FAQService
    from app.services.intake_service import IntakeService

    class Knowledge:
        def answer(self, *, tenant_id, business_id, question):
            assert (tenant_id, business_id) == ("tenant-1", "biz-1")
            assert question == "What is the cancellation policy?"
            return "Please call our office for cancellation details."

    service = IntakeService(
        trusted_tenant_id="tenant-1",
        uow_factory=lambda: UnitOfWork(lambda: Session(client.app.state.inbound_test_engine)),
        faq_service=FAQService(knowledge_adapter=Knowledge()),
        now_provider=lambda: datetime(2026, 9, 24, 18, 0, tzinfo=UTC),
    )
    client.app.dependency_overrides[get_inbound_service] = lambda: service
    payload = valid_inbound_payload()
    payload["text"] = "What is the cancellation policy?"
    response = client.post("/inbound", json=payload)
    assert response.status_code == 200
    assert response.json()["agent_response"] == "Please call our office for cancellation details."
    with Session(client.app.state.inbound_test_engine) as session:
        event = session.exec(select(EventLog).where(
            EventLog.event_type == "faq.answer.generated",
        )).one()
    assert event.payload["source"] == "knowledge_base"


def test_escalation_resolution_can_send_policy_checked_staff_response(client):
    from app.services.escalation_workflow_service import EscalationWorkflowService

    payload = valid_inbound_payload()
    payload["text"] = "This is an emergency, please help immediately"
    assert client.post("/inbound", json=payload).status_code == 200
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        ticket = session.exec(select(EscalationTicket)).one()

    principal = AuthenticatedPrincipal(
        "staff-identity", "tenant-1", ("workflow_executor",), "test",
        AuthDatetime.now(AUTH_UTC),
    )
    service = EscalationWorkflowService(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
    )
    inbox = service.list_tickets(principal)
    assert [row["id"] for row in inbox] == [ticket.id]
    result = service.resolve(
        principal, ticket.id, resolution="Staff reviewed the request",
        response_to_contact="A staff member is now helping you.",
    )

    assert result["status"] == "resolved"
    assert result["response_delivery_status"] == "sent"
    with Session(engine) as session:
        ticket = session.get(EscalationTicket, ticket.id)
        replies = session.exec(select(Message).where(
            Message.direction == "outbound",
            Message.content == "A staff member is now helping you.",
        )).all()
    assert ticket.status == "resolved"
    assert len(replies) == 1


def test_calendar_busy_slot_is_filtered_before_appointment_proposal(client):
    from app.services.intake_service import IntakeService

    engine = client.app.state.inbound_test_engine
    now = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)
    busy_start = now + timedelta(days=1)

    class Calendar:
        def availability(self, **kwargs):
            assert kwargs["tenant_id"] == "tenant-1"
            assert kwargs["business_id"] == "biz-1"
            return [{
                "start": {"dateTime": busy_start.isoformat()},
                "end": {"dateTime": (busy_start + timedelta(minutes=30)).isoformat()},
            }]

    service = IntakeService(
        trusted_tenant_id="tenant-1", uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        now_provider=lambda: now, calendar_provider=Calendar(),
    )
    client.app.dependency_overrides[get_inbound_service] = lambda: service
    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 200
    with Session(engine) as session:
        appointment = session.exec(select(Appointment)).one()
    assert appointment.starts_at.replace(tzinfo=UTC) != busy_start


def test_calendar_conflict_at_confirmation_reproposes_a_new_slot(client):
    from app.services.intake_service import IntakeService

    first = client.post("/inbound", json=valid_inbound_payload())
    assert first.status_code == 200
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        original = session.exec(select(Appointment)).one()
        busy_start, original_id = original.starts_at, original.id

    class Calendar:
        def availability(self, **kwargs):
            return [{
                "start": {"dateTime": busy_start.replace(tzinfo=UTC).isoformat()},
                "end": {"dateTime": (busy_start.replace(tzinfo=UTC) + timedelta(minutes=30)).isoformat()},
            }]

    service = IntakeService(
        trusted_tenant_id="tenant-1", uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        now_provider=lambda: datetime(2026, 9, 24, 18, 0, tzinfo=UTC),
        calendar_provider=Calendar(),
    )
    client.app.dependency_overrides[get_inbound_service] = lambda: service
    payload = valid_inbound_payload()
    payload["text"] = "Yes, that works"
    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    with Session(engine) as session:
        tickets = session.exec(select(EscalationTicket)).all()
        original = session.exec(select(Appointment).where(Appointment.id == original_id)).one()
        alternatives = session.exec(select(Appointment).where(Appointment.id != original_id)).all()
    assert response.json()["workflow_state"] == "appointment_reproposed", [
        (ticket.reason, ticket.priority) for ticket in tickets
    ] + [
        (original.status, [(row.status, row.starts_at) for row in alternatives])
    ]
    assert original.status == "conflict"
    assert len(alternatives) == 1 and alternatives[0].status == "proposed"
    assert alternatives[0].starts_at != busy_start


def test_sms_receptionist_response_is_policy_checked_and_queued_atomically(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.phone = "+15555550123"
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        ))
        session.commit()
    payload = valid_inbound_payload()
    payload.update(channel="sms", text="What are your business hours?")

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    with Session(engine) as session:
        outbound = session.exec(select(Message).where(Message.direction == "outbound")).one()
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "communication.deliver_message",
        )).one()
        decisions = session.exec(select(PolicyDecisionLog)).all()
    assert outbound.delivery_status == "pending"
    assert task.payload == {"message_id": str(outbound.id)}
    assert task.correlation_id
    assert any(item.direction == "outbound" and item.decision == "allow" for item in decisions)


def test_delivery_worker_rechecks_policy_before_provider_call(client, monkeypatch):
    from app.queue.runtime import QueueTaskEnvelope, QueueTaskRunner
    from app.workers import handlers as worker_handlers

    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.phone = "+15555550123"
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        ))
        session.commit()
    payload = valid_inbound_payload()
    payload.update(channel="sms", text="What are your business hours?")
    assert client.post("/inbound", json=payload).status_code == 200
    with Session(engine) as session:
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "communication.deliver_message",
        )).one()
        task_id, payload_message_id = task.id, task.payload["message_id"]
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="revoked", source="test_opt_out",
        ))
        session.commit()

    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))

    class Sender:
        calls = []

        def send(self, **kwargs):
            self.calls.append(kwargs)
            return "provider-message-1"

    sender = Sender()
    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(message_sender=sender),
    )
    result = runner.execute(task_id, expected_domain="outreach")

    assert result["status"] == "succeeded", result
    assert sender.calls == []
    with Session(engine) as session:
        outbound = session.exec(select(Message).where(
            Message.id == UUID(payload_message_id),
        )).one()
    assert outbound.delivery_status == "blocked"


def test_queue_correlation_survives_inbound_worker_and_events(client, monkeypatch):
    from app.queue.runtime import QueueTaskRunner
    from app.queue.service import QueueTaskService
    from app.workers import handlers as worker_handlers

    engine = client.app.state.inbound_test_engine
    client.app.dependency_overrides[get_queue_task_service] = lambda: QueueTaskService(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
    )
    response = client.post("/inbound/queue", headers={"Idempotency-Key": "corr-test-1"}, json={
        "tenant_id": "tenant-1", "business_id": "biz-1", "contact_id": "contact-1",
        "channel": "chat", "text": "What are your business hours?",
    })
    assert response.status_code == 202
    task_id = UUID(response.json()["id"])
    correlation_id = response.json()["correlation_id"]
    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))
    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(),
    )

    assert runner.execute(task_id, expected_domain="inbound")["status"] == "succeeded"
    with Session(engine) as session:
        conversation = session.exec(select(Conversation).where(
            Conversation.correlation_id == correlation_id,
        )).one()
        events = session.exec(select(EventLog).where(
            EventLog.correlation_id == correlation_id,
        )).all()
    assert events
    assert all(event.conversation_id == conversation.id for event in events)


def test_delivery_worker_sends_once_and_persists_provider_reference(client, monkeypatch):
    from app.queue.runtime import QueueTaskRunner
    from app.workers import handlers as worker_handlers

    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.phone = "+15555550123"
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        ))
        session.commit()
    payload = valid_inbound_payload()
    payload.update(channel="sms", text="What are your business hours?")
    assert client.post("/inbound", json=payload).status_code == 200
    with Session(engine) as session:
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "communication.deliver_message",
        )).one()
        task_id, payload_message_id = task.id, task.payload["message_id"]

    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))

    class Sender:
        calls = []

        def send(self, **kwargs):
            self.calls.append(kwargs)
            return "provider-message-1"

    sender = Sender()
    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(message_sender=sender),
    )
    result = runner.execute(task_id, expected_domain="outreach")

    assert result["status"] == "succeeded"
    assert len(sender.calls) == 1
    assert sender.calls[0]["idempotency_key"] == f"message:{payload_message_id}:delivery"
    with Session(engine) as session:
        message = session.exec(select(Message).where(Message.id == UUID(payload_message_id))).one()
    assert message.delivery_status == "sent"
    assert message.provider_message_id == "provider-message-1"


def test_provider_failure_is_recorded_and_delivery_task_reaches_dlq(client, monkeypatch):
    from app.queue.runtime import QueueTaskRunner
    from app.workers import handlers as worker_handlers

    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.phone = "+15555550123"
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        ))
        session.commit()
    payload = valid_inbound_payload()
    payload.update(channel="sms", text="What are your business hours?")
    assert client.post("/inbound", json=payload).status_code == 200
    with Session(engine) as session:
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "communication.deliver_message",
        )).one()
        task_id, message_id = task.id, UUID(task.payload["message_id"])
        task.max_attempts = 1
        session.commit()

    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))

    class BrokenSender:
        def send(self, **kwargs):
            raise RuntimeError("private provider response detail")

    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(message_sender=BrokenSender()),
    )
    result = runner.execute(task_id, expected_domain="outreach")
    assert result["status"] == "dead_letter"
    with Session(engine) as session:
        task = session.get(QueueTaskRecord, task_id)
        message = session.get(Message, message_id)
    assert task.status == "dead_letter"
    assert task.error_code == "RuntimeError"
    assert "private provider" not in str(task.model_dump())
    assert message.delivery_status == "failed"


def test_appointment_reminder_worker_queues_policy_checked_delivery(client, monkeypatch):
    from app.queue.runtime import QueueTaskEnvelope
    from app.services.appointment_reminder_service import AppointmentReminderService
    from app.workers import handlers as worker_handlers

    assert client.post("/inbound", json=valid_inbound_payload()).status_code == 200
    engine = client.app.state.inbound_test_engine
    now = datetime.now(UTC)
    with Session(engine) as session:
        conversation = session.exec(select(Conversation)).one()
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.phone = "+15555550123"
        contact.preferred_channel = "sms"
        appointment = Appointment(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            conversation_id=conversation.id, starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=2, minutes=30), status="confirmed",
            correlation_id="reminder-correlation",
        )
        session.add_all([appointment, ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        )])
        session.commit()
        appointment_id = appointment.id
    with UnitOfWork(lambda: Session(engine)) as uow:
        task, _ = uow.queue_tasks.enqueue(
            tenant_id="tenant-1", business_id="biz-1", domain="scheduling",
            task_name="appointment.reminder.prepare",
            idempotency_key=f"appointment:{appointment_id}:reminder:1h",
            aggregate_type="appointment", aggregate_id=str(appointment_id),
            payload={"appointment_id": str(appointment_id), "reminder": "1h"},
            correlation_id="reminder-correlation", traceparent=None,
            available_at=now, max_attempts=4,
        )
        task_id = task.id

    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))
    registry = worker_handlers.build_default_handlers()
    with Session(engine) as session:
        stored_task = session.get(QueueTaskRecord, task_id)
        envelope = QueueTaskEnvelope(
            id=stored_task.id, tenant_id=stored_task.tenant_id,
            business_id=stored_task.business_id, domain=stored_task.domain,
            task_name=stored_task.task_name, idempotency_key=stored_task.idempotency_key,
            aggregate_type=stored_task.aggregate_type, aggregate_id=stored_task.aggregate_id,
            payload=dict(stored_task.payload), correlation_id=stored_task.correlation_id,
            traceparent=stored_task.traceparent, attempts=1, max_attempts=stored_task.max_attempts,
        )
    result = registry.get("scheduling", "appointment.reminder.prepare")(envelope)

    assert result["status"] == "pending", result
    reminder_id = AppointmentReminderService.message_id(appointment_id, "1h")
    with Session(engine) as session:
        reminder = session.exec(select(Message).where(Message.id == reminder_id)).one()
        delivery = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "communication.deliver_message",
        )).one()
    assert reminder.delivery_status == "pending"
    assert delivery.correlation_id == "reminder-correlation"
    assert delivery.payload == {"message_id": str(reminder_id)}


def test_appointment_completion_worker_runs_followup_service(client, monkeypatch):
    from app.queue.runtime import QueueTaskRunner
    from app.queue.contracts import QueueDomain
    from app.workers import handlers as worker_handlers

    engine = client.app.state.inbound_test_engine
    response = client.post("/inbound", json=valid_inbound_payload())
    assert response.status_code == 200
    with Session(engine) as session:
        contact = session.get(Contact, "contact-1")
        contact.phone = "+15555550123"
        contact.preferred_channel = "sms"
        session.add(contact)
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        ))
        appointment = session.exec(select(Appointment)).one()
        appointment.status = "confirmed"
        appointment.starts_at = datetime.now(UTC) - timedelta(hours=2)
        appointment.ends_at = datetime.now(UTC) - timedelta(hours=1)
        appointment.correlation_id = "followup-correlation"
        appointment_id = appointment.id
        conversation_id = appointment.conversation_id
        session.add(appointment)
        session.commit()
    with UnitOfWork(lambda: Session(engine)) as uow:
        task, _ = uow.queue_tasks.enqueue(
            tenant_id="tenant-1", business_id="biz-1",
            domain=QueueDomain.SCHEDULING.value, task_name="appointment.followup.prepare",
            idempotency_key=f"appointment:{appointment_id}:followup:completion",
            aggregate_type="appointment", aggregate_id=str(appointment_id),
            payload={"appointment_id": str(appointment_id)},
            correlation_id="followup-correlation", traceparent=None,
            available_at=datetime.now(UTC), max_attempts=3,
        )
        task_id = task.id
    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))
    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(),
    )

    result = runner.execute(task_id, expected_domain=QueueDomain.SCHEDULING.value)

    assert result["status"] == "succeeded"
    assert result["result"]["followup_status"] == "pending"
    with Session(engine) as session:
        appointment = session.get(Appointment, appointment_id)
        completed_event = session.exec(select(EventLog).where(
            EventLog.event_type == "appointment.completed",
            EventLog.correlation_id == "followup-correlation",
        )).one()
        followup = session.exec(select(Message).where(
            Message.conversation_id == conversation_id,
            Message.direction == "outbound",
            Message.content.contains("How was your experience?"),
        )).one()
        crm_task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "crm.sync_activity",
            QueueTaskRecord.aggregate_id == str(appointment_id),
        )).one()
        delivery_task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "communication.deliver_message",
            QueueTaskRecord.correlation_id == "followup-correlation",
        )).one()
    assert appointment.status == "completed"
    assert completed_event.correlation_id == crm_task.correlation_id == delivery_task.correlation_id


def test_chat_response_is_recorded_as_http_delivery_without_provider_queue(client):
    response = client.post("/inbound", json=valid_inbound_payload())
    assert response.status_code == 200
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        outbound = session.exec(select(Message).where(Message.direction == "outbound")).one()
        tasks = session.exec(select(QueueTaskRecord)).all()
    assert outbound.delivery_status == "sent"
    assert tasks == []


def test_malformed_inbound_json_returns_validation_error(client):
    response = client.post(
        "/inbound",
        content='{"tenant_id":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422


def test_missing_required_inbound_field_returns_validation_error(client):
    payload = valid_inbound_payload()
    del payload["tenant_id"]

    response = client.post("/inbound", json=payload)

    assert response.status_code == 422


def test_invalid_inbound_field_type_returns_validation_error(client):
    payload = valid_inbound_payload()
    payload["channel"] = ["chat"]

    response = client.post("/inbound", json=payload)

    assert response.status_code == 422


def test_inbound_rejected_when_policy_requires_missing_consent(client):
    payload = valid_inbound_payload()
    payload.update(channel="sms", consent_status="denied")
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.consent_status = "denied"
        session.commit()

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "status": "blocked",
        "workflow_state": "blocked",
        "agent_response": None,
        "reason": "sms requires consent in US: requires_consent",
    }


def test_duplicate_inbound_requests_are_accepted(client):
    payload = valid_inbound_payload()

    first = client.post("/inbound", json=payload)
    second = client.post("/inbound", json=deepcopy(payload))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["success"] is True
    assert second.json()["success"] is True


def test_inbound_response_matches_response_schema(client):
    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 200
    parsed = InboundResponse.model_validate(response.json())
    assert parsed.success is True
    assert parsed.status == "ok"
    assert parsed.workflow_state == "appointment_proposed"
    assert parsed.agent_response is not None
    assert parsed.reason is None


def test_unexpected_extra_input_does_not_cause_server_error(client):
    payload = valid_inbound_payload()
    payload["future_extension"] = {"labels": ["priority", 3], "enabled": True}

    response = client.post("/inbound", json=payload)

    assert response.status_code < 500
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_body_tenant_cannot_select_a_different_tenant(client):
    payload = valid_inbound_payload()
    payload["tenant_id"] = "attacker-tenant"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 403
    assert response.json() == {"detail": "request tenant does not match trusted tenant"}


def test_inbound_persists_conversation_messages_workflow_and_events(client):
    response = client.post("/inbound", json=valid_inbound_payload())
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    with Session(engine) as session:
        conversations = session.exec(select(Conversation)).all()
        messages = session.exec(select(Message)).all()
        events = session.exec(select(EventLog)).all()

    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation.tenant_id == "tenant-1"
    assert conversation.contact_id == "contact-1"
    assert conversation.workflow_state == "appointment_proposed"
    assert {message.direction for message in messages} == {"inbound", "outbound"}
    assert all(message.conversation_id == conversation.id for message in messages)
    assert "appointment.proposed" in {item.event_type for item in events}
    assert all(item.tenant_id == "tenant-1" for item in events)
    with Session(engine) as session:
        assert len(session.exec(select(Appointment)).all()) == 1
        assert "appointment_proposed" in {
            record.state for record in session.exec(select(WorkflowStateRecord)).all()
        }


def test_duplicate_inbound_requests_create_independent_persisted_conversations(client):
    payload = valid_inbound_payload()
    first = client.post("/inbound", json=payload)
    second = client.post("/inbound", json=deepcopy(payload))
    engine = client.app.state.inbound_test_engine

    assert first.status_code == second.status_code == 200
    with Session(engine) as session:
        conversations = session.exec(select(Conversation)).all()

    assert len(conversations) == 2
    assert conversations[0].id != conversations[1].id
    assert conversations[0].contact_id == conversations[1].contact_id == "contact-1"


def test_after_hours_chat_routes_to_configured_voicemail(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        rule = session.exec(select(RoutingRule)).one()
        rule.opens_at = time(14)
        session.commit()
    payload = valid_inbound_payload()
    payload["text"] = "Can I schedule an appointment?"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "after_hours_voicemail"
    assert "leave a voicemail" in response.json()["agent_response"]
    with Session(engine) as session:
        assert session.exec(select(Appointment)).all() == []
        assert session.exec(select(EventLog).where(
            EventLog.event_type == "after_hours.request.routed"
        )).one()


def test_appointment_confirmation_persists_booking_and_reminder_event(client):
    first = client.post("/inbound", json=valid_inbound_payload())
    assert first.status_code == 200
    payload = valid_inbound_payload()
    payload["text"] = "yes, confirm"

    response = client.post("/inbound", json=payload)
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "appointment_confirmed"
    with Session(engine) as session:
        appointment = session.exec(select(Appointment)).one()
        assert appointment.status == "confirmed"
        slot = session.exec(select(AppointmentSlot).where(
            AppointmentSlot.id == appointment.slot_id
        )).one()
        assert slot.status == "booked"
        assert session.exec(select(EventLog).where(
            EventLog.event_type == "appointment.reminder.prepared"
        )).one()


def test_crm_failure_is_isolated_from_confirmed_appointment(client, monkeypatch):
    from app.queue.runtime import QueueTaskRunner
    from app.persistence.models import CRMSyncRecord
    from app.workers import handlers as worker_handlers

    assert client.post("/inbound", json=valid_inbound_payload()).status_code == 200
    confirmation = valid_inbound_payload()
    confirmation["text"] = "yes, confirm"
    assert client.post("/inbound", json=confirmation).status_code == 200
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        appointment = session.exec(select(Appointment)).one()
        appointment_id = appointment.id
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "crm.sync_activity",
        )).one()
        task_id = task.id
        task.max_attempts = 1
        session.commit()

    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))

    class BrokenCRM:
        provider_key = "test_crm"

        def create_activity(self, *args, **kwargs):
            raise RuntimeError("provider detail must not escape")

    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(crm_provider=BrokenCRM()),
    )
    result = runner.execute(task_id, expected_domain="analytics")

    assert result == {"status": "dead_letter", "error_code": "RuntimeError"}
    with Session(engine) as session:
        appointment = session.get(Appointment, appointment_id)
        sync = session.exec(select(CRMSyncRecord)).one()
        task = session.get(QueueTaskRecord, task_id)
    assert appointment.status == "confirmed"
    assert sync.status == "failed"
    assert sync.last_error == "provider_error:RuntimeError"
    assert task.status == "dead_letter"
    assert "provider detail" not in str(sync.model_dump())


def test_sms_style_numbered_slot_reply_confirms_exact_offered_slot(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.phone = "+15555550123"
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="sms", status="granted", source="trusted_test_provisioning",
        ))
        session.commit()
    request = valid_inbound_payload()
    request.update(channel="sms", text="I need an appointment")
    first = client.post("/inbound", json=request)
    assert first.status_code == 200
    assert "1)" in first.json()["agent_response"]
    assert "2)" in first.json()["agent_response"]
    with Session(engine) as session:
        proposal = session.exec(select(Appointment).where(
            Appointment.status == "proposed",
        )).one()
        proposal_id = proposal.id
        offered_ids = list(proposal.proposal_slot_ids)
        assert len(offered_ids) == 3
        expected_slot = session.get(AppointmentSlot, UUID(offered_ids[1]))
        expected_start = expected_slot.starts_at

    reply = valid_inbound_payload()
    reply.update(channel="sms", text="option 2")
    confirmed = client.post("/inbound", json=reply)
    assert confirmed.status_code == 200
    assert confirmed.json()["workflow_state"] == "appointment_confirmed"
    with Session(engine) as session:
        appointment = session.get(Appointment, proposal_id)
        assert appointment.status == "confirmed"
        assert appointment.slot_id == UUID(offered_ids[1])
        assert appointment.starts_at == expected_start


def test_stale_numbered_slot_reproposes_instead_of_booking_other_slot(client):
    engine = client.app.state.inbound_test_engine
    first = client.post("/inbound", json=valid_inbound_payload())
    assert first.status_code == 200
    with Session(engine) as session:
        proposal = session.exec(select(Appointment).where(
            Appointment.status == "proposed",
        )).one()
        proposal_id = proposal.id
        stale_slot_id = UUID(proposal.proposal_slot_ids[1])
        stale = session.get(AppointmentSlot, stale_slot_id)
        stale.status = "booked"
        session.commit()
    request = valid_inbound_payload()
    request["text"] = "2"
    response = client.post("/inbound", json=request)
    assert response.status_code == 200
    assert response.json()["workflow_state"] == "appointment_reproposed"
    assert "no longer available" in response.json()["agent_response"]
    with Session(engine) as session:
        original = session.get(Appointment, proposal_id)
        assert original.status == "conflict"
        active_proposals = session.exec(select(Appointment).where(
            Appointment.status == "proposed",
        )).all()
        assert len(active_proposals) == 1
        assert active_proposals[0].id != original.id


def test_urgent_request_creates_escalation_ticket_and_alert_event(client):
    payload = valid_inbound_payload()
    payload["text"] = "This is an emergency, I need help immediately"

    response = client.post("/inbound", json=payload)
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "escalated"
    with Session(engine) as session:
        ticket = session.exec(select(EscalationTicket)).one()
        assert ticket.priority == "urgent"
        assert session.exec(select(EventLog).where(
            EventLog.event_type == "human.escalation.alert"
        )).one()
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "escalation.notify_staff",
        )).one()
        assert task.payload == {"ticket_id": str(ticket.id)}
        assert task.correlation_id


def test_inbound_call_with_urgent_issue_creates_escalation_workflow(client):
    payload = valid_inbound_payload()
    payload.update(channel="phone", text="Medical emergency, I cannot breathe")
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.add(ConsentRecord(
            tenant_id="tenant-1", business_id="biz-1", contact_id="contact-1",
            channel="phone", status="granted", source="trusted_test_provisioning",
        ))
        session.commit()
    response = client.post("/inbound", json=payload)
    assert response.status_code == 200
    assert response.json()["workflow_state"] == "escalated"
    with Session(engine) as session:
        ticket = session.exec(select(EscalationTicket)).one()
        conversation = session.get(Conversation, ticket.conversation_id)
        alert = session.exec(select(EventLog).where(
            EventLog.event_type == "human.escalation.alert",
        )).one()
    assert ticket.priority == "urgent"
    assert conversation.channel == "phone"
    assert alert.correlation_id == conversation.correlation_id


def test_escalation_worker_notifies_active_staff_through_scoped_email_sender(client, monkeypatch):
    from app.queue.runtime import QueueTaskRunner
    from app.workers import handlers as worker_handlers

    payload = valid_inbound_payload()
    payload["text"] = "This is an emergency, I need help immediately"
    assert client.post("/inbound", json=payload).status_code == 200
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        staff = session.exec(select(Staff)).one()
        staff.email = "oncall@example.test"
        staff_id = staff.id
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "escalation.notify_staff",
        )).one()
        task_id, ticket_id = task.id, task.payload["ticket_id"]
        session.commit()

    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))

    class Sender:
        def __init__(self):
            self.calls = []

        def send(self, **kwargs):
            self.calls.append(kwargs)
            return "internal-alert-1"

    sender = Sender()
    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        handlers=worker_handlers.build_default_handlers(message_sender=sender),
    )
    result = runner.execute(task_id, expected_domain="escalation")
    assert result == {"status": "succeeded",
                      "result": {"ticket_id": ticket_id, "status": "notified", "notified": 1}}
    assert len(sender.calls) == 1
    assert sender.calls[0]["channel"] == "email"
    assert sender.calls[0]["recipient"] == "oncall@example.test"
    assert sender.calls[0]["idempotency_key"] == f"escalation:{ticket_id}:staff:{staff_id}:email"
    assert "emergency" not in sender.calls[0]["content"]["body"].casefold()
    with Session(engine) as session:
        notified = session.exec(select(EventLog).where(
            EventLog.event_type == "human.escalation.notified",
        )).one()
    assert notified.payload["recipient_count"] == 1


def test_policy_rejection_persists_inbound_and_blocked_workflow_without_reply(client):
    payload = valid_inbound_payload()
    payload.update(channel="sms", consent_status="granted")
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.consent_status = "denied"
        session.commit()

    response = client.post("/inbound", json=payload)
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    with Session(engine) as session:
        conversation = session.exec(select(Conversation)).one()
        messages = session.exec(select(Message)).all()
        events = session.exec(select(EventLog)).all()
        states = session.exec(select(WorkflowStateRecord)).all()
        policy_decisions = session.exec(select(PolicyDecisionLog)).all()

    assert conversation.workflow_state == "blocked"
    assert len(messages) == 1 and messages[0].direction == "inbound"
    assert "intake.received" in {item.event_type for item in events}
    assert [item.state for item in states] == ["intake_received", "blocked"]
    assert len(policy_decisions) == 1
    assert policy_decisions[0].decision == "block"
    assert policy_decisions[0].country_code == "US"


def test_agent_failure_rolls_back_all_inbound_persistence(client, monkeypatch):
    from app.services.intake_service import IntakeService

    def fail(*args, **kwargs):
        raise RuntimeError("intake failure")

    monkeypatch.setattr(IntakeService, "_outbound", staticmethod(fail))

    with pytest.raises(RuntimeError, match="intake failure"):
        client.post("/inbound", json=valid_inbound_payload())

    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        assert session.exec(select(Conversation)).all() == []
        assert session.exec(select(Message)).all() == []
        assert session.exec(select(EventLog)).all() == []
        assert session.exec(select(WorkflowStateRecord)).all() == []
        assert session.exec(select(Appointment)).all() == []
        slot = session.exec(select(AppointmentSlot)).first()
        assert slot.status == "available"


def test_missing_provisioned_contact_is_not_created_from_inbound(client):
    payload = valid_inbound_payload()
    payload["contact_id"] = "unprovisioned-contact"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 404
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contacts = session.exec(select(Contact)).all()
        assert len(contacts) == 1
        assert contacts[0].id == "contact-1"
        assert session.exec(select(Conversation)).all() == []


def test_missing_provisioned_business_returns_404_without_creating_it(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.exec(delete(Appointment))
        session.exec(delete(AppointmentSlot))
        session.exec(delete(RoutingRule))
        session.exec(delete(Staff))
        session.exec(delete(Service))
        session.exec(delete(Contact).where(Contact.id == "contact-1"))
        session.exec(delete(Business).where(Business.id == "biz-1"))
        session.commit()

    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Business)).all() == []
        assert session.exec(select(Conversation)).all() == []


def test_contact_provisioned_for_other_business_is_rejected(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.add(Business(
            id="biz-other", tenant_id="tenant-1", name="Other business", vertical="general"
        ))
        session.flush()
        session.add(Contact(
            id="contact-other", tenant_id="tenant-1", business_id="biz-other"
        ))
        session.commit()
    payload = valid_inbound_payload()
    payload["contact_id"] = "contact-other"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Conversation)).all() == []


def test_missing_provisioned_tenant_returns_404_without_creating_it(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.exec(delete(Appointment))
        session.exec(delete(AppointmentSlot))
        session.exec(delete(RoutingRule))
        session.exec(delete(Staff))
        session.exec(delete(Service))
        session.exec(delete(Contact).where(Contact.id == "contact-1"))
        session.exec(delete(Business).where(Business.id == "biz-1"))
        session.exec(delete(Tenant).where(Tenant.id == "tenant-1"))
        session.commit()

    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Tenant)).all() == []
        assert session.exec(select(Conversation)).all() == []
