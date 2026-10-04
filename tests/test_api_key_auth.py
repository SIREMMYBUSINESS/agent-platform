from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from app.auth.api_keys import ApiKeyVerifier, generate_api_key, hash_api_key
from app.auth.dependencies import get_auth_uow_factory
from app.main import app
from app.persistence.models import (
    ApiKey, ApiKeyGrant, Tenant, Business, Contact, Conversation,
    Message, WorkflowStateRecord, EventLog,
)
from app.persistence.unit_of_work import UnitOfWork


@pytest.fixture
def secured_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    key = generate_api_key()
    with Session(engine) as session:
        session.add_all([Tenant(id="secure-tenant", name="Secure"), Tenant(id="other-tenant", name="Other")])
        session.flush()
        for tenant, business, contact in [("secure-tenant", "secure-business", "secure-contact"),
                                           ("other-tenant", "other-business", "other-contact")]:
            session.add(Business(id=business, tenant_id=tenant, name="Business", vertical="general"))
            session.flush()
            session.add(Contact(id=contact, tenant_id=tenant, business_id=business))
        session.add(ApiKey(id="key-1", tenant_id="secure-tenant", key_hash=hash_api_key(key),
                           issued_at=datetime.now(UTC) - timedelta(minutes=1)))
        session.flush()
        session.add(ApiKeyGrant(principal_id="key-1", tenant_id="secure-tenant",
                               roles=["inbound_writer", "lead_reader", "campaign_reader"]))
        session.commit()
    factory = lambda: Session(engine)
    app.dependency_overrides[get_auth_uow_factory] = lambda: lambda: UnitOfWork(factory)
    monkeypatch.setattr("app.main.session_factory", factory)
    monkeypatch.setattr("app.routes.leads.session_factory", factory)
    monkeypatch.setattr("app.routes.campaigns.session_factory", factory)
    # Deliberately different deployment tenant: only the principal may select scope.
    monkeypatch.setattr("app.config.settings.inbound_tenant_id", "other-tenant")
    monkeypatch.setattr("app.config.settings.lead_business_id", "secure-business")
    try:
        with TestClient(app) as client:
            yield client, engine, key
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def payload(**changes):
    return dict(tenant_id="secure-tenant", business_id="secure-business",
                contact_id="secure-contact", channel="chat", text="Hello") | changes


def headers(key):
    return {"Authorization": f"Bearer {key}"}


def assert_no_ingestion(engine):
    with Session(engine) as session:
        for model in (Conversation, Message, WorkflowStateRecord, EventLog):
            assert session.exec(select(model)).all() == []
        assert len(session.exec(select(Tenant)).all()) == 2
        assert len(session.exec(select(Business)).all()) == 2
        assert len(session.exec(select(Contact)).all()) == 2


@pytest.mark.parametrize("credential", [None, "malformed", "ap_" + "x" * 43])
def test_invalid_api_key_denied_without_ingestion(secured_client, credential):
    client, engine, _ = secured_client
    response = client.post("/inbound", json=payload(), headers=headers(credential) if credential else {})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert_no_ingestion(engine)


@pytest.mark.parametrize("change", ["revoked", "expired", "grant_disabled", "grant_expired", "no_role", "no_grant"])
def test_credential_and_grant_rejections_before_ingestion(secured_client, change):
    client, engine, key = secured_client
    with Session(engine) as session:
        credential = session.get(ApiKey, "key-1")
        grant = session.get(ApiKeyGrant, ("key-1", "secure-tenant"))
        if change == "revoked":
            credential.revoked = True
        elif change == "expired":
            credential.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "grant_disabled":
            grant.enabled = False
        elif change == "grant_expired":
            grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "no_role":
            grant.roles = ["lead_reader"]
        else:
            session.delete(grant)
        session.commit()
    response = client.post("/inbound", json=payload(), headers=headers(key))
    assert response.status_code == (401 if change in {"revoked", "expired"} else 403)
    assert_no_ingestion(engine)


def test_authenticated_tenant_overrides_deployment_scope(secured_client):
    client, engine, key = secured_client
    response = client.post("/inbound", json=payload(), headers=headers(key))
    assert response.status_code == 200
    with Session(engine) as session:
        conversations = session.exec(select(Conversation)).all()
        assert len(conversations) == 1
        assert conversations[0].tenant_id == "secure-tenant"


@pytest.mark.parametrize("changes,status", [
    ({"tenant_id": "other-tenant"}, 403),
    ({"business_id": "other-business", "contact_id": "other-contact"}, 404),
    ({"contact_id": "other-contact"}, 404),
    ({"contact_id": "unknown-contact"}, 404),
    ({"business_id": "unknown-business"}, 404),
])
def test_caller_ids_cannot_authorize_other_tenant_or_provision_records(secured_client, changes, status):
    client, engine, key = secured_client
    response = client.post("/inbound", json=payload(**changes), headers=headers(key))
    assert response.status_code == status
    assert_no_ingestion(engine)


@pytest.mark.parametrize("method,path", [
    ("get", "/leads"), ("get", "/leads/" + str(uuid4())), ("post", "/leads/import"),
    ("post", "/campaigns"), ("get", "/campaigns/" + str(uuid4())),
    ("post", "/campaigns/" + str(uuid4()) + "/enroll"),
    ("post", "/campaigns/" + str(uuid4()) + "/trigger"),
    ("post", "/campaigns/" + str(uuid4()) + "/replies"),
    ("get", "/campaigns/" + str(uuid4()) + "/analytics"),
])
def test_all_business_routes_require_credentials(secured_client, method, path):
    client, engine, _ = secured_client
    assert client.request(method, path).status_code == 401
    assert_no_ingestion(engine)


def test_read_role_cannot_create_campaign(secured_client):
    client, engine, key = secured_client
    assert client.post("/campaigns", headers=headers(key), json={}).status_code == 403
    assert_no_ingestion(engine)


def test_health_is_public(secured_client):
    client, _, _ = secured_client
    assert client.get("/health").json() == {"status": "ok"}


def test_revocation_is_rechecked_on_next_request(secured_client):
    client, engine, key = secured_client
    assert client.get("/leads", headers=headers(key)).status_code == 200
    with Session(engine) as session:
        session.get(ApiKey, "key-1").revoked = True
        session.commit()
    assert client.get("/leads", headers=headers(key)).status_code == 401


def test_grant_cannot_reference_key_in_another_tenant(secured_client):
    _, engine, _ = secured_client
    with Session(engine) as session:
        session.add(ApiKeyGrant(principal_id="key-1", tenant_id="other-tenant", roles=["inbound_writer"]))
        with pytest.raises(IntegrityError):
            session.commit()


def test_verifier_passes_only_hash_to_repository_and_checks_it():
    key = generate_api_key()
    repository = Mock()
    repository.find_by_hash.return_value = ("0" * 64, Mock())
    assert ApiKeyVerifier(repository).lookup_verified(key) is None
    repository.find_by_hash.assert_called_once_with(hash_api_key(key))


def test_key_secret_is_not_stored(secured_client):
    _, engine, key = secured_client
    with Session(engine) as session:
        row = session.get(ApiKey, "key-1")
        assert row.key_hash == hash_api_key(key)
        assert key not in str(row.model_dump())


def test_openapi_declares_bearer_auth_for_protected_routes():
    schema = app.openapi()
    for path, operations in schema["paths"].items():
        if path.startswith(("/inbound", "/leads", "/campaigns")):
            for operation in operations.values():
                assert {"ApiKey": []} in operation["security"]
    assert "security" not in schema["paths"]["/health"]["get"]
