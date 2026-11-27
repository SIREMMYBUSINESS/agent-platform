from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings
from app.persistence.models import (
    Business,
    ConsentRecord,
    Contact,
    Conversation,
    EventLog,
    Message,
    QueueTaskRecord,
    RoutingRule,
    Staff,
    Tenant,
)
from app.persistence.unit_of_work import UnitOfWork
from app.queue.runtime import QueueTaskRunner
from app.workers import handlers


TENANT_ID = "tenant-hours"
BUSINESS_ID = "business-hours"
CONTACT_ID = "contact-hours"
NOW = datetime(2026, 11, 27, 18, 0, tzinfo=UTC)  # Friday 10:00 in Los Angeles.


class RecordingSender:
    def __init__(self):
        self.calls = []

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return "SM-hours-test"


@pytest.fixture
def composer_delivery_env(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        engine,
        "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    SQLModel.metadata.create_all(engine)

    staff_id = uuid4()
    conversation_id = uuid4()
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_ID, name="Hours tenant", region="US"))
        session.flush()
        session.add(Business(
            id=BUSINESS_ID,
            tenant_id=TENANT_ID,
            name="Hours business",
            vertical="general",
            region="US",
            country_code="US",
            timezone="America/Los_Angeles",
        ))
        session.flush()
        session.add(Contact(
            id=CONTACT_ID,
            tenant_id=TENANT_ID,
            business_id=BUSINESS_ID,
            phone="+15555550123",
            timezone="America/Los_Angeles",
        ))
        session.flush()
        session.add_all([
            Staff(
                id=staff_id,
                tenant_id=TENANT_ID,
                business_id=BUSINESS_ID,
                display_name="Composer staff",
                active=True,
            ),
            Conversation(
                id=conversation_id,
                tenant_id=TENANT_ID,
                business_id=BUSINESS_ID,
                contact_id=CONTACT_ID,
                channel="sms",
            ),
            RoutingRule(
                tenant_id=TENANT_ID,
                business_id=BUSINESS_ID,
                weekday=4,  # Friday in the business timezone.
                opens_at=time(9, 0),
                closes_at=time(17, 0),
                active=True,
            ),
            ConsentRecord(
                tenant_id=TENANT_ID,
                business_id=BUSINESS_ID,
                contact_id=CONTACT_ID,
                channel="sms",
                status="granted",
                source="composer_delivery_hours_test",
            ),
        ])
        session.commit()

    clock = {"now": NOW}
    monkeypatch.setattr(handlers, "session_factory", lambda: Session(engine))
    monkeypatch.setattr(handlers, "_utcnow", lambda: clock["now"])
    monkeypatch.setattr(settings, "workflow_dry_run", False)

    env = {
        "engine": engine,
        "staff_id": staff_id,
        "conversation_id": conversation_id,
        "clock": clock,
        "uow_factory": lambda: UnitOfWork(lambda: Session(engine)),
    }
    try:
        yield env
    finally:
        engine.dispose()


def _deliver(env, *, composer=True, sender=None):
    sender = sender or RecordingSender()
    message_id = uuid4()
    sent_by_staff_id = env["staff_id"] if composer else None
    with Session(env["engine"]) as session:
        session.add(Message(
            id=message_id,
            tenant_id=TENANT_ID,
            conversation_id=env["conversation_id"],
            sender="agent",
            channel="sms",
            content="A delivery-time business-hours test",
            direction="outbound",
            sent_by_staff_id=sent_by_staff_id,
            delivery_status="pending",
        ))
        session.commit()

    with env["uow_factory"]() as uow:
        task, created = uow.queue_tasks.enqueue(
            tenant_id=TENANT_ID,
            business_id=BUSINESS_ID,
            domain="outreach",
            task_name="communication.deliver_message",
            idempotency_key=f"message:{message_id}:delivery",
            aggregate_type="message",
            aggregate_id=str(message_id),
            payload={"message_id": str(message_id)},
            correlation_id=f"hours:{message_id}",
            traceparent=None,
            available_at=env["clock"]["now"] - timedelta(seconds=1),
            max_attempts=5,
        )
        assert created
        task_id = task.id

    runner = QueueTaskRunner(
        uow_factory=env["uow_factory"],
        handlers=handlers.build_default_handlers(message_sender=sender),
        now_provider=lambda: env["clock"]["now"] + timedelta(seconds=1),
    )
    result = runner.execute(task_id, expected_domain="outreach")
    with Session(env["engine"]) as session:
        message = session.get(Message, message_id)
        events = session.scalars(
            select(EventLog).where(
                EventLog.event_type == "outbound.delivery.blocked",
                EventLog.source == "message_delivery_worker",
                EventLog.conversation_id == env["conversation_id"],
            )
        ).all()
        stored_task = session.get(QueueTaskRecord, task_id)
    return result, sender, message, events, stored_task


def test_composer_delivery_outside_hours_is_blocked_without_provider_call(composer_delivery_env):
    env = composer_delivery_env
    env["clock"]["now"] = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)  # Friday 18:00 PST.

    result, sender, message, events, task = _deliver(env)

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == "blocked"
    assert task.status == "succeeded"
    assert message.delivery_status == "blocked"
    assert message.delivery_error_code == "outside_business_hours"
    assert sender.calls == []
    assert len(events) == 1
    assert events[0].payload["reason_code"] == "outside_business_hours"


def test_composer_delivery_inside_hours_is_sent(composer_delivery_env):
    result, sender, message, events, task = _deliver(composer_delivery_env)

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == "sent"
    assert task.status == "succeeded"
    assert message.delivery_status == "sent"
    assert message.provider_message_id == "SM-hours-test"
    assert len(sender.calls) == 1
    assert events == []


def test_composer_delivery_without_active_routing_rules_is_blocked(composer_delivery_env):
    env = composer_delivery_env
    with Session(env["engine"]) as session:
        rules = session.scalars(select(RoutingRule)).all()
        for rule in rules:
            rule.active = False
        session.commit()

    result, sender, message, events, task = _deliver(env)

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == "blocked"
    assert task.status == "succeeded"
    assert message.delivery_status == "blocked"
    assert message.delivery_error_code == "business_hours_not_configured"
    assert sender.calls == []
    assert len(events) == 1
    assert events[0].payload["reason_code"] == "business_hours_not_configured"


def test_composer_hours_recheck_runs_before_worker_dry_run(composer_delivery_env, monkeypatch):
    env = composer_delivery_env
    env["clock"]["now"] = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)
    monkeypatch.setattr(settings, "workflow_dry_run", True)

    result, sender, message, events, task = _deliver(env)

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == "blocked"
    assert task.status == "succeeded"
    assert message.delivery_status == "blocked"
    assert message.delivery_error_code == "outside_business_hours"
    assert message.delivery_status != "dry_run"
    assert sender.calls == []
    assert len(events) == 1


def test_non_composer_outbound_is_not_subject_to_hours_recheck(composer_delivery_env):
    env = composer_delivery_env
    env["clock"]["now"] = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)

    result, sender, message, events, task = _deliver(env, composer=False)

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == "sent"
    assert task.status == "succeeded"
    assert message.sent_by_staff_id is None
    assert message.delivery_status == "sent"
    assert len(sender.calls) == 1
    assert events == []


@pytest.mark.parametrize(
    ("now", "expected_status", "expected_reason"),
    [
        # 09:00 at the business location is the inclusive opening boundary.
        (datetime(2026, 11, 27, 17, 0, tzinfo=UTC), "sent", None),
        # 17:00 at the business location is the exclusive closing boundary.
        (datetime(2026, 11, 28, 1, 0, tzinfo=UTC), "blocked", "outside_business_hours"),
    ],
)
def test_delivery_hours_use_business_timezone_open_and_close_boundaries(
    composer_delivery_env, now, expected_status, expected_reason,
):
    env = composer_delivery_env
    env["clock"]["now"] = now

    result, sender, message, events, task = _deliver(env)

    assert result["status"] == "succeeded"
    assert result["result"]["status"] == expected_status
    assert task.status == "succeeded"
    assert message.delivery_status == expected_status
    if expected_reason:
        assert message.delivery_error_code == expected_reason
        assert sender.calls == []
        assert len(events) == 1
        assert events[0].payload["reason_code"] == expected_reason
    else:
        assert message.delivery_error_code is None
        assert len(sender.calls) == 1
        assert events == []
