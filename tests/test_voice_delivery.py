from types import SimpleNamespace

import pytest
from datetime import UTC, datetime, timedelta
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.persistence.models import Business, Contact, QueueTaskRecord, Tenant
from app.persistence.unit_of_work import UnitOfWork
from app.services.voice_delivery_service import VoiceDeliveryBlocked, VoiceDeliveryService
from app.security import AuthenticatedPrincipal
from app.main import app
from app.routes import provider_ingress
from app.routes.voice import get_voice_delivery_service
from app.auth.dependencies import get_authenticated_principal
from app.queue.contracts import QueueDomain, QueueTaskView, TaskStatus
from uuid import uuid4


NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


@pytest.fixture
def voice_store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="A"))
        session.flush()
        session.add(Business(id="business-a", tenant_id="tenant-a", name="A", vertical="general"))
        session.flush()
        session.add(Contact(id="contact-a", tenant_id="tenant-a", business_id="business-a"))
        session.commit()
    yield engine, lambda: UnitOfWork(lambda: Session(engine))
    engine.dispose()


def principal(*roles, tenant="tenant-a"):
    return AuthenticatedPrincipal("actor-a", tenant, tuple(roles), "test",
                                  NOW - timedelta(minutes=1))


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed

    def evaluate(self, *_args, **_kwargs):
        return SimpleNamespace(allowed=self.allowed, reason_code="consent_missing")


def test_voice_request_persists_idempotent_scoped_queue_task(voice_store):
    engine, factory = voice_store
    with Session(engine) as session:
        contact = session.get(Contact, "contact-a")
        contact.phone = "+15555550123"
        session.add(contact)
        session.commit()

    service = VoiceDeliveryService(uow_factory=factory, provider=None, policy_service=Policy())
    actor = principal("campaign_writer")
    queued = service.enqueue(actor, business_id="business-a", contact_id="contact-a",
                             idempotency_key="test-call-1")
    replay = service.enqueue(actor, business_id="business-a", contact_id="contact-a",
                             idempotency_key="test-call-1")

    assert queued.id == replay.id
    with Session(engine) as session:
        row = session.exec(select(QueueTaskRecord).where(QueueTaskRecord.id == queued.id)).one()
    assert row.tenant_id == "tenant-a"
    assert row.business_id == "business-a"
    assert row.task_name == "voice.initiate_call"
    assert row.payload == {"contact_id": "contact-a"}
    assert row.max_attempts == 1


def test_voice_enqueue_fails_closed_for_role_scope_and_policy(voice_store):
    engine, factory = voice_store
    with Session(engine) as session:
        contact = session.get(Contact, "contact-a")
        contact.phone = "+15555550123"
        session.add(contact)
        session.commit()
    service = VoiceDeliveryService(uow_factory=factory, provider=None, policy_service=Policy())
    with pytest.raises(VoiceDeliveryBlocked, match="authorization denied"):
        service.enqueue(principal("campaign_reader"), business_id="business-a",
                        contact_id="contact-a", idempotency_key="unauthorized")
    with pytest.raises(VoiceDeliveryBlocked, match="unavailable in authorized scope"):
        service.enqueue(principal("campaign_writer"), business_id="other-business",
                        contact_id="contact-a", idempotency_key="wrong-scope")

    denied = VoiceDeliveryService(uow_factory=factory, provider=None, policy_service=Policy(False))
    with pytest.raises(VoiceDeliveryBlocked, match="consent_missing"):
        denied.enqueue(principal("campaign_writer"), business_id="business-a",
                        contact_id="contact-a", idempotency_key="blocked")


def test_voice_worker_reloads_destination_and_uses_trusted_scope(voice_store):
    engine, factory = voice_store
    with Session(engine) as session:
        contact = session.get(Contact, "contact-a")
        contact.phone = "+15555550123"
        session.add(contact)
        session.commit()

    class Provider:
        def __init__(self):
            self.call = None

        def initiate_call(self, **kwargs):
            self.call = kwargs
            return "CA-test"

    provider = Provider()
    service = VoiceDeliveryService(uow_factory=factory, provider=provider,
                                   policy_service=Policy())
    worker = AuthenticatedPrincipal("worker:test", "tenant-a", ("campaign_writer",),
                                    "internal_worker", NOW)
    call_id = service.initiate(worker, business_id="business-a", contact_id="contact-a",
                               idempotency_key="voice:task-1")
    assert call_id == "CA-test"
    assert provider.call == {
        "tenant_id": "tenant-a", "business_id": "business-a",
        "recipient": "+15555550123", "idempotency_key": "voice:task-1",
        "region_id": None,
    }


def test_outbound_twiml_requires_twilio_signature_and_escapes_configured_text(monkeypatch):
    callback = ("https://agent.example/webhooks/call/twiml"
                "?tenant_id=tenant-a&business_id=business-a")
    monkeypatch.setattr(provider_ingress, "_scoped_config", lambda *_: {
        "outbound_call_twiml_url": callback,
        "outbound_call_message": "Hello <customer> & welcome",
        "auth_token": "test-auth-token",
    })
    params = {"CallSid": "CA-test", "Direction": "outbound-api", "To": "+15555550123"}
    signature = RequestValidator("test-auth-token").compute_signature(callback, params)
    with TestClient(app) as client:
        accepted = client.post(
            "/webhooks/call/twiml?tenant_id=tenant-a&business_id=business-a",
            data=params, headers={"X-Twilio-Signature": signature},
        )
        rejected = client.post(
            "/webhooks/call/twiml?tenant_id=tenant-a&business_id=business-a",
            data=params, headers={"X-Twilio-Signature": "invalid"},
        )
    assert accepted.status_code == 200
    assert "Hello &lt;customer&gt; &amp; welcome" in accepted.text
    assert rejected.status_code == 403


def test_authenticated_voice_endpoint_persists_request_without_accepting_destination():
    class Service:
        def enqueue(self, actor, *, business_id, contact_id, idempotency_key):
            assert actor.tenant_id == "tenant-a"
            assert (business_id, contact_id, idempotency_key) == (
                "business-a", "contact-a", "request-1",
            )
            return QueueTaskView(
                id=uuid4(), tenant_id="tenant-a", business_id=business_id,
                domain=QueueDomain.OUTREACH, task_name="voice.initiate_call",
                idempotency_key="voice:request-1", status=TaskStatus.QUEUED,
                attempts=0, max_attempts=1, available_at=NOW,
                correlation_id="voice:request-1",
            )

    app.dependency_overrides[get_authenticated_principal] = lambda: principal("campaign_writer")
    app.dependency_overrides[get_voice_delivery_service] = lambda: Service()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/voice/calls", headers={"Idempotency-Key": "request-1"},
                json={"business_id": "business-a", "contact_id": "contact-a"},
            )
            invalid = client.post(
                "/voice/calls", headers={"Idempotency-Key": "request-2"},
                json={"business_id": "business-a", "contact_id": "contact-a",
                      "to": "+15555550123"},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 202
    assert invalid.status_code == 422
