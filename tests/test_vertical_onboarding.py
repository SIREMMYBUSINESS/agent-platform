from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.persistence.models import (
    ConsentRecord, Contact, CountryPolicy, Tenant, TenantRegionConfig,
)
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import CampaignCreateRequest, CampaignMessageTemplate, CampaignStepInput
from app.security import AuthenticatedPrincipal
from app.services.campaign_service import CampaignService
from app.services.consent_service import ConsentService
from app.services.policy_service import DEFAULT_COUNTRY_POLICIES
from app.integrations.provider_support import MappingTenantConfig
from app.verticals.outreach import DentalOutreachService
from app.verticals.schemas import DentalOnboardingRequest
from app.verticals.service import VerticalOnboardingService
from app.verticals.templates import DENTAL_CLINIC, render_message


@pytest.fixture
def vertical_store(monkeypatch):
    from app.config import settings

    # This fixture provisions a US-only tenant regardless of the CI deployment defaults.
    monkeypatch.setattr(settings, "database_region_id", "us-east-1")
    monkeypatch.setattr(settings, "deployment_region_id", "us-east-1")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-dental", name="Dental tenant",
                           default_region_id="us-east-1",
                           supported_region_ids=["us-east-1"]))
        session.flush()
        session.add(TenantRegionConfig(
            tenant_id="tenant-dental", region_id="us-east-1", country_code="US",
            timezone="America/New_York", language="en-US",
            data_residency_region="us-east-1", allowed_storage_regions=["us-east-1"],
            provider_preferences={"sms_region": "us-east-1", "calendar_region": "us-east-1"},
            compliance_profile={"region_group": "US"},
            allowed_channels=["sms", "email", "chat"], active=True,
            approval_status="approved", approved_by="reviewer",
        ))
        session.add(CountryPolicy(
            country_code="US", region_group="US",
            rules=DEFAULT_COUNTRY_POLICIES["US"]["rules"], active=True,
        ))
        session.commit()
    yield engine
    engine.dispose()


def principal(*roles):
    return AuthenticatedPrincipal("clinic-owner", "tenant-dental", tuple(roles),
                                  "test", datetime.now(UTC) - timedelta(seconds=1))


def uow_factory(engine):
    return lambda: UnitOfWork(lambda: Session(engine))


def onboarding_request(**overrides):
    values = {"business_name": "Harbor Dental", "staff": [{"display_name": "Dr. Rivera"}]}
    values.update(overrides)
    return DentalOnboardingRequest(**values)


def create_business(engine, *, resolver=None):
    service = VerticalOnboardingService(uow_factory=uow_factory(engine),
                                        provider_config_resolver=resolver)
    result = service.onboard(principal("business_onboarder"), onboarding_request())
    return service, result["business_id"]


def add_contact(engine, business_id, *, last_visit_at=None):
    with Session(engine) as session:
        contact = Contact(id="patient-1", tenant_id="tenant-dental",
            business_id=business_id, full_name="Jordan Patient", phone="+15551234567",
            consent_status="unknown", last_visit_at=last_visit_at)
        session.add(contact)
        session.commit()
    with UnitOfWork(lambda: Session(engine)) as uow:
        ConsentService().record(uow, tenant_id="tenant-dental", business_id=business_id,
            contact_id="patient-1", channel="sms", status="granted",
            source="trusted_patient_permission")


def test_dental_template_has_sequential_questions_services_and_metrics():
    assert DENTAL_CLINIC.vertical_id == "healthcare_dental"
    assert [question.key for question in DENTAL_CLINIC.intake_questions] == [
        "service_type", "reason", "insurance_provider", "preferred_date",
    ]
    assert {service.duration_minutes for service in DENTAL_CLINIC.services} == {30, 60}
    assert "booking_rate" in DENTAL_CLINIC.success_metrics


def test_template_rendering_only_accepts_declared_variables():
    text = render_message("Hello {{contact_name}}, welcome to {{clinic_name}}.",
                          {"contact_name": "Jordan", "clinic_name": "Harbor Dental"})
    assert text == "Hello Jordan, welcome to Harbor Dental."
    with pytest.raises(ValueError):
        render_message("{{unknown}}", {"unknown": "value"})


def test_onboarding_persists_region_scoped_services_hours_staff_and_health(vertical_store):
    resolver = MappingTenantConfig({("tenant-dental", "unused"): {}})
    service, business_id = create_business(vertical_store, resolver=resolver)
    with UnitOfWork(lambda: Session(vertical_store)) as uow:
        business = uow.businesses.get(tenant_id="tenant-dental", business_id=business_id)
        assert business.vertical == "healthcare_dental"
        assert business.region_id == "us-east-1"
        assert len(uow.services.list_active(tenant_id="tenant-dental", business_id=business_id)) == 4
        assert len(uow.routing_rules.list_for_business(tenant_id="tenant-dental", business_id=business_id)) == 6
        assert len(uow.staff.list_active(tenant_id="tenant-dental", business_id=business_id)) == 1
    health = service.health_check(principal("business_onboarder"), business_id=business_id)
    assert health["checks"]["region_approved"] is True
    assert health["checks"]["country_policy"] is True
    assert health["ready"] is False


def test_health_check_gates_go_live_on_provider_configuration(vertical_store):
    class ReadyResolver:
        def resolve(self, *, tenant_id, business_id):
            return {"sms_provider": "twilio", "account_sid": "ACtest",
                    "auth_token": "secret-test", "from_sms": "+15550000000",
                    "calendar_provider": "google", "access_token": "token-test",
                    "calendar_id": "clinic-calendar"}

    service, business_id = create_business(vertical_store, resolver=ReadyResolver())
    health = service.health_check(principal("business_onboarder"), business_id=business_id)
    assert health["ready"] is True
    assert service.activate(principal("business_onboarder"), business_id=business_id)["status"] == "live"


def test_optional_email_provider_does_not_block_go_live(vertical_store):
    class RequiredOnlyResolver:
        def resolve(self, *, tenant_id, business_id):
            return {"sms_provider": "twilio", "account_sid": "ACtest",
                    "auth_token": "secret-test", "from_sms": "+15550000000",
                    "calendar_provider": "google", "access_token": "token-test",
                    "calendar_id": "clinic-calendar"}

    service = VerticalOnboardingService(uow_factory=uow_factory(vertical_store),
                                        provider_config_resolver=RequiredOnlyResolver())
    request = onboarding_request(provider_selection={
        "sms": "twilio", "calendar": "google", "email": "sendgrid",
    })
    business_id = service.onboard(principal("business_onboarder"), request)["business_id"]
    health = service.health_check(principal("business_onboarder"), business_id=business_id)
    assert health["checks"]["provider_email"] is False
    assert health["ready"] is True


def test_intake_persists_answers_and_escalates_urgent_symptoms(vertical_store):
    service, business_id = create_business(vertical_store)
    add_contact(vertical_store, business_id)
    session = service.start_intake(principal("workflow_executor"),
        business_id=business_id, contact_id="patient-1", channel="sms",
        idempotency_key="patient-intake-0001")
    assert session["question_key"] == "service_type"
    session_id = session["session_id"]
    questions = ["Dental cleaning", "I have breathing difficulty", "", ""]
    result = None
    for index, answer in enumerate(questions):
        result = service.answer_intake(principal("workflow_executor"),
            business_id=business_id, session_id=session_id, answer=answer,
            idempotency_key=f"patient-answer-{index:02d}")
    assert result["status"] == "escalated"
    with UnitOfWork(lambda: Session(vertical_store)) as uow:
        intake = uow.verticals.intake(tenant_id="tenant-dental", business_id=business_id,
                                      session_id=session_id)
        assert intake.answers["service_type"] == "Dental cleaning"
        assert intake.status == "escalated"
        assert uow.escalations.get_in_tenant(tenant_id="tenant-dental",
                                             ticket_id=result["escalation_id"]) is not None


def test_dental_recall_enrolls_overdue_patient_and_policy_blocks_opted_out(vertical_store):
    service, business_id = create_business(vertical_store)
    now = datetime.now(UTC)
    add_contact(vertical_store, business_id, last_visit_at=now - timedelta(days=250))
    with Session(vertical_store) as session:
        session.add(Contact(id="patient-opted-out", tenant_id="tenant-dental",
            business_id=business_id, full_name="Opted Out", phone="+15557654321",
            consent_status="unknown", last_visit_at=now - timedelta(days=300)))
        session.commit()
    with UnitOfWork(lambda: Session(vertical_store)) as uow:
        ConsentService().record(uow, tenant_id="tenant-dental", business_id=business_id,
            contact_id="patient-opted-out", channel="sms", status="revoked",
            source="patient_opt_out")
    campaign = CampaignService(trusted_tenant_id="tenant-dental",
        trusted_business_id=business_id, uow_factory=uow_factory(vertical_store)).create(
            CampaignCreateRequest(tenant_id="tenant-dental", name="Six month recall",
                channel="sms", audience_filters={"minimum_priority_score": 0},
                steps=[CampaignStepInput(delay_seconds=0, template=CampaignMessageTemplate(
                    body="Hello {{contact_name}}, it may be time to book with {{business_name}}."))]),
            principal("campaign_writer"),
        )
    result = DentalOutreachService(uow_factory=uow_factory(vertical_store)).enroll_overdue(
        principal("campaign_writer"), business_id=business_id,
        campaign_id=campaign.campaign.id, months_since_visit=6,
    )
    assert result["enrolled"] == 1
    assert len(result["lead_ids"]) == 1
    assert result["excluded_by_policy"] == 1
