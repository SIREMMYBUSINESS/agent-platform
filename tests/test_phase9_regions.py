from datetime import UTC, datetime, time
from types import SimpleNamespace

import pytest

from app.observability.metrics import MetricsRegistry
from app.regions.catalog import get_region, validate_country
from app.regions.service import RegionService
from app.services.enrollment_service import EnrollmentService
from app.queue.routing import route_for
from app.services.routing_service import RoutingService


def test_region_catalog_rejects_country_outside_deployment_region():
    assert validate_country("eu-west-1", "FR") == "FR"
    assert validate_country("af-east-1", "KE") == "KE"
    with pytest.raises(ValueError):
        validate_country("eu-west-1", "US")
    with pytest.raises(ValueError):
        get_region("unknown-region")


def test_africa_catalog_uses_explicit_safe_mode():
    definition = get_region("af-south-1")
    assert definition.compliance_profile["safe_mode"] is True
    assert definition.compliance_profile["country_specific_policy_required"] is True


def test_queue_route_is_region_and_domain_scoped():
    route = route_for("outreach", tenant_id="tenant-1", business_id="business-1",
                      region_id="eu-west-1")
    assert route.queue == "agent.eu-west-1.outreach"


def test_business_hours_fall_back_to_approved_region_timezone():
    business = SimpleNamespace(timezone=None)
    tenant = SimpleNamespace(default_timezone="UTC")
    rule = SimpleNamespace(weekday=0, opens_at=time(9), closes_at=time(17),
                           active=True, after_hours_action="callback")
    result = RoutingService().evaluate(
        business=business, contact=None, tenant=tenant, rules=[rule],
        now=datetime(2026, 10, 5, 14, 0, tzinfo=UTC),
        region_timezone="America/Toronto",
    )
    assert result.business_timezone == "America/Toronto"
    assert result.is_open is True


def test_regional_metrics_have_bounded_region_labels():
    registry = MetricsRegistry()
    registry.region_event(region_id="eu-west-1", operation="residency_decision",
                          outcome="blocked")
    registry.region_event(region_id="caller-controlled-region", operation="residency_decision",
                          outcome="blocked")
    rendered = registry.render()
    assert 'region_id="eu-west-1"' in rendered
    assert 'region_id="unknown"' in rendered
    assert "caller-controlled-region" not in rendered


def test_cross_region_storage_is_blocked_and_audited():
    audits = []
    region = SimpleNamespace(allowed_storage_regions=["eu-west-1"],
                             data_residency_region="eu-west-1")
    repo = SimpleNamespace(
        get=lambda **_: region,
        add_audit=lambda **values: audits.append(values),
    )
    allowed = RegionService(uow_factory=None).authorize_storage(
        uow=SimpleNamespace(regions=repo), tenant_id="tenant-1",
        region_id="eu-west-1", target_region_id="us-east-1",
        resource_type="contact", resource_id="contact-1",
    )
    assert allowed is False
    assert audits[0]["decision"] == "block"
    assert audits[0]["details"]["target_region_id"] == "us-east-1"


def test_region_retention_policy_accepts_bounded_categories_and_durations():
    validate = RegionService._validate_retention_policy
    assert validate({"contact_days": 365, "audit_days": 730}) == {
        "contact_days": 365, "audit_days": 730,
    }
    with pytest.raises(ValueError):
        validate({"contact_days": 0})
    with pytest.raises(ValueError):
        validate({"raw_pii_days": 10})


def test_campaign_template_uses_provisioned_contact_locale():
    result = EnrollmentService._render(
        {"locales": {
            "en": {"subject": "Hello", "body": "Welcome {{contact_name}}"},
            "fr-CA": {"subject": "Bonjour", "body": "Bienvenue {{contact_name}}"},
        }},
        contact=SimpleNamespace(language="fr-CA", full_name="Camille"),
        business=SimpleNamespace(name="Example", default_language="en"),
        enrichment=None,
        region_language="en-CA",
    )
    assert result == {"subject": "Bonjour", "body": "Bienvenue Camille"}
