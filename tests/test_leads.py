from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.persistence.lead_repositories import SQLLeadEventRepository
from app.persistence.models import (
    Business, Contact, Lead, LeadEnrichment, LeadEventLog, LeadScore,
    LeadSource, LeadStateTransition, Tenant,
)
from app.persistence.unit_of_work import UnitOfWork
from app.routes.leads import get_lead_import_service
from app.services.lead_import_service import LeadImportService
from app.services.lead_scoring_service import LeadScoringService


class FixedEnrichment:
    name = "test_adapter"

    def enrich(self, *, contact, business):
        return {
            "company_info": {"industry": "home services"},
            "public_profile_data": {"profile_type": "business"},
            "website_metadata": {"title": "Example business"},
            "custom_business_context": {"target_segment": "local"},
            "lead_fit_signal": {"fit_score": 90, "urgency_score": 50, "intent_score": 80},
        }


class FailedEnrichment:
    name = "test_adapter"

    def enrich(self, *, contact, business):
        raise RuntimeError("provider error containing private data")


@pytest.fixture
def lead_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="trusted-tenant", name="Tenant"))
        session.flush()
        session.add(Business(
            id="business-1", tenant_id="trusted-tenant", name="Business",
            vertical="general", timezone="UTC",
        ))
        session.flush()
        session.add(Contact(
            id="contact-1", tenant_id="trusted-tenant", business_id="business-1",
            full_name="Provisioned contact", consent_status="granted",
        ))
        session.add(Business(
            id="business-2", tenant_id="trusted-tenant", name="Other business",
            vertical="general",
        ))
        session.flush()
        session.add(Contact(
            id="contact-2", tenant_id="trusted-tenant", business_id="business-2",
            full_name="Other contact",
        ))
        session.commit()

    service = LeadImportService(
        trusted_tenant_id="trusted-tenant",
        trusted_business_id="business-1",
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        enrichment_adapter=FixedEnrichment(),
    )
    app.dependency_overrides[get_lead_import_service] = lambda: service
    try:
        with TestClient(app) as client:
            yield client, engine, service
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def payload(**updates):
    value = {
        "tenant_id": "trusted-tenant",
        "contact_id": "contact-1",
        "source_name": "CSV Import",
        "source_type": "csv",
        "source_reference": "batch-2026-09",
        "source_metadata": {"file_name": "prospects.csv"},
    }
    value.update(updates)
    return value


def test_valid_lead_import_persists_lead_scores_transitions_and_events(lead_client):
    client, engine, _ = lead_client

    response = client.post("/leads/import", json=payload())

    assert response.status_code == 201
    body = response.json()
    assert body["tenant_id"] == "trusted-tenant"
    assert body["business_id"] == "business-1"
    assert body["status"] == "qualified"
    assert body["scores"]["priority_score"] == 75
    assert isinstance(UUID(body["id"]), UUID)
    with Session(engine) as session:
        lead = session.exec(select(Lead)).one()
        assert lead.contact_id == "contact-1"
        assert [row.to_state for row in session.exec(select(LeadStateTransition).order_by(LeadStateTransition.created_at, LeadStateTransition.id)).all()] == [
            "imported", "enriched", "scored", "qualified"
        ]
        assert {row.event_type for row in session.exec(select(LeadEventLog)).all()} == {
            "lead.imported", "lead.enriched", "lead.scored", "lead.qualified"
        }


def test_tenant_mismatch_rejected_without_persisting_lead(lead_client):
    client, engine, _ = lead_client

    response = client.post("/leads/import", json=payload(tenant_id="attacker-tenant"))

    assert response.status_code == 403
    with Session(engine) as session:
        assert session.exec(select(Lead)).all() == []


def test_caller_cannot_override_trusted_business_id(lead_client):
    client, engine, _ = lead_client

    response = client.post("/leads/import", json=payload(business_id="business-2", contact_id="contact-2"))

    assert response.status_code == 422
    with Session(engine) as session:
        assert session.exec(select(Lead)).all() == []


def test_contact_must_belong_to_trusted_business(lead_client):
    client, engine, _ = lead_client

    response = client.post("/leads/import", json=payload(contact_id="contact-2"))

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Lead)).all() == []


def test_scoring_clamps_signals_and_calculates_priority():
    score = LeadScoringService().score({
        "company_info": {}, "public_profile_data": {}, "website_metadata": {},
        "lead_fit_signal": {"fit_score": 120, "urgency_score": -1, "intent_score": 60},
    })

    assert (score.fit_score, score.urgency_score, score.intent_score) == (100, 0, 60)
    assert score.priority_score == 58


def test_enrichment_is_persisted_and_returned_by_lead_detail(lead_client):
    client, engine, _ = lead_client
    imported = client.post("/leads/import", json=payload())
    lead_id = imported.json()["id"]

    detail = client.get(f"/leads/{lead_id}")
    listing = client.get("/leads?offset=0&limit=10")

    assert detail.status_code == listing.status_code == 200
    assert detail.json()["enrichment"]["provider_name"] == "test_adapter"
    assert detail.json()["enrichment"]["company_info"]["industry"] == "home services"
    assert len(detail.json()["transitions"]) == 4
    assert len(listing.json()["items"]) == 1
    with Session(engine) as session:
        assert len(session.exec(select(LeadEnrichment)).all()) == 1
        assert len(session.exec(select(LeadScore)).all()) == 1
        assert len(session.exec(select(LeadSource)).all()) == 1


def test_enrichment_provider_failure_does_not_abort_import(lead_client):
    _, engine, _ = lead_client
    failed_service = LeadImportService(
        trusted_tenant_id="trusted-tenant", trusted_business_id="business-1",
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        enrichment_adapter=FailedEnrichment(),
    )
    from app.schemas import LeadImportRequest
    result = failed_service.import_lead(LeadImportRequest.model_validate(payload()))

    assert result.lead.status == "scored"
    assert result.enrichment is None
    assert {row.event_type for row in result.events} == {"lead.imported", "lead.enrichment.failed", "lead.scored"}
    with Session(engine) as session:
        assert len(session.exec(select(Lead)).all()) == 1
        assert session.exec(select(LeadEnrichment)).all() == []


def test_failed_import_rolls_back_lead_source_scores_and_audit(lead_client, monkeypatch):
    client, engine, _ = lead_client

    original_add = SQLLeadEventRepository.add
    def fail_on_scored(self, **kwargs):
        if kwargs["event_type"] == "lead.scored":
            raise RuntimeError("event persistence failed")
        return original_add(self, **kwargs)
    monkeypatch.setattr(SQLLeadEventRepository, "add", fail_on_scored)

    with pytest.raises(RuntimeError, match="event persistence failed"):
        client.post("/leads/import", json=payload())

    with Session(engine) as session:
        for model in (Lead, LeadSource, LeadEnrichment, LeadScore, LeadStateTransition, LeadEventLog):
            assert session.exec(select(model)).all() == []
