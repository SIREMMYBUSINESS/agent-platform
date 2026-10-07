from __future__ import annotations

from datetime import UTC, datetime, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.persistence.repositories import EntityNotFoundError
from app.schemas import InboundRequest
from app.services.inbound import (
    InboundProvisioningError,
    InboundService,
    InboundTenantMismatchError,
)


class FakeRepositories:
    def __init__(self, *, provisioned: bool = True, consent_status="granted"):
        self.provisioned = provisioned
        self.consent_status = consent_status
        self.calls = []
        self.conversation = SimpleNamespace(
            id=uuid4(), tenant_id="trusted-tenant", business_id="business-a",
            contact_id="contact-a", correlation_id=None,
        )
        self.tenants = self.businesses = self.contacts = self
        self.conversations = self.messages = self.workflows = self.events = self
        self.routing_rules = self
        self.services = SimpleNamespace(list_active=lambda **kwargs: [])
        self.consent_records = SimpleNamespace(current=lambda **kwargs: None)
        self.contact_channel_consents = SimpleNamespace(get=lambda **kwargs: None)
        self.policy_profiles = SimpleNamespace(get=lambda **kwargs: None)
        self.country_policies = SimpleNamespace(get=lambda **kwargs: None)
        self.suppressions = SimpleNamespace(find_active=lambda **kwargs: None)
        self.policy_decisions = SimpleNamespace(add=lambda **kwargs: self.calls.append(("policy_decision", kwargs)))
        self.optimization = SimpleNamespace(
            add_feedback=lambda **kwargs: self.calls.append(("feedback", kwargs))
            or SimpleNamespace(id=uuid4()),
        )

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        if not self.provisioned:
            return None
        if "tenant_id" in kwargs and len(kwargs) == 1:
            return SimpleNamespace(id=kwargs["tenant_id"], default_timezone="UTC")
        if "business_id" in kwargs:
            return SimpleNamespace(
                id=kwargs["business_id"], tenant_id="trusted-tenant",
                region="US", timezone="UTC"
            )
        if "contact_id" in kwargs:
            return SimpleNamespace(
                id=kwargs["contact_id"], tenant_id="trusted-tenant", business_id="business-a",
                consent_status=self.consent_status, timezone="UTC",
            )
        if "conversation_id" in kwargs:
            return self.conversation if kwargs["conversation_id"] == self.conversation.id else None

    def get_by_correlation_id(self, **kwargs):
        return None

    def list_for_business(self, **kwargs):
        return [SimpleNamespace(
            weekday=3, opens_at=time(0), closes_at=time(23, 59),
            after_hours_action="callback", active=True,
        )]

    def exists(self, **kwargs):
        return True

    def create(self, **kwargs):
        self.calls.append(("conversation.create", kwargs))
        self.conversation.correlation_id = kwargs.get("correlation_id")
        return self.conversation

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))
        if kwargs.get("direction") == "outbound":
            return SimpleNamespace(id=uuid4())

    def set_delivery(self, **kwargs):
        self.calls.append(("message.delivery", kwargs))
        return SimpleNamespace(id=kwargs["message_id"], delivery_status=kwargs["status"])

    def append(self, **kwargs):
        self.calls.append(("workflow.append", kwargs))


class FakeUnitOfWork:
    def __init__(self, repositories):
        self.__dict__.update(repositories.__dict__)
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.rolled_back = exc_type is not None
        self.committed = exc_type is None


def request(**updates):
    payload = {
        "tenant_id": "body-tenant",
        "business_id": "business-a",
        "contact_id": "contact-a",
        "channel": "chat",
        "text": "What are your business hours?",
        "contact_name": "Alex",
        "consent_status": "granted",
        "metadata": {"region": "US"},
    }
    payload.update(updates)
    return InboundRequest.model_validate(payload)


def test_service_uses_trusted_tenant_and_persists_successful_flow():
    repos = FakeRepositories()
    uow = FakeUnitOfWork(repos)
    service = InboundService(
        trusted_tenant_id="trusted-tenant", uow_factory=lambda: uow,
        now_provider=lambda: datetime(2026, 9, 24, 10, tzinfo=UTC),
    )

    result = service.handle(request(tenant_id="trusted-tenant"))

    assert result["success"] is True
    assert result["workflow_state"] == "business_hours_checked"
    assert uow.committed is True
    assert uow.rolled_back is False
    assert all(
        call[1].get("tenant_id") == "trusted-tenant"
        for call in repos.calls
        if isinstance(call[1], dict) and "tenant_id" in call[1]
    )
    assert len([call for call in repos.calls if call[0] == "conversation.create"]) == 1
    message_calls = [call[1] for call in repos.calls if call[0] == "add" and "direction" in call[1]]
    assert [message["direction"] for message in message_calls] == ["inbound", "outbound"]
    event_calls = [call[1] for call in repos.calls if call[0] == "add" and "event_type" in call[1]]
    assert "intake.received" in [item["event_type"] for item in event_calls]


def test_service_rejects_request_tenant_mismatch_before_opening_uow():
    calls = []
    service = InboundService(
        trusted_tenant_id="trusted-tenant", uow_factory=lambda: calls.append("opened")
    )

    with pytest.raises(InboundTenantMismatchError):
        service.handle(request(tenant_id="attacker-tenant"))

    assert calls == []


def test_service_does_not_provision_missing_records():
    repos = FakeRepositories(provisioned=False)
    uow = FakeUnitOfWork(repos)
    service = InboundService(trusted_tenant_id="trusted-tenant", uow_factory=lambda: uow)

    with pytest.raises(InboundProvisioningError):
        service.handle(request(tenant_id="trusted-tenant"))

    assert not any(call[0] == "conversation.create" for call in repos.calls)
    assert uow.rolled_back is True


def test_service_policy_rejection_persists_inbound_then_marks_blocked():
    repos = FakeRepositories(consent_status="denied")
    uow = FakeUnitOfWork(repos)
    service = InboundService(trusted_tenant_id="trusted-tenant", uow_factory=lambda: uow)

    result = service.handle(
        request(tenant_id="trusted-tenant", channel="sms", consent_status="granted")
    )

    assert result["success"] is False
    assert result["workflow_state"] == "blocked"
    assert uow.committed is True
    assert len([call for call in repos.calls if call[0] == "workflow.append"]) == 2
    assert not any(
        call[0] == "add" and call[1].get("direction") == "outbound" for call in repos.calls
    )


def test_policy_region_comes_from_provisioned_business_not_request_metadata():
    repos = FakeRepositories(consent_status="denied")
    def get(**kwargs):
        value = FakeRepositories.get(repos, **kwargs)
        if "business_id" in kwargs:
            value.region = "CA"
        return value
    repos.get = get
    service = InboundService(
        trusted_tenant_id="trusted-tenant", uow_factory=lambda: FakeUnitOfWork(repos),
        now_provider=lambda: datetime(2026, 9, 24, 10, tzinfo=UTC),
    )

    result = service.handle(
        request(tenant_id="trusted-tenant", channel="email", consent_status="denied",
                metadata={"region": "US"})
    )

    assert result["success"] is False
    assert result["reason"] == "email requires consent in CA: casl_consent_required"
