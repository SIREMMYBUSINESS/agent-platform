from app.auth.dependencies import get_authenticated_principal
from app.security import AuthenticatedPrincipal
from datetime import datetime as AuthDatetime, UTC as AUTH_UTC
from copy import deepcopy
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, delete, select

from app.main import app, get_inbound_service
from app.persistence.models import (
    Appointment,
    AppointmentSlot,
    Business,
    Contact,
    Conversation,
    EscalationTicket,
    EventLog,
    Message,
    PolicyDecisionLog,
    RoutingRule,
    Service,
    Staff,
    Tenant,
    WorkflowStateRecord,
)
from app.persistence.unit_of_work import UnitOfWork
from app.schemas import InboundResponse
from app.services.inbound import InboundService


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda conn, record: conn.execute("PRAGMA foreign_keys=ON"))
    SQLModel.metadata.create_all(engine)
    service_id = uuid4()
    staff_id = uuid4()
    trusted_now = datetime(2026, 9, 24, 18, 0, tzinfo=UTC)  # Thursday, 11:00 in Los Angeles.
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Test tenant"))
        session.flush()
        session.add(Business(
            id="biz-1", tenant_id="tenant-1", name="Test business", vertical="general",
            timezone="America/Los_Angeles",
        ))
        session.flush()
        session.add(Contact(
            id="contact-1", tenant_id="tenant-1", business_id="biz-1", full_name="Alex",
            consent_status="granted", timezone="America/Los_Angeles",
        ))
        session.add_all([
            Staff(id=staff_id, tenant_id="tenant-1", business_id="biz-1", display_name="Taylor"),
            Service(
                id=service_id, tenant_id="tenant-1", business_id="biz-1",
                name="Consultation", duration_minutes=30,
            ),
            RoutingRule(
                tenant_id="tenant-1", business_id="biz-1", weekday=3,
                opens_at=time(9), closes_at=time(17), after_hours_action="voicemail",
            ),
        ])
        session.flush()
        session.add_all([
            AppointmentSlot(
                tenant_id="tenant-1", business_id="biz-1", staff_id=staff_id,
                service_id=service_id, starts_at=trusted_now + timedelta(days=1),
                ends_at=trusted_now + timedelta(days=1, minutes=30),
            ),
            AppointmentSlot(
                tenant_id="tenant-1", business_id="biz-1", staff_id=staff_id,
                service_id=service_id, starts_at=trusted_now + timedelta(days=2),
                ends_at=trusted_now + timedelta(days=2, minutes=30),
            ),
        ])
        session.commit()

    service = InboundService(
        trusted_tenant_id="tenant-1",
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
        now_provider=lambda: trusted_now,
    )
    app.dependency_overrides[get_inbound_service] = lambda: service
    app.state.inbound_test_engine = engine
    app.state.inbound_test_service = service
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        "test-identity", "tenant-1",
        ("inbound_writer", "lead_reader", "lead_writer", "campaign_reader", "campaign_writer"),
        "test", AuthDatetime.now(AUTH_UTC),
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        del app.state.inbound_test_engine
        del app.state.inbound_test_service
        engine.dispose()


def valid_inbound_payload():
    return {
        "tenant_id": "tenant-1",
        "business_id": "biz-1",
        "contact_id": "contact-1",
        "channel": "chat",
        "text": "I need to schedule an appointment",
        "contact_name": "Alex",
        "consent_status": "granted",
        "metadata": {"region": "US"},
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_inbound_ok(client):
    response = client.post("/inbound", json=valid_inbound_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "schedule" in body["agent_response"].casefold()


def test_normal_chat_intake_routes_business_hours_question(client):
    payload = valid_inbound_payload()
    payload["text"] = "What are your business hours?"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "business_hours_checked"
    assert "9:00 AM" in response.json()["agent_response"]


def test_malformed_inbound_json_returns_validation_error(client):
    response = client.post(
        "/inbound",
        content='{"tenant_id":',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422


def test_missing_required_inbound_field_returns_validation_error(client):
    payload = valid_inbound_payload()
    del payload["tenant_id"]

    response = client.post("/inbound", json=payload)

    assert response.status_code == 422


def test_invalid_inbound_field_type_returns_validation_error(client):
    payload = valid_inbound_payload()
    payload["channel"] = ["chat"]

    response = client.post("/inbound", json=payload)

    assert response.status_code == 422


def test_inbound_rejected_when_policy_requires_missing_consent(client):
    payload = valid_inbound_payload()
    payload.update(channel="sms", consent_status="denied")
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.consent_status = "denied"
        session.commit()

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "status": "blocked",
        "workflow_state": "blocked",
        "agent_response": None,
        "reason": "sms requires consent in US: requires_consent",
    }


def test_duplicate_inbound_requests_are_accepted(client):
    payload = valid_inbound_payload()

    first = client.post("/inbound", json=payload)
    second = client.post("/inbound", json=deepcopy(payload))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["success"] is True
    assert second.json()["success"] is True


def test_inbound_response_matches_response_schema(client):
    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 200
    parsed = InboundResponse.model_validate(response.json())
    assert parsed.success is True
    assert parsed.status == "ok"
    assert parsed.workflow_state == "appointment_proposed"
    assert parsed.agent_response is not None
    assert parsed.reason is None


def test_unexpected_extra_input_does_not_cause_server_error(client):
    payload = valid_inbound_payload()
    payload["future_extension"] = {"labels": ["priority", 3], "enabled": True}

    response = client.post("/inbound", json=payload)

    assert response.status_code < 500
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_body_tenant_cannot_select_a_different_tenant(client):
    payload = valid_inbound_payload()
    payload["tenant_id"] = "attacker-tenant"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 403
    assert response.json() == {"detail": "request tenant does not match trusted tenant"}


def test_inbound_persists_conversation_messages_workflow_and_events(client):
    response = client.post("/inbound", json=valid_inbound_payload())
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    with Session(engine) as session:
        conversations = session.exec(select(Conversation)).all()
        messages = session.exec(select(Message)).all()
        events = session.exec(select(EventLog)).all()

    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation.tenant_id == "tenant-1"
    assert conversation.contact_id == "contact-1"
    assert conversation.workflow_state == "appointment_proposed"
    assert {message.direction for message in messages} == {"inbound", "outbound"}
    assert all(message.conversation_id == conversation.id for message in messages)
    assert "appointment.proposed" in {item.event_type for item in events}
    assert all(item.tenant_id == "tenant-1" for item in events)
    with Session(engine) as session:
        assert len(session.exec(select(Appointment)).all()) == 1
        assert "appointment_proposed" in {
            record.state for record in session.exec(select(WorkflowStateRecord)).all()
        }


def test_duplicate_inbound_requests_create_independent_persisted_conversations(client):
    payload = valid_inbound_payload()
    first = client.post("/inbound", json=payload)
    second = client.post("/inbound", json=deepcopy(payload))
    engine = client.app.state.inbound_test_engine

    assert first.status_code == second.status_code == 200
    with Session(engine) as session:
        conversations = session.exec(select(Conversation)).all()

    assert len(conversations) == 2
    assert conversations[0].id != conversations[1].id
    assert conversations[0].contact_id == conversations[1].contact_id == "contact-1"


def test_after_hours_chat_routes_to_configured_voicemail(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        rule = session.exec(select(RoutingRule)).one()
        rule.opens_at = time(14)
        session.commit()
    payload = valid_inbound_payload()
    payload["text"] = "Can I schedule an appointment?"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "after_hours_voicemail"
    assert "leave a voicemail" in response.json()["agent_response"]
    with Session(engine) as session:
        assert session.exec(select(Appointment)).all() == []
        assert session.exec(select(EventLog).where(
            EventLog.event_type == "after_hours.request.routed"
        )).one()


def test_appointment_confirmation_persists_booking_and_reminder_event(client):
    first = client.post("/inbound", json=valid_inbound_payload())
    assert first.status_code == 200
    payload = valid_inbound_payload()
    payload["text"] = "yes, confirm"

    response = client.post("/inbound", json=payload)
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "appointment_confirmed"
    with Session(engine) as session:
        appointment = session.exec(select(Appointment)).one()
        assert appointment.status == "confirmed"
        slot = session.exec(select(AppointmentSlot).where(
            AppointmentSlot.id == appointment.slot_id
        )).one()
        assert slot.status == "booked"
        assert session.exec(select(EventLog).where(
            EventLog.event_type == "appointment.reminder.prepared"
        )).one()


def test_urgent_request_creates_escalation_ticket_and_alert_event(client):
    payload = valid_inbound_payload()
    payload["text"] = "This is an emergency, I need help immediately"

    response = client.post("/inbound", json=payload)
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "escalated"
    with Session(engine) as session:
        ticket = session.exec(select(EscalationTicket)).one()
        assert ticket.priority == "urgent"
        assert session.exec(select(EventLog).where(
            EventLog.event_type == "human.escalation.alert"
        )).one()


def test_policy_rejection_persists_inbound_and_blocked_workflow_without_reply(client):
    payload = valid_inbound_payload()
    payload.update(channel="sms", consent_status="granted")
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contact = session.exec(select(Contact).where(Contact.id == "contact-1")).one()
        contact.consent_status = "denied"
        session.commit()

    response = client.post("/inbound", json=payload)
    engine = client.app.state.inbound_test_engine

    assert response.status_code == 200
    with Session(engine) as session:
        conversation = session.exec(select(Conversation)).one()
        messages = session.exec(select(Message)).all()
        events = session.exec(select(EventLog)).all()
        states = session.exec(select(WorkflowStateRecord)).all()
        policy_decisions = session.exec(select(PolicyDecisionLog)).all()

    assert conversation.workflow_state == "blocked"
    assert len(messages) == 1 and messages[0].direction == "inbound"
    assert "intake.received" in {item.event_type for item in events}
    assert [item.state for item in states] == ["intake_received", "blocked"]
    assert len(policy_decisions) == 1
    assert policy_decisions[0].decision == "block"
    assert policy_decisions[0].country_code == "US"


def test_agent_failure_rolls_back_all_inbound_persistence(client, monkeypatch):
    from app.services.intake_service import IntakeService

    def fail(*args, **kwargs):
        raise RuntimeError("intake failure")

    monkeypatch.setattr(IntakeService, "_outbound", staticmethod(fail))

    with pytest.raises(RuntimeError, match="intake failure"):
        client.post("/inbound", json=valid_inbound_payload())

    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        assert session.exec(select(Conversation)).all() == []
        assert session.exec(select(Message)).all() == []
        assert session.exec(select(EventLog)).all() == []
        assert session.exec(select(WorkflowStateRecord)).all() == []
        assert session.exec(select(Appointment)).all() == []
        slot = session.exec(select(AppointmentSlot)).first()
        assert slot.status == "available"


def test_missing_provisioned_contact_is_not_created_from_inbound(client):
    payload = valid_inbound_payload()
    payload["contact_id"] = "unprovisioned-contact"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 404
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        contacts = session.exec(select(Contact)).all()
        assert len(contacts) == 1
        assert contacts[0].id == "contact-1"
        assert session.exec(select(Conversation)).all() == []


def test_missing_provisioned_business_returns_404_without_creating_it(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.exec(delete(Appointment))
        session.exec(delete(AppointmentSlot))
        session.exec(delete(RoutingRule))
        session.exec(delete(Staff))
        session.exec(delete(Service))
        session.exec(delete(Contact).where(Contact.id == "contact-1"))
        session.exec(delete(Business).where(Business.id == "biz-1"))
        session.commit()

    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Business)).all() == []
        assert session.exec(select(Conversation)).all() == []


def test_contact_provisioned_for_other_business_is_rejected(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.add(Business(
            id="biz-other", tenant_id="tenant-1", name="Other business", vertical="general"
        ))
        session.flush()
        session.add(Contact(
            id="contact-other", tenant_id="tenant-1", business_id="biz-other"
        ))
        session.commit()
    payload = valid_inbound_payload()
    payload["contact_id"] = "contact-other"

    response = client.post("/inbound", json=payload)

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Conversation)).all() == []


def test_missing_provisioned_tenant_returns_404_without_creating_it(client):
    engine = client.app.state.inbound_test_engine
    with Session(engine) as session:
        session.exec(delete(Appointment))
        session.exec(delete(AppointmentSlot))
        session.exec(delete(RoutingRule))
        session.exec(delete(Staff))
        session.exec(delete(Service))
        session.exec(delete(Contact).where(Contact.id == "contact-1"))
        session.exec(delete(Business).where(Business.id == "biz-1"))
        session.exec(delete(Tenant).where(Tenant.id == "tenant-1"))
        session.commit()

    response = client.post("/inbound", json=valid_inbound_payload())

    assert response.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(Tenant)).all() == []
        assert session.exec(select(Conversation)).all() == []
