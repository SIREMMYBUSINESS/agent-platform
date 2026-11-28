"""Route-level regression tests for fail-closed lead business scoping.

The Tenant A lead ID is fixed at fixture creation; tests never query Tenant A rows.
"""
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.auth.dependencies import get_authenticated_principal
from app.main import app
from app.persistence.lead_repositories import (
    SQLLeadEnrichmentRepository,
    SQLLeadEventRepository,
    SQLLeadRepository,
    SQLLeadScoreRepository,
    SQLLeadSourceRepository,
    SQLLeadTransitionRepository,
)
from app.persistence.models import Business, Contact, Lead, LeadSource, Tenant
from app.persistence.unit_of_work import UnitOfWork
from app.routes.leads import get_lead_import_service
from app.security import AuthenticatedPrincipal
from app.services.lead_import_service import LeadImportService


TENANT_A_LEAD_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TENANT_B_LEAD_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@pytest.fixture
def scoped_api():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for tenant_id, business_id, contact_id, lead_id in (
            ("tenant-a", "business-a", "contact-a", TENANT_A_LEAD_ID),
            ("tenant-b", "business-b", "contact-b", TENANT_B_LEAD_ID),
        ):
            session.add(Tenant(id=tenant_id, name=tenant_id))
            session.flush()
            session.add(Business(
                id=business_id, tenant_id=tenant_id, name=business_id,
                vertical="general", timezone="UTC",
            ))
            session.flush()
            session.add(Contact(
                id=contact_id, tenant_id=tenant_id, business_id=business_id,
                full_name=f"{tenant_id} contact",
            ))
            source = LeadSource(
                tenant_id=tenant_id, business_id=business_id,
                name=f"source-{tenant_id}", source_type="manual",
            )
            session.add(source)
            session.flush()
            session.add(Lead(
                id=lead_id, tenant_id=tenant_id, business_id=business_id,
                contact_id=contact_id, source_id=source.id,
            ))
        session.commit()

    def service_for(tenant_id: str, business_id: str) -> LeadImportService:
        return LeadImportService(
            trusted_tenant_id=tenant_id,
            trusted_business_id=business_id,
            uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        )

    def principal_for(tenant_id: str) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            f"test-{tenant_id}", tenant_id,
            ("lead_reader", "lead_writer"), "test", datetime.now(UTC),
        )

    def client_for(tenant_id: str, business_id: str) -> TestClient:
        app.dependency_overrides[get_authenticated_principal] = lambda: principal_for(tenant_id)
        app.dependency_overrides[get_lead_import_service] = lambda: service_for(tenant_id, business_id)
        return TestClient(app)

    try:
        yield engine, client_for
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def import_payload():
    return {
        "tenant_id": "tenant-b",
        "contact_id": "contact-b",
        "source_name": "Tenant B import",
        "source_type": "manual",
        "source_reference": "tenant-b-batch",
        "source_metadata": {},
    }


def test_tenant_b_list_and_import_are_scoped_to_business_b(scoped_api):
    engine, client_for = scoped_api
    with client_for("tenant-b", "business-b") as client:
        listing = client.get("/leads")
        imported = client.post("/leads/import", json=import_payload())

    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["items"]] == [str(TENANT_B_LEAD_ID)]
    assert imported.status_code == 201
    assert imported.json()["tenant_id"] == "tenant-b"
    assert imported.json()["business_id"] == "business-b"

    # Inspect only Tenant B rows; no Tenant A query is used by this assertion.
    with Session(engine) as session:
        tenant_b_rows = session.query(Lead).filter(Lead.tenant_id == "tenant-b").all()
        assert {row.id for row in tenant_b_rows} == {TENANT_B_LEAD_ID, UUID(imported.json()["id"])}


def test_tenant_b_get_of_tenant_a_lead_id_returns_404_without_reading_tenant_a(scoped_api, monkeypatch):
    _, client_for = scoped_api
    original_get = SQLLeadRepository.get
    scopes = []

    def scoped_get(self, *, tenant_id, business_id, lead_id):
        scopes.append((tenant_id, business_id, lead_id))
        return original_get(self, tenant_id=tenant_id, business_id=business_id, lead_id=lead_id)

    monkeypatch.setattr(SQLLeadRepository, "get", scoped_get)
    with client_for("tenant-b", "business-b") as client:
        response = client.get(f"/leads/{TENANT_A_LEAD_ID}")

    assert response.status_code == 404
    assert scopes == [("tenant-b", "business-b", TENANT_A_LEAD_ID)]


def test_wrong_configured_business_returns_404_before_lead_queries_or_writes(scoped_api, monkeypatch):
    _, client_for = scoped_api
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("lead repository accessed before business validation")

    monkeypatch.setattr(SQLLeadRepository, "get", forbidden)
    monkeypatch.setattr(SQLLeadRepository, "list", forbidden)
    monkeypatch.setattr(SQLLeadRepository, "create", forbidden)
    monkeypatch.setattr(SQLLeadSourceRepository, "get_or_create", forbidden)
    monkeypatch.setattr(SQLLeadTransitionRepository, "record_imported", forbidden)
    monkeypatch.setattr(SQLLeadTransitionRepository, "transition", forbidden)
    monkeypatch.setattr(SQLLeadEventRepository, "add", forbidden)
    monkeypatch.setattr(SQLLeadEnrichmentRepository, "add", forbidden)
    monkeypatch.setattr(SQLLeadScoreRepository, "add", forbidden)

    with client_for("tenant-b", "business-a") as client:
        listed = client.get("/leads")
        fetched = client.get(f"/leads/{TENANT_A_LEAD_ID}")
        imported = client.post("/leads/import", json=import_payload())

    assert [listed.status_code, fetched.status_code, imported.status_code] == [404, 404, 404]
    assert calls == []
