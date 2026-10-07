from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.optimization.feedback import FeedbackEventCapture
from app.optimization.optimizers import (
    AudienceSegmentOptimizer, LeadScoringOptimizer, MessageVariantOptimizer, NextBestActionEngine,
    RoutingOptimizer, TimingOptimizer,
)
from app.optimization.service import OptimizationService
from app.optimization.segments import normalized_segment, segment_for_lead
from app.optimization.tracker import OptimizationTracker
from app.persistence.models import (
    Business, FeedbackEvent, OptimizationRecommendation, Tenant,
)
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal


@pytest.fixture
def uow_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-a", name="Tenant A"))
        session.flush()
        session.add(Business(id="business-a", tenant_id="tenant-a", name="Business A",
                             vertical="general"))
        session.commit()
    yield lambda: UnitOfWork(lambda: Session(engine))
    engine.dispose()


def _event(event_type, outcome, *, lead=None, decision=None, campaign=None):
    return SimpleNamespace(event_type=event_type, outcome=outcome,
                           lead_id=lead, decision=decision or {}, campaign_id=campaign)


def test_feedback_capture_is_durable_idempotent_and_rejects_pii(uow_factory):
    capture = FeedbackEventCapture()
    values = dict(tenant_id="tenant-a", business_id="business-a", event_key="lead:1:booked",
                  correlation_id="lead:1", event_type="appointment_booked", outcome="booked",
                  decision={"variant": "A"}, event_metadata={"time_to_conversion_days": 3})
    with uow_factory() as uow:
        first = capture.capture(uow=uow, **values)
        second = capture.capture(uow=uow, **values)
        assert first.id == second.id
    with pytest.raises(ValueError, match="PII"):
        with uow_factory() as uow:
            capture.capture(uow=uow, **(values | {"event_key": "bad", "event_metadata": {"message_text": "private"}}))
    with uow_factory() as uow:
        rows = uow.optimization.feedback(tenant_id="tenant-a", business_id="business-a")
        assert len(rows) == 1
        assert rows[0].correlation_id == "lead:1"
        assert rows[0].event_metadata["time_to_conversion_days"] == 3


def test_optimization_tracker_records_scoring_signals_with_safe_segments():
    stored = []
    uow = SimpleNamespace(optimization=SimpleNamespace(
        add_feedback=lambda **values: stored.append(values) or values,
    ))
    lead_id, score_id = uuid4(), uuid4()
    OptimizationTracker().record_lead_score(
        uow=uow, tenant_id="tenant-a", business_id="business-a", lead_id=lead_id,
        score_id=score_id, correlation_id=f"lead:{lead_id}", fit_score=72,
        urgency_score=30, intent_score=85, source="referral", industry="healthcare",
        country="CA", weights={"fit_score": 0.4}, in_rollout=False,
    )
    assert stored[0]["event_type"] == "lead_scored"
    assert stored[0]["decision"]["fit_score"] == 72
    assert stored[0]["decision"]["country"] == "CA"


def test_lead_scoring_optimizer_recommends_predictive_signal_weights():
    events = []
    for index in range(60):
        booked = index < 30
        lead = uuid4()
        events.append(_event("lead_scored", "scored", lead=lead,
                             decision={"fit_score": 90 if booked else 10,
                                       "urgency_score": 10 if booked else 90,
                                       "intent_score": (index * 37) % 100}))
        events.append(_event("appointment_booked" if booked else "opted_out",
                             "booked" if booked else "opt_out", lead=lead))
    recommendation = LeadScoringOptimizer().recommend(events, minimum_sample=30,
                                                        confidence_threshold=0.75)
    assert recommendation is not None
    assert recommendation.change["weights"]["fit_score"] > 0.4
    assert recommendation.change["weights"]["urgency_score"] < 0.3
    assert recommendation.confidence >= 0.75


def test_variant_optimizer_keeps_test_groups_isolated():
    events = []
    campaign = uuid4()
    for group in ("test_group_a", "test_group_b"):
        for variant, replies in (("variant_a", 18), ("variant_b", 4)):
            for index in range(20):
                decision = {"variant": variant, "test_group": group,
                            "campaign_key": str(campaign), "step": 0}
                events.append(_event("campaign_step_sent", "sent", decision=decision,
                                     campaign=campaign))
                if index < replies:
                    events.append(_event("reply_received", "positive", decision=decision,
                                         campaign=campaign))
    recommendations = MessageVariantOptimizer().recommend(
        events, minimum_sample=10, confidence_threshold=0.75,
    )
    assert len(recommendations) == 2
    assert {item.change["test_group"] for item in recommendations} == {
        "test_group_a", "test_group_b",
    }
    assert all(item.change["weights"]["variant_a"] == 80 for item in recommendations)


def test_timing_optimizer_recommends_hour_with_higher_engagement():
    events = []
    for hour, replies in ((9, 3), (14, 18)):
        for index in range(20):
            decision = {"sent_hour": hour, "segment": "healthcare"}
            events.append(_event("campaign_step_sent", "sent", decision=decision))
            if index < replies:
                events.append(_event("reply_received", "positive", decision=decision))
    recommendations = TimingOptimizer().recommend(events, minimum_sample=10,
                                                    confidence_threshold=0.75)
    assert recommendations and recommendations[0].change["send_hour"] == 14


def test_timing_segments_are_bounded_and_resolved_from_scoped_lead_data():
    lead_id, source_id = uuid4(), uuid4()
    uow = SimpleNamespace(
        leads=SimpleNamespace(get=lambda **_: SimpleNamespace(id=lead_id, source_id=source_id)),
        lead_sources=SimpleNamespace(get=lambda **_: SimpleNamespace(source_type="referral")),
        lead_enrichments=SimpleNamespace(latest=lambda **_: SimpleNamespace(
            company_info={"industry": "healthcare", "country": "CA"},
        )),
    )
    assert segment_for_lead(uow, tenant_id="tenant-a", business_id="business-a",
                            lead_id=lead_id) == (
        "source=referral;industry=healthcare;country=CA"
    )
    assert normalized_segment(source="customer-private-name", industry="private text",
                              country="unknown") == (
        "source=other;industry=other;country=unknown"
    )


def test_audience_optimizer_recommends_only_significant_high_performance_segments():
    events = []
    for index in range(80):
        segment = "healthcare" if index < 40 else "retail"
        lead_id = uuid4()
        events.append(_event("lead_scored", "scored", lead=lead_id, decision={
            "source": "outreach", "industry": segment, "country": "US",
        }))
        if (index < 40 and index < 28) or (index >= 40 and index < 48):
            events.append(_event("appointment_booked", "booked", lead=lead_id))
        else:
            events.append(_event("opted_out", "opt_out", lead=lead_id))
    recommendations = AudienceSegmentOptimizer().recommend(
        events, minimum_sample=20, confidence_threshold=0.75,
    )
    assert recommendations
    assert recommendations[0].change["industry"] == "healthcare"
    assert recommendations[0].change["suggestion"] == "review_similar_audience_expansion"


def test_next_best_action_ranks_actions_and_never_recommends_contact_to_opted_out():
    engine = NextBestActionEngine()
    events = [_event("action_taken", "appointment_booked", decision={
        "action": "propose_appointment", "state": "lead_replied_positive",
    }) for _ in range(8)]
    events += [_event("action_taken", "unknown", decision={
        "action": "send_sms", "state": "lead_replied_positive",
    }) for _ in range(8)]
    ranked = engine.recommend(state="lead_replied_positive", feedback_events=events)
    assert ranked[0]["action"] == "propose_appointment"
    opted_out = engine.recommend(state="opted_out", allowed_actions={"send_sms", "send_email"})
    assert [row["action"] for row in opted_out] == []


def test_routing_optimizer_adjusts_threshold_from_escalation_outcomes():
    events = [_event("contact_escalated", "timeout") for _ in range(35)]
    recommendation = RoutingOptimizer().recommend(events, minimum_sample=20,
                                                    confidence_threshold=0.75)
    assert recommendation is not None
    assert recommendation.change["threshold_delta"] < 0
    quickly_resolved = [_event("contact_escalated", "resolved") for _ in range(35)]
    recommendation = RoutingOptimizer().recommend(
        quickly_resolved, minimum_sample=20, confidence_threshold=0.75,
    )
    assert recommendation and recommendation.change["threshold_delta"] > 0


def test_routing_optimizer_counts_resolution_once_per_ticket():
    ticket_id = str(uuid4())
    events = [
        _event("contact_escalated", "escalated", decision={"ticket_id": ticket_id}),
        _event("contact_escalated", "resolved", decision={"ticket_id": ticket_id}),
    ]
    assert RoutingOptimizer().recommend(events, minimum_sample=2) is None


def test_batch_dry_run_persists_no_recommendation_and_approval_stages_ten_percent(uow_factory):
    with uow_factory() as uow:
        for index in range(60):
            lead_id = uuid4()
            for event_type, outcome, decision in (
                ("lead_scored", "scored", {"fit_score": 90 if index < 30 else 10,
                                            "urgency_score": 10 if index < 30 else 90,
                                            "intent_score": index % 10}),
                (("appointment_booked", "booked", {}) if index < 30 else
                 ("opted_out", "opt_out", {})),
            ):
                uow.optimization.add_feedback(
                    tenant_id="tenant-a", business_id="business-a",
                    event_key=f"batch:{index}:{event_type}", correlation_id=f"lead:{lead_id}",
                    lead_id=lead_id, event_type=event_type, outcome=outcome,
                    decision=decision, event_metadata={}, occurred_at=datetime.now(UTC),
                )
    service = OptimizationService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                                  uow_factory=uow_factory, minimum_sample=30,
                                  confidence_threshold=0.75)
    dry = service.run_batch(dry_run=True)
    assert dry["dry_run"] is True and dry["recommendations"]
    assert service.recommendations() == []
    stored = service.run_batch(dry_run=False)
    recommendations = service.recommendations()
    assert stored["stored_recommendation_ids"]
    principal = AuthenticatedPrincipal("reviewer", "tenant-a", ("optimization_approver",),
                                       "test", datetime.now(UTC))
    rec_id = recommendations[0]["id"]
    applied = service.approve(principal, rec_id)
    assert applied["feature_flag"]["rollout_percentage"] == 10
    assert service.audit()[0]["action"] == "approved_and_staged"


def test_audience_expansion_approval_is_audited_but_does_not_change_targeting(uow_factory):
    with uow_factory() as uow:
        recommendation = uow.optimization.add_recommendation(
            tenant_id="tenant-a", business_id="business-a",
            recommendation_type="audience_expansion", status="pending",
            change={"industry": "healthcare", "suggestion": "review_similar_audience_expansion"},
            confidence=0.9, expected_impact=0.1, sample_size=80,
            rationale="Higher observed conversion in healthcare leads.",
        )
        recommendation_id = recommendation.id
    service = OptimizationService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                                  uow_factory=uow_factory)
    principal = AuthenticatedPrincipal("reviewer", "tenant-a", ("optimization_approver",),
                                       "test", datetime.now(UTC))
    result = service.approve(principal, recommendation_id)
    assert result["requires_campaign_review"] is True
    assert result["feature_flag"] is None
    assert service.audit()[0]["action"] == "audience_expansion_approved"


def test_optimization_scope_rejects_cross_tenant_access(uow_factory):
    with uow_factory() as uow:
        uow.optimization.add_feedback(
            tenant_id="tenant-a", business_id="business-a", event_key="scoped",
            correlation_id="scoped", event_type="manual_feedback", outcome="ok",
            decision={}, event_metadata={}, occurred_at=datetime.now(UTC),
        )
    service = OptimizationService(trusted_tenant_id="tenant-other", trusted_business_id="business-a",
                                  uow_factory=uow_factory)
    assert service.recommendations() == []
    assert service.metrics() == []


def test_rollout_reverts_on_conversion_regression_and_audits(uow_factory):
    now = datetime.now(UTC)
    with uow_factory() as uow:
        uow.optimization.put_feature_flag(
            tenant_id="tenant-a", business_id="business-a",
            flag_key="optimization.lead_scoring", enabled=True,
            rollout_percentage=50,
            config={"approved_at": (now - timedelta(days=1)).isoformat(),
                    "baseline_conversion_rate": 0.5,
                    "previous": {"enabled": False, "rollout_percentage": 0,
                                 "config": {}}},
        )
        for index in range(20):
            control_lead_id = uuid4()
            uow.optimization.add_feedback(
                tenant_id="tenant-a", business_id="business-a",
                event_key=f"rollout-exposure-{index}", correlation_id=f"lead:{index}",
                    lead_id=uuid4(), event_type="feature_flag_exposure", outcome="exposed",
                decision={"flag_key": "optimization.lead_scoring", "applied": True},
                event_metadata={},
                occurred_at=now,
            )
            uow.optimization.add_feedback(
                tenant_id="tenant-a", business_id="business-a",
                event_key=f"rollout-control-{index}", correlation_id=f"control:{index}",
                lead_id=control_lead_id, event_type="feature_flag_exposure", outcome="exposed",
                decision={"flag_key": "optimization.lead_scoring", "applied": False},
                event_metadata={}, occurred_at=now,
            )
            if index < 10:
                uow.optimization.add_feedback(
                    tenant_id="tenant-a", business_id="business-a",
                    event_key=f"rollout-control-booked-{index}",
                    correlation_id=f"control:{index}", lead_id=control_lead_id,
                    event_type="appointment_booked", outcome="booked",
                    decision={}, event_metadata={}, occurred_at=now,
                )
    service = OptimizationService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                                  uow_factory=uow_factory)
    result = service.monitor_rollout(flag_key="optimization.lead_scoring", minimum_sample=20)
    assert result["status"] == "rolled_back"
    with uow_factory() as uow:
        flag = uow.optimization.feature_flag(
            tenant_id="tenant-a", business_id="business-a",
            flag_key="optimization.lead_scoring",
        )
        assert flag.enabled is False and flag.rollout_percentage == 0
        assert uow.optimization.audit(tenant_id="tenant-a", business_id="business-a")[0].action == "automatic_rollback"


def test_no_show_feedback_uses_trusted_appointment_link_and_idempotency():
    from app.optimization.service import OptimizationService

    appointment = SimpleNamespace(
        id=uuid4(), status="confirmed", ends_at=datetime.now(UTC) - timedelta(hours=1),
        campaign_enrollment_id=None, campaign_id=None, contact_id="contact-1",
        correlation_id="conversation-correlation",
    )
    inserted, audited = [], []

    class FakeUow:
        appointments = SimpleNamespace(get_for_tenant=lambda **kwargs: appointment)
        leads = SimpleNamespace(get=lambda **kwargs: None)
        optimization = SimpleNamespace(
            add_feedback=lambda **kwargs: inserted.append(kwargs) or SimpleNamespace(id=uuid4()),
            add_audit=lambda **kwargs: audited.append(kwargs),
        )
        def __enter__(self): return self
        def __exit__(self, *args): return False

    service = OptimizationService(trusted_tenant_id="tenant-a", trusted_business_id="business-a",
                                  uow_factory=FakeUow)
    service.capture_manual_feedback(
        event_key="caller-key-is-ignored-for-appointment", correlation_id="caller-correlation",
        event_type="appointment_no_show", outcome="ignored", decision={}, event_metadata={},
        appointment_id=appointment.id,
    )
    assert inserted[0]["event_key"] == f"appointment-no-show:{appointment.id}"
    assert inserted[0]["correlation_id"] == "conversation-correlation"
    assert inserted[0]["outcome"] == "no_show"
    assert inserted[0]["contact_id"] == "contact-1"
    assert audited[0]["action"] == "manual_feedback_submitted"


def test_campaign_funnel_metric_counts_distinct_leads_and_flags_underperformer():
    campaign_id = uuid4()
    enrolled = [uuid4() for _ in range(30)]
    metric_rows = []
    service = OptimizationService(
        trusted_tenant_id="tenant-a", trusted_business_id="business-a",
        uow_factory=lambda: None, minimum_sample=10,
    )
    uow = SimpleNamespace(optimization=SimpleNamespace(add_metric=lambda **kwargs: metric_rows.append(kwargs)))
    campaign_events = [SimpleNamespace(
        campaign_id=campaign_id, enrollment_id=item, event_type="campaign.enrolled",
    ) for item in enrolled]
    feedback = []
    for index, lead_id in enumerate(enrolled):
        feedback.append(_event("campaign_step_sent", "sent", lead=lead_id, campaign=campaign_id))
        if index < 10:
            feedback.append(_event("appointment_booked", "booked", lead=lead_id, campaign=campaign_id))
    service._store_campaign_funnels(
        uow, campaign_events=campaign_events, feedback_events=feedback,
        since=datetime.now(UTC) - timedelta(days=7), until=datetime.now(UTC),
    )
    metric = metric_rows[0]
    assert metric["metric_type"] == "campaign_funnel"
    assert metric["metric_values"]["leads_enrolled"] == 30
    assert metric["metric_values"]["leads_contacted"] == 30
    assert metric["metric_values"]["leads_booked"] == 10
    assert metric["metric_values"]["conversion_rate"] == pytest.approx(1 / 3)


def test_sendgrid_open_webhook_replay_captures_campaign_variant_without_pii():
    message_id, campaign_id, enrollment_id, lead_id = uuid4(), uuid4(), uuid4(), uuid4()
    stored = []
    message = SimpleNamespace(id=message_id, channel="email")
    run = SimpleNamespace(enrollment_id=enrollment_id, step_order=2,
                          sent_at=datetime.now(UTC).replace(hour=14))
    enrollment = SimpleNamespace(id=enrollment_id, lead_id=lead_id,
                                 campaign_id=campaign_id, sequence_id=uuid4(),
                                 experiment_group="test_group_a")

    class FakeUow:
        messages = SimpleNamespace(get_in_tenant=lambda **kwargs: message)
        campaign_step_runs = SimpleNamespace(get_by_message=lambda **kwargs: run)
        campaign_enrollments = SimpleNamespace(get_by_id=lambda **kwargs: enrollment)
        campaign_sequences = SimpleNamespace(list_for_campaign=lambda **kwargs: [
            SimpleNamespace(id=enrollment.sequence_id, name="variant_b"),
        ])
        leads = SimpleNamespace(get=lambda **kwargs: None)
        optimization = SimpleNamespace(add_feedback=lambda **kwargs: stored.append(kwargs) or SimpleNamespace())

    event = SimpleNamespace(
        id=uuid4(), tenant_id="tenant-a", business_id="business-a",
        created_at=datetime.now(UTC),
        payload={"events": [{"event": "open", "email": "private@example.com",
                            "custom_args": {"message_id": str(message_id)}}]},
    )
    captured = FeedbackEventCapture().replay_provider_event(uow=FakeUow(), event=event)
    assert captured == 1
    assert stored[0]["event_type"] == "message_opened"
    assert stored[0]["decision"]["variant"] == "variant_b"
    assert stored[0]["decision"]["test_group"] == "test_group_a"
    assert "email" not in stored[0]["event_metadata"]


def test_staff_routing_selects_fast_high_satisfaction_resolver():
    from app.services.escalation_service import EscalationService

    fast = SimpleNamespace(id=uuid4())
    slow = SimpleNamespace(id=uuid4())
    feedback = []
    for staff, seconds, satisfaction in ((fast, 180, 5), (slow, 1800, 2)):
        for _ in range(5):
            feedback.append(SimpleNamespace(
                outcome="resolved",
                event_metadata={"staff_id": str(staff.id),
                                "resolution_time_seconds": seconds,
                                "customer_satisfaction": satisfaction},
            ))
    uow = SimpleNamespace(optimization=SimpleNamespace(
        feedback=lambda **kwargs: feedback,
    ))
    selected = EscalationService._select_staff(
        uow, tenant_id="tenant-a", business_id="business-a",
        contact_id="contact-a", staff=[slow, fast],
    )
    assert selected.id == fast.id
