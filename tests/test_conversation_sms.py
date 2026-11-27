from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.auth.dependencies import get_authenticated_principal
from app.queue.replay import EventReplayService
from app.persistence.unit_of_work import UnitOfWork
from app.main import app
from app.persistence.models import (
    Business, ConsentRecord, Contact, Conversation, ConversationAssignment,
    EventLog, Message, QueueTaskRecord, RoutingRule, Staff, SuppressionRule,
    Tenant, WorkspaceStaffMapping,
)
from app.persistence.repositories import SQLContactRepository
from app.security import AuthenticatedPrincipal


NOW = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)


def _workspace_principal(user_id, *, roles=("conversation_writer",), auth_source="email_magic_link"):
    return AuthenticatedPrincipal(
        principal_id=f"workspace-user:{user_id}",
        tenant_id="tenant-a",
        roles=tuple(roles),
        auth_source=auth_source,
        issued_at=NOW - timedelta(minutes=1),
    )


@pytest.fixture
def sms_environment(monkeypatch):
    from app.routes import conversation_sms as sms_route

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

    workspace_user_id = uuid4()
    staff_id = uuid4()
    other_staff_id = uuid4()
    conversation_id = uuid4()
    foreign_conversation_id = uuid4()

    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="Tenant A", region="US"))
        session.add(Tenant(id="tenant-b", name="Tenant B", region="US"))
        session.flush()
        session.add_all([
            Business(
                id="business-a", tenant_id="tenant-a", name="Business A",
                vertical="general", region="US", country_code="US", timezone="UTC",
            ),
            Business(
                id="business-b", tenant_id="tenant-b", name="Business B",
                vertical="general", region="US", country_code="US", timezone="UTC",
            ),
        ])
        session.flush()
        session.add_all([
            Contact(
                id="contact-a", tenant_id="tenant-a", business_id="business-a",
                phone="+15551234567",
            ),
            Contact(
                id="contact-b", tenant_id="tenant-b", business_id="business-b",
                phone="+15557654321",
            ),
        ])
        session.flush()
        session.add_all([
            Staff(
                id=staff_id, tenant_id="tenant-a", business_id="business-a",
                display_name="Mapped staff",
            ),
            Staff(
                id=other_staff_id, tenant_id="tenant-a", business_id="business-a",
                display_name="Other staff",
            ),
        ])
        session.add_all([
            Conversation(
                id=conversation_id, tenant_id="tenant-a", business_id="business-a",
                contact_id="contact-a", channel="sms",
            ),
            Conversation(
                id=foreign_conversation_id, tenant_id="tenant-b", business_id="business-b",
                contact_id="contact-b", channel="sms",
            ),
        ])
        session.flush()
        session.add_all([
            WorkspaceStaffMapping(
                tenant_id="tenant-a", business_id="business-a",
                workspace_user_id=workspace_user_id, staff_id=staff_id,
            ),
            ConversationAssignment(
                tenant_id="tenant-a", business_id="business-a",
                conversation_id=conversation_id, staff_id=staff_id,
                assigned_by="test-owner",
            ),
            *[
                RoutingRule(
                    tenant_id="tenant-a", business_id="business-a", weekday=day,
                    opens_at=time(0, 0), closes_at=time(23, 59, 59),
                    after_hours_action="callback", active=True,
                )
                for day in range(7)
            ],
            ConsentRecord(
                tenant_id="tenant-a", business_id="business-a",
                contact_id="contact-a", channel="sms", status="granted",
                source="test",
            ),
        ])
        session.commit()

    monkeypatch.setattr(sms_route.settings, "environment", "staging")
    monkeypatch.setattr(sms_route.settings, "conversation_sms_composer_enabled", True)
    monkeypatch.setattr(sms_route, "session_factory", lambda: Session(engine))
    monkeypatch.setattr(sms_route, "utcnow", lambda: NOW)

    current_principal = {"value": _workspace_principal(workspace_user_id)}

    def set_principal(*, user_id=None, roles=("conversation_writer",), auth_source="email_magic_link"):
        selected_user = user_id or workspace_user_id
        current_principal["value"] = _workspace_principal(
            selected_user, roles=roles, auth_source=auth_source,
        )
        app.dependency_overrides[get_authenticated_principal] = lambda: current_principal["value"]

    set_principal()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield {
                "engine": engine,
                "client": client,
                "workspace_user_id": workspace_user_id,
                "staff_id": staff_id,
                "other_staff_id": other_staff_id,
                "conversation_id": conversation_id,
                "foreign_conversation_id": foreign_conversation_id,
                "set_principal": set_principal,
            }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _url(env, conversation_id=None, business_id="business-a"):
    conversation_id = conversation_id or env["conversation_id"]
    return f"/businesses/{business_id}/conversations/{conversation_id}/sms"


def _send(env, *, content="Hello", client_key="client-1", conversation_id=None):
    return env["client"].post(
        _url(env, conversation_id),
        json={"content": content, "client_key": client_key},
    )


def _events(engine):
    with Session(engine) as session:
        return session.scalars(
            select(EventLog).where(EventLog.source == "conversation_sms_composer")
        ).all()


def _messages(engine):
    with Session(engine) as session:
        return session.scalars(select(Message)).all()


def _queue_rows(engine):
    with Session(engine) as session:
        return session.scalars(select(QueueTaskRecord)).all()


def test_sms_composer_is_hidden_when_flag_is_off_and_does_not_queue(sms_environment, monkeypatch):
    from app.routes import conversation_sms as sms_route

    monkeypatch.setattr(sms_route.settings, "conversation_sms_composer_enabled", False)
    response = _send(sms_environment)
    assert response.status_code == 404
    assert _messages(sms_environment["engine"]) == []
    assert _queue_rows(sms_environment["engine"]) == []


def test_sms_composer_is_hidden_outside_staging_even_when_flag_is_on(sms_environment, monkeypatch):
    from app.routes import conversation_sms as sms_route

    monkeypatch.setattr(sms_route.settings, "environment", "production")
    response = _send(sms_environment)
    assert response.status_code == 404
    assert _messages(sms_environment["engine"]) == []


def test_api_key_principal_and_missing_writer_role_are_denied(sms_environment):
    env = sms_environment
    env["set_principal"](auth_source="api_key")
    assert _send(env).status_code == 403
    env["set_principal"](roles=())
    assert _send(env).status_code == 403
    assert _messages(env["engine"]) == []


def test_unmapped_workspace_user_is_denied(sms_environment):
    env = sms_environment
    env["set_principal"](user_id=uuid4())
    response = _send(env)
    assert response.status_code == 403
    assert response.json()["detail"] == "No staff mapping for this workspace user."
    assert _messages(env["engine"]) == []


def test_unassigned_conversation_is_conflict_and_audited(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        assignment = session.get(ConversationAssignment, env["conversation_id"])
        session.delete(assignment)
        session.commit()

    response = _send(env)
    assert response.status_code == 409
    assert response.json()["reason_code"] == "conversation_unassigned"
    audits = _events(env["engine"])
    assert len(audits) == 1
    assert audits[0].payload["reason_code"] == "conversation_unassigned"
    assert audits[0].payload["staff_id"] == str(env["staff_id"])


def test_conversation_assigned_to_another_staff_is_forbidden_and_audited(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        assignment = session.get(ConversationAssignment, env["conversation_id"])
        assignment.staff_id = env["other_staff_id"]
        session.commit()

    response = _send(env)
    assert response.status_code == 403
    assert response.json()["reason_code"] == "conversation_assigned_to_another_staff"
    assert _events(env["engine"])[0].payload["staff_id"] == str(env["staff_id"])


def test_missing_conversation_is_404_with_business_scoped_audit(sms_environment):
    env = sms_environment
    missing_id = uuid4()
    response = _send(env, conversation_id=missing_id)
    assert response.status_code == 404
    audits = _events(env["engine"])
    assert len(audits) == 1
    assert audits[0].conversation_id is None
    assert audits[0].payload["requested_conversation_id"] == str(missing_id)
    assert audits[0].payload["reason_code"] == "conversation_not_found"
    assert _messages(env["engine"]) == []


def test_foreign_tenant_conversation_is_hidden_as_404(sms_environment):
    env = sms_environment
    response = _send(env, conversation_id=env["foreign_conversation_id"])
    assert response.status_code == 404
    assert _events(env["engine"])[0].conversation_id is None
    assert _messages(env["engine"]) == []


def test_foreign_contact_result_is_hidden_as_404(sms_environment, monkeypatch):
    env = sms_environment
    original_get = SQLContactRepository.get

    def return_foreign_contact(repository, *, tenant_id, contact_id):
        if tenant_id == "tenant-a" and contact_id == "contact-a":
            return original_get(repository, tenant_id="tenant-b", contact_id="contact-b")
        return original_get(repository, tenant_id=tenant_id, contact_id=contact_id)

    monkeypatch.setattr(SQLContactRepository, "get", return_foreign_contact)
    response = _send(env)
    assert response.status_code == 404
    assert _events(env["engine"])[0].payload["reason_code"] == "contact_not_found"
    assert _messages(env["engine"]) == []


def test_no_active_routing_rules_is_conflict_even_with_valid_timezone(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        rules = session.scalars(
            select(RoutingRule).where(RoutingRule.business_id == "business-a")
        ).all()
        for rule in rules:
            rule.active = False
        session.commit()

    response = _send(env)
    assert response.status_code == 409
    assert response.json()["reason_code"] == "business_hours_not_configured"
    assert _events(env["engine"])[0].payload["reason_code"] == "business_hours_not_configured"
    assert _messages(env["engine"]) == []


def test_outside_hours_returns_next_business_time_and_audit(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        rules = session.scalars(
            select(RoutingRule).where(RoutingRule.business_id == "business-a")
        ).all()
        for rule in rules:
            rule.opens_at = time(12, 0)
            rule.closes_at = time(17, 0)
        session.commit()

    response = _send(env)
    assert response.status_code == 409
    payload = response.json()
    assert payload["reason_code"] == "outside_business_hours"
    assert payload["next_business_time"] == "2026-11-27T12:00:00+00:00"
    assert _events(env["engine"])[0].payload["reason_code"] == "outside_business_hours"
    assert _messages(env["engine"]) == []


def test_inside_hours_queues_sms_with_staff_attribution(sms_environment):
    env = sms_environment
    response = _send(env)
    assert response.status_code == 202
    payload = response.json()
    assert payload["channel"] == "sms"
    assert payload["delivery_status"] == "pending"
    assert payload["sent_by_staff_id"] == str(env["staff_id"])
    rows = _messages(env["engine"])
    assert len(rows) == 1
    assert rows[0].sent_by_staff_id == env["staff_id"]
    assert len(_queue_rows(env["engine"])) == 1


def test_suppressed_contact_keeps_blocked_message_and_returns_reason(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        session.add(SuppressionRule(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
            channel="sms", reason_code="do_not_contact", created_by="test",
        ))
        session.commit()

    response = _send(env)
    assert response.status_code == 409
    assert response.json()["reason_code"] == "suppressed"
    assert response.json()["message"]["delivery_status"] == "blocked"
    assert len(_messages(env["engine"])) == 1
    assert len(_queue_rows(env["engine"])) == 0


def test_missing_channel_consent_is_blocked_and_policy_event_is_recorded(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        consent = session.scalars(
            select(ConsentRecord).where(
                ConsentRecord.tenant_id == "tenant-a",
                ConsentRecord.contact_id == "contact-a",
                ConsentRecord.channel == "sms",
            )
        ).first()
        session.delete(consent)
        session.commit()

    response = _send(env)
    assert response.status_code == 409
    assert response.json()["reason_code"] == "consent_unknown"
    assert response.json()["message"]["delivery_status"] == "blocked"
    with Session(env["engine"]) as session:
        policy_event = session.scalars(
            select(EventLog).where(
                EventLog.event_type == "outbound.delivery.blocked",
            )
        ).first()
    assert policy_event is not None
    assert policy_event.payload["reason_code"] == "consent_unknown"


def test_same_client_key_is_idempotent_for_message_and_enqueue(sms_environment):
    env = sms_environment
    first = _send(env, content="Hello", client_key="retry-1")
    second = _send(env, content="Hello", client_key="retry-1")
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert len(_messages(env["engine"])) == 1
    assert len(_queue_rows(env["engine"])) == 1


def test_same_client_key_with_different_content_conflicts(sms_environment):
    env = sms_environment
    first = _send(env, content="Hello", client_key="reuse-1")
    second = _send(env, content="Different content", client_key="reuse-1")
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["reason_code"] == "idempotency_key_conflict"
    assert len(_messages(env["engine"])) == 1
    assert len(_queue_rows(env["engine"])) == 1

def test_business_scoped_sms_refusal_event_cannot_be_replayed_as_conversation_event(sms_environment):
    env = sms_environment
    with Session(env["engine"]) as session:
        audit = EventLog(
            tenant_id="tenant-a", business_id="business-a", conversation_id=None,
            event_type="conversation.sms.refused", payload={"reason_code": "conversation_not_found"},
            source="conversation_sms_composer",
        )
        session.add(audit)
        session.commit()
        event_id = audit.id

    service = EventReplayService(
        uow_factory=lambda: UnitOfWork(lambda: Session(env["engine"])),
        queue_tasks=None,
    )
    with pytest.raises(LookupError, match="conversation replay"):
        service.replay(
            _workspace_principal(env["workspace_user_id"], roles=("inbound_writer",)),
            business_id="business-a", event_id=event_id,
            domain="inbound", task_name="events.replay", replay_key="sms-audit-replay",
        )
