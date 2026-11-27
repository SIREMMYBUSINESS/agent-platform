from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.auth.api_keys import generate_api_key, hash_api_key
from app.auth.dependencies import get_auth_uow_factory
from app.main import app
from app.persistence.models import (
    ApiKey, ApiKeyGrant, Business, Contact, EventLog, Message, Tenant,
)
from app.persistence.unit_of_work import UnitOfWork


def _headers(keys, tenant_id="tenant-a"):
    return {"Authorization": "Bearer " + keys[tenant_id]}


@pytest.fixture
def contact_environment(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    keys = {tenant_id: generate_api_key() for tenant_id in ("tenant-a", "tenant-b")}
    with Session(engine) as session:
        session.add_all([Tenant(id=tenant_id, name=tenant_id) for tenant_id in keys])
        session.flush()
        session.add_all([
            Business(id="business-a", tenant_id="tenant-a", name="Business A", vertical="general"),
            Business(id="business-a2", tenant_id="tenant-a", name="Business A2", vertical="general"),
            Business(id="business-b", tenant_id="tenant-b", name="Business B", vertical="general"),
        ])
        session.flush()
        session.add_all([
            Contact(id="contact-a", tenant_id="tenant-a", business_id="business-a"),
            Contact(id="contact-other-business", tenant_id="tenant-a", business_id="business-a2"),
            Contact(id="contact-b", tenant_id="tenant-b", business_id="business-b"),
        ])
        for tenant_id, key in keys.items():
            session.add(ApiKey(id=tenant_id + "-key", tenant_id=tenant_id, key_hash=hash_api_key(key)))
        session.flush()
        for tenant_id in keys:
            session.add(ApiKeyGrant(
                principal_id=tenant_id + "-key",
                tenant_id=tenant_id,
                roles=["conversation_reader"],
            ))
        session.commit()

    factory = lambda: UnitOfWork(lambda: Session(engine))
    app.dependency_overrides[get_auth_uow_factory] = lambda: factory
    monkeypatch.setattr("app.routes.contact_conversations.session_factory", lambda: Session(engine))
    monkeypatch.setattr("app.routes.contact_conversations.settings.lead_business_id", "business-a")
    try:
        with TestClient(app) as client:
            yield engine, client, keys, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _create_conversation(factory, contact_id="contact-a", channel="sms"):
    with factory() as uow:
        row = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a",
            contact_id=contact_id, channel=channel,
        )
        return row.id


def test_timeline_merges_messages_and_events_across_contact_conversations(contact_environment):
    engine, client, keys, factory = contact_environment
    first = _create_conversation(factory, channel="sms")
    second = _create_conversation(factory, channel="call")
    timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all([
            Message(
                id=UUID(int=10), tenant_id="tenant-a", conversation_id=first,
                sender="customer", channel="sms", content="First message",
                direction="inbound", delivery_status="not_applicable", created_at=timestamp,
            ),
            EventLog(
                id=UUID(int=20), tenant_id="tenant-a", business_id="business-a",
                conversation_id=first, event_type="human.escalation.alert",
                payload={"reason": "Customer requested a person"}, source="test",
                created_at=timestamp + timedelta(seconds=1),
            ),
            Message(
                id=UUID(int=30), tenant_id="tenant-a", conversation_id=second,
                sender="agent", channel="call", content="Call summary",
                direction="outbound", delivery_status="sent", created_at=timestamp + timedelta(seconds=2),
            ),
        ])
        session.commit()

    response = client.get("/contacts/contact-a/timeline", headers=_headers(keys))
    assert response.status_code == 200
    items = response.json()["items"]
    assert [(row["kind"], row.get("content"), row.get("event_type")) for row in items] == [
        ("message", "First message", None),
        ("event", None, "human.escalation.alert"),
        ("message", "Call summary", None),
    ]
    assert len({row["conversation_id"] for row in items}) == 2


@pytest.mark.parametrize("contact_id", ["contact-b", "contact-other-business", "missing-contact"])
@pytest.mark.parametrize("endpoint", ["conversations", "timeline"])
def test_contact_reads_hide_foreign_or_unknown_contacts_as_404(contact_environment, contact_id, endpoint):
    _, client, keys, _ = contact_environment
    response = client.get(
        "/contacts/" + contact_id + "/" + endpoint,
        headers=_headers(keys, "tenant-a"),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Contact not found"


def test_timestamp_ties_page_stably_using_id_tiebreak(contact_environment):
    engine, client, keys, factory = contact_environment
    conversation = _create_conversation(factory)
    timestamp = datetime(2026, 2, 3, 4, 5, tzinfo=UTC)
    with Session(engine) as session:
        session.add_all([
            Message(
                id=UUID(int=value), tenant_id="tenant-a", conversation_id=conversation,
                sender="customer", channel="sms", content="Message " + str(value),
                direction="inbound", delivery_status="not_applicable", created_at=timestamp,
            )
            for value in range(1, 6)
        ])
        session.commit()

    first = client.get("/contacts/contact-a/timeline?limit=2", headers=_headers(keys))
    assert first.status_code == 200
    first_items = first.json()["items"]
    assert [int(UUID(item["id"])) for item in first_items] == [4, 5]
    older_cursor = first.json()["older_cursor"]
    assert older_cursor

    second = client.get(
        "/contacts/contact-a/timeline?limit=2&cursor=" + older_cursor,
        headers=_headers(keys),
    )
    assert second.status_code == 200
    second_items = second.json()["items"]
    assert [int(UUID(item["id"])) for item in second_items] == [2, 3]
    newer_cursor = second.json()["newer_cursor"]
    assert newer_cursor

    newer = client.get(
        "/contacts/contact-a/timeline?limit=2&cursor=" + newer_cursor,
        headers=_headers(keys),
    )
    assert newer.status_code == 200
    assert [int(UUID(item["id"])) for item in newer.json()["items"]] == [4, 5]


def test_conversation_read_cap_is_reported_and_enforced(contact_environment):
    _, client, keys, factory = contact_environment
    for _ in range(101):
        _create_conversation(factory)

    conversations = client.get(
        "/contacts/contact-a/conversations",
        headers=_headers(keys),
    )
    assert conversations.status_code == 200
    payload = conversations.json()
    assert len(payload["items"]) == 100
    assert payload["conversations_included"] == 100
    assert payload["conversation_limit"] == 100
    assert payload["conversations_truncated"] is True

    timeline = client.get("/contacts/contact-a/timeline", headers=_headers(keys))
    assert timeline.status_code == 200
    assert timeline.json()["conversations_included"] == 100
    assert timeline.json()["conversations_truncated"] is True


def test_timeline_requires_reader_role_and_valid_cursor(contact_environment):
    engine, client, keys, _ = contact_environment
    bad_cursor = client.get(
        "/contacts/contact-a/timeline?cursor=not-a-cursor",
        headers=_headers(keys),
    )
    assert bad_cursor.status_code == 422
    with Session(engine) as session:
        grant = session.get(ApiKeyGrant, ("tenant-a-key", "tenant-a"))
        grant.roles = []
        session.commit()
    denied = client.get("/contacts/contact-a/timeline", headers=_headers(keys))
    assert denied.status_code == 403
