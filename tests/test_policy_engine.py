from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.persistence.models import (
    Business, ConsentRecord, Contact, CountryPolicy, PolicyDecisionLog,
    PolicyProfile, SuppressionRule, Tenant,
)
from app.persistence.unit_of_work import UnitOfWork
from app.services.consent_service import ConsentService
from app.services.policy_service import PolicyService
from app.services.suppression_service import SuppressionService


@pytest.fixture
def policy_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-policy", name="Policy tenant", region="US"))
        session.flush()
        session.add(Business(id="business-policy", tenant_id="tenant-policy",
                             name="Policy business", vertical="general", region="US"))
        session.flush()
        session.add(Contact(id="contact-policy", tenant_id="tenant-policy",
                            business_id="business-policy", consent_status="unknown"))
        session.commit()
    yield engine
    engine.dispose()


def evaluate(engine, *, region: str, direction: str, channel: str,
             consent: str | None = None, now=None, override_actor=None,
             override_reason=None, create_override_profile=False,
             country_code: str | None = None):
    with Session(engine) as session:
        business = session.exec(select(Business).where(Business.id == "business-policy")).one()
        business.region = region
        business.country_code = country_code
        if consent is not None:
            contact = session.exec(select(Contact).where(Contact.id == "contact-policy")).one()
            contact.consent_status = consent
        if create_override_profile:
            session.add(PolicyProfile(
                tenant_id="tenant-policy", business_id="business-policy",
                allow_manual_override=True,
                custom_rules={"outbound": {"email": {"allowed": False}}},
            ))
        session.commit()

    uow_factory = lambda: UnitOfWork(lambda: Session(engine))
    service = PolicyService(now_provider=lambda: now or datetime.now(UTC))
    with uow_factory() as uow:
        tenant = uow.tenants.get(tenant_id="tenant-policy")
        business = uow.businesses.get(tenant_id="tenant-policy", business_id="business-policy")
        contact = uow.contacts.get(tenant_id="tenant-policy", contact_id="contact-policy")
        return service.evaluate(
            uow, tenant=tenant, business=business, contact=contact,
            direction=direction, channel=channel,
            override_actor=override_actor, override_reason=override_reason,
        )


def grant(engine, channel: str, *, expires_at=None):
    with UnitOfWork(lambda: Session(engine)) as uow:
        ConsentService().record(
            uow, tenant_id="tenant-policy", business_id="business-policy",
            contact_id="contact-policy", channel=channel, status="granted",
            source="trusted_test_provisioning", expires_at=expires_at,
        )


def test_eu_requires_channel_consent_and_logs_both_decisions(policy_database):
    blocked = evaluate(policy_database, region="EU", direction="inbound", channel="email")
    assert not blocked.allowed
    assert blocked.reason_code == "consent_unknown"
    grant(policy_database, "email")
    allowed = evaluate(policy_database, region="EU", direction="inbound", channel="email")
    assert allowed.allowed
    with Session(policy_database) as session:
        rows = session.exec(select(PolicyDecisionLog)).all()
        assert [row.decision for row in rows] == ["block", "allow"]
        assert {row.country_code for row in rows} == {"EU"}


def test_specific_eu_country_code_requires_an_exact_country_policy(policy_database):
    grant(policy_database, "email")
    pending = evaluate(policy_database, region="EU", country_code="DE",
                      direction="outbound", channel="email")
    assert not pending.allowed
    with Session(policy_database) as session:
        session.add(CountryPolicy(
            country_code="DE", region_group="EU",
            rules={"inbound": {"email": {"allowed": True, "consent_required": True}},
                   "outbound": {"email": {"allowed": True, "consent_required": True}}},
        ))
        session.commit()
    result = evaluate(policy_database, region="EU", country_code="DE",
                      direction="outbound", channel="email")
    assert result.allowed
    assert result.country_code == "DE"


@pytest.mark.parametrize("channel", ["email", "sms"])
def test_canada_requires_consent_for_email_and_sms(policy_database, channel):
    decision = evaluate(policy_database, region="CA", direction="outbound", channel=channel)
    assert not decision.allowed
    assert decision.reason_code == "consent_unknown"
    grant(policy_database, channel)
    assert evaluate(policy_database, region="CA", direction="outbound", channel=channel).allowed


def test_contact_wide_legacy_grant_is_not_channel_consent(policy_database):
    decision = evaluate(policy_database, region="CA", direction="outbound",
                         channel="email", consent="granted")
    assert not decision.allowed
    assert decision.consent_status == "unknown"


@pytest.mark.parametrize("channel", ["call", "sms"])
def test_us_requires_consent_for_call_and_sms(policy_database, channel):
    assert not evaluate(policy_database, region="US", direction="inbound", channel=channel).allowed
    grant(policy_database, channel)
    assert evaluate(policy_database, region="US", direction="inbound", channel=channel).allowed


def test_expired_consent_does_not_pass_policy_gate(policy_database):
    grant(policy_database, "sms", expires_at=datetime.now(UTC) - timedelta(seconds=1))
    result = evaluate(policy_database, region="US", direction="outbound", channel="sms")
    assert not result.allowed
    assert result.reason_code == "consent_expired"


def test_suppression_rule_blocks_contact_and_is_logged(policy_database):
    with UnitOfWork(lambda: Session(policy_database)) as uow:
        SuppressionService().add(
            uow, tenant_id="tenant-policy", business_id="business-policy",
            contact_id="contact-policy", channel="email", reason_code="manual_block",
        )
    decision = evaluate(policy_database, region="US", direction="outbound",
                        channel="email", consent="granted")
    assert not decision.allowed
    assert decision.reason_code == "suppressed"
    with Session(policy_database) as session:
        assert session.exec(select(SuppressionRule)).one().active
        logs = session.exec(select(PolicyDecisionLog)).all()
        assert any(row.reason_code == "manual_block" for row in logs)
        assert any(row.reason_code == "suppressed" and row.decision == "block" for row in logs)


def test_opt_out_record_blocks_future_outbound(policy_database):
    with UnitOfWork(lambda: Session(policy_database)) as uow:
        ConsentService().revoke(
            uow, tenant_id="tenant-policy", business_id="business-policy",
            contact_id="contact-policy", channel="sms",
        )
    result = evaluate(policy_database, region="US", direction="outbound",
                      channel="sms", consent="granted")
    assert not result.allowed
    assert result.reason_code == "consent_revoked"
    with Session(policy_database) as session:
        assert session.exec(select(ConsentRecord)).one().status == "revoked"


def test_unrecognized_region_uses_fail_closed_safe_mode(policy_database):
    decision = evaluate(policy_database, region="ZZ", direction="outbound",
                        channel="email", consent="granted")
    assert not decision.allowed
    assert decision.reason_code == "unknown_region"
    with Session(policy_database) as session:
        record = session.exec(select(PolicyDecisionLog)).one()
        assert record.policy_source == "safe_default:UNKNOWN"
        assert record.decision == "block"


def test_deactivated_country_policy_fails_closed(policy_database):
    with Session(policy_database) as session:
        session.add(CountryPolicy(
            country_code="US", region_group="US", active=False,
            rules={"inbound": {}, "outbound": {}},
        ))
        session.commit()
    decision = evaluate(policy_database, region="US", direction="outbound",
                        channel="email", consent="granted")
    assert not decision.allowed
    assert decision.reason_code == "country_policy_inactive"


def test_africa_requires_a_country_specific_policy(policy_database):
    denied = evaluate(policy_database, region="AFRICA", direction="inbound",
                      channel="chat", consent="granted")
    assert not denied.allowed
    assert denied.reason_code == "country_specific_policy_required"
    with Session(policy_database) as session:
        session.add(CountryPolicy(
            country_code="NG", region_group="AFRICA",
            rules={"inbound": {"chat": {"allowed": True, "consent_required": False}},
                   "outbound": {}},
        ))
        session.commit()
        business = session.exec(select(Business)).one()
        business.country_code = "NG"
        session.add(business)
        session.commit()
    assert evaluate(policy_database, region="AFRICA", direction="inbound",
                    channel="chat", consent="unknown", country_code="NG").allowed


def test_explicit_override_is_logged_and_only_applies_to_configured_custom_block(policy_database):
    grant(policy_database, "email")
    denied = evaluate(policy_database, region="US", direction="outbound",
                      channel="email", consent="granted", create_override_profile=True)
    assert not denied.allowed
    allowed = evaluate(policy_database, region="US", direction="outbound",
                       channel="email", consent="granted", override_actor="admin-1",
                       override_reason="approved exception")
    assert allowed.allowed and allowed.overridden
    with Session(policy_database) as session:
        row = session.exec(select(PolicyDecisionLog).order_by(
            PolicyDecisionLog.created_at.desc(), PolicyDecisionLog.id.desc())).first()
        assert row.overridden is True
        assert row.override_actor == "admin-1"
        assert row.override_reason == "approved exception"


def test_policy_decisions_cannot_override_revoked_consent(policy_database):
    with UnitOfWork(lambda: Session(policy_database)) as uow:
        ConsentService().revoke(
            uow, tenant_id="tenant-policy", business_id="business-policy",
            contact_id="contact-policy", channel="email",
        )
    result = evaluate(policy_database, region="US", direction="outbound",
                      channel="email", consent="granted", create_override_profile=True,
                      override_actor="admin-1", override_reason="cannot override consent")
    assert not result.allowed
    assert not result.overridden
