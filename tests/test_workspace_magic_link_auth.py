from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.auth.dependencies import get_auth_uow_factory
from app.persistence.models import Tenant, TenantMembership, WorkspaceUser
from app.persistence.unit_of_work import UnitOfWork
from app.routes.auth import get_magic_link_mailer
from app.main import app


class RecordingMagicLinkMailer:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str]] = []

    def send_magic_link(self, *, recipient: str, link: str) -> None:
        self.deliveries.append((recipient, link))


@pytest.fixture
def passwordless_client(monkeypatch):
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
    now = datetime.now(UTC)
    user_id = uuid4()
    membership_id = uuid4()
    tenant_id = "workspace-auth-tenant"
    with Session(engine) as session:
        session.add(Tenant(id=tenant_id, name="Workspace Auth Test"))
        session.flush()
        session.add(WorkspaceUser(
            id=user_id, email="owner@example.test", display_name="Workspace Owner",
            status="invited",
        ))
        session.flush()
        session.add(TenantMembership(
            id=membership_id, user_id=user_id, tenant_id=tenant_id,
            workspace_label="Workspace Auth Test", role="owner", status="invited",
            created_at=now, updated_at=now,
        ))
        session.commit()

    session_factory = lambda: Session(engine, expire_on_commit=False)
    mailer = RecordingMagicLinkMailer()
    app.dependency_overrides[get_auth_uow_factory] = lambda: (
        lambda: UnitOfWork(session_factory)
    )
    app.dependency_overrides[get_magic_link_mailer] = lambda: mailer
    monkeypatch.setattr("app.routes.auth.settings.auth_public_origin", "https://workspace.example.test")
    monkeypatch.setattr("app.routes.auth.settings.auth_magic_link_ttl_seconds", 600)
    monkeypatch.setattr("app.routes.auth.settings.auth_login_ticket_ttl_seconds", 300)
    monkeypatch.setattr("app.routes.auth.settings.auth_session_ttl_seconds", 43200)

    try:
        with TestClient(app) as client:
            yield client, engine, mailer, tenant_id, user_id, membership_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def request_link(client: TestClient, mailer: RecordingMagicLinkMailer) -> str:
    response = client.post("/auth/magic-link", json={"email": "OWNER@example.test"})
    assert response.status_code == 202
    assert "If an eligible workspace account exists" in response.json()["message"]
    assert len(mailer.deliveries) == 1
    recipient, link = mailer.deliveries[0]
    assert recipient == "owner@example.test"
    parsed = urlsplit(link)
    assert parsed.scheme == "https"
    assert parsed.netloc == "workspace.example.test"
    return parse_qs(parsed.query)["token"][0]


def complete_login(client: TestClient, token: str, membership_id: str):
    verified = client.post("/auth/magic-link/verify", json={"token": token})
    assert verified.status_code == 200
    payload = verified.json()
    assert payload["display_name"] == "Workspace Owner"
    assert len(payload["memberships"]) == 1
    assert payload["memberships"][0]["membership_id"] == membership_id

    session = client.post("/auth/session", json={
        "login_ticket": payload["login_ticket"],
        "membership_id": membership_id,
    })
    assert session.status_code == 200
    assert session.json()["session_token"].startswith("ws_")
    return session.json()["session_token"]


def test_passwordless_login_verifies_email_selects_workspace_and_revokes_session(passwordless_client):
    client, engine, mailer, tenant_id, user_id, membership_id = passwordless_client
    token = request_link(client, mailer)

    session_token = complete_login(client, token, str(membership_id))
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert me.status_code == 200
    assert me.json()["tenant_id"] == tenant_id
    assert me.json()["auth_source"] == "email_magic_link"
    assert "conversation_reader" in me.json()["roles"]
    assert "business_onboarder" in me.json()["roles"]
    assert "campaign_writer" in me.json()["roles"]

    with Session(engine) as session:
        user = session.get(WorkspaceUser, user_id)
        membership = session.get(TenantMembership, membership_id)
        assert user.status == "active"
        assert user.email_verified_at is not None
        assert membership.status == "active"

    second_use = client.post("/auth/magic-link/verify", json={"token": token})
    assert second_use.status_code == 401

    logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {session_token}"})
    assert logout.status_code == 204
    denied = client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert denied.status_code == 401


def test_magic_link_request_does_not_disclose_unknown_accounts(passwordless_client):
    client, _engine, mailer, *_ = passwordless_client
    response = client.post("/auth/magic-link", json={"email": "not-invited@example.test"})
    assert response.status_code == 202
    assert response.json()["message"] == (
        "If an eligible workspace account exists for that email, a sign-in link will be sent."
    )
    assert mailer.deliveries == []


def test_workspace_session_is_invalidated_when_membership_is_suspended(passwordless_client):
    client, engine, mailer, _tenant_id, _user_id, membership_id = passwordless_client
    token = request_link(client, mailer)
    session_token = complete_login(client, token, str(membership_id))

    with Session(engine) as session:
        membership = session.get(TenantMembership, membership_id)
        membership.status = "suspended"
        session.commit()

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert response.status_code == 401


def test_workspace_login_ticket_cannot_select_another_users_membership(passwordless_client):
    client, engine, mailer, _tenant_id, _user_id, _membership_id = passwordless_client
    token = request_link(client, mailer)
    verified = client.post("/auth/magic-link/verify", json={"token": token})
    assert verified.status_code == 200
    ticket = verified.json()["login_ticket"]

    other_tenant_id = "other-workspace-tenant"
    other_user_id = uuid4()
    other_membership_id = uuid4()
    with Session(engine) as session:
        session.add(Tenant(id=other_tenant_id, name="Other Workspace"))
        session.flush()
        session.add(WorkspaceUser(
            id=other_user_id, email="other@example.test",
            display_name="Other User", status="active",
            email_verified_at=datetime.now(UTC),
        ))
        session.flush()
        session.add(TenantMembership(
            id=other_membership_id, user_id=other_user_id,
            tenant_id=other_tenant_id, workspace_label="Other Workspace",
            role="owner", status="active",
        ))
        session.commit()

    response = client.post("/auth/session", json={
        "login_ticket": ticket,
        "membership_id": str(other_membership_id),
    })
    assert response.status_code == 403


def test_workspace_role_maps_to_existing_permission_contract(passwordless_client):
    client, engine, mailer, _tenant_id, _user_id, membership_id = passwordless_client
    token = request_link(client, mailer)
    session_token = complete_login(client, token, str(membership_id))

    with Session(engine) as session:
        membership = session.get(TenantMembership, membership_id)
        membership.role = "viewer"
        session.commit()

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert response.status_code == 200
    roles = response.json()["roles"]
    assert "lead_reader" in roles
    assert "conversation_reader" in roles
    assert "business_onboarder" not in roles
    assert "campaign_writer" not in roles


def test_staging_owner_bypass_requires_secret_and_creates_scoped_session(passwordless_client, monkeypatch):
    client, _engine, mailer, tenant_id, _user_id, membership_id = passwordless_client
    monkeypatch.setattr("app.routes.auth.settings.environment", "staging")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_enabled", True)
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_token", "test-only-high-entropy-secret")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_email", "owner@example.test")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_tenant_id", tenant_id)

    denied = client.post("/auth/staging-owner-session", json={
        "email": "owner@example.test", "tenant_id": tenant_id,
    })
    assert denied.status_code == 401

    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": "test-only-high-entropy-secret"},
        json={"email": "OWNER@example.test", "tenant_id": tenant_id},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["tenant_id"] == tenant_id
    assert response.json()["session_token"].startswith("ws_")
    assert mailer.deliveries == []

    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {response.json()['session_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["tenant_id"] == tenant_id
    assert "business_onboarder" in me.json()["roles"]
    assert "campaign_writer" in me.json()["roles"]

    wrong_tenant = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": "test-only-high-entropy-secret"},
        json={"email": "owner@example.test", "tenant_id": "another-tenant"},
    )
    assert wrong_tenant.status_code == 403


def test_staging_owner_bypass_is_unavailable_in_production(passwordless_client, monkeypatch):
    client, *_ = passwordless_client
    monkeypatch.setattr("app.routes.auth.settings.environment", "production")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_enabled", True)
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_token", "test-only-high-entropy-secret")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_email", "owner@example.test")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_tenant_id", "workspace-auth-tenant")
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": "test-only-high-entropy-secret"},
        json={"email": "owner@example.test", "tenant_id": "workspace-auth-tenant"},
    )
    assert response.status_code == 404



def configure_staging_owner_bypass(monkeypatch, tenant_id: str, email: str = "owner@example.test") -> str:
    secret = "test-only-high-entropy-secret"
    monkeypatch.setattr("app.routes.auth.settings.environment", "staging")
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_enabled", True)
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_token", secret)
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_email", email)
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_tenant_id", tenant_id)
    return secret


def assert_staging_owner_denial_is_generic(response, caplog, category: str, secret: str, *private_values: str):
    assert response.status_code == 403
    assert response.json() == {"detail": "Staging workspace access is unavailable"}
    for value in (*private_values, secret):
        if value:
            assert value not in response.text
    assert "denial_category" not in response.json()
    assert any(
        record.getMessage() == "staging owner session denied"
        and getattr(record, "denial_category", None) == category
        for record in caplog.records
    )


def test_staging_owner_bypass_logs_configured_email_mismatch_without_disclosing_identity(
    passwordless_client, monkeypatch, caplog,
):
    client, _engine, _mailer, tenant_id, *_ = passwordless_client
    secret = configure_staging_owner_bypass(monkeypatch, tenant_id)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "different@example.test", "tenant_id": tenant_id},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "configured_email_mismatch", secret,
        "different@example.test", "owner@example.test", tenant_id,
    )


def test_staging_owner_bypass_logs_configured_tenant_mismatch_without_disclosing_identity(
    passwordless_client, monkeypatch, caplog,
):
    client, _engine, _mailer, tenant_id, *_ = passwordless_client
    secret = configure_staging_owner_bypass(monkeypatch, tenant_id)
    requested_tenant = "unconfigured-tenant"
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "owner@example.test", "tenant_id": requested_tenant},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "configured_tenant_mismatch", secret,
        "owner@example.test", tenant_id, requested_tenant,
    )


def test_staging_owner_bypass_logs_missing_user_without_disclosing_email(
    passwordless_client, monkeypatch, caplog,
):
    client, _engine, _mailer, tenant_id, *_ = passwordless_client
    missing_email = "missing-owner@example.test"
    secret = configure_staging_owner_bypass(monkeypatch, tenant_id, email=missing_email)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": missing_email, "tenant_id": tenant_id},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "user_not_found", secret, missing_email, tenant_id,
    )


def test_staging_owner_bypass_logs_ineligible_user_status(passwordless_client, monkeypatch, caplog):
    client, engine, _mailer, tenant_id, user_id, *_ = passwordless_client
    with Session(engine) as session:
        session.get(WorkspaceUser, user_id).status = "suspended"
        session.commit()

    secret = configure_staging_owner_bypass(monkeypatch, tenant_id)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "owner@example.test", "tenant_id": tenant_id},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "user_ineligible_status", secret, "owner@example.test", tenant_id,
    )


def test_staging_owner_bypass_logs_missing_tenant_membership(passwordless_client, monkeypatch, caplog):
    client, _engine, _mailer, _tenant_id, *_ = passwordless_client
    configured_tenant = "configured-but-unassigned-tenant"
    secret = configure_staging_owner_bypass(monkeypatch, configured_tenant)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "owner@example.test", "tenant_id": configured_tenant},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "tenant_membership_not_found", secret,
        "owner@example.test", configured_tenant,
    )


def test_staging_owner_bypass_logs_missing_owner_role(passwordless_client, monkeypatch, caplog):
    client, engine, _mailer, tenant_id, _user_id, membership_id = passwordless_client
    with Session(engine) as session:
        session.get(TenantMembership, membership_id).role = "viewer"
        session.commit()

    secret = configure_staging_owner_bypass(monkeypatch, tenant_id)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "owner@example.test", "tenant_id": tenant_id},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "owner_role_not_found", secret, "owner@example.test", tenant_id,
    )


def test_staging_owner_bypass_logs_ineligible_owner_membership(passwordless_client, monkeypatch, caplog):
    client, engine, _mailer, tenant_id, _user_id, membership_id = passwordless_client
    with Session(engine) as session:
        session.get(TenantMembership, membership_id).status = "suspended"
        session.commit()

    secret = configure_staging_owner_bypass(monkeypatch, tenant_id)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "owner@example.test", "tenant_id": tenant_id},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "owner_membership_ineligible_status", secret,
        "owner@example.test", tenant_id,
    )


def test_staging_owner_bypass_logs_missing_identity_configuration(passwordless_client, monkeypatch, caplog):
    client, _engine, _mailer, tenant_id, *_ = passwordless_client
    secret = configure_staging_owner_bypass(monkeypatch, tenant_id)
    monkeypatch.setattr("app.routes.auth.settings.staging_auth_bypass_email", None)
    response = client.post(
        "/auth/staging-owner-session",
        headers={"X-Staging-Auth-Bypass": secret},
        json={"email": "owner@example.test", "tenant_id": tenant_id},
    )

    assert_staging_owner_denial_is_generic(
        response, caplog, "identity_configuration_missing", secret,
        "owner@example.test", tenant_id,
    )
