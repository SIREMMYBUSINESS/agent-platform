from app.auth.dependencies import get_authenticated_principal
from app.security import AuthenticatedPrincipal
from datetime import datetime as AuthDatetime, UTC as AUTH_UTC
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.persistence.campaign_repositories import SQLCampaignEnrollmentRepository
from app.persistence.models import (
    Business, Campaign, CampaignEnrollment, CampaignEventLog,
    CampaignStepRun, ConsentRecord, Contact, Lead, LeadScore, LeadSource,
    PolicyDecisionLog, Tenant,
)
from app.persistence.unit_of_work import UnitOfWork
from app.routes.campaigns import (get_campaign_service, get_enrollment_service,
                                  get_reply_service, get_campaign_analytics_service)
from app.services.campaign_service import CampaignService
from app.services.enrollment_service import EnrollmentService
from app.services.reply_service import ReplyService
from app.services.campaign_analytics_service import CampaignAnalyticsService


@pytest.fixture
def campaign_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    lead_id = uuid4()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="Tenant"))
        session.flush()
        session.add(Business(
            id="business-a", tenant_id="tenant-a", name="Example business",
            vertical="general", region="US",
        ))
        session.flush()
        session.add(Contact(
            id="contact-a", tenant_id="tenant-a", business_id="business-a",
            full_name="Jordan", consent_status="granted",
        ))
        session.flush()
        session.add(ConsentRecord(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
            channel="email", status="granted", source="trusted_test_provisioning",
        ))
        session.flush()
        source = LeadSource(
            tenant_id="tenant-a", business_id="business-a", name="Import",
            source_type="csv",
        )
        session.add(source)
        session.flush()
        session.add(Lead(
            id=lead_id, tenant_id="tenant-a", business_id="business-a",
            contact_id="contact-a", source_id=source.id, status="qualified",
        ))
        session.flush()
        session.add(LeadScore(
            tenant_id="tenant-a", business_id="business-a", lead_id=lead_id,
            fit_score=90, urgency_score=80, intent_score=90, priority_score=87,
        ))
        session.commit()

    uow_factory = lambda: UnitOfWork(lambda: Session(engine))
    campaign_service = CampaignService(
        trusted_tenant_id="tenant-a", trusted_business_id="business-a",
        uow_factory=uow_factory,
    )
    enrollment_service = EnrollmentService(
        trusted_tenant_id="tenant-a", trusted_business_id="business-a",
        uow_factory=uow_factory, now_provider=lambda: now,
    )
    reply_service = ReplyService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                                 uow_factory=uow_factory)
    analytics_service = CampaignAnalyticsService(trusted_tenant_id="tenant-a",
                                                 trusted_business_id="business-a",
                                                 uow_factory=uow_factory)
    app.dependency_overrides[get_campaign_service] = lambda: campaign_service
    app.dependency_overrides[get_enrollment_service] = lambda: enrollment_service
    app.dependency_overrides[get_reply_service] = lambda: reply_service
    app.dependency_overrides[get_campaign_analytics_service] = lambda: analytics_service
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        "test-identity", "tenant-a",
        ("inbound_writer", "lead_reader", "lead_writer", "campaign_reader", "campaign_writer"),
        "test", AuthDatetime.now(AUTH_UTC),
    )
    try:
        with TestClient(app) as client:
            yield client, engine, lead_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def campaign_payload(**updates):
    data = {
        "tenant_id": "tenant-a",
        "name": "Spring outreach",
        "channel": "email",
        "audience_filters": {"minimum_priority_score": 65},
        "steps": [
            {"delay_seconds": 0, "template": {
                "subject": "Hello {{contact_name}}",
                "body": "{{business_name}} would like to speak with {{contact_name}}.",
            }},
            {"delay_seconds": 0, "template": {
                "body": "A follow-up for {{company_name}}.",
            }},
        ],
    }
    data.update(updates)
    return data


def create_campaign(client):
    response = client.post("/campaigns", json=campaign_payload())
    assert response.status_code == 201, response.text
    return response.json()


def enroll(client, campaign_id, lead_id):
    return client.post(
        f"/campaigns/{campaign_id}/enroll",
        json={"tenant_id": "tenant-a", "lead_ids": [str(lead_id)]},
    )


def test_campaign_creation_persists_tenant_scoped_sequence_and_event(campaign_client):
    client, engine, _ = campaign_client

    response = client.post("/campaigns", json=campaign_payload())

    assert response.status_code == 201
    campaign = response.json()
    assert campaign["status"] == "draft"
    assert campaign["tenant_id"] == "tenant-a"
    assert campaign["business_id"] == "business-a"
    assert len(campaign["sequences"][0]["steps"]) == 2
    assert campaign["events"][0]["event_type"] == "campaign.created"
    with Session(engine) as session:
        assert session.exec(select(Campaign)).one().status == "draft"


def test_campaign_sequence_variants_assign_one_stable_arm_per_lead(campaign_client):
    client, engine, lead_id = campaign_client
    payload = campaign_payload(sequence_variants=[
        {"name": "subject_a", "steps": [{"delay_seconds": 0, "template": {
            "subject": "Welcome A", "body": "Hello {{contact_name}}.",
        }}]},
        {"name": "subject_b", "steps": [{"delay_seconds": 0, "template": {
            "subject": "Welcome B", "body": "Hello {{contact_name}}.",
        }}]},
    ])
    response = client.post("/campaigns", json=payload)
    assert response.status_code == 201, response.text
    campaign = response.json()
    assert [sequence["name"] for sequence in campaign["sequences"]] == [
        "default", "subject_a", "subject_b",
    ]
    first = enroll(client, campaign["id"], lead_id)
    repeated = enroll(client, campaign["id"], lead_id)
    assert first.status_code == repeated.status_code == 200
    assert first.json()["enrollments"][0]["id"] == repeated.json()["enrollments"][0]["id"]
    with Session(engine) as session:
        enrollment = session.exec(select(CampaignEnrollment)).one()
        event = session.exec(select(CampaignEventLog).where(
            CampaignEventLog.event_type == "campaign.enrolled",
        )).one()
        selected = next(sequence for sequence in campaign["sequences"]
                        if sequence["id"] == str(enrollment.sequence_id))
        assert event.payload["variant_id"] == selected["name"]
        assert event.payload["sequence_id"] == str(enrollment.sequence_id)


def test_campaign_enrolls_qualified_lead_once_and_logs_event(campaign_client):
    client, engine, lead_id = campaign_client
    campaign = create_campaign(client)

    first = enroll(client, campaign["id"], lead_id)
    repeated = enroll(client, campaign["id"], lead_id)

    assert first.status_code == repeated.status_code == 200
    assert first.json()["enrollments"][0]["id"] == repeated.json()["enrollments"][0]["id"]
    with Session(engine) as session:
        assert len(session.exec(select(CampaignEnrollment)).all()) == 1
        enrolled_events = session.exec(select(CampaignEventLog).where(
            CampaignEventLog.event_type == "campaign.enrolled"
        )).all()
        assert len(enrolled_events) == 1


def test_sequence_generation_and_step_progression_completes_campaign(campaign_client):
    client, engine, lead_id = campaign_client
    campaign = create_campaign(client)
    enrollment_response = enroll(client, campaign["id"], lead_id)
    enrollment_id = enrollment_response.json()["enrollments"][0]["id"]

    first = client.post(
        f"/campaigns/{campaign['id']}/trigger",
        json={"tenant_id": "tenant-a", "enrollment_id": enrollment_id},
    )
    second = client.post(
        f"/campaigns/{campaign['id']}/trigger",
        json={"tenant_id": "tenant-a", "enrollment_id": enrollment_id},
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["sent"] == second.json()["sent"] == 1
    assert first.json()["status"] == "active"
    assert second.json()["status"] == "completed"
    with Session(engine) as session:
        runs = session.exec(select(CampaignStepRun).order_by(CampaignStepRun.step_order)).all()
        assert [run.status for run in runs] == ["sent", "sent"]
        assert runs[0].rendered_content["subject"] == "Hello Jordan"
        assert runs[0].rendered_content["body"] == "Example business would like to speak with Jordan."
        events = {row.event_type for row in session.exec(select(CampaignEventLog)).all()}
        assert {
            "campaign.created", "CampaignCreated", "campaign.enrolled", "campaign.state.changed",
            "step.generated", "step.sent", "campaign.completed",
        } <= events

    detail = client.get(f"/campaigns/{campaign['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert len(detail.json()["step_runs"]) == 2


def test_step_delay_defers_the_next_step_until_due(campaign_client):
    client, _, lead_id = campaign_client
    data = campaign_payload()
    data["steps"][1]["delay_seconds"] = 3600
    campaign_response = client.post("/campaigns", json=data)
    assert campaign_response.status_code == 201
    campaign = campaign_response.json()
    enrollment_response = enroll(client, campaign["id"], lead_id)
    enrollment_id = enrollment_response.json()["enrollments"][0]["id"]

    first = client.post(
        f"/campaigns/{campaign['id']}/trigger",
        json={"tenant_id": "tenant-a", "enrollment_id": enrollment_id},
    )
    early = client.post(
        f"/campaigns/{campaign['id']}/trigger",
        json={"tenant_id": "tenant-a", "enrollment_id": enrollment_id},
    )
    service = app.dependency_overrides[get_enrollment_service]()
    service._now = lambda: datetime(2026, 9, 24, 12, tzinfo=UTC) + timedelta(hours=1)
    due = client.post(
        f"/campaigns/{campaign['id']}/trigger",
        json={"tenant_id": "tenant-a", "enrollment_id": enrollment_id},
    )

    assert first.json()["sent"] == 1
    assert early.json()["processed"] == 0
    assert due.json()["sent"] == 1
    assert due.json()["status"] == "completed"


def test_step_is_skipped_when_contact_consent_is_revoked(campaign_client):
    client, engine, lead_id = campaign_client
    campaign = create_campaign(client)
    enroll(client, campaign["id"], lead_id)
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-a")).one()
        contact.consent_status = "revoked"
        session.add(ConsentRecord(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
            channel="email", status="revoked", source="trusted_test_provisioning",
        ))
        session.commit()

    response = client.post(f"/campaigns/{campaign['id']}/trigger", json={"tenant_id": "tenant-a"})

    assert response.status_code == 200
    assert response.json()["skipped"] == 1
    with Session(engine) as session:
        run = session.exec(select(CampaignStepRun)).one()
        assert run.status == "skipped"
        assert run.skip_reason == "contact consent is not granted"
        decisions = session.exec(select(PolicyDecisionLog)).all()
        assert any(row.decision == "block" and row.reason_code == "consent_revoked"
                   for row in decisions)
        assert session.exec(select(CampaignEventLog).where(
            CampaignEventLog.event_type == "step.skipped"
        )).one()


def test_campaign_tenant_mismatch_is_rejected(campaign_client):
    client, engine, _ = campaign_client

    response = client.post("/campaigns", json=campaign_payload(tenant_id="tenant-other"))

    assert response.status_code == 403
    with Session(engine) as session:
        assert session.exec(select(Campaign)).all() == []


def test_enrollment_rejects_lead_from_another_business(campaign_client):
    client, engine, _ = campaign_client
    campaign = create_campaign(client)
    outside_lead_id = uuid4()
    with Session(engine) as session:
        session.add(Business(id="business-b", tenant_id="tenant-a", name="Other", vertical="general"))
        session.flush()
        session.add(Contact(id="contact-b", tenant_id="tenant-a", business_id="business-b", consent_status="granted"))
        session.flush()
        source = LeadSource(tenant_id="tenant-a", business_id="business-b", name="Other source")
        session.add(source)
        session.flush()
        session.add(Lead(id=outside_lead_id, tenant_id="tenant-a", business_id="business-b", contact_id="contact-b", source_id=source.id, status="qualified"))
        session.flush()
        session.add(LeadScore(tenant_id="tenant-a", business_id="business-b", lead_id=outside_lead_id, fit_score=100, urgency_score=100, intent_score=100, priority_score=100))
        session.commit()

    response = enroll(client, campaign["id"], outside_lead_id)

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(CampaignEnrollment)).all() == []


def test_failed_step_progress_update_rolls_back_generation_and_campaign_activation(campaign_client, monkeypatch):
    client, engine, lead_id = campaign_client
    campaign = create_campaign(client)
    enrollment_response = enroll(client, campaign["id"], lead_id)
    enrollment_id = enrollment_response.json()["enrollments"][0]["id"]

    def fail_update(self, **kwargs):
        raise RuntimeError("step progress update failed")
    monkeypatch.setattr(SQLCampaignEnrollmentRepository, "advance", fail_update)

    with pytest.raises(RuntimeError, match="step progress update failed"):
        client.post(
            f"/campaigns/{campaign['id']}/trigger",
            json={"tenant_id": "tenant-a", "enrollment_id": enrollment_id},
        )

    with Session(engine) as session:
        persisted_campaign = session.exec(select(Campaign)).one()
        enrollment = session.exec(select(CampaignEnrollment)).one()
        assert persisted_campaign.status == "draft"
        assert enrollment.current_step_order == -1
        assert session.exec(select(CampaignStepRun)).all() == []
        assert {event.event_type for event in session.exec(select(CampaignEventLog)).all()} == {
            "campaign.created", "CampaignCreated", "campaign.enrolled"
        }
