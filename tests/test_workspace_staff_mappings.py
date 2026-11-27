from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth.dependencies import get_auth_uow_factory, get_authenticated_principal
from app.main import app
from app.persistence.models import (
    Business, Staff, Tenant, TenantMembership, WorkspaceStaffMappingEvent,
    WorkspaceUser,
)
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.workspace_staff_mapping_service import resolve_staff_id


@pytest.fixture
def mapping_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    event.listen(
        engine, "connect",
        lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
    )
    SQLModel.metadata.create_all(engine)
    now = datetime.now(UTC)
    owner_id, agent_id, other_id = uuid4(), uuid4(), uuid4()
    membership_ids = uuid4(), uuid4(), uuid4()
    staff_a, staff_a2, staff_b = uuid4(), uuid4(), uuid4()
    with Session(engine) as session:
        session.add_all([
            Tenant(id="mapping-tenant", name="Mapping Tenant"),
            Tenant(id="other-tenant", name="Other Tenant"),
        ])
        session.flush()
        session.add_all([
            Business(id="business-a", tenant_id="mapping-tenant", name="A", vertical="general"),
            Business(id="business-a2", tenant_id="mapping-tenant", name="A2", vertical="general"),
            Business(id="business-other", tenant_id="other-tenant", name="Other", vertical="general"),
        ])
        session.flush()
        session.add_all([
            WorkspaceUser(
                id=owner_id, email="owner@mapping.test", display_name="Owner",
                status="active", email_verified_at=now,
            ),
            WorkspaceUser(
                id=agent_id, email="agent@mapping.test", display_name="Agent",
                status="active", email_verified_at=now,
            ),
            WorkspaceUser(
                id=other_id, email="other@mapping.test", display_name="Other",
                status="active", email_verified_at=now,
            ),
        ])
        session.flush()
        session.add_all([
            TenantMembership(
                id=membership_ids[0], user_id=owner_id, tenant_id="mapping-tenant",
                role="owner", status="active",
            ),
            TenantMembership(
                id=membership_ids[1], user_id=agent_id, tenant_id="mapping-tenant",
                role="agent", status="active",
            ),
            TenantMembership(
                id=membership_ids[2], user_id=other_id, tenant_id="other-tenant",
                role="owner", status="active",
            ),
        ])
        session.flush()
        session.add_all([
            Staff(id=staff_a, tenant_id="mapping-tenant", business_id="business-a",
                  display_name="Staff A", active=True),
            Staff(id=staff_a2, tenant_id="mapping-tenant", business_id="business-a",
                  display_name="Staff A2", active=True),
            Staff(id=staff_b, tenant_id="mapping-tenant", business_id="business-a2",
                  display_name="Staff B", active=True),
        ])
        session.commit()

    factory = lambda: Session(engine, expire_on_commit=False)
    current = {"principal": AuthenticatedPrincipal(
        principal_id=f"workspace-user:{owner_id}",
        tenant_id="mapping-tenant", roles=("conversation_writer",),
        auth_source="email_magic_link", issued_at=now,
    )}

    def set_principal(user_id=owner_id, *, tenant_id="mapping-tenant", auth_source="email_magic_link"):
        current["principal"] = AuthenticatedPrincipal(
            principal_id=f"workspace-user:{user_id}" if auth_source == "email_magic_link" else str(user_id),
            tenant_id=tenant_id, roles=("conversation_writer",),
            auth_source=auth_source, issued_at=datetime.now(UTC),
        )

    app.dependency_overrides[get_authenticated_principal] = lambda: current["principal"]
    app.dependency_overrides[get_auth_uow_factory] = lambda: (lambda: UnitOfWork(factory))
    monkeypatch.setattr("app.routes.workspace_staff_mappings.session_factory", factory)
    try:
        with TestClient(app) as client:
            yield {
                "client": client, "engine": engine, "ids": {
                    "owner": owner_id, "agent": agent_id, "other": other_id,
                    "staff_a": staff_a, "staff_a2": staff_a2, "staff_b": staff_b,
                }, "set_principal": set_principal, "factory": factory,
            }
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _create(client, user_id, staff_id, business_id="business-a"):
    return client.post(
        f"/businesses/{business_id}/workspace-staff-mappings",
        json={"workspace_user_id": str(user_id), "staff_id": str(staff_id)},
    )


def test_owner_can_create_list_and_audit_mapping(mapping_client):
    client, ids, engine = (
        mapping_client["client"], mapping_client["ids"], mapping_client["engine"]
    )
    created = _create(client, ids["agent"], ids["staff_a"])
    assert created.status_code == 201
    data = created.json()
    assert data["workspace_user_id"] == str(ids["agent"])
    assert data["staff_id"] == str(ids["staff_a"])

    listed = client.get("/businesses/business-a/workspace-staff-mappings")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [data["id"]]

    with Session(engine) as session:
        events = session.exec(select(WorkspaceStaffMappingEvent)).all()
    assert len(events) == 1
    assert events[0].event_type == "created"
    assert events[0].workspace_user_id == ids["agent"]
    assert events[0].actor_workspace_user_id == ids["owner"]


def test_admin_can_create_mapping(mapping_client):
    client, ids, engine = (
        mapping_client["client"], mapping_client["ids"], mapping_client["engine"]
    )
    with Session(engine) as session:
        membership = session.exec(
            select(TenantMembership).where(TenantMembership.user_id == ids["owner"])
        ).first()
        membership.role = "admin"
        session.commit()
    assert _create(client, ids["agent"], ids["staff_a"]).status_code == 201


def test_mapping_is_one_to_one_within_business_but_scoped_across_businesses(mapping_client):
    client, ids = mapping_client["client"], mapping_client["ids"]
    assert _create(client, ids["agent"], ids["staff_a"]).status_code == 201
    assert _create(client, ids["agent"], ids["staff_a2"]).status_code == 409
    assert _create(client, ids["owner"], ids["staff_a"]).status_code == 409
    # The same workspace identity may have a distinct staff identity in another business.
    assert _create(client, ids["agent"], ids["staff_b"], "business-a2").status_code == 201


def test_only_active_members_of_same_tenant_can_be_mapped(mapping_client):
    client, ids = mapping_client["client"], mapping_client["ids"]
    response = _create(client, ids["other"], ids["staff_a"])
    assert response.status_code == 404
    assert client.get("/businesses/business-a/workspace-staff-mappings").json()["items"] == []


def test_only_workspace_owners_and_admins_can_manage_mappings(mapping_client):
    client, ids, engine = (
        mapping_client["client"], mapping_client["ids"], mapping_client["engine"]
    )
    with Session(engine) as session:
        membership = session.exec(
            select(TenantMembership).where(
                TenantMembership.user_id == ids["owner"],
                TenantMembership.tenant_id == "mapping-tenant",
            )
        ).first()
        membership.role = "manager"
        session.commit()
    response = _create(client, ids["agent"], ids["staff_a"])
    assert response.status_code == 403
    assert response.json() == {"detail": "Workspace owner or admin access is required"}


def test_api_key_principal_cannot_create_workspace_staff_mapping(mapping_client):
    client, ids = mapping_client["client"], mapping_client["ids"]
    mapping_client["set_principal"]("key-1", auth_source="api_key")
    assert _create(client, ids["agent"], ids["staff_a"]).status_code == 403


def test_removal_is_audited_and_resolver_fails_closed_after_removal(mapping_client):
    client, ids, engine, factory = (
        mapping_client["client"], mapping_client["ids"],
        mapping_client["engine"], mapping_client["factory"],
    )
    created = _create(client, ids["agent"], ids["staff_a"])
    mapping_id = created.json()["id"]
    principal = AuthenticatedPrincipal(
        principal_id=f"workspace-user:{ids['agent']}",
        tenant_id="mapping-tenant", roles=("conversation_writer",),
        auth_source="email_magic_link", issued_at=datetime.now(UTC),
    )
    with UnitOfWork(factory) as uow:
        assert resolve_staff_id(uow, principal, business_id="business-a") == ids["staff_a"]

    removed = client.delete(
        f"/businesses/business-a/workspace-staff-mappings/{mapping_id}"
    )
    assert removed.status_code == 204
    with UnitOfWork(factory) as uow:
        assert resolve_staff_id(uow, principal, business_id="business-a") is None

    with Session(engine) as session:
        events = session.exec(
            select(WorkspaceStaffMappingEvent).order_by(WorkspaceStaffMappingEvent.created_at)
        ).all()
    assert [row.event_type for row in events] == ["created", "removed"]
    assert events[1].mapping_id == UUID(mapping_id)
    assert events[1].actor_workspace_user_id == ids["owner"]


def test_resolver_rejects_missing_cross_business_and_non_workspace_identities(mapping_client):
    ids, factory = mapping_client["ids"], mapping_client["factory"]
    principal = AuthenticatedPrincipal(
        principal_id=f"workspace-user:{ids['agent']}",
        tenant_id="mapping-tenant", roles=("conversation_writer",),
        auth_source="email_magic_link", issued_at=datetime.now(UTC),
    )
    other_business = AuthenticatedPrincipal(
        principal_id=principal.principal_id, tenant_id="other-tenant",
        roles=("conversation_writer",), auth_source="email_magic_link",
        issued_at=datetime.now(UTC),
    )
    api_key = AuthenticatedPrincipal(
        principal_id="integration-1", tenant_id="mapping-tenant",
        roles=("conversation_writer",), auth_source="api_key",
        issued_at=datetime.now(UTC),
    )
    with UnitOfWork(factory) as uow:
        assert resolve_staff_id(uow, principal, business_id="business-a") is None
        assert resolve_staff_id(uow, principal, business_id="business-a2") is None
        assert resolve_staff_id(uow, other_business, business_id="business-a") is None
        assert resolve_staff_id(uow, api_key, business_id="business-a") is None
