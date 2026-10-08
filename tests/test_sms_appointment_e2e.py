"""Provider-isolated end-to-end test for SMS booking and reminders."""
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.persistence.models import (
    Appointment,
    AppointmentSlot,
    Business,
    ConsentRecord,
    Contact,
    Message,
    QueueTaskRecord,
    RoutingRule,
    Service,
    Staff,
    Tenant,
)
from app.persistence.unit_of_work import UnitOfWork
from app.queue.runtime import QueueTaskRunner
from app.workers.handlers import build_default_handlers


def test_sms_intake_appointment_calendar_invite_and_reminder(monkeypatch):
    import app.routes.provider_ingress as ingress
    import app.workers.handlers as worker_handlers

    tenant_id, business_id, contact_id = "tenant-e2e", "business-e2e", "contact-e2e"
    phone, caller_id = "+15559990001", "+15550000001"
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    staff_id, service_id = uuid4(), uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="E2E tenant", region="US"))
        session.flush()
        session.add(Business(
            id=business_id, tenant_id=tenant_id, name="E2E business",
            vertical="general", region="US", timezone="UTC",
        ))
        session.flush()
        session.add(Contact(
            id=contact_id, tenant_id=tenant_id, business_id=business_id,
            full_name="Alex Example", phone=phone, email="alex@example.test",
            consent_status="granted", timezone="UTC", preferred_channel="sms",
        ))
        session.flush()
        session.add(ConsentRecord(
            tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
            channel="sms", status="granted", source="e2e_test_fixture",
        ))
        session.add_all([
            Staff(id=staff_id, tenant_id=tenant_id, business_id=business_id,
                  display_name="Taylor"),
            Service(id=service_id, tenant_id=tenant_id, business_id=business_id,
                    name="Consultation", duration_minutes=30),
            *[
                RoutingRule(tenant_id=tenant_id, business_id=business_id,
                            weekday=weekday, opens_at=time(0), closes_at=time(23, 59))
                for weekday in range(7)
            ],
        ])
        session.flush()
        session.add_all([
            *[
                AppointmentSlot(
                    tenant_id=tenant_id, business_id=business_id,
                    staff_id=staff_id, service_id=service_id,
                    starts_at=now + timedelta(days=days),
                    ends_at=now + timedelta(days=days, minutes=30),
                )
                for days in (3, 4, 5)
            ],
        ])
        session.commit()

    class FakeTenantConfig:
        def __init__(self, _secrets_dir):
            pass

        def resolve(self, *, tenant_id: str, business_id: str):
            assert (tenant_id, business_id) == ("tenant-e2e", "business-e2e")
            return {
                "account_sid": "AC_TEST",
                "auth_token": "test-only-token",
                "from_sms": caller_id,
                "sms_inbound_webhook_url": (
                    "https://api.example.test/webhooks/sms?tenant_id=tenant-e2e"
                    "&business_id=business-e2e"
                ),
            }

    class RecordingSms:
        def __init__(self):
            self.sent = []

        def send(self, **kwargs):
            self.sent.append(kwargs)
            return f"SM_OUT_{len(self.sent)}"

    class RecordingCalendar:
        def __init__(self):
            self.events = []

        def availability(self, **_kwargs):
            return []

        def create_event(self, **kwargs):
            self.events.append(kwargs)
            return {"id": "calendar-event-e2e"}

    sms, calendar = RecordingSms(), RecordingCalendar()
    session_factory = lambda: Session(engine, expire_on_commit=False)
    monkeypatch.setattr(ingress.settings, "provider_secrets_dir", Path("test-secrets"))
    monkeypatch.setattr(ingress, "DirectoryTenantConfigResolver", FakeTenantConfig)
    monkeypatch.setattr(ingress, "validate_twilio_signature", lambda **_kwargs: True)
    monkeypatch.setattr(ingress, "session_factory", session_factory)
    monkeypatch.setattr(worker_handlers, "session_factory", session_factory)
    # Regional transfer authorization is tested separately; these fake adapters
    # have no region boundary and cannot contact external providers.
    monkeypatch.setattr(worker_handlers, "_authorize_provider_region", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker_handlers.settings, "workflow_dry_run", False)
    runner = QueueTaskRunner(
        uow_factory=lambda: UnitOfWork(session_factory),
        handlers=build_default_handlers(message_sender=sms, calendar_provider=calendar),
    )

    def receive_sms(message_sid: str, body: str):
        response = client.post(
            f"/webhooks/sms?tenant_id={tenant_id}&business_id={business_id}",
            data={"AccountSid": "AC_TEST", "From": phone, "To": caller_id,
                  "MessageSid": message_sid, "Body": body},
            headers={"X-Twilio-Signature": "test-signature"},
        )
        assert response.status_code == 202, response.text
        assert response.json()["accepted"] is True
        with Session(engine) as session:
            task = session.exec(select(QueueTaskRecord).where(
                QueueTaskRecord.task_name == "inbound.process",
                QueueTaskRecord.status == "pending",
            ).order_by(QueueTaskRecord.created_at.desc())).first()
            assert task is not None
            task_id = task.id
        result = runner.execute(task_id, expected_domain="inbound")
        assert result["status"] == "succeeded", result
        return result["result"]

    def deliver_next_sms():
        with Session(engine) as session:
            task = session.exec(select(QueueTaskRecord).where(
                QueueTaskRecord.task_name == "communication.deliver_message",
                QueueTaskRecord.status == "pending",
            ).order_by(QueueTaskRecord.created_at)).first()
            assert task is not None
            task_id = task.id
        result = runner.execute(task_id, expected_domain="outreach")
        assert result["status"] == "succeeded", result
        assert result["result"]["status"] == "sent", result

    with TestClient(app) as client:
        proposed = receive_sms("SM" + "1" * 32, "I need an appointment for Consultation")
        assert proposed["workflow_state"] == "appointment_proposed"
        deliver_next_sms()
        assert "Consultation" in sms.sent[-1]["content"]["text"]

        confirmed = receive_sms("SM" + "2" * 32, "1")
        assert confirmed["workflow_state"] == "appointment_confirmed"
        deliver_next_sms()
        assert "confirmed" in sms.sent[-1]["content"]["text"].casefold()

        with Session(engine) as session:
            appointment = session.exec(select(Appointment)).one()
            appointment_id = appointment.id
            queued = session.exec(select(QueueTaskRecord).where(
                QueueTaskRecord.task_name == "scheduling.calendar_sync",
                QueueTaskRecord.status == "pending",
            )).one()
            calendar_task_id = queued.id
        calendar_result = runner.execute(calendar_task_id, expected_domain="scheduling")
        assert calendar_result["status"] == "succeeded", calendar_result
        assert calendar.events[0]["event"]["attendees"] == [{"email": "alex@example.test"}]
        assert calendar.events[0]["event"]["summary"] == "Appointment with Alex Example"
        with Session(engine) as session:
            appointment = session.get(Appointment, appointment_id)
            assert appointment.status == "confirmed"
            assert appointment.calendar_event_id == "calendar-event-e2e"
            reminder_tasks = session.exec(select(QueueTaskRecord).where(
                QueueTaskRecord.task_name == "appointment.reminder.prepare",
                QueueTaskRecord.status == "pending",
            )).all()
            reminder = next(task for task in reminder_tasks if task.payload["reminder"] == "24h")
            # Advance this durable timer into its due window for the test run.
            reminder.available_at = datetime.now(UTC) - timedelta(seconds=1)
            reminder_id = reminder.id
            session.commit()

        reminder_result = runner.execute(reminder_id, expected_domain="scheduling")
        assert reminder_result["status"] == "succeeded", reminder_result
        assert reminder_result["result"]["status"] == "pending"
        deliver_next_sms()
        assert "Reminder:" in sms.sent[-1]["content"]["text"]

        with Session(engine) as session:
            reminder_message = session.exec(select(Message).where(
                Message.content.like("Reminder:%"),
            )).one()
            assert reminder_message.delivery_status == "sent"
            assert reminder_message.provider_message_id == "SM_OUT_3"

    engine.dispose()
