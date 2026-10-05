from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.auth.api_keys import generate_api_key, hash_api_key
from app.auth.dependencies import get_auth_uow_factory
from app.auth.principal_resolver import AuthorizationError
from app.main import app
from app.models import Channel, ConversationStatus
from app.persistence.models import (
    ApiKey, ApiKeyGrant, Business, Contact, Conversation, ConversationAssignment,
    EventLog, Staff, Tenant,
)
from app.persistence.repositories import InvalidTenantRelationshipError
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.conversation_service import ConversationConflictError, ConversationService


def principal(tenant="tenant-a", roles=("conversation_reader", "conversation_writer")):
    return AuthenticatedPrincipal("actor-a", tenant, roles, "test", datetime.now(UTC) - timedelta(seconds=1))


@pytest.fixture
def environment(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    staff_ids = {name: uuid4() for name in ("active", "inactive", "other_business", "other_tenant")}
    keys = {tenant: generate_api_key() for tenant in ("tenant-a", "tenant-b")}
    with Session(engine) as session:
        session.add_all([Tenant(id=t, name=t) for t in keys])
        session.flush()
        for business, tenant in [("business-a", "tenant-a"), ("business-a2", "tenant-a"), ("business-b", "tenant-b")]:
            session.add(Business(id=business, tenant_id=tenant, name=business, vertical="general"))
        session.flush()
        session.add_all([
            Contact(id="contact-a", tenant_id="tenant-a", business_id="business-a"),
            Contact(id="contact-b", tenant_id="tenant-b", business_id="business-b"),
        ])
        for name, business, tenant in [("active", "business-a", "tenant-a"), ("inactive", "business-a", "tenant-a"),
                                        ("other_business", "business-a2", "tenant-a"), ("other_tenant", "business-b", "tenant-b")]:
            session.add(Staff(id=staff_ids[name], tenant_id=tenant, business_id=business,
                              display_name=name, active=name != "inactive"))
        for tenant, key in keys.items():
            session.add(ApiKey(id=tenant + "-key", tenant_id=tenant, key_hash=hash_api_key(key)))
        session.flush()
        for tenant in keys:
            session.add(ApiKeyGrant(principal_id=tenant + "-key", tenant_id=tenant,
                                   roles=["conversation_reader", "conversation_writer"]))
        session.commit()
    factory = lambda: UnitOfWork(lambda: Session(engine))
    service = ConversationService(uow_factory=factory)
    app.dependency_overrides[get_auth_uow_factory] = lambda: factory
    monkeypatch.setattr("app.routes.conversations.session_factory", lambda: Session(engine))
    try:
        with TestClient(app) as client:
            yield engine, service, client, keys, staff_ids
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create(service, tenant="tenant-a"):
    return service.create(principal(tenant), contact_id="contact-a" if tenant == "tenant-a" else "contact-b", channel=Channel.CHAT)


def headers(keys, tenant="tenant-a"):
    return {"Authorization": "Bearer " + keys[tenant]}


def test_create_derives_business_and_tenant_from_trusted_scope(environment):
    engine, service, _, _, _ = environment
    result = create(service)
    assert (result.tenant_id, result.business_id, result.contact_id) == ("tenant-a", "business-a", "contact-a")
    assert (result.status, result.version, result.assigned_staff_id) == ("open", 0, None)
    with Session(engine) as session:
        events = session.exec(select(EventLog)).all()
        assert [(e.event_type, e.payload["actor_id"]) for e in events] == [("conversation.created", "actor-a")]
        assert len(session.exec(select(Tenant)).all()) == 2
        assert len(session.exec(select(Contact)).all()) == 2


@pytest.mark.parametrize("contact", ["contact-b", "missing"])
def test_service_rejects_unavailable_contacts_without_provisioning(environment, contact):
    engine, service, _, _, _ = environment
    with pytest.raises(AuthorizationError):
        service.create(principal(), contact_id=contact, channel=Channel.CHAT)
    with Session(engine) as session:
        assert not session.exec(select(Conversation)).all()
        assert not session.exec(select(EventLog)).all()


@pytest.mark.parametrize("identity", [
    "tenant-a", principal(roles=()),
    replace(principal(), issued_at=datetime.now(UTC) + timedelta(days=1)),
    replace(principal(), issued_at=datetime.now(UTC) - timedelta(days=2), expires_at=datetime.now(UTC) - timedelta(days=1)),
])
def test_service_authorizes_before_opening_unit_of_work(identity):
    factory = Mock()
    service = ConversationService(uow_factory=factory)
    with pytest.raises(AuthorizationError):
        service.create(identity, contact_id="contact-a", channel=Channel.CHAT)
    factory.assert_not_called()


def test_lifecycle_transitions_and_idempotent_status_update(environment):
    engine, service, _, _, _ = environment
    row = create(service)
    for version, status in enumerate(["pending", "open", "closed", "open"]):
        row = service.transition(principal(), row.id, status=ConversationStatus(status), expected_version=version)
        assert row.version == version + 1
        assert row.status == status
    same = service.transition(principal(), row.id, status=ConversationStatus.OPEN, expected_version=3)
    assert same.version == 4
    with Session(engine) as session:
        assert len(session.exec(select(EventLog)).all()) == 5


def test_closed_to_pending_and_stale_updates_conflict(environment):
    _, service, _, _, _ = environment
    row = create(service)
    service.transition(principal(), row.id, status=ConversationStatus.CLOSED, expected_version=0)
    with pytest.raises(ConversationConflictError):
        service.transition(principal(), row.id, status=ConversationStatus.PENDING, expected_version=1)
    with pytest.raises(ConversationConflictError):
        service.transition(principal(), row.id, status=ConversationStatus.OPEN, expected_version=0)
    assert service.get(principal(), row.id).status == "closed"


def test_assignment_retries_unassignment_and_audit(environment):
    engine, service, _, _, staff = environment
    row = create(service)
    assigned = service.assign(principal(), row.id, staff_id=staff["active"], expected_version=0)
    retry = service.assign(principal(), row.id, staff_id=staff["active"], expected_version=0)
    assert assigned.assigned_staff_id == staff["active"]
    assert retry.version == assigned.version == 1
    cleared = service.assign(principal(), row.id, staff_id=None, expected_version=1)
    assert cleared.assigned_staff_id is None
    assert cleared.version == 2
    with Session(engine) as session:
        assignments = session.exec(select(ConversationAssignment)).all()
        assert len(assignments) == 1
        assert assignments[0].assigned_by == "actor-a"
        assert len(session.exec(select(EventLog)).all()) == 3


@pytest.mark.parametrize("name", ["inactive", "other_business", "other_tenant", "unknown"])
def test_assignment_requires_active_staff_in_same_tenant_and_business(environment, name):
    engine, service, _, _, staff = environment
    row = create(service)
    with pytest.raises(AuthorizationError):
        service.assign(principal(), row.id, staff_id=staff.get(name, uuid4()), expected_version=0)
    assert service.get(principal(), row.id).version == 0
    with Session(engine) as session:
        assert not session.exec(select(ConversationAssignment)).all()


def test_closed_assignment_and_stale_assignment_rejected(environment):
    _, service, _, _, staff = environment
    row = create(service)
    service.transition(principal(), row.id, status=ConversationStatus.PENDING, expected_version=0)
    with pytest.raises(ConversationConflictError):
        service.assign(principal(), row.id, staff_id=staff["active"], expected_version=0)
    service.transition(principal(), row.id, status=ConversationStatus.CLOSED, expected_version=1)
    with pytest.raises(ConversationConflictError):
        service.assign(principal(), row.id, staff_id=staff["active"], expected_version=2)


def test_audit_failure_rolls_back_assignment_and_version(environment, monkeypatch):
    engine, service, _, _, staff = environment
    row = create(service)
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(service, "_event", fail)
    with pytest.raises(RuntimeError):
        service.assign(principal(), row.id, staff_id=staff["active"], expected_version=0)
    assert service.get(principal(), row.id).version == 0
    with Session(engine) as session:
        assert not session.exec(select(ConversationAssignment)).all()
        assert len(session.exec(select(EventLog)).all()) == 1


def test_repositories_hide_other_tenant_and_reject_stale_version(environment):
    engine, service, _, _, staff = environment
    row = create(service)
    service.assign(principal(), row.id, staff_id=staff["active"], expected_version=0)
    with UnitOfWork(lambda: Session(engine)) as uow:
        assert uow.conversations.get(tenant_id="tenant-b", conversation_id=row.id) is None
        assert uow.conversation_assignments.get(tenant_id="tenant-b", conversation_id=row.id) is None
        assert uow.conversations.change(tenant_id="tenant-b", conversation_id=row.id, expected_version=1, status="closed") is None
        assert uow.conversations.change(tenant_id="tenant-a", conversation_id=row.id, expected_version=0, status="closed") is None


def test_database_blocks_cross_business_assignment(environment):
    engine, service, _, _, staff = environment
    row = create(service)
    with pytest.raises(InvalidTenantRelationshipError):
        with UnitOfWork(lambda: Session(engine)) as uow:
            uow.conversation_assignments.set(tenant_id="tenant-a", business_id="business-a", conversation_id=row.id,
                                            staff_id=staff["other_business"], assigned_by="actor-a")


def test_repository_updates_never_commit_or_rollback(environment):
    engine, service, _, _, staff = environment
    row = create(service)
    with Session(engine) as session:
        session.commit = Mock(side_effect=AssertionError("repository must not commit"))
        session.rollback = Mock(side_effect=AssertionError("repository must not rollback"))
        from app.persistence.repositories import SQLConversationRepository
        from app.persistence.conversation_repositories import SQLConversationAssignmentRepository
        assert SQLConversationRepository(session).change(tenant_id="tenant-a", conversation_id=row.id, expected_version=0, status="pending")
        SQLConversationAssignmentRepository(session).set(tenant_id="tenant-a", business_id="business-a", conversation_id=row.id,
                                                       staff_id=staff["active"], assigned_by="actor-a")
        session.commit.assert_not_called()
        session.rollback.assert_not_called()


def test_conversation_routes_with_real_authentication(environment):
    _, _, client, keys, staff = environment
    response = client.post("/conversations", headers=headers(keys), json={"contact_id": "contact-a", "channel": "chat"})
    assert response.status_code == 201
    row = response.json()
    url = "/conversations/" + row["id"]
    assert client.get(url, headers=headers(keys)).status_code == 200
    assigned = client.put(url + "/assignment", headers=headers(keys), json={"staff_id": str(staff["active"]), "expected_version": 0})
    assert assigned.status_code == 200
    assert assigned.json()["version"] == 1
    closed = client.patch(url + "/status", headers=headers(keys), json={"status": "closed", "expected_version": 1})
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert client.patch(url + "/status", headers=headers(keys), json={"status": "open", "expected_version": 0}).status_code == 409


@pytest.mark.parametrize("method,suffix,body", [
    ("get", "", None), ("patch", "/status", {"status": "closed", "expected_version": 0}),
    ("put", "/assignment", {"staff_id": None, "expected_version": 0}),
])
def test_http_cross_tenant_is_403(environment, method, suffix, body):
    _, service, client, keys, _ = environment
    row = create(service)
    response = client.request(method, f"/conversations/{row.id}{suffix}", headers=headers(keys, "tenant-b"), json=body)
    assert response.status_code == 403
    assert service.get(principal(), row.id).version == 0


def test_tenant_scoped_pagination_and_untrusted_query(environment):
    _, service, client, keys, _ = environment
    first = create(service)
    second = create(service)
    create(service, "tenant-b")
    result = client.get("/conversations?tenant_id=tenant-b&limit=1&offset=1", headers=headers(keys))
    assert result.status_code == 200
    assert [row["id"] for row in result.json()["items"]] == [str(second.id)]
    assert [row.id for row in service.list(principal())] == [first.id, second.id]


@pytest.mark.parametrize("field", ["tenant_id", "business_id", "conversation_id"])
def test_create_rejects_caller_scope_fields(environment, field):
    _, _, client, keys, _ = environment
    assert client.post("/conversations", headers=headers(keys), json={
        "contact_id": "contact-a", "channel": "chat", field: "untrusted",
    }).status_code == 422


def test_unknown_conversation_is_uniform_403(environment):
    _, _, client, keys, _ = environment
    assert client.get(f"/conversations/{uuid4()}", headers=headers(keys)).status_code == 403


@pytest.mark.parametrize("method,path,body", [
    ("get", "/conversations", None), ("get", f"/conversations/{UUID(int=1)}", None),
    ("post", "/conversations", {"contact_id": "contact-a", "channel": "chat"}),
    ("patch", f"/conversations/{UUID(int=1)}/status", {"status": "closed", "expected_version": 0}),
    ("put", f"/conversations/{UUID(int=1)}/assignment", {"staff_id": None, "expected_version": 0}),
])
def test_new_routes_require_credentials(environment, method, path, body):
    _, _, client, _, _ = environment
    assert client.request(method, path, json=body).status_code == 401


def test_read_only_identity_cannot_create_conversation(environment):
    engine, _, client, keys, _ = environment
    with Session(engine) as session:
        session.get(ApiKeyGrant, ("tenant-a-key", "tenant-a")).roles = ["conversation_reader"]
        session.commit()
    assert client.post("/conversations", headers=headers(keys), json={"contact_id": "contact-a", "channel": "chat"}).status_code == 403
