from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlmodel import Session, select

from app.persistence.models import (
    CampaignEventLog, CampaignReply, CampaignEnrollment, CampaignStepRun, ContactChannelConsent,
    Lead, LeadStateTransition,
    QueueTaskRecord,
    Appointment, AppointmentSlot, Service, Staff, Conversation,
)
from app.services.reply_parser_service import ReplyClassification, ReplyParserService
from app.services.campaign_analytics_service import CampaignAnalyticsService
from app.persistence.unit_of_work import UnitOfWork
from test_campaigns import campaign_client


@pytest.mark.parametrize(("text", "expected"), [
    ("Yes, sounds good", ReplyClassification.POSITIVE),
    ("No thanks", ReplyClassification.NEGATIVE),
    ("STOP", ReplyClassification.OPT_OUT),
    ("Can I book an appointment?", ReplyClassification.BOOK_APPOINTMENT),
    ("Please call me back", ReplyClassification.CALLBACK),
    ("Maybe next week", ReplyClassification.UNKNOWN),
    ("No, stop", ReplyClassification.OPT_OUT),
])
def test_reply_parser_classifies_deterministically(text, expected):
    assert ReplyParserService().classify(text) == expected


def _campaign_and_enrollment(client, lead_id):
    campaign_response = client.post("/campaigns", json={
        "tenant_id": "tenant-a", "name": "Reply test", "channel": "email",
        "audience_filters": {"minimum_priority_score": 65},
        "steps": [
            {"delay_seconds": 0, "template": {"body": "Hello"}},
            {"delay_seconds": 0, "template": {"body": "Follow up"}},
        ],
    })
    assert campaign_response.status_code == 201, campaign_response.text
    campaign_id = campaign_response.json()["id"]
    enrolled = client.post(f"/campaigns/{campaign_id}/enroll", json={
        "tenant_id": "tenant-a", "lead_ids": [str(lead_id)],
    })
    assert enrolled.status_code == 200, enrolled.text
    enrollment_id = enrolled.json()["enrollments"][0]["id"]
    return campaign_id, enrollment_id


def test_opt_out_revokes_channel_cancels_future_steps_and_persists_events(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    first = client.post(f"/campaigns/{campaign_id}/trigger", json={"tenant_id": "tenant-a"})
    assert first.status_code == 200 and first.json()["queued"] == 1

    reply = client.post(f"/campaigns/{campaign_id}/replies", json={
        "tenant_id": "tenant-a", "enrollment_id": enrollment_id,
        "text": "STOP", "external_message_id": "reply-stop-1",
    })
    assert reply.status_code == 201, reply.text
    assert reply.json()["classification"] == "opt_out"

    with Session(engine) as session:
        consent = session.exec(select(ContactChannelConsent)).one()
        enrollment = session.exec(select(CampaignEnrollment)).one()
        lead = session.exec(select(Lead)).one()
        event_types = {e.event_type for e in session.exec(select(CampaignEventLog)).all()}
        transitions = session.exec(select(LeadStateTransition)).all()
        assert (consent.channel, consent.status) == ("email", "revoked")
        assert (enrollment.status, enrollment.next_step_at) == ("canceled", None)
        assert lead.status == "opted_out"
        assert "campaign.opted_out" in event_types
        assert any(row.to_state == "opted_out" for row in transitions)

    second = client.post(f"/campaigns/{campaign_id}/trigger", json={"tenant_id": "tenant-a"})
    assert second.status_code == 200
    assert second.json()["sent"] == 0


def test_reply_classification_persists_event_and_external_id_is_idempotent(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    payload = {"tenant_id": "tenant-a", "enrollment_id": enrollment_id,
               "text": "Yes, I am interested", "external_message_id": "reply-123"}
    first = client.post(f"/campaigns/{campaign_id}/replies", json=payload)
    duplicate = client.post(f"/campaigns/{campaign_id}/replies", json=payload)
    assert first.status_code == 201 and first.json()["classification"] == "positive"
    assert duplicate.status_code == 201 and duplicate.json()["duplicate"] is True
    with Session(engine) as session:
        assert len(session.exec(select(CampaignReply)).all()) == 1
        reply_event = session.exec(select(CampaignEventLog).where(
            CampaignEventLog.event_type == "campaign.reply.received")).one()
        assert reply_event.payload["classification"] == "positive"
        assert "Yes" not in str(reply_event.payload)


def test_unknown_reply_is_valid_classification_and_recorded(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    response = client.post(f"/campaigns/{campaign_id}/replies", json={
        "tenant_id": "tenant-a", "enrollment_id": enrollment_id, "text": "Maybe someday",
    })
    assert response.status_code == 201
    assert response.json()["classification"] == "unknown"
    with Session(engine) as session:
        assert session.exec(select(CampaignReply)).one().classification == "unknown"


def test_negative_reply_closes_lead_and_stops_remaining_sequence(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    response = client.post(f"/campaigns/{campaign_id}/replies", json={
        "tenant_id": "tenant-a", "enrollment_id": enrollment_id,
        "text": "No thanks, not interested", "external_message_id": "reply-negative-1",
    })
    assert response.status_code == 201
    with Session(engine) as session:
        lead = session.get(Lead, lead_id)
        enrollment = session.get(CampaignEnrollment, UUID(enrollment_id))
        transition = session.exec(select(LeadStateTransition).where(
            LeadStateTransition.lead_id == lead_id,
            LeadStateTransition.to_state == "closed",
        )).one()
        event = session.exec(select(CampaignEventLog).where(
            CampaignEventLog.event_type == "campaign.negative_reply.closed",
        )).one()
    assert lead.status == "closed"
    assert enrollment.status == "canceled"
    assert enrollment.next_step_at is None
    assert transition.context["reason"] == "negative_reply"
    assert event.payload["lead_id"] == str(lead_id)


def test_positive_campaign_reply_queues_shared_appointment_workflow(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    response = client.post(f"/campaigns/{campaign_id}/replies", json={
        "tenant_id": "tenant-a", "enrollment_id": enrollment_id,
        "text": "Yes, sounds good. Monday morning please.",
        "external_message_id": "reply-book-1",
    })
    assert response.status_code == 201
    assert response.json()["classification"] == "positive"
    with Session(engine) as session:
        task = session.exec(select(QueueTaskRecord).where(
            QueueTaskRecord.task_name == "scheduling.propose_for_lead",
        )).one()
        task_values = {
            "id": task.id, "tenant_id": task.tenant_id, "business_id": task.business_id,
            "domain": task.domain, "task_name": task.task_name,
            "idempotency_key": task.idempotency_key,
            "aggregate_type": task.aggregate_type, "aggregate_id": task.aggregate_id,
            "payload": dict(task.payload), "correlation_id": task.correlation_id,
            "traceparent": task.traceparent, "attempts": 1,
            "max_attempts": task.max_attempts,
        }
        staff = Staff(tenant_id="tenant-a", business_id="business-a", display_name="Taylor")
        service = Service(
            tenant_id="tenant-a", business_id="business-a", name="Consultation",
            duration_minutes=30,
        )
        session.add_all([staff, service])
        session.flush()
        session.add(AppointmentSlot(
            tenant_id="tenant-a", business_id="business-a", staff_id=staff.id,
            service_id=service.id, starts_at=datetime.now(UTC) + timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        ))
        session.commit()
    assert task_values["domain"] == "scheduling"
    assert task_values["payload"]["campaign_id"] == campaign_id
    assert task_values["payload"]["enrollment_id"] == enrollment_id
    assert task_values["payload"]["lead_id"] == str(lead_id)
    assert task_values["payload"]["preferences"]["weekday_preference"] == 0
    assert task_values["payload"]["preferences"]["time_preference"] == ["06:00:00", "12:00:00"]
    assert task_values["correlation_id"].startswith("campaign-reply:")

    from app.queue.runtime import QueueTaskEnvelope
    from app.workers import handlers as worker_handlers
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_handlers, "session_factory", lambda: Session(engine))
    envelope = QueueTaskEnvelope(**task_values)
    handler = worker_handlers.build_default_handlers().get("scheduling", "scheduling.propose_for_lead")
    try:
        result = handler(envelope)
        repeated = handler(envelope)
    finally:
        monkeypatch.undo()
    assert result["status"] == "proposed"
    assert repeated["status"] == "already_processed"
    with Session(engine) as session:
        appointment = session.exec(select(Appointment)).one()
        conversation = session.exec(select(Conversation).where(
            Conversation.correlation_id == task_values["correlation_id"],
        )).one()
    assert appointment.source == "outreach"
    assert appointment.campaign_id == UUID(campaign_id)
    assert appointment.campaign_enrollment_id == UUID(enrollment_id)
    assert appointment.status == "proposed"
    assert conversation.id == appointment.conversation_id


def test_analytics_aggregates_persisted_sends_opens_replies_and_booking_events(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    triggered = client.post(f"/campaigns/{campaign_id}/trigger", json={"tenant_id": "tenant-a"})
    assert triggered.status_code == 200 and triggered.json()["queued"] == 1
    with Session(engine) as session:
        run = session.exec(select(CampaignStepRun)).one()
        run.status = "sent"
        run.sent_at = datetime.now(UTC)
        session.add(run)
        session.commit()
    for text, message_id in (("Yes, interested", "analytics-r1"),
                             ("Maybe later", "analytics-r2"),
                             ("STOP", "analytics-r3")):
        reply = client.post(f"/campaigns/{campaign_id}/replies", json={
            "tenant_id": "tenant-a", "enrollment_id": enrollment_id,
            "text": text, "external_message_id": message_id,
        })
        assert reply.status_code == 201
    with Session(engine) as session:
        session.add(CampaignEventLog(
            tenant_id="tenant-a", business_id="business-a", campaign_id=UUID(campaign_id),
            enrollment_id=UUID(enrollment_id), event_type="step.opened", payload={},
        ))
        session.add(CampaignEventLog(
            tenant_id="tenant-a", business_id="business-a", campaign_id=UUID(campaign_id),
            enrollment_id=UUID(enrollment_id), event_type="campaign.appointment.booked", payload={},
        ))
        session.add(CampaignEventLog(
            tenant_id="tenant-a", business_id="business-a", campaign_id=UUID(campaign_id),
            enrollment_id=UUID(enrollment_id), event_type="campaign.appointment.completed", payload={},
        ))
        session.commit()

    response = client.get(f"/campaigns/{campaign_id}/analytics")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "campaign_id": campaign_id, "sent_count": 1, "contacted_count": 1, "opened_count": 1,
        "reply_count": 3, "positive_reply_rate": 1 / 3, "opt_out_count": 1,
        "booked_appointments": 1,
        "completed_appointments": 1,
        "variant_metrics": [{
            "variant_id": "default", "enrolled_count": 1,
            "sent_count": 1, "conversions": 1, "conversion_rate": 1.0,
        }],
    }


def test_business_funnel_aggregates_lead_states_and_appointments(campaign_client):
    client, engine, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    sent = client.post(f"/campaigns/{campaign_id}/trigger", json={"tenant_id": "tenant-a"})
    assert sent.status_code == 200 and sent.json()["queued"] == 1
    replied = client.post(f"/campaigns/{campaign_id}/replies", json={
        "tenant_id": "tenant-a", "enrollment_id": enrollment_id,
        "text": "Yes, I am interested", "external_message_id": "business-funnel-reply",
    })
    assert replied.status_code == 201
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add_all([
            Appointment(
                tenant_id="tenant-a", business_id="business-a", contact_id="contact-a",
                starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=2) + timedelta(hours=1),
                status="completed",
            ),
        ])
        session.commit()
    service = CampaignAnalyticsService(
        trusted_tenant_id="tenant-a", trusted_business_id=None,
        uow_factory=lambda: UnitOfWork(lambda: Session(engine)),
    )
    funnel = service.business_funnel(business_id="business-a", roles=("campaign_reader",))
    assert funnel == {
        "business_id": "business-a", "lead_count": 1, "contacted_count": 1,
        "replied_count": 1, "booked_count": 1, "completed_count": 1,
        "converted_count": 0,
    }


def test_reply_rejects_tenant_mismatch(campaign_client):
    client, _, lead_id = campaign_client
    campaign_id, enrollment_id = _campaign_and_enrollment(client, lead_id)
    response = client.post(f"/campaigns/{campaign_id}/replies", json={
        "tenant_id": "tenant-other", "enrollment_id": enrollment_id, "text": "yes",
    })
    assert response.status_code == 403
