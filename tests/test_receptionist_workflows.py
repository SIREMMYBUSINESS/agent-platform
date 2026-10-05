from datetime import UTC, datetime, time, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.auth.api_keys import generate_api_key, hash_api_key
from app.auth.dependencies import get_auth_uow_factory
from app.auth.principal_resolver import AuthorizationError
from app.main import app
from app.persistence.models import (
    ApiKey, ApiKeyGrant, Business, Contact, Conversation, ConversationAssignment,
    EscalationTicket, EventLog, FollowUpTask, RoutingRule, Staff, Tenant,
    WorkflowExecution, WorkflowStateRecord,
)
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.conversation_service import ConversationService
from app.workflows.business_hours import BusinessHoursService
from app.workflows.escalation import EscalationRules, EscalationService
from app.workflows.receptionist import (
    ReceptionistActions, ReceptionistWorkflowEngine, WorkflowConflictError,
    WorkflowNotFoundError, WorkflowRegistry,
)


NOW = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)  # Monday 09:00 in New York.


def principal(tenant="tenant-a", roles=("workflow_executor", "conversation_reader", "conversation_writer")):
    return AuthenticatedPrincipal("actor-a", tenant, roles, "test", datetime.now(UTC) - timedelta(minutes=1))


@pytest.fixture
def platform(monkeypatch):
    engine_db = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine_db, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine_db)
    staff_id = uuid4()
    keys = {tenant: generate_api_key() for tenant in ("tenant-a", "tenant-b")}
    with Session(engine_db) as session:
        session.add_all([Tenant(id=tenant, name=tenant, default_timezone="America/New_York") for tenant in keys])
        session.flush()
        session.add_all([
            Business(id="business-a", tenant_id="tenant-a", name="A", vertical="general", timezone="America/New_York"),
            Business(id="business-b", tenant_id="tenant-b", name="B", vertical="general", timezone="America/New_York"),
        ])
        session.flush()
        session.add_all([
            Contact(id="contact-a", tenant_id="tenant-a", business_id="business-a"),
            Contact(id="contact-b", tenant_id="tenant-b", business_id="business-b"),
            Staff(id=staff_id, tenant_id="tenant-a", business_id="business-a", display_name="Operator"),
        ])
        session.add_all([RoutingRule(tenant_id="tenant-a", business_id="business-a", weekday=day,
                                     opens_at=time(9), closes_at=time(17), after_hours_action="callback")
                         for day in range(5)])
        for tenant, key in keys.items():
            session.add(ApiKey(id=tenant + "-key", tenant_id=tenant, key_hash=hash_api_key(key)))
        session.flush()
        session.add_all([ApiKeyGrant(principal_id=tenant + "-key", tenant_id=tenant,
                                     roles=["workflow_executor", "conversation_reader", "conversation_writer"])
                         for tenant in keys])
        session.commit()
    def uow_factory():
        return UnitOfWork(lambda: Session(engine_db))
    workflow_engine = ReceptionistWorkflowEngine(uow_factory=uow_factory, now_provider=lambda: NOW)
    actions = ReceptionistActions(workflow_engine)
    conversation_service = ConversationService(uow_factory=uow_factory, now_provider=lambda: NOW)
    app.dependency_overrides[get_auth_uow_factory] = lambda: uow_factory
    monkeypatch.setattr("app.routes.conversations.session_factory", lambda: Session(engine_db))
    monkeypatch.setattr("app.routes.conversations.get_receptionist_actions", lambda: actions)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield engine_db, uow_factory, workflow_engine, actions, conversation_service, client, keys, staff_id
    finally:
        app.dependency_overrides.clear()
        engine_db.dispose()


def create_conversation(service, tenant="tenant-a"):
    return service.create(principal(tenant), contact_id="contact-a" if tenant == "tenant-a" else "contact-b", channel="chat")


def auth(keys, tenant="tenant-a"):
    return {"Authorization": "Bearer " + keys[tenant]}


def test_workflow_registration_is_validated_and_stable(platform):
    _, _, engine, _, _, _, _, _ = platform
    handler = lambda context: {"done": True}
    engine.register("test.flow", handler)
    assert engine.registry.get("test.flow") is handler
    with pytest.raises(ValueError, match="already registered"):
        engine.register("test.flow", handler)
    with pytest.raises(ValueError, match="workflow name"):
        engine.register("Not Valid", handler)
    with pytest.raises(WorkflowNotFoundError):
        engine.registry.get("missing")


def test_execution_is_tenant_scoped_persisted_and_idempotent(platform):
    db, _, engine, _, conversation_service, _, _, _ = platform
    conversation = create_conversation(conversation_service)
    calls = []
    engine.register("test.once", lambda context: calls.append(context.principal.tenant_id) or {"ok": 1})
    first = engine.execute(principal(), conversation.id, workflow_name="test.once",
                           idempotency_key="operation-1", input_data={"x": 1})
    replay = engine.execute(principal(), conversation.id, workflow_name="test.once",
                            idempotency_key="operation-1", input_data={"x": 1})
    assert first.status == "succeeded" and first.attempts == 1 and not first.replayed
    assert replay.replayed and replay.result == {"ok": 1}
    assert calls == ["tenant-a"]
    with Session(db) as session:
        records = session.exec(select(WorkflowExecution)).all()
        assert len(records) == 1 and records[0].tenant_id == "tenant-a"
        assert records[0].status == "succeeded"
        assert [row.state for row in session.exec(select(WorkflowStateRecord).where(
            WorkflowStateRecord.tenant_id == "tenant-a")).all()][-2:] == ["workflow_running", "workflow_succeeded"]


def test_conflicting_idempotency_replay_and_cross_tenant_execution_denied(platform):
    _, _, engine, _, conversation_service, _, _, _ = platform
    conversation = create_conversation(conversation_service)
    engine.register("test.conflict", lambda _: {"ok": True})
    engine.execute(principal(), conversation.id, workflow_name="test.conflict", idempotency_key="same", input_data={"x": 1})
    with pytest.raises(WorkflowConflictError):
        engine.execute(principal(), conversation.id, workflow_name="test.conflict", idempotency_key="same", input_data={"x": 2})
    with pytest.raises(AuthorizationError):
        engine.execute(principal("tenant-b"), conversation.id, workflow_name="test.conflict", idempotency_key="another")


def test_workflow_role_and_principal_expiry_checked_before_uow(platform):
    _, _, _, _, _, _, _, _ = platform
    factory = Mock()
    engine = ReceptionistWorkflowEngine(uow_factory=factory, now_provider=lambda: NOW)
    engine.register("test.role", lambda _: {})
    for actor in [principal(roles=()), AuthenticatedPrincipal("actor", "tenant-a", ("workflow_executor",), "test", NOW - timedelta(days=1), NOW - timedelta(seconds=1))]:
        with pytest.raises(AuthorizationError):
            engine.execute(actor, uuid4(), workflow_name="test.role", idempotency_key="key")
    factory.assert_not_called()


def test_failed_execution_rolls_back_handler_and_persists_retryable_failure(platform):
    db, _, engine, _, service, _, _, _ = platform
    conversation = create_conversation(service)
    count = 0
    def flaky(context):
        nonlocal count
        count += 1
        context.uow.events.add(tenant_id=context.principal.tenant_id,
            business_id=context.conversation.business_id, conversation_id=context.conversation.id,
            event_type="test.side_effect", payload={}, source="test")
        if count == 1:
            raise RuntimeError("retry me")
        return {"ok": True}
    engine.register("test.flaky", flaky)
    with pytest.raises(RuntimeError, match="retry me"):
        engine.execute(principal(), conversation.id, workflow_name="test.flaky", idempotency_key="retry-key")
    with Session(db) as session:
        record = session.exec(select(WorkflowExecution)).one()
        assert (record.status, record.attempts, record.error_code) == ("failed", 1, "RuntimeError")
        assert not session.exec(select(EventLog).where(EventLog.event_type == "test.side_effect")).all()
        assert session.exec(select(WorkflowStateRecord).where(WorkflowStateRecord.state == "workflow_failed")).one()
    retried = engine.execute(principal(), conversation.id, workflow_name="test.flaky", idempotency_key="retry-key")
    assert retried.attempts == 2 and count == 2
    with Session(db) as session:
        assert session.exec(select(WorkflowExecution)).one().attempts == 2
        assert len(session.exec(select(EventLog).where(EventLog.event_type == "test.side_effect")).all()) == 1


def test_workflow_failure_audit_failure_rolls_back_all_side_effects(platform, monkeypatch):
    db, _, engine, _, service, _, _, _ = platform
    conversation = create_conversation(service)
    engine.register("test.audit_failure", lambda context: {"value": object()})
    with pytest.raises(TypeError, match="JSON"):
        engine.execute(principal(), conversation.id, workflow_name="test.audit_failure", idempotency_key="audit")
    with Session(db) as session:
        record = session.exec(select(WorkflowExecution)).one()
        assert record.status == "failed"
        assert session.exec(select(WorkflowStateRecord).where(WorkflowStateRecord.state == "workflow_failed")).one()


def test_assignment_and_unassignment_actions_are_audited_and_idempotent(platform):
    db, _, _, actions, service, _, _, staff_id = platform
    conversation = create_conversation(service)
    assigned = actions.execute(principal(), conversation.id, action="assign",
        idempotency_key="assign-1", input_data={"staff_id": str(staff_id)})
    replay = actions.execute(principal(), conversation.id, action="assign",
        idempotency_key="assign-1", input_data={"staff_id": str(staff_id)})
    assert replay.replayed and assigned.result["assigned_staff_id"] == str(staff_id)
    removed = actions.execute(principal(), conversation.id, action="unassign", idempotency_key="unassign-1")
    assert removed.result["assigned_staff_id"] is None
    with Session(db) as session:
        assert session.exec(select(ConversationAssignment)).one().staff_id is None
        assert len(session.exec(select(WorkflowExecution)).all()) == 2
        assert len(session.exec(select(EventLog).where(EventLog.event_type == "workflow.execution.succeeded")).all()) == 2


def test_escalate_close_reopen_and_follow_up_actions(platform):
    db, _, _, actions, service, _, _, _ = platform
    conversation = create_conversation(service)
    ticket = actions.execute(principal(), conversation.id, action="escalate", idempotency_key="escalate-1",
        input_data={"reason": "staff requested", "priority": "high"})
    assert ticket.result["priority"] == "high"
    closed = actions.execute(principal(), conversation.id, action="close", idempotency_key="close-1")
    reopened = actions.execute(principal(), conversation.id, action="reopen", idempotency_key="reopen-1")
    assert closed.result["status"] == "closed" and reopened.result["status"] == "open"
    due = NOW + timedelta(hours=2)
    follow_up = actions.execute(principal(), conversation.id, action="schedule_follow_up",
        idempotency_key="follow-up-1", input_data={"scheduled_at": due.isoformat(), "reason": "callback_requested"})
    replay = actions.execute(principal(), conversation.id, action="schedule_follow_up",
        idempotency_key="follow-up-1", input_data={"scheduled_at": due.isoformat(), "reason": "callback_requested"})
    assert replay.replayed and follow_up.result["workflow_state"] == "callback_scheduled"
    with Session(db) as session:
        assert len(session.exec(select(EscalationTicket)).all()) == 1
        task = session.exec(select(FollowUpTask)).one()
        assert (task.reason, task.workflow_state, task.status) == ("callback_requested", "callback_scheduled", "scheduled")


@pytest.mark.parametrize("reason,state", [
    ("callback_requested", "callback_scheduled"),
    ("missed_response", "missed_response_follow_up_scheduled"),
    ("nurture", "nurture_scheduled"),
])
def test_follow_up_reasons_and_timezone_required(platform, reason, state):
    db, _, _, actions, service, _, _, _ = platform
    conversation = create_conversation(service)
    result = actions.execute(principal(), conversation.id, action="schedule_follow_up",
        idempotency_key="follow-" + reason,
        input_data={"scheduled_at": (NOW + timedelta(days=1)).isoformat(), "reason": reason})
    assert result.result["workflow_state"] == state
    with pytest.raises(ValueError, match="timezone-aware"):
        actions.execute(principal(), conversation.id, action="schedule_follow_up",
            idempotency_key="bad-time", input_data={"scheduled_at": "2026-10-06T12:00:00", "reason": reason})
    with Session(db) as session:
        assert len(session.exec(select(FollowUpTask)).all()) == 1


def test_follow_up_future_window_and_due_query_are_tenant_scoped(platform):
    _, uow_factory, _, actions, service, _, _, _ = platform
    a = create_conversation(service)
    b = create_conversation(service, "tenant-b")
    actions.execute(principal(), a.id, action="schedule_follow_up", idempotency_key="a-task",
        input_data={"scheduled_at": (NOW + timedelta(hours=1)).isoformat(), "reason": "nurture"})
    actions.execute(principal("tenant-b"), b.id, action="schedule_follow_up", idempotency_key="b-task",
        input_data={"scheduled_at": (NOW + timedelta(hours=1)).isoformat(), "reason": "nurture"})
    with uow_factory() as uow:
        assert len(uow.follow_ups.list_due(tenant_id="tenant-a", business_id="business-a", now=NOW + timedelta(hours=2))) == 1
        assert uow.follow_ups.list_due(tenant_id="tenant-a", business_id="business-b", now=NOW + timedelta(hours=2)) == []


def test_business_hours_open_closed_next_open_timezone_and_tenant_scope(platform):
    _, uow_factory, _, _, _, _, _, _ = platform
    service = BusinessHoursService()
    monday_after_hours = datetime(2026, 10, 5, 22, 0, tzinfo=UTC)
    with uow_factory() as uow:
        assert service.within_business_hours(principal(), uow=uow, business_id="business-a", at=NOW)
        assert not service.within_business_hours(principal(), uow=uow, business_id="business-a", at=monday_after_hours)
        assert service.next_business_time(principal(), uow=uow, business_id="business-a", after=monday_after_hours) == datetime(2026, 10, 6, 13, 0, tzinfo=UTC)
        with pytest.raises(AuthorizationError):
            service.within_business_hours(principal("tenant-b"), uow=uow, business_id="business-a", at=NOW)
        with pytest.raises(ValueError, match="timezone-aware"):
            service.next_business_time(principal(), uow=uow, business_id="business-a", after=datetime(2026, 10, 5))


@pytest.mark.parametrize("age,assigned,failed,expected", [
    (timedelta(minutes=4), False, False, None),
    (timedelta(minutes=6), False, False, "conversation remained unassigned"),
    (timedelta(minutes=35), True, False, "conversation response timed out"),
    (timedelta(minutes=1), True, True, "outbound delivery failed"),
])
def test_escalation_rules_are_deterministic(age, assigned, failed, expected, platform):
    _, uow_factory, _, actions, service, _, _, staff_id = platform
    conversation = create_conversation(service)
    if assigned:
        actions.execute(principal(), conversation.id, action="assign", idempotency_key="assign-rule",
                         input_data={"staff_id": str(staff_id)})
    with uow_factory() as uow:
        row = uow.conversations.get(tenant_id="tenant-a", conversation_id=conversation.id)
        row.updated_at = NOW - age
        row.started_at = NOW - age
        uow.session.flush()
        assignment = uow.conversation_assignments.get(tenant_id="tenant-a", conversation_id=conversation.id)
        events = []
        if failed:
            events = [Mock(event_type="outbound.delivery.failed", id=uuid4())]
        decision = EscalationRules().decide(conversation=row, assignment=assignment, events=events, now=NOW)
    assert (decision.reason if decision else None) == expected


def test_escalation_sweep_creates_once_and_uses_business_tenant_scope(platform):
    db, uow_factory, engine, actions, service, _, _, _ = platform
    conversation = create_conversation(service)
    escalation = EscalationService(uow_factory=uow_factory, actions=actions,
        rules=EscalationRules(timeout_after=timedelta(minutes=1), unassigned_after=timedelta(minutes=1)),
        now_provider=lambda: NOW + timedelta(minutes=10))
    results = escalation.run_due(principal(), business_id="business-a")
    assert len(results) == 1 and results[0].result["priority"] == "normal"
    assert escalation.run_due(principal(), business_id="business-a") == []
    with pytest.raises(AuthorizationError):
        escalation.run_due(principal("tenant-b"), business_id="business-a")
    with Session(db) as session:
        tickets = session.exec(select(EscalationTicket)).all()
        assert len(tickets) == 1 and tickets[0].conversation_id == conversation.id


def test_escalation_sweep_reads_persisted_delivery_failure(platform):
    db, uow_factory, _, actions, service, _, _, _ = platform
    conversation = create_conversation(service)
    with uow_factory() as uow:
        uow.events.add(tenant_id="tenant-a", business_id="business-a", conversation_id=conversation.id,
            event_type="outbound.delivery.failed", payload={"provider_code": "timeout"}, source="delivery_adapter")
    escalation = EscalationService(uow_factory=uow_factory, actions=actions,
        now_provider=lambda: NOW + timedelta(seconds=1))
    escalation.run_due(principal(), business_id="business-a")
    with Session(db) as session:
        ticket = session.exec(select(EscalationTicket)).one()
        assert (ticket.reason, ticket.priority) == ("outbound delivery failed", "high")


def test_authenticated_workflow_route_requires_key_and_executes_idempotently(platform):
    _, _, _, _, service, client, keys, staff_id = platform
    conversation = create_conversation(service)
    url = f"/conversations/{conversation.id}/workflows/assign"
    payload = {"idempotency_key": "http-assign", "staff_id": str(staff_id)}
    assert client.post(url, json=payload).status_code == 401
    first = client.post(url, json=payload, headers=auth(keys))
    second = client.post(url, json=payload, headers=auth(keys))
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["replayed"] is False and second.json()["replayed"] is True
    assert client.post(f"/conversations/{conversation.id}/workflows/unknown", json=payload,
                       headers=auth(keys)).status_code == 404


def test_invalid_action_input_rolls_back_and_can_retry(platform, monkeypatch):
    db, _, _, actions, service, client, keys, _ = platform
    conversation = create_conversation(service)
    url = f"/conversations/{conversation.id}/workflows/escalate"
    request = {"idempotency_key": "retry-action", "priority": "urgent",
               "escalation_reason": "staff handoff requested"}
    with Session(db) as session:
        assert client.post(url, json={"idempotency_key": "bad", "priority": "urgent"},
                          headers=auth(keys)).status_code == 422
    original_event = ReceptionistWorkflowEngine._event
    failing = True
    def transient_audit_failure(uow, actor, row, event_type, **details):
        nonlocal failing
        if event_type == "human.escalation.alert" and failing:
            raise RuntimeError("temporary event storage issue")
        return original_event(uow, actor, row, event_type, **details)
    # The route shares this action service from the platform fixture.
    monkeypatch.setattr(ReceptionistWorkflowEngine, "_event", staticmethod(transient_audit_failure))
    assert client.post(url, json=request, headers=auth(keys)).status_code == 500
    with Session(db) as session:
        assert not session.exec(select(EscalationTicket)).all()
        assert session.exec(select(WorkflowExecution)).one().status == "failed"
    failing = False
    response = client.post(url, json=request, headers=auth(keys))
    assert response.status_code == 200
    assert response.json()["attempts"] == 2
    with Session(db) as session:
        assert session.exec(select(WorkflowExecution)).one().attempts == 2
