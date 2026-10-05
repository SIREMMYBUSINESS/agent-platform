from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from app.integrations.crm.events import CRMEventType
from app.integrations.crm.service import CRMIntegrationService
from app.integrations.crm.sync_engine import CRMConflict, CRMNotFound, CRMSyncEngine
from app.persistence.crm_repositories import SQLCRMSyncRepository
from app.persistence.models import Contact, ConsentRecord, CRMSyncRecord, SuppressionRule
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal

pytest_plugins = ["test_campaigns"]


NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


class FakeCRM:
    provider_key = "fake_crm"

    def __init__(self):
        self.calls = []
        self.fail_next = False

    def _call(self, method, data, idempotency_key):
        self.calls.append((method, data, idempotency_key))
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("provider payload with private data")
        return {"id": "external-contact-1", "updated_at": NOW.isoformat()}

    def create_contact(self, contact, *, tenant_id, business_id, idempotency_key):
        return self._call("create_contact", dict(contact), idempotency_key)

    def update_contact(self, external_id, contact, *, tenant_id, business_id, idempotency_key):
        return self._call("update_contact", {"id": external_id, **dict(contact)}, idempotency_key)

    def get_contact(self, external_id, *, tenant_id, business_id):
        return {"id": external_id}

    def create_activity(self, activity, *, tenant_id, business_id, idempotency_key):
        return self._call("create_activity", dict(activity), idempotency_key)

    def create_conversation_note(self, external_contact_id, note, *, tenant_id, business_id, idempotency_key):
        return self._call("create_conversation_note",
                          {"external_contact_id": external_contact_id, **dict(note)}, idempotency_key)


@pytest.fixture
def crm(campaign_client):
    _, engine, _ = campaign_client
    uow_factory = lambda: UnitOfWork(lambda: Session(engine))
    provider = FakeCRM()
    service = CRMIntegrationService(CRMSyncEngine(
        provider=provider, uow_factory=uow_factory, business_id="business-a",
        max_attempts=3, now_provider=lambda: NOW,
    ))
    principal = AuthenticatedPrincipal(
        "crm-actor", "tenant-a", ("crm_sync",), "test",
        NOW - timedelta(minutes=1), NOW + timedelta(days=1),
    )
    yield engine, service, principal, provider, uow_factory


def test_contact_sync_includes_consent_and_suppression_and_is_idempotent(crm):
    engine, service, principal, provider, _ = crm
    with Session(engine) as session:
        session.add(ConsentRecord(tenant_id="tenant-a", business_id="business-a",
                                  contact_id="contact-a", channel="email", status="granted",
                                  source="trusted_test"))
        session.add(SuppressionRule(tenant_id="tenant-a", business_id="business-a",
                                    contact_id="contact-a", reason_code="do_not_contact"))
        session.commit()
    first = service.sync_contact(principal, "contact-a", idempotency_key="contact:1",
                                 event_type=CRMEventType.CONTACT_CREATED)
    replay = service.sync_contact(principal, "contact-a", idempotency_key="contact:1",
                                  event_type=CRMEventType.CONTACT_CREATED)
    updated = service.sync_contact(principal, "contact-a", idempotency_key="contact:2",
                                   event_type=CRMEventType.CONTACT_UPDATED)
    assert updated.last_error is None, updated.last_error
    assert first.status == replay.status == updated.status == "synced"
    assert first.external_id == "external-contact-1"
    assert len(provider.calls) == 2
    assert provider.calls[1][0] == "update_contact"
    outbound = provider.calls[0][1]
    assert outbound["consent_status"]["email"] == "granted"
    assert outbound["suppressed"] is True


def test_conversation_close_and_campaign_delivery_sync_as_activities(crm):
    engine, service, principal, provider, uow_factory = crm
    with uow_factory() as uow:
        conversation = uow.conversations.create(
            tenant_id="tenant-a", business_id="business-a", contact_id="contact-a", channel="chat",
        )
        campaign = uow.campaigns.create(
            tenant_id="tenant-a", business_id="business-a", name="CRM test", channel="email",
            audience_filters={},
        )
        conversation_id = conversation.id
        campaign_id = campaign.id
    service.sync_conversation(principal, conversation_id, idempotency_key="conversation:created",
                              event_type=CRMEventType.CONVERSATION_CREATED)
    service.sync_conversation(principal, conversation_id, idempotency_key="conversation:closed",
                              event_type=CRMEventType.CONVERSATION_CLOSED)
    service.sync_conversation(principal, conversation_id, idempotency_key="conversation:follow-up",
                              event_type=CRMEventType.FOLLOW_UP_SCHEDULED,
                              payload={"scheduled_at": NOW.isoformat()})
    service.sync_conversation(principal, conversation_id, idempotency_key="conversation:outreach",
                              event_type=CRMEventType.OUTREACH_ACTIVITY)
    service.sync_campaign_event(principal, campaign_id, idempotency_key="campaign:created",
                                event_type=CRMEventType.CAMPAIGN_CREATED)
    service.sync_campaign_event(principal, campaign_id, idempotency_key="campaign:delivery",
                                event_type=CRMEventType.DELIVERY_RESULT,
                                payload={"result": "delivered"})
    service.sync_campaign_event(principal, campaign_id, idempotency_key="campaign:engagement",
                                event_type=CRMEventType.ENGAGEMENT,
                                payload={"type": "reply", "tenant_id": "tenant-b",
                                         "campaign_id": "spoofed-campaign"})
    assert [call[0] for call in provider.calls] == [
        "create_conversation_note", "create_conversation_note", "create_activity",
        "create_activity", "create_activity", "create_activity", "create_activity",
    ]
    last_activity = provider.calls[-1][1]
    assert last_activity["campaign_id"] == str(campaign_id)
    assert "tenant_id" not in last_activity


def test_inbound_contact_sync_ignores_payload_tenant_and_applies_status(crm):
    engine, service, principal, _, _ = crm
    result = service.sync_inbound_contact(principal, {
        "id": "remote-77", "tenant_id": "tenant-b", "business_id": "business-b",
        "name": "Remote Contact", "email": "remote@example.test",
        "consent_status": {"email": "revoked"}, "suppressed": True,
    }, idempotency_key="inbound:remote-77:v1")
    replay = service.sync_inbound_contact(principal, {
        "id": "remote-77", "tenant_id": "tenant-b", "name": "Changed on replay",
    }, idempotency_key="inbound:remote-77:v1")
    assert result.status == replay.status == "synced"
    assert result.entity_id == replay.entity_id
    with Session(engine) as session:
        contact = session.get(Contact, result.entity_id)
        assert contact.tenant_id == "tenant-a" and contact.business_id == "business-a"
        assert contact.full_name == "Remote Contact"
        assert session.exec(select(ConsentRecord).where(
            ConsentRecord.contact_id == contact.id,
        )).one().status == "revoked"
        assert session.exec(select(SuppressionRule).where(
            SuppressionRule.contact_id == contact.id,
        )).one().active is True


def test_provider_failure_can_be_retried_and_same_key_does_not_duplicate(crm):
    _, service, principal, provider, _ = crm
    provider.fail_next = True
    failed = service.sync_contact(principal, "contact-a", idempotency_key="contact:retry")
    assert failed.status == "failed" and failed.attempts == 1
    assert failed.last_error == "provider_error:RuntimeError"
    retried = service.retry(principal, failed.id)
    assert retried.status == "synced" and retried.attempts == 2
    replay = service.sync_contact(principal, "contact-a", idempotency_key="contact:retry")
    assert replay.status == "synced"
    assert len(provider.calls) == 2


def test_inbound_conflict_is_recorded_without_overwriting_newer_local_contact(crm):
    engine, service, principal, _, _ = crm
    first = service.sync_inbound_contact(principal, {
        "id": "remote-conflict", "name": "Original",
        "updated_at": (NOW - timedelta(days=2)).isoformat(),
    }, idempotency_key="inbound:conflict:1")
    with Session(engine) as session:
        contact = session.get(Contact, first.entity_id)
        contact.full_name = "Local edit"
        contact.updated_at = datetime.now(UTC) + timedelta(days=1)
        session.add(contact)
        session.commit()
    conflict = service.sync_inbound_contact(principal, {
        "id": "remote-conflict", "name": "Stale remote edit",
        "updated_at": (NOW - timedelta(days=3)).isoformat(),
    }, idempotency_key="inbound:conflict:2")
    assert conflict.status == "conflict"
    with Session(engine) as session:
        assert session.get(Contact, first.entity_id).full_name == "Local edit"


def test_cross_tenant_contact_is_hidden_from_sync(crm):
    _, service, _, _, _ = crm
    other = AuthenticatedPrincipal("other", "tenant-b", ("crm_sync",), "test",
                                   NOW - timedelta(minutes=1), NOW + timedelta(days=1))
    with pytest.raises(CRMNotFound):
        service.sync_contact(other, "contact-a", idempotency_key="cross-tenant")


def test_inbound_transaction_rolls_back_contact_when_sync_audit_fails(crm, monkeypatch):
    engine, service, principal, _, _ = crm
    original = SQLCRMSyncRepository.set_result

    def fail_result(self, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(SQLCRMSyncRepository, "set_result", fail_result)
    with pytest.raises(RuntimeError, match="audit write failed"):
        service.sync_inbound_contact(principal, {
            "id": "rollback-contact", "name": "Must roll back",
        }, idempotency_key="inbound:rollback")
    monkeypatch.setattr(SQLCRMSyncRepository, "set_result", original)
    with Session(engine) as session:
        assert session.exec(select(Contact).where(Contact.full_name == "Must roll back")).first() is None
        assert session.exec(select(CRMSyncRecord).where(
            CRMSyncRecord.idempotency_key == "inbound:rollback",
        )).first() is None
