from datetime import UTC, datetime

from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select
from fastapi.testclient import TestClient

from app.integrations.calendar.google import GoogleCalendarProvider
from app.integrations.factory import ProviderAdapterFactory, TenantScopedMessageSender
from app.integrations.provider_support import MappingTenantConfig
from app.integrations.voice.twilio import TwilioVoiceProvider
from app.persistence.models import Business, ConsentRecord, Contact, ProviderWebhookEvent, Tenant
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.services.voice_delivery_service import VoiceDeliveryBlocked, VoiceDeliveryService


class Audit:
    def __init__(self): self.events = []
    def emit(self, event): self.events.append(event)


class Transport:
    def __init__(self, responses=None): self.calls, self.responses = [], list(responses or [])
    def request(self, method, url, *, headers, body=None, timeout=10.0):
        self.calls.append((method, url, dict(headers), body, timeout))
        return self.responses.pop(0) if self.responses else (200, {"items": [], "sid": "CA123"})


def test_google_calendar_availability_uses_tenant_calendar_token_and_bounds():
    transport, audit = Transport(), Audit()
    provider = GoogleCalendarProvider(
        configs=MappingTenantConfig({("t1", "b1"): {
            "access_token": "access-t1", "calendar_id": "cal-1",
        }}), audit_sink=audit, transport=transport,
    )
    result = provider.availability(tenant_id="t1", business_id="b1",
                                   time_min="2026-10-07T09:00:00Z", time_max="2026-10-07T17:00:00Z")
    assert result == []
    assert transport.calls[0][1].startswith("https://www.googleapis.com/calendar/v3/calendars/cal-1/events?")
    assert transport.calls[0][2]["Authorization"] == "Bearer access-t1"


def test_twilio_voice_call_is_tenant_scoped_and_retry_keyed():
    transport = Transport()
    provider = TwilioVoiceProvider(configs=MappingTenantConfig({("t1", "b1"): {
        "account_sid": "AC123", "auth_token": "private", "from_call": "+15550000001",
    }}), audit_sink=Audit(), transport=transport)
    call_sid = provider.initiate_call(tenant_id="t1", business_id="b1",
        recipient="+15559990001", callback_url="https://app.example.test/webhooks/call?tenant_id=t1&business_id=b1",
        idempotency_key="call-1")
    assert call_sid == "CA123"
    assert transport.calls[0][2]["Idempotency-Key"] == "call-1"
    assert "/Accounts/AC123/Calls.json" in transport.calls[0][1]


def test_sendgrid_email_enable_tracking_and_preserve_idempotency_key():
    import json
    from app.integrations.email.sendgrid import SendGridEmailSender
    transport, audit = Transport([(202, {"message_id": "mail-1"})]), Audit()
    provider = SendGridEmailSender(configs=MappingTenantConfig({("t1", "b1"): {
        "api_key": "protected", "from_address": "team@example.test",
    }}), audit_sink=audit, transport=transport)
    result = provider.send(tenant_id="t1", business_id="b1", channel="email",
        recipient="person@example.test", content={"subject": "Hi", "body": "Hello"},
        idempotency_key="message-1")
    payload = json.loads(transport.calls[0][3])
    assert result == "mail-1"
    assert transport.calls[0][2]["Idempotency-Key"] == "message-1"
    assert payload["tracking_settings"]["open_tracking"]["enable"]
    assert payload["tracking_settings"]["click_tracking"]["enable"]


def test_twilio_retry_after_provider_failure_reuses_durable_key():
    import pytest
    from app.integrations.sms.twilio import TwilioMessageSender
    transport, audit = Transport([(503, {}), (201, {"sid": "SM123"})]), Audit()
    sender = TwilioMessageSender(configs=MappingTenantConfig({("t1", "b1"): {
        "account_sid": "AC123", "auth_token": "private", "from_sms": "+15550000001",
    }}), audit_sink=audit, transport=transport)
    kwargs = dict(tenant_id="t1", business_id="b1", channel="sms", recipient="+15559990001",
                  content={"body": "Hello"}, idempotency_key="durable-message-1")
    with pytest.raises(RuntimeError):
        sender.send(**kwargs)
    assert sender.send(**kwargs) == "SM123"
    assert [call[2]["Idempotency-Key"] for call in transport.calls] == [
        "durable-message-1", "durable-message-1",
    ]
    assert [entry.outcome for entry in audit.events] == ["failed", "succeeded"]


def test_adapter_factory_selects_by_scoped_business_configuration():
    config = MappingTenantConfig({
        ("tenant-a", "business-a"): {"sms_provider": "twilio", "account_sid": "AC123",
                                       "auth_token": "private", "from_sms": "+15550000001"},
        ("tenant-b", "business-b"): {"sms_provider": "twilio"},
    })
    factory = ProviderAdapterFactory(configs=config, audit_sink=Audit(), transport=Transport())
    sender = TenantScopedMessageSender(factory)
    sender.send(tenant_id="tenant-a", business_id="business-a", channel="sms",
                recipient="+15550000002", content={"body": "test"}, idempotency_key="message-1")
    assert "Accounts" in factory.transport.calls[0][1]
    try:
        factory.build("sms", tenant_id="tenant-c", business_id="business-c")
    except Exception as exc:
        assert "configuration is unavailable" in str(exc)
    else:
        raise AssertionError("missing scoped provider configuration must fail closed")


def test_provider_webhook_records_are_tenant_scoped_and_idempotent():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-webhook", name="Webhook tenant", region="US"))
        session.flush()
        session.add(Business(id="business-webhook", tenant_id="tenant-webhook", name="Webhook business",
                             vertical="general", region="US"))
        session.commit()
    with UnitOfWork(lambda: Session(engine)) as uow:
        first, created = uow.provider_webhooks.record(
            tenant_id="tenant-webhook", business_id="business-webhook", provider="sendgrid",
            event_key="event-1", event_type="email.open",
            payload={"sg_event_id": "event-1", "event": "open"},
            status="processed", result={"accepted": True},
        )
        assert created
        first_id = first.id
    with UnitOfWork(lambda: Session(engine)) as uow:
        repeated, created = uow.provider_webhooks.record(
            tenant_id="tenant-webhook", business_id="business-webhook", provider="sendgrid",
            event_key="event-1", event_type="email.open",
            payload={"sg_event_id": "event-1", "event": "open"},
        )
        assert not created and repeated.id == first_id
    with Session(engine) as session:
        row = session.exec(select(ProviderWebhookEvent)).one()
        assert row.status == "processed" and row.result == {"accepted": True}
    engine.dispose()


def test_inbound_sms_webhook_verifies_then_enqueues_once(monkeypatch):
    import app.routes.provider_ingress as ingress
    from app.main import app

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-sms-hook", name="SMS tenant", region="US"))
        session.flush()
        session.add(Business(id="business-sms-hook", tenant_id="tenant-sms-hook", name="SMS business",
                             vertical="general", region="US"))
        session.flush()
        session.add(Contact(id="contact-sms-hook", tenant_id="tenant-sms-hook",
                            business_id="business-sms-hook", phone="+15559990001"))
        session.commit()

    class Resolver:
        def __init__(self, _): pass
        def resolve(self, *, tenant_id, business_id):
            assert tenant_id == "tenant-sms-hook" and business_id == "business-sms-hook"
            return {"account_sid": "AC123", "auth_token": "token",
                    "from_sms": "+15550000001",
                    "sms_inbound_webhook_url": "https://example.test/webhooks/sms?tenant_id=tenant-sms-hook&business_id=business-sms-hook"}

    monkeypatch.setattr(ingress.settings, "provider_secrets_dir", __import__("pathlib").Path("secrets"))
    monkeypatch.setattr(ingress, "DirectoryTenantConfigResolver", Resolver)
    monkeypatch.setattr(ingress, "validate_twilio_signature", lambda **kwargs: True)
    monkeypatch.setattr(ingress, "session_factory", lambda: Session(engine))
    client = TestClient(app)
    url = "/webhooks/sms?tenant_id=tenant-sms-hook&business_id=business-sms-hook"
    payload = {"AccountSid": "AC123", "From": "+15559990001", "To": "+15550000001",
               "MessageSid": "SM" + "a" * 32, "Body": "Hello"}
    first = client.post(url, data=payload, headers={"X-Twilio-Signature": "verified"})
    second = client.post(url, data=payload, headers={"X-Twilio-Signature": "verified"})
    assert first.status_code == second.status_code == 202
    assert first.json()["duplicate"] is False and second.json()["duplicate"] is True
    optout = dict(payload, MessageSid="SM" + "b" * 32, Body="STOP")
    stopped = client.post(url, data=optout, headers={"X-Twilio-Signature": "verified"})
    assert stopped.status_code == 202 and stopped.json()["consent_status"] == "revoked"
    with Session(engine) as session:
        assert session.exec(select(ConsentRecord)).one().status == "revoked"
    engine.dispose()


def test_voice_policy_block_happens_before_provider_call():
    import pytest
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-call", name="Call tenant", region="US"))
        session.flush()
        session.add(Business(id="business-call", tenant_id="tenant-call", name="Call business",
                             vertical="general", region="US"))
        session.flush()
        session.add(Contact(id="contact-call", tenant_id="tenant-call", business_id="business-call",
                            phone="+15551112222", consent_status="unknown"))
        session.commit()
    class Provider:
        def __init__(self): self.calls = []
        def initiate_call(self, **kwargs): self.calls.append(kwargs); return "CA1"
    provider = Provider()
    service = VoiceDeliveryService(uow_factory=lambda: UnitOfWork(lambda: Session(engine)), provider=provider)
    principal = AuthenticatedPrincipal("writer", "tenant-call", ("campaign_writer",),
                                       "test", datetime.now(UTC))
    with pytest.raises(VoiceDeliveryBlocked, match="consent_unknown"):
        service.initiate(principal, business_id="business-call", contact_id="contact-call",
                         callback_url="https://example.test/webhooks/call", idempotency_key="call-1")
    assert provider.calls == []
    engine.dispose()
