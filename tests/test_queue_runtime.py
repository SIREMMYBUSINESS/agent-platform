from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from fastapi.testclient import TestClient

from app.auth.api_keys import generate_api_key, hash_api_key
from app.auth.dependencies import get_auth_uow_factory
from app.main import app, get_queue_task_service
from app.observability.metrics import MetricsRegistry
from app.persistence.models import ApiKey, ApiKeyGrant, Business, Contact, Conversation, EventLog, Tenant
from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueDomain, QueueTaskSubmission
from app.queue.dispatcher import DurableQueueDispatcher
from app.queue.routing import route_for, worker_pool_assumptions
from app.queue.replay import EventReplayService
from app.queue.runtime import QueueTaskRunner, TaskHandlerRegistry
from app.queue.service import QueueAuthorizationError, QueueTaskConflict, QueueTaskService
from app.security import AuthenticatedPrincipal
from app.services.lead_scoring_service import LeadScoringService
from app.services.next_best_action import NextBestActionService
from app.services.policy_service import PolicyDecision
from app.services.scheduling_optimization import SchedulingOptimizationService
from app.services.sequence_experiments import SequenceExperimentService


NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


@pytest.fixture
def queue_store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="A"))
        session.flush()
        session.add(Business(id="business-a", tenant_id="tenant-a", name="A", vertical="general"))
        session.flush()
        session.add(Contact(id="contact-a", tenant_id="tenant-a", business_id="business-a"))
        session.commit()
    factory = lambda: UnitOfWork(lambda: Session(engine))
    yield engine, factory
    engine.dispose()


def principal(*roles):
    return AuthenticatedPrincipal("actor-a", "tenant-a", tuple(roles), "test", NOW - timedelta(minutes=1))


def submission(*, key="inbound:contact-a", available_at=NOW, max_attempts=3):
    return QueueTaskSubmission(
        domain=QueueDomain.INBOUND, task_name="inbound.process",
        business_id="business-a", idempotency_key=key,
        aggregate_type="contact", aggregate_id="contact-a",
        payload={"contact_id": "contact-a", "channel": "chat", "text": "Need help"},
        correlation_id="correlation-a", available_at=available_at,
        max_attempts=max_attempts,
    )


def create_service(factory):
    return QueueTaskService(uow_factory=factory, now_provider=lambda: NOW)


def test_enqueue_is_durable_idempotent_and_scoped(queue_store):
    _, factory = queue_store
    service = create_service(factory)
    writer = principal("inbound_writer")
    first = service.enqueue(writer, submission())
    replay = service.enqueue(writer, submission())
    assert replay.id == first.id
    assert first.status == "pending"
    with pytest.raises(QueueAuthorizationError):
        service.enqueue(principal("campaign_reader"), submission(key="wrong-role"))
    with pytest.raises(QueueAuthorizationError):
        service.enqueue(writer, submission(key="wrong-contact").model_copy(update={"aggregate_id": "missing"}))
    with pytest.raises(QueueTaskConflict, match="conflicts"):
        service.enqueue(writer, submission().model_copy(update={"payload": {"text": "different"}}))


def test_inbound_queue_endpoint_returns_accepted_and_is_idempotent(queue_store):
    _, factory = queue_store
    key = generate_api_key()
    with factory() as uow:
        uow.session.add(ApiKey(id="queue-key", tenant_id="tenant-a", key_hash=hash_api_key(key)))
        uow.session.flush()
        uow.session.add(ApiKeyGrant(
            principal_id="queue-key", tenant_id="tenant-a", roles=["inbound_writer"],
        ))
    app.dependency_overrides[get_auth_uow_factory] = lambda: factory
    app.dependency_overrides[get_queue_task_service] = lambda: create_service(factory)
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {key}", "Idempotency-Key": "queued-inbound-1"}
            body = {"tenant_id": "tenant-a", "business_id": "business-a",
                    "contact_id": "contact-a", "channel": "chat", "text": "Hello"}
            first = client.post("/inbound/queue", headers=headers, json=body)
            replay = client.post("/inbound/queue", headers=headers, json=body)
            assert first.status_code == 202, first.text
            assert replay.status_code == 202
            assert first.json()["id"] == replay.json()["id"]
            assert first.json()["status"] == "pending"
            mismatch = client.post("/inbound/queue", headers=headers,
                                   json={**body, "tenant_id": "other-tenant"})
            assert mismatch.status_code == 403
            assert "result" not in first.json()
    finally:
        app.dependency_overrides.clear()


def test_dispatch_routes_tasks_to_domain_queue_with_tenant_business_metadata(queue_store):
    _, factory = queue_store
    service = create_service(factory)
    task = service.enqueue(principal("inbound_writer"), submission())

    class Publisher:
        def __init__(self):
            self.calls = []

        def send_task(self, name, *, args, task_id, queue, headers):
            self.calls.append((name, args, task_id, queue, headers))

    publisher = Publisher()
    dispatched = DurableQueueDispatcher(
        uow_factory=factory, publisher=publisher, now_provider=lambda: NOW,
    ).dispatch_due()
    assert dispatched == 1
    name, args, task_id, queue, headers = publisher.calls[0]
    assert (name, queue) == ("agent_platform.worker.inbound", "agent.inbound")
    assert args == [str(task.id)] and task_id == str(task.id)
    assert headers["tenant_id"] == "tenant-a"
    assert headers["business_id"] == "business-a"


def test_scheduled_tasks_wait_until_due_and_stale_worker_claims_are_recovered(queue_store):
    _, factory = queue_store
    task = create_service(factory).enqueue(
        principal("inbound_writer"),
        submission(key="future", available_at=NOW + timedelta(minutes=10)),
    )
    class Publisher:
        def __init__(self):
            self.calls = []

        def send_task(self, name, *, args, task_id, queue, headers):
            self.calls.append(task_id)

    publisher = Publisher()
    clock = [NOW]
    dispatcher = DurableQueueDispatcher(
        uow_factory=factory, publisher=publisher, now_provider=lambda: clock[0],
    )
    assert dispatcher.dispatch_due() == 0
    clock[0] += timedelta(minutes=10)
    assert dispatcher.dispatch_due() == 1
    with factory() as uow:
        claimed = uow.queue_tasks.claim(
            task_id=task.id, now=clock[0], lease=timedelta(hours=1),
        )
        assert claimed is not None and claimed.status == "running"
    clock[0] += timedelta(hours=1, seconds=1)
    assert dispatcher.dispatch_due() == 1
    assert publisher.calls == [str(task.id), str(task.id)]


def test_event_replay_queues_only_a_tenant_scoped_persisted_event(queue_store, monkeypatch):
    engine, factory = queue_store
    with Session(engine) as session:
        conversation = Conversation(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat",
        )
        session.add(conversation)
        session.flush()
        event = EventLog(
            tenant_id="tenant-a", business_id="business-a", conversation_id=conversation.id,
            event_type="workflow.state.changed", payload={"state": "open"}, source="test",
        )
        session.add(event)
        session.commit()
        event_id = event.id
    service = create_service(factory)
    replay = EventReplayService(uow_factory=factory, queue_tasks=service)
    first = replay.replay(
        principal("inbound_writer"), business_id="business-a", event_id=event_id,
        domain=QueueDomain.INBOUND, task_name="events.replay", replay_key="replay-1",
    )
    second = replay.replay(
        principal("inbound_writer"), business_id="business-a", event_id=event_id,
        domain=QueueDomain.INBOUND, task_name="events.replay", replay_key="replay-1",
    )
    assert first.id == second.id
    assert first.status == "pending"
    from app.workers import handlers as worker_handlers
    from app.workers.event_replay import register_event_subscriber
    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))
    effects = []
    register_event_subscriber(
        "workflow.state.changed",
        lambda event, task: effects.append((event.id, task.idempotency_key)) or {"replayed": True},
    )
    runner = QueueTaskRunner(
        uow_factory=factory, handlers=worker_handlers.build_default_handlers(),
        now_provider=lambda: datetime.now(UTC) + timedelta(seconds=1),
    )
    assert runner.execute(first.id, expected_domain="inbound")["status"] == "succeeded"
    assert runner.execute(first.id, expected_domain="inbound")["status"] == "duplicate_or_terminal"
    assert effects == [(event_id, first.idempotency_key)]


def test_worker_retry_dead_letter_and_redrive_are_durable(queue_store):
    engine, factory = queue_store
    service = create_service(factory)
    task = service.enqueue(
        principal("inbound_writer"), submission(key="retry-me", max_attempts=2),
    )
    clock = [NOW]
    registry = TaskHandlerRegistry()
    registry.register("inbound", "inbound.process", lambda _: (_ for _ in ()).throw(RuntimeError("private detail")))
    runner = QueueTaskRunner(
        uow_factory=factory, handlers=registry, now_provider=lambda: clock[0],
        base_retry_seconds=5,
    )
    first = runner.execute(task.id, expected_domain="inbound")
    assert first == {"status": "retrying", "error_code": "RuntimeError"}
    clock[0] += timedelta(seconds=5)
    second = runner.execute(task.id, expected_domain="inbound")
    assert second == {"status": "dead_letter", "error_code": "RuntimeError"}
    with Session(engine) as session:
        from app.persistence.models import QueueTaskRecord
        row = session.get(QueueTaskRecord, task.id)
        assert row.status == "dead_letter" and row.attempts == 2
        assert "private detail" not in str(row.model_dump())
    redriven = service.redrive(principal("workflow_executor"),
                               business_id="business-a", task_id=task.id)
    assert redriven.status == "pending" and redriven.attempts == 0


def test_duplicate_worker_delivery_runs_handler_once(queue_store):
    _, factory = queue_store
    task = create_service(factory).enqueue(principal("inbound_writer"), submission(key="once"))
    calls = []
    registry = TaskHandlerRegistry()
    registry.register("inbound", "inbound.process", lambda _: calls.append("run") or {"ok": True})
    runner = QueueTaskRunner(uow_factory=factory, handlers=registry, now_provider=lambda: NOW)
    assert runner.execute(task.id, expected_domain="inbound")["status"] == "succeeded"
    assert runner.execute(task.id, expected_domain="inbound")["status"] == "duplicate_or_terminal"
    assert calls == ["run"]


def test_worker_pool_assumptions_and_domain_routing_are_bounded():
    assert worker_pool_assumptions() == {
        "inbound": 4, "outreach": 8, "scheduling": 2,
        "escalation": 2, "analytics": 2,
    }
    assert route_for("outreach", tenant_id="tenant-a", business_id="business-a").queue == "agent.outreach"
    with pytest.raises(ValueError):
        route_for("inbound", tenant_id="", business_id="business-a")


def test_queue_metrics_have_bounded_domain_labels_and_no_tenant_labels():
    registry = MetricsRegistry()
    registry.queue_event("analytics", "dispatch", "queued")
    output = registry.render()
    assert 'queue_tasks_total{operation="dispatch",outcome="queued",queue_domain="analytics"} 1' in output
    assert "tenant_id" not in output
    with pytest.raises(ValueError):
        registry.queue_event("tenant-secret", "dispatch", "queued")


def test_next_best_action_uses_policy_score_urgency_and_business_constraints():
    service = NextBestActionService()
    allowed = PolicyDecision(True, "policy_allowed", "US", "granted", "country")
    urgent = service.select(
        enrichment={"lead_fit_signal": {"fit_score": 90, "urgency_score": 95, "intent_score": 90}},
        policy_decision=allowed, contactable=True, business_open=False,
        appointment_available=False, campaign_capacity=False, allowed_channels=(),
    )
    assert urgent.decision.action == "human_escalation"
    routine = service.select(
        enrichment={"lead_fit_signal": {"fit_score": 100, "urgency_score": 30, "intent_score": 100}},
        policy_decision=allowed, contactable=True, business_open=True,
        appointment_available=True, campaign_capacity=True, allowed_channels=("email",),
    )
    assert routine.decision.action == "offer_appointment"
    closed = service.select(
        enrichment={"lead_fit_signal": {"fit_score": 90, "urgency_score": 30, "intent_score": 90}},
        policy_decision=allowed, contactable=True, business_open=False,
        appointment_available=True, campaign_capacity=True, allowed_channels=("email",),
    )
    assert closed.decision.action == "schedule_follow_up"
    blocked = service.select(
        enrichment=None,
        policy_decision=PolicyDecision(False, "suppressed", "US", "revoked", "policy"),
        contactable=True, business_open=True, appointment_available=False,
        campaign_capacity=True, allowed_channels=("email",),
    )
    assert blocked.decision.action == "policy_blocked"
    base = LeadScoringService().score({"lead_fit_signal": {"fit_score": 50, "urgency_score": 50, "intent_score": 50}})
    improved = LeadScoringService().score({"lead_fit_signal": {
        "fit_score": 50, "urgency_score": 50, "intent_score": 50,
        "engagement_score": 100, "recency_score": 100,
    }})
    assert improved.priority_score > base.priority_score


def test_sequence_ab_assignment_is_stable_and_conversion_analysis_is_explicit():
    experiment = SequenceExperimentService()
    args = dict(tenant_id="tenant-a", business_id="business-a", campaign_id="campaign-a",
                lead_id="lead-a", experiment_id="subject-line-v1", weights={"A": 1, "B": 1})
    assert experiment.assign_variant(**args) == experiment.assign_variant(**args)
    results = experiment.compare({"A": (100, 12), "B": (100, 18), "C": (0, 0)})
    assert [(row.variant, row.conversion_rate) for row in results] == [
        ("A", 0.12), ("B", 0.18), ("C", 0.0),
    ]


def test_scheduling_optimizer_applies_local_preferences_and_horizon():
    optimizer = SchedulingOptimizationService()
    now = datetime(2026, 10, 5, 12, tzinfo=UTC)  # Monday
    later_monday = SimpleNamespace(starts_at=datetime(2026, 10, 12, 14, tzinfo=UTC), staff_id="staff-b")
    early_tuesday = SimpleNamespace(starts_at=datetime(2026, 10, 6, 9, tzinfo=UTC), staff_id="staff-a")
    ranked = optimizer.rank_slots(
        [early_tuesday, later_monday], now=now, timezone_name="UTC",
        allowed_weekdays=(0,), preferred_time=(time(13), time(17)), max_advance_days=14,
    )
    assert ranked == [later_monday]
    assert optimizer.rank_slots([later_monday], now=now, timezone_name="UTC",
                                max_advance_days=2) == []
    with pytest.raises(ValueError):
        optimizer.rank_slots([later_monday], now=now, timezone_name="UTC", allowed_weekdays=(7,))
