"""Two-tenant regression tests for lead tenant/business isolation."""

from datetime import datetime, UTC
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.auth.dependencies import get_authenticated_principal
from app.main import app
from app.persistence.models import Business, Contact, Lead, LeadSource, Tenant
from app.persistence.unit_of_work import UnitOfWork
from app.routes.leads import get_lead_import_service
from app.security import AuthenticatedPrincipal
from app.services.lead_import_service import LeadImportService, LeadProvisioningError


TENANT_A_LEAD_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest.fixture
def two_tenant_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for tenant_id, business_id, contact_id in (
            ("tenant-a", "business-a", "contact-a"),
            ("tenant-b", "business-b", "contact-b"),
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
                tenant_id=tenant_id, business_id=business_id, name=f"source-{tenant_id}",
                source_type="manual",
            )
            session.add(source)
            session.flush()
            session.add(Lead(
                id=TENANT_A_LEAD_ID if tenant_id == "tenant-a" else UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                tenant_id=tenant_id, business_id=business_id,
                contact_id=contact_id, source_id=source.id,
            ))
        session.commit()
    yield engine
    engine.dispose()


def service_for(engine, tenant_id, business_id):
    return LeadImportService(
        trusted_tenant_id=tenant_id,
        trusted_business_id=business_id,
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
    )


def principal_for(tenant_id):
    return AuthenticatedPrincipal(
        f"test-{tenant_id}", tenant_id,
        ("lead_reader", "lead_writer"),
        "test", datetime.now(UTC),
    )


def test_two_tenant_lead_lists_are_scoped(two_tenant_store):
    service_b = service_for(two_tenant_store, "tenant-b", "business-b")

    assert [item.lead.tenant_id for item in service_b.list()] == ["tenant-b"]


def test_tenant_b_cannot_get_tenant_a_lead(two_tenant_store):
    service_b = service_for(two_tenant_store, "tenant-b", "business-b")
    with pytest.raises(LeadProvisioningError, match="lead not found"):
        service_b.get(lead_id=TENANT_A_LEAD_ID)


def test_tenant_b_cross_tenant_lead_request_returns_http_404(two_tenant_store):
    service_b = service_for(two_tenant_store, "tenant-b", "business-b")
    app.dependency_overrides[get_authenticated_principal] = lambda: principal_for("tenant-b")
    app.dependency_overrides[get_lead_import_service] = lambda: service_b
    try:
        with TestClient(app) as client:
            response = client.get(f"/leads/{TENANT_A_LEAD_ID}")
        assert response.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_tenant_b_import_creates_only_tenant_b_lead(two_tenant_store):
    service_b = service_for(two_tenant_store, "tenant-b", "business-b")
    from app.schemas import LeadImportRequest

    request = LeadImportRequest(
        tenant_id="tenant-b",
        contact_id="contact-b",
        source_name="regression",
        source_type="manual",
        source_reference=None,
        source_metadata={},
    )
    imported = service_b.import_lead(request)
    assert imported.lead.tenant_id == "tenant-b"
    assert imported.lead.business_id == "business-b"
    with Session(two_tenant_store) as session:
        assert len(session.exec(select(Lead).where(Lead.tenant_id == "tenant-b")).all()) == 2


def test_misconfigured_business_fails_before_lead_queries_on_list_and_get(two_tenant_store, monkeypatch):
    from app.persistence.lead_repositories import (
        SQLLeadEnrichmentRepository, SQLLeadEventRepository, SQLLeadRepository,
        SQLLeadScoreRepository, SQLLeadSourceRepository, SQLLeadTransitionRepository,
    )

    def unexpected_read(*args, **kwargs):
        raise AssertionError("lead query ran before business validation")

    monkeypatch.setattr(SQLLeadRepository, "get", unexpected_read)
    monkeypatch.setattr(SQLLeadRepository, "list", unexpected_read)
    monkeypatch.setattr(SQLLeadSourceRepository, "get_or_create", unexpected_read)
    monkeypatch.setattr(SQLLeadTransitionRepository, "record_imported", unexpected_read)
    monkeypatch.setattr(SQLLeadTransitionRepository, "transition", unexpected_read)
    monkeypatch.setattr(SQLLeadEventRepository, "add", unexpected_read)
    monkeypatch.setattr(SQLLeadEnrichmentRepository, "add", unexpected_read)
    monkeypatch.setattr(SQLLeadScoreRepository, "add", unexpected_read)

    misconfigured = service_for(two_tenant_store, "tenant-b", "business-a")
    with pytest.raises(LeadProvisioningError, match="business is not provisioned"):
        misconfigured.list()
    with pytest.raises(LeadProvisioningError, match="business is not provisioned"):
        misconfigured.get(lead_id=uuid4())


def test_misconfigured_business_fails_before_import_writes(two_tenant_store, monkeypatch):
    from app.persistence.lead_repositories import (
        SQLLeadEnrichmentRepository, SQLLeadEventRepository, SQLLeadRepository,
        SQLLeadScoreRepository, SQLLeadSourceRepository, SQLLeadTransitionRepository,
    )

    def unexpected_write(*args, **kwargs):
        raise AssertionError("lead write ran before business validation")

    monkeypatch.setattr(SQLLeadRepository, "create", unexpected_write)
    monkeypatch.setattr(SQLLeadSourceRepository, "get_or_create", unexpected_write)
    monkeypatch.setattr(SQLLeadTransitionRepository, "record_imported", unexpected_write)
    monkeypatch.setattr(SQLLeadTransitionRepository, "transition", unexpected_write)
    monkeypatch.setattr(SQLLeadEventRepository, "add", unexpected_write)
    monkeypatch.setattr(SQLLeadEnrichmentRepository, "add", unexpected_write)
    monkeypatch.setattr(SQLLeadScoreRepository, "add", unexpected_write)
    misconfigured = service_for(two_tenant_store, "tenant-b", "business-a")
    from app.schemas import LeadImportRequest

    request = LeadImportRequest(
        tenant_id="tenant-b", contact_id="contact-b",
        source_name="regression", source_type="manual",
    )
    with pytest.raises(LeadProvisioningError, match="business is not provisioned"):
        misconfigured.import_lead(request)
