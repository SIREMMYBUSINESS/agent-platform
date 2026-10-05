from urllib.parse import parse_qs

import pytest

from app.integrations.crm.hubspot import HubSpotCRMProvider
from app.integrations.crm.salesforce import SalesforceCRMProvider
from app.integrations.email.smtp import SMTPEmailSender
from app.integrations.messaging.router import MultiChannelMessageSender
from app.integrations.provider_support import (
    MappingTenantConfig, ProviderAuditEvent,
)
from app.integrations.sms.twilio import TwilioMessageSender


class AuditCollector:
    def __init__(self):
        self.events = []

    def emit(self, event: ProviderAuditEvent):
        self.events.append(event)


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.messages = []
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def starttls(self):
        self.tls = True

    def login(self, username, password):
        self.login_data = (username, password)

    def send_message(self, message):
        self.messages.append(message)


class FakeHTTP:
    def __init__(self):
        self.calls = []
        self.responses = []

    def request(self, method, url, *, headers, body=None, timeout=10.0):
        self.calls.append((method, url, dict(headers), body, timeout))
        if self.responses:
            return self.responses.pop(0)
        return 201, {"id": "remote-1", "updatedAt": "2026-10-05T12:00:00Z"}


def test_smtp_sender_uses_tenant_config_stable_message_id_and_audit():
    FakeSMTP.instances.clear()
    audit = AuditCollector()
    configs = MappingTenantConfig({("tenant-a", "business-a"): {
        "host": "smtp.example.test", "port": "587", "from_address": "team@example.test",
        "username": "tenant-a-user", "password": "private", "starttls": "true",
    }})
    sender = SMTPEmailSender(configs=configs, audit_sink=audit, smtp_factory=FakeSMTP)
    kwargs = dict(tenant_id="tenant-a", business_id="business-a", channel="email",
                  recipient="person@example.test", content={"subject": "Hello", "body": "World"},
                  idempotency_key="email-key-1")
    sender.send(**kwargs)
    sender.send(**kwargs)
    messages = [smtp.messages[0] for smtp in FakeSMTP.instances]
    assert messages[0]["Message-ID"] == messages[1]["Message-ID"]
    assert messages[0]["X-Idempotency-Key"] == "email-key-1"
    assert FakeSMTP.instances[0].tls is True
    assert [event.outcome for event in audit.events] == ["succeeded", "succeeded"]
    assert all(event.tenant_id == "tenant-a" for event in audit.events)


def test_smtp_adapter_fails_closed_without_tenant_business_credentials():
    sender = SMTPEmailSender(configs=MappingTenantConfig({}), audit_sink=AuditCollector(),
                             smtp_factory=FakeSMTP)
    with pytest.raises(Exception, match="smtp_error:ProviderConfigurationError"):
        sender.send(tenant_id="tenant-b", business_id="business-b", channel="email",
                    recipient="p@example.test", content={"body": "Hello"},
                    idempotency_key="tenant-b-key")


def test_twilio_routes_sms_and_whatsapp_with_scoped_auth_and_retry_key():
    audit = AuditCollector()
    transport = FakeHTTP()
    configs = MappingTenantConfig({("tenant-a", "business-a"): {
        "account_sid": "AC123", "auth_token": "secret",
        "from_sms": "+15550000001", "from_whatsapp": "+15550000002",
    }})
    sender = TwilioMessageSender(configs=configs, audit_sink=audit, transport=transport)
    for channel, recipient in (("sms", "+15559990001"), ("whatsapp", "+15559990002")):
        sender.send(tenant_id="tenant-a", business_id="business-a", channel=channel,
                    recipient=recipient, content={"body": "Hello"},
                    idempotency_key=f"send-{channel}")
    sender.send(tenant_id="tenant-a", business_id="business-a", channel="sms",
                recipient="+15559990001", content={"body": "Hello"},
                idempotency_key="send-sms")
    sms, whatsapp, retried_sms = transport.calls
    assert sms[1].endswith("/Accounts/AC123/Messages.json")
    assert sms[2]["Idempotency-Key"] == "send-sms"
    assert parse_qs(sms[3].decode())["To"] == ["+15559990001"]
    assert parse_qs(whatsapp[3].decode())["To"] == ["whatsapp:+15559990002"]
    assert parse_qs(whatsapp[3].decode())["From"] == ["whatsapp:+15550000002"]
    assert retried_sms[2]["Idempotency-Key"] == sms[2]["Idempotency-Key"]
    assert [e.operation for e in audit.events] == ["send_sms", "send_whatsapp", "send_sms"]


def test_twilio_request_failure_is_sanitized_and_audited():
    audit = AuditCollector()
    transport = FakeHTTP()
    transport.responses.append((503, {"message": "contains customer data"}))
    sender = TwilioMessageSender(configs=MappingTenantConfig({("t", "b"): {
        "account_sid": "AC123", "auth_token": "secret", "from_sms": "+15550000001",
    }}), audit_sink=audit, transport=transport)
    with pytest.raises(RuntimeError, match="provider_http_error:503") as error:
        sender.send(tenant_id="t", business_id="b", channel="sms", recipient="+15559990001",
                    content={"body": "Hello"}, idempotency_key="retry-key")
    assert "customer data" not in str(error.value)
    assert audit.events[0].outcome == "failed"
    assert audit.events[0].error_code == "provider_http_error:503"


def test_channel_router_delegates_by_persisted_channel():
    class Sender:
        def __init__(self):
            self.calls = []

        def send(self, **kwargs):
            self.calls.append(kwargs)

    audit = AuditCollector()
    email, text = Sender(), Sender()
    router = MultiChannelMessageSender(email_sender=email, twilio_sender=text, audit_sink=audit)
    router.send(tenant_id="tenant-a", business_id="business-a", channel="whatsapp",
                recipient="whatsapp:+1555", content={"body": "hello"}, idempotency_key="m-1")
    assert len(email.calls) == 0 and text.calls[0]["idempotency_key"] == "m-1"


def test_hubspot_adapter_uses_tenant_token_and_documented_api_routes():
    audit = AuditCollector()
    transport = FakeHTTP()
    provider = HubSpotCRMProvider(
        configs=MappingTenantConfig({("tenant-a", "business-a"): {"access_token": "token-a"}}),
        audit_sink=audit, transport=transport,
    )
    created = provider.create_contact(
        {"full_name": "Jordan Lee", "email": "jordan@example.test", "suppressed": True},
        tenant_id="tenant-a", business_id="business-a", idempotency_key="crm-contact-1",
    )
    assert created["id"] == "remote-1"
    method, url, headers, body, _ = transport.calls[0]
    assert method == "POST" and url.endswith("/crm/objects/2026-03/contacts")
    assert headers["Authorization"] == "Bearer token-a"
    assert headers["Idempotency-Key"] == "crm-contact-1"
    assert b'"firstname":"Jordan"' in body and b'"lastname":"Lee"' in body
    assert audit.events[0].provider == "hubspot" and audit.events[0].tenant_id == "tenant-a"


def test_hubspot_reads_use_same_tenant_credentials():
    transport = FakeHTTP()
    provider = HubSpotCRMProvider(
        configs=MappingTenantConfig({("tenant-a", "business-a"): {"access_token": "token-a"}}),
        audit_sink=AuditCollector(), transport=transport,
    )
    with pytest.raises(RuntimeError, match="hubspot_error:ProviderConfigurationError"):
        provider.get_contact("contact-1", tenant_id="tenant-b", business_id="business-b")
    assert transport.calls == []


def test_hubspot_update_activity_and_conversation_note_use_provider_contract():
    transport = FakeHTTP()
    provider = HubSpotCRMProvider(
        configs=MappingTenantConfig({("tenant-a", "business-a"): {"access_token": "token-a"}}),
        audit_sink=AuditCollector(), transport=transport,
    )
    provider.update_contact("remote-contact", {"email": "jordan@example.test"},
                            tenant_id="tenant-a", business_id="business-a",
                            idempotency_key="update-1")
    provider.create_activity({"type": "campaign.failed", "campaign_id": "c-1"},
                             tenant_id="tenant-a", business_id="business-a",
                             idempotency_key="activity-1")
    provider.create_conversation_note("remote-contact", {"status": "closed"},
                                      tenant_id="tenant-a", business_id="business-a",
                                      idempotency_key="note-1")
    assert transport.calls[0][0] == "PATCH"
    assert transport.calls[0][1].endswith("/contacts/remote-contact")
    assert transport.calls[1][1].endswith("/tasks")
    assert transport.calls[2][1].endswith("/notes")
    assert b'"associationTypeId":202' in transport.calls[2][3]


def test_salesforce_contact_upsert_and_activity_are_retry_keyed_and_scoped():
    audit = AuditCollector()
    transport = FakeHTTP()
    tenant_configs = MappingTenantConfig({("tenant-a", "business-a"): {
        "instance_url": "https://tenant-a.my.salesforce.com", "access_token": "token-a",
        "api_version": "v66.0", "contact_external_id_field": "Platform_Key__c",
        "activity_external_id_field": "Platform_Key__c",
    }})
    provider = SalesforceCRMProvider(configs=tenant_configs, audit_sink=audit, transport=transport)
    contact = provider.create_contact(
        {"full_name": "Jordan Lee", "email": "jordan@example.test"},
        tenant_id="tenant-a", business_id="business-a", idempotency_key="sf-contact-key",
    )
    assert contact["id"] == "remote-1"
    assert transport.calls[0][0] == "PATCH"
    assert "/sobjects/Contact/Platform_Key__c/sf-contact-key" in transport.calls[0][1]
    assert transport.calls[0][2]["Authorization"] == "Bearer token-a"
    provider.create_activity({"type": "campaign.delivery_result"}, tenant_id="tenant-a",
                             business_id="business-a", idempotency_key="sf-activity-key")
    assert "/sobjects/Task/Platform_Key__c/sf-activity-key" in transport.calls[2][1]
    assert all(event.tenant_id == "tenant-a" for event in audit.events)


def test_salesforce_tenant_without_credentials_never_calls_transport():
    transport = FakeHTTP()
    provider = SalesforceCRMProvider(configs=MappingTenantConfig({}),
                                     audit_sink=AuditCollector(), transport=transport)
    with pytest.raises(RuntimeError, match="salesforce_error:ProviderConfigurationError"):
        provider.get_contact("id", tenant_id="tenant-x", business_id="business-x")
    assert transport.calls == []


def test_salesforce_update_read_and_conversation_note_use_tenant_account():
    transport = FakeHTTP()
    provider = SalesforceCRMProvider(configs=MappingTenantConfig({("tenant-a", "business-a"): {
        "instance_url": "https://tenant-a.my.salesforce.com", "access_token": "token-a",
        "activity_external_id_field": "Platform_Key__c",
    }}), audit_sink=AuditCollector(), transport=transport)
    provider.update_contact("0030001", {"email": "jordan@example.test"}, tenant_id="tenant-a",
                            business_id="business-a", idempotency_key="update-sf")
    provider.create_conversation_note("0030001", {"status": "closed"},
                                      tenant_id="tenant-a", business_id="business-a",
                                      idempotency_key="note-sf")
    assert "/sobjects/Contact/0030001" in transport.calls[0][1]
    assert transport.calls[1][0] == "GET"
    assert "/sobjects/Task/Platform_Key__c/note-sf" in transport.calls[2][1]
