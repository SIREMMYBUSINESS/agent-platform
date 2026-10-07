from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.appointment_workflow_service import AppointmentWorkflowService
from app.services.appointment_reminder_service import AppointmentReminderService
from app.services.contact_action_tokens import ContactActionTokens


class FakeUow:
    def __init__(self, appointment):
        self.appointment = appointment
        self.tasks = []
        self.events = []
        def confirm(**kwargs):
            self.appointment.status = "confirmed"
            return self.appointment

        self.appointments = SimpleNamespace(
            confirm=confirm,
            cancel=lambda **kwargs: self.appointment,
            get_in_tenant=lambda **kwargs: self.appointment,
            get_for_tenant=lambda **kwargs: self.appointment,
            latest_proposed=lambda **kwargs: self.appointment,
        )
        self.contacts = SimpleNamespace(get=lambda **kwargs: SimpleNamespace(timezone="UTC"))
        self.queue_tasks = SimpleNamespace(enqueue=lambda **kwargs: self.tasks.append(kwargs))
        self.events = SimpleNamespace(add=lambda **kwargs: self.events_list.append(kwargs))
        self.events_list = []
        self.workflows = SimpleNamespace(append=lambda **kwargs: None)
        self.optimization = SimpleNamespace(
            add_feedback=lambda **kwargs: SimpleNamespace(id=uuid4()),
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def session(self):
        return SimpleNamespace(exec=lambda stmt: SimpleNamespace(first=lambda: self.appointment))


def principal():
    return SimpleNamespace(tenant_id="tenant-a", roles=("workflow_executor",))


def test_confirmation_records_idempotent_calendar_task_and_correlates_event(monkeypatch):
    queued = []
    class Outbound:
        def queue(self, *args, **kwargs):
            queued.append(kwargs)
    appointment = SimpleNamespace(
        id=uuid4(), tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
        starts_at=datetime.now(UTC) + timedelta(days=1), ends_at=datetime.now(UTC) + timedelta(days=1, hours=1),
        status="proposed", source="inbound", correlation_id=None,
        conversation_id=uuid4(), calendar_event_id=None,
        campaign_id=None, campaign_enrollment_id=None, slot_id=None,
    )
    uow = FakeUow(appointment)
    conversation = SimpleNamespace(
        id=appointment.conversation_id, channel="sms", tenant_id="tenant-a",
        business_id="business-a", contact_id="contact-a",
    )
    uow.conversations = SimpleNamespace(get=lambda **kwargs: conversation)
    uow.tenants = SimpleNamespace(get=lambda **kwargs: SimpleNamespace(id="tenant-a"))
    uow.businesses = SimpleNamespace(get=lambda **kwargs: SimpleNamespace(
        id="business-a", tenant_id="tenant-a",
    ))
    monkeypatch.setattr("app.services.appointment_workflow_service.OutboundDeliveryService", Outbound)
    monkeypatch.setattr(
        "app.services.appointment_workflow_service.ContactPreferenceService",
        lambda: SimpleNamespace(choose_channel=lambda **kwargs: "sms"),
    )
    service = AppointmentWorkflowService(
        trusted_tenant_id="tenant-a", uow_factory=lambda: uow,
    )
    result = service.confirm(principal(), appointment.id)
    task_count = len(uow.tasks)
    assert service.confirm(principal(), appointment.id) is appointment
    assert len(uow.tasks) == task_count

    assert result.status == "confirmed"
    calendar_task = next(task for task in uow.tasks if task["task_name"] == "scheduling.calendar_sync")
    assert calendar_task["idempotency_key"] == f"appointment:{appointment.id}:calendar:create"
    assert calendar_task["correlation_id"] == str(appointment.conversation_id)
    assert uow.events_list[0]["correlation_id"] == str(appointment.conversation_id)
    assert queued[0]["content"].startswith("Your appointment is confirmed for")


def test_workflow_action_requires_executor_role():
    service = AppointmentWorkflowService(trusted_tenant_id="tenant-a", uow_factory=lambda: None)
    with pytest.raises(PermissionError):
        service.get(SimpleNamespace(tenant_id="tenant-a", roles=("campaign_reader",)), uuid4())


def test_contact_action_token_is_hashed_and_derives_tenant_from_appointment():
    appointment = SimpleNamespace(
        id=uuid4(), tenant_id="trusted-tenant", business_id="business-a", contact_id="contact-a",
        starts_at=datetime.now(UTC) + timedelta(days=1), status="proposed",
    )
    uow = FakeUow(appointment)
    looked_up = []
    uow.appointments.get_by_contact_action_token = lambda **kwargs: (
        looked_up.append(kwargs) or appointment
    )
    token = "a" * 48
    service = AppointmentWorkflowService(
        trusted_tenant_id="contact-action", uow_factory=lambda: uow,
    )

    scoped, principal, appointment_id = service._contact_principal(token)

    assert looked_up == [{"token_hash": ContactActionTokens.digest(token)}]
    assert principal.tenant_id == "trusted-tenant"
    assert principal.auth_source == "appointment_action_token"
    assert scoped._tenant_id == "trusted-tenant"
    assert appointment_id == appointment.id


def test_contact_action_token_rejects_expired_appointment():
    appointment = SimpleNamespace(
        id=uuid4(), tenant_id="trusted-tenant", business_id="business-a", contact_id="contact-a",
        starts_at=datetime.now(UTC) - timedelta(minutes=1), status="proposed",
    )
    uow = FakeUow(appointment)
    uow.appointments.get_by_contact_action_token = lambda **kwargs: appointment
    service = AppointmentWorkflowService(
        trusted_tenant_id="contact-action", uow_factory=lambda: uow,
    )
    with pytest.raises(LookupError):
        service._contact_principal("a" * 48)


def test_confirmed_appointment_schedules_24h_and_1h_reminders():
    now = datetime.now(UTC)
    appointment = SimpleNamespace(
        id=uuid4(), tenant_id="tenant-a", business_id="business-a",
        starts_at=now + timedelta(hours=48), ends_at=now + timedelta(hours=49),
        correlation_id="corr-1",
    )
    uow = FakeUow(appointment)

    count = AppointmentReminderService().schedule(uow, appointment=appointment, now=now)

    assert count == 3
    assert {task["task_name"] for task in uow.tasks} == {
        "appointment.reminder.prepare", "appointment.followup.prepare",
    }
    assert {task["payload"].get("reminder") for task in uow.tasks
            if task["task_name"] == "appointment.reminder.prepare"} == {"24h", "1h"}
    assert all(task["correlation_id"] == "corr-1" for task in uow.tasks)
