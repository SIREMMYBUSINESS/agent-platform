from __future__ import annotations

from inspect import signature
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.persistence.models import Business, Contact, Tenant
from app.persistence.repositories import (
    DuplicateEntityError,
    EntityNotFoundError,
    InvalidTenantRelationshipError,
    PersistenceError,
    SQLBusinessRepository,
    SQLConversationRepository,
)
from app.persistence.unit_of_work import UnitOfWork


class CountingSession(Session):
    commits = 0
    rollbacks = 0

    def commit(self) -> None:
        type(self).commits += 1
        super().commit()

    def rollback(self) -> None:
        type(self).rollbacks += 1
        super().rollback()


@pytest.fixture
def repository_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            # IDs are deliberately caller-provisioned for this fixture.
            Tenant(id="tenant-a", name="A"),
            Tenant(id="tenant-b", name="B"),
        ])
        session.flush()
        session.add_all([
            Business(id="business-a", tenant_id="tenant-a", name="A", vertical="general"),
            Business(id="business-b", tenant_id="tenant-b", name="B", vertical="general"),
        ])
        session.flush()
        session.add_all([
            Contact(id="contact-a", tenant_id="tenant-a", business_id="business-a"),
            Contact(id="contact-b", tenant_id="tenant-b", business_id="business-b"),
        ])
        session.commit()
    CountingSession.commits = CountingSession.rollbacks = 0
    yield engine
    engine.dispose()


def make_uow(engine: object) -> UnitOfWork:
    return UnitOfWork(lambda: CountingSession(engine))


def test_tenant_business_and_contact_lookups_are_tenant_scoped(repository_db):
    with make_uow(repository_db) as uow:
        assert uow.tenants.get(tenant_id="tenant-a").name == "A"
        assert uow.tenants.exists(tenant_id="tenant-a")
        assert not uow.tenants.exists(tenant_id="missing")
        assert uow.businesses.get(tenant_id="tenant-a", business_id="business-a")
        assert uow.businesses.get(tenant_id="tenant-a", business_id="business-b") is None
        assert uow.contacts.get(tenant_id="tenant-a", contact_id="contact-a")
        assert uow.contacts.get(tenant_id="tenant-a", contact_id="contact-b") is None


def test_conversation_creation_uses_independent_uuids_and_allows_many_per_contact(repository_db):
    with make_uow(repository_db) as uow:
        first = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat"
        )
        second = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="sms"
        )
        assert isinstance(first.id, UUID)
        assert first.id != second.id
        assert first.id != first.contact_id
        listed = uow.conversations.list_for_contact(tenant_id="tenant-a", contact_id="contact-a")
        assert {item.id for item in listed} == {first.id, second.id}
        assert len(listed) == 2
        assert uow.conversations.get(tenant_id="tenant-b", conversation_id=first.id) is None


def test_invalid_tenant_relationship_is_translated(repository_db):
    with pytest.raises(InvalidTenantRelationshipError):
        with make_uow(repository_db) as uow:
            uow.conversations.create(
                tenant_id="tenant-a", business_id="business-b", contact_id="contact-a", channel="chat"
            )


def test_messages_append_query_and_have_deterministic_order(repository_db):
    with make_uow(repository_db) as uow:
        conversation = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat"
        )
        first = uow.messages.add(
            tenant_id="tenant-a", conversation_id=conversation.id, sender="contact",
            channel="chat", content="hello", direction="inbound",
        )
        second = uow.messages.add(
            tenant_id="tenant-a", conversation_id=conversation.id, sender="agent",
            channel="chat", content="hi", direction="outbound",
        )
        rows = uow.messages.list_for_conversation(
            tenant_id="tenant-a", conversation_id=conversation.id
        )
        assert {row.id for row in rows} == {first.id, second.id}
        assert rows == sorted(rows, key=lambda row: (row.created_at, row.id.hex))
        assert uow.messages.list_for_conversation(
            tenant_id="tenant-b", conversation_id=conversation.id
        ) == []


def test_workflow_append_records_history_and_updates_current_state(repository_db):
    with make_uow(repository_db) as uow:
        conversation = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat"
        )
        assert uow.workflows.get_current(
            tenant_id="tenant-a", conversation_id=conversation.id
        ).workflow_state == "new"
        initial = uow.workflows.append(
            tenant_id="tenant-a", conversation_id=conversation.id, state="intent_classified",
            context={"intent": "appointment"},
        )
        final = uow.workflows.append(
            tenant_id="tenant-a", conversation_id=conversation.id, state="blocked",
            context={"reason": "no consent"},
        )
        current = uow.workflows.get_current(tenant_id="tenant-a", conversation_id=conversation.id)
        history = uow.workflows.history(tenant_id="tenant-a", conversation_id=conversation.id)
        assert current.workflow_state == "blocked"
        assert {entry.id for entry in history} == {initial.id, final.id}
        assert history == sorted(history, key=lambda row: (row.created_at, row.id.hex))


def test_events_append_and_query_are_tenant_scoped_and_ordered(repository_db):
    with make_uow(repository_db) as uow:
        conversation = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat"
        )
        event1 = uow.events.add(
            tenant_id="tenant-a", business_id="business-a", conversation_id=conversation.id,
            event_type="inbound.received", payload={"count": 1}, source="api",
        )
        event2 = uow.events.add(
            tenant_id="tenant-a", business_id="business-a", conversation_id=conversation.id,
            event_type="agent.responded", payload={"count": 2}, source="agent",
        )
        rows = uow.events.list_for_conversation(
            tenant_id="tenant-a", conversation_id=conversation.id
        )
        assert {row.id for row in rows} == {event1.id, event2.id}
        assert rows == sorted(rows, key=lambda row: (row.created_at, row.id.hex))
        assert uow.events.list_for_conversation(
            tenant_id="tenant-b", conversation_id=conversation.id
        ) == []


def test_repository_operations_never_commit_or_rollback(repository_db):
    with make_uow(repository_db) as uow:
        conversation = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat"
        )
        uow.messages.add(
            tenant_id="tenant-a", conversation_id=conversation.id, sender="contact",
            channel="chat", content="hello", direction="inbound",
        )
        uow.workflows.append(
            tenant_id="tenant-a", conversation_id=conversation.id, state="qualifying"
        )
        uow.events.add(
            tenant_id="tenant-a", business_id="business-a", conversation_id=conversation.id,
            event_type="test", payload={}, source="test",
        )
        assert CountingSession.commits == 0
        assert CountingSession.rollbacks == 0
    assert CountingSession.commits == 1
    assert CountingSession.rollbacks == 0


def test_tenant_id_is_required_by_tenant_owned_repository_operations():
    tenant_owned = [
        # Protocol methods express the public contracts.
        ("tenant", "get"), ("tenant", "exists"), ("business", "get"),
        ("contact", "get"), ("conversation", "get"), ("conversation", "create"),
        ("conversation", "list_for_contact"), ("message", "add"),
        ("message", "list_for_conversation"), ("workflow", "get_current"),
        ("workflow", "history"), ("workflow", "append"), ("event", "add"),
        ("event", "list_for_conversation"),
    ]
    from app.persistence.repositories import (
        BusinessRepository, ContactRepository, ConversationRepository, EventRepository,
        MessageRepository, TenantRepository, WorkflowRepository,
    )
    interfaces = {
        "tenant": TenantRepository, "business": BusinessRepository,
        "contact": ContactRepository, "conversation": ConversationRepository,
        "message": MessageRepository, "workflow": WorkflowRepository, "event": EventRepository,
    }
    for interface, method in tenant_owned:
        assert "tenant_id" in signature(getattr(interfaces[interface], method)).parameters


def test_blank_tenant_id_is_rejected_before_query(repository_db):
    with make_uow(repository_db) as uow:
        with pytest.raises(ValueError, match="tenant_id is required"):
            uow.businesses.get(tenant_id=" ", business_id="business-a")


def test_workflow_append_reports_missing_conversation(repository_db):
    with make_uow(repository_db) as uow:
        with pytest.raises(EntityNotFoundError, match="conversation not found"):
            uow.workflows.append(
                tenant_id="tenant-a", conversation_id=UUID(int=1), state="blocked"
            )


def test_sqlalchemy_errors_are_translated_to_persistence_errors():
    class FailingSession:
        def exec(self, statement):
            raise OperationalError("select", {}, RuntimeError("database unavailable"))

    with pytest.raises(PersistenceError, match="database operation failed") as caught:
        SQLBusinessRepository(FailingSession()).get(
            tenant_id="tenant-a", business_id="business-a"
        )
    assert not isinstance(caught.value, OperationalError)


def test_unique_constraint_errors_are_translated_to_duplicate_error():
    class DuplicateSession:
        def add(self, entity):
            pass

        def flush(self):
            raise IntegrityError("insert", {}, RuntimeError("UNIQUE constraint failed: id"))

    with pytest.raises(DuplicateEntityError, match="uniqueness constraint"):
        SQLConversationRepository(DuplicateSession()).create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat"
        )


def test_repository_contracts_do_not_expose_update_of_unspecified_contact_fields():
    from app.persistence.repositories import ContactRepository

    assert set(dir(ContactRepository)) >= {"get"}
    assert not hasattr(ContactRepository, "update")
