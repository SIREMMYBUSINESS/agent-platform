"""Voice gateway number resolution: separate service credential, fail-closed scope lookup."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth.api_keys import generate_api_key, hash_api_key
from app.auth.dependencies import get_auth_uow_factory
from app.config import Settings
from app.main import app
from app.persistence.models import ApiKey, ApiKeyGrant, Business, Tenant, VoiceNumberRoute
from app.persistence.unit_of_work import UnitOfWork

GATEWAY_KEY = "vg_test_" + "k" * 40
NUMBER = "+15550001111"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def headers(key=GATEWAY_KEY):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def resolver_client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    tenant_key = generate_api_key()
    with Session(engine) as session:
        long_tenant = "t" * 70
        session.add_all([Tenant(id="tenant-a", name="A"), Tenant(id="tenant-b", name="B"),
                         Tenant(id=long_tenant, name="Long")])
        session.flush()
        session.add_all([
            Business(id="biz-a", tenant_id="tenant-a", name="A", vertical="general"),
            Business(id="biz-b", tenant_id="tenant-b", name="B", vertical="general"),
            # real businesses whose ids the gateway could not embed safely in a URL path
            Business(id="biz.dotted", tenant_id="tenant-a", name="Dotted", vertical="general"),
            Business(id="biz-long", tenant_id=long_tenant, name="Long", vertical="general"),
        ])
        session.add(ApiKey(id="key-a", tenant_id="tenant-a", key_hash=hash_api_key(tenant_key),
                           issued_at=datetime.now(UTC) - timedelta(minutes=1)))
        session.flush()
        session.add(ApiKeyGrant(principal_id="key-a", tenant_id="tenant-a",
                                roles=["inbound_writer", "campaign_writer"]))
        session.add_all([
            VoiceNumberRoute(e164=NUMBER, tenant_id="tenant-a", business_id="biz-a"),
            VoiceNumberRoute(e164="+15550002222", tenant_id="tenant-b", business_id="biz-b"),
            VoiceNumberRoute(e164="+15550003333", tenant_id="tenant-a", business_id="biz-a", status="disabled"),
            # business exists, but under another tenant
            VoiceNumberRoute(e164="+15550004444", tenant_id="tenant-a", business_id="biz-b"),
            # business does not exist
            VoiceNumberRoute(e164="+15550005555", tenant_id="tenant-a", business_id="ghost"),
            # both rows reference real businesses; only the id-safety check can reject them
            VoiceNumberRoute(e164="+15550006666", tenant_id="tenant-a", business_id="biz.dotted"),
            VoiceNumberRoute(e164="+15550007777", tenant_id=long_tenant, business_id="biz-long"),
        ])
        session.commit()
    factory = lambda: Session(engine)
    app.dependency_overrides[get_auth_uow_factory] = lambda: lambda: UnitOfWork(factory)
    monkeypatch.setattr("app.routes.voice.session_factory", factory)
    monkeypatch.setattr("app.config.settings.voice_gateway_key_sha256", sha256(GATEWAY_KEY))
    try:
        with TestClient(app) as client:
            yield client, engine, tenant_key
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def resolve(client, number=NUMBER, key=GATEWAY_KEY):
    return client.get("/voice/resolve-number", params={"to": number},
                      headers=headers(key) if key else {})


def test_resolves_dialed_number_to_exact_scope_only(resolver_client):
    client, _, _ = resolver_client
    response = resolve(client)
    assert response.status_code == 200
    assert response.json() == {"tenant_id": "tenant-a", "business_id": "biz-a"}
    assert response.headers["cache-control"] == "no-store"
    other = resolve(client, "+15550002222")
    assert other.json() == {"tenant_id": "tenant-b", "business_id": "biz-b"}


@pytest.mark.parametrize("key", [None, "", "wrong-key", "vg_test_" + "x" * 40])
def test_missing_or_wrong_gateway_key_is_401_and_reveals_nothing(resolver_client, key):
    client, _, _ = resolver_client
    response = resolve(client, key=key)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "tenant" not in response.text


def test_tenant_api_key_is_not_a_gateway_credential(resolver_client):
    client, _, tenant_key = resolver_client
    assert resolve(client, key=tenant_key).status_code == 401


def test_gateway_key_is_not_a_tenant_credential(resolver_client):
    client, _, _ = resolver_client
    response = client.post("/voice/calls", headers={**headers(), "Idempotency-Key": "k1"},
                           json={"business_id": "biz-a", "contact_id": "c1"})
    assert response.status_code == 401


def test_unconfigured_gateway_key_returns_503_not_open_access(resolver_client, monkeypatch):
    client, _, _ = resolver_client
    monkeypatch.setattr("app.config.settings.voice_gateway_key_sha256", None)
    assert resolve(client).status_code == 503
    assert resolve(client, key=None).status_code == 401


def test_auth_is_checked_before_input_validation(resolver_client):
    client, _, _ = resolver_client
    assert resolve(client, "not-a-number", key="wrong").status_code == 401


@pytest.mark.parametrize("number", [
    "15550001111", "+0123456789", "+1 555 000 1111", "+1555", "+1555000111122223", "+1555000111a",
    "+15550001111\n",
])
def test_non_e164_input_is_rejected(resolver_client, number):
    client, _, _ = resolver_client
    assert resolve(client, number).status_code == 422


def test_missing_to_parameter_is_rejected(resolver_client):
    client, _, _ = resolver_client
    assert client.get("/voice/resolve-number", headers=headers()).status_code == 422


@pytest.mark.parametrize("number,reason", [
    ("+15559999999", "unknown number"),
    ("+15550003333", "disabled route"),
    ("+15550004444", "business belongs to another tenant"),
    ("+15550005555", "business does not exist"),
    ("+15550006666", "business id with characters unsafe for a URL path"),
    ("+15550007777", "tenant id longer than the gateway allows"),
])
def test_unusable_mappings_fail_closed_with_a_uniform_404(resolver_client, number, reason):
    client, _, _ = resolver_client
    response = resolve(client, number)
    assert response.status_code == 404, reason
    assert response.json() == {"detail": "Voice number is not provisioned"}


@pytest.mark.parametrize("status,headers_out", [
    (421, {"X-Tenant-Home-Region": "eu-west-1"}), (403, None), (503, None),
])
def test_region_guard_failures_surface_and_never_return_scope(resolver_client, monkeypatch, status, headers_out):
    client, _, _ = resolver_client
    seen = []

    def guard(tenant_id):
        seen.append(tenant_id)
        raise HTTPException(status, "blocked", headers=headers_out)

    monkeypatch.setattr("app.routes.voice.require_tenant_home_region", guard)
    response = resolve(client)
    assert response.status_code == status
    assert seen == ["tenant-a"]
    assert "tenant-a" not in response.text and "biz-a" not in response.text
    if headers_out:
        assert response.headers["x-tenant-home-region"] == "eu-west-1"


def test_region_guard_runs_before_the_business_lookup(resolver_client, monkeypatch):
    client, _, _ = resolver_client

    def guard(tenant_id):
        raise HTTPException(403, "Tenant access denied")

    monkeypatch.setattr("app.routes.voice.require_tenant_home_region", guard)
    # Unknown business id would be a 404 if the lookup ran first.
    assert resolve(client, "+15550005555").status_code == 403


def test_openapi_documents_the_gateway_scheme_and_failures():
    spec = app.openapi()
    operation = spec["paths"]["/voice/resolve-number"]["get"]
    assert operation["security"] == [{"VoiceGatewayKey": []}]
    assert {"401", "403", "404", "421", "503"} <= set(operation["responses"])
    parameter = next(p for p in operation["parameters"] if p.get("name") == "to")
    assert parameter["required"] is True and parameter["schema"]["pattern"].startswith("^\\+")
    assert "VoiceGatewayKey" in spec["components"]["securitySchemes"]
    assert spec["paths"]["/voice/calls"]["post"]["security"] == [{"ApiKey": []}]


def test_route_repository_is_exact_match_and_status_filtered(resolver_client):
    _, engine, _ = resolver_client
    with UnitOfWork(lambda: Session(engine)) as uow:
        repo = uow.voice_number_routes
        assert repo.get_active(e164=NUMBER).tenant_id == "tenant-a"
        assert repo.get_active(e164="+1555000111") is None
        assert repo.get_active(e164="+15550003333") is None
        assert repo.get_active(e164="") is None
        repo.upsert(e164="+15550003333", tenant_id="tenant-a", business_id="biz-a")
        assert repo.get_active(e164="+15550003333").business_id == "biz-a"
        moved = repo.upsert(e164=NUMBER, tenant_id="tenant-b", business_id="biz-b", status="disabled")
        assert moved.tenant_id == "tenant-b" and repo.get_active(e164=NUMBER) is None


def test_a_number_cannot_map_to_two_businesses(resolver_client):
    _, engine, _ = resolver_client
    with Session(engine) as session:
        session.add(VoiceNumberRoute(e164=NUMBER, tenant_id="tenant-b", business_id="biz-b"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_route_status_is_constrained(resolver_client):
    _, engine, _ = resolver_client
    with Session(engine) as session:
        session.add(VoiceNumberRoute(e164="+15550008888", tenant_id="tenant-a",
                                     business_id="biz-a", status="paused"))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("value", ["short", "z" * 64, "a" * 63, "a" * 65, "vg_" + "a" * 61])
def test_settings_reject_malformed_gateway_key_digest(value):
    with pytest.raises(ValidationError):
        Settings(voice_gateway_key_sha256=value)


def test_settings_normalize_and_default_gateway_key_digest():
    assert Settings(voice_gateway_key_sha256="  " + "AB" * 32 + "  ").voice_gateway_key_sha256 == "ab" * 32
    assert Settings(voice_gateway_key_sha256="").voice_gateway_key_sha256 is None
    assert Settings().voice_gateway_key_sha256 is None
    assert "ab" * 32 not in repr(Settings(voice_gateway_key_sha256="ab" * 32))


def test_migration_creates_and_drops_the_directory_table(capsys):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0030_tenant_db_route:0031_voice_number_routes", sql=True)
    sql = capsys.readouterr().out.lower()
    assert "create table voice_number_routes" in sql
    assert "pk_voice_number_routes" in sql and "ck_voice_number_routes_status" in sql
    assert "ix_voice_number_routes_scope" in sql
    command.downgrade(config, "0031_voice_number_routes:0030_tenant_db_route", sql=True)
    down = capsys.readouterr().out.lower()
    assert down.index("drop index ix_voice_number_routes_scope") < down.index("drop table voice_number_routes")
