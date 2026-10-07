"""Tenant-scoped feedback analysis, recommendation review, and flag rollout."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID

from app.optimization.feedback import FeedbackEventCapture
from app.optimization.optimizers import (
    AudienceSegmentOptimizer, LeadScoringOptimizer, MessageVariantOptimizer, NextBestActionEngine,
    RoutingOptimizer, TimingOptimizer,
)
from app.persistence.unit_of_work import UnitOfWork
from app.security import AuthenticatedPrincipal
from app.observability.metrics import metrics
from app.services.contact_preference_service import ContactPreferenceService
from app.services.policy_service import PolicyService
from app.optimization.segments import ALLOWED_INDUSTRIES, ALLOWED_SOURCES


FLAG_KEYS = {
    "lead_scoring_weights": "optimization.lead_scoring",
    "message_variant_weights": "optimization.message_variants",
    "send_time": "optimization.send_time",
    "escalation_threshold": "optimization.escalation_threshold",
}


class OptimizationService:
    def __init__(self, *, trusted_tenant_id: str, trusted_business_id: str,
                 uow_factory: Callable[[], UnitOfWork], now_provider=None,
                 minimum_sample: int = 30, confidence_threshold: float = 0.75,
                 dry_run: bool = False) -> None:
        if not trusted_tenant_id or not trusted_business_id:
            raise ValueError("trusted tenant and business scope are required")
        if minimum_sample < 2 or not 0.5 <= confidence_threshold <= 1:
            raise ValueError("optimization thresholds are invalid")
        self.tenant_id, self.business_id = trusted_tenant_id, trusted_business_id
        self._uow_factory = uow_factory
        self._now = now_provider or (lambda: datetime.now(UTC))
        self.minimum_sample = minimum_sample
        self.confidence_threshold = confidence_threshold
        self.dry_run = dry_run
        self.feedback = FeedbackEventCapture()
        self._optimizers = (
            LeadScoringOptimizer(), MessageVariantOptimizer(), TimingOptimizer(), RoutingOptimizer(),
            AudienceSegmentOptimizer(),
        )
        self.next_best_action = NextBestActionEngine()

    def run_batch(self, *, days: int = 7, dry_run: bool | None = None) -> dict:
        if not 1 <= days <= 90:
            raise ValueError("optimization analysis window must be 1-90 days")
        is_dry = self.dry_run if dry_run is None else dry_run
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        since = now - timedelta(days=days)
        with self._uow_factory() as uow:
            backfilled = 0
            for event in uow.events.list_for_business(
                tenant_id=self.tenant_id, business_id=self.business_id,
                since=since, limit=50000,
            ):
                backfilled += int(self.feedback.replay_persisted_event(uow=uow, event=event))
            campaign_events = uow.optimization.campaign_events(
                tenant_id=self.tenant_id, business_id=self.business_id,
                since=since, limit=50000,
            )
            for event in campaign_events:
                backfilled += int(self.feedback.replay_campaign_event(uow=uow, event=event))
            for event in uow.optimization.provider_events(
                tenant_id=self.tenant_id, business_id=self.business_id,
                since=since, limit=20000,
            ):
                backfilled += self.feedback.replay_provider_event(uow=uow, event=event)
            events = uow.optimization.feedback(
                tenant_id=self.tenant_id, business_id=self.business_id,
                since=since, until=now, limit=50000,
            )
            drafts = []
            for optimizer in self._optimizers:
                if isinstance(optimizer, (MessageVariantOptimizer, TimingOptimizer,
                                          AudienceSegmentOptimizer)):
                    drafts.extend(optimizer.recommend(
                        events, minimum_sample=self.minimum_sample,
                        confidence_threshold=self.confidence_threshold,
                    ))
                else:
                    draft = optimizer.recommend(
                        events, minimum_sample=self.minimum_sample,
                        confidence_threshold=self.confidence_threshold,
                    )
                    if draft is not None:
                        drafts.append(draft)
            stored = []
            if not is_dry:
                for draft in drafts:
                    if draft.recommendation_type not in FLAG_KEYS and draft.recommendation_type != "audience_expansion":
                        continue
                    existing = uow.optimization.recommendations(
                        tenant_id=self.tenant_id, business_id=self.business_id,
                        status="pending", limit=100,
                    )
                    if any(row.recommendation_type == draft.recommendation_type
                           and row.change == draft.change for row in existing):
                        continue
                    row = uow.optimization.add_recommendation(
                        tenant_id=self.tenant_id, business_id=self.business_id,
                        recommendation_type=draft.recommendation_type,
                        status="pending", change=draft.change,
                        confidence=draft.confidence, expected_impact=draft.expected_impact,
                        sample_size=draft.sample_size, rationale=draft.rationale,
                    )
                    stored.append(row)
                    metrics.event("workflow", "optimization_recommendation", "generated")
                    uow.optimization.add_audit(
                        tenant_id=self.tenant_id, business_id=self.business_id,
                        recommendation_id=row.id, action="recommendation_generated",
                        actor_id="optimization_batch",
                        details={"recommendation_type": draft.recommendation_type,
                                 "confidence": draft.confidence,
                                 "expected_impact": draft.expected_impact,
                                 "sample_size": draft.sample_size,
                                 "rationale": draft.rationale},
                    )
                uow.optimization.add_metric(
                    tenant_id=self.tenant_id, business_id=self.business_id,
                    metric_key=f"batch:{since.isoformat()}", metric_type="feedback_batch",
                    segment={}, sample_size=len(events),
                    metric_values={"draft_count": len(drafts), "recommendation_count": len(stored),
                                   "dry_run": False},
                    period_start=since, period_end=now,
                )
                self._store_campaign_funnels(
                    uow, campaign_events=campaign_events, feedback_events=events,
                    since=since, until=now,
                )
                self._store_segment_metrics(
                    uow, feedback_events=events, since=since, until=now,
                )
            return {
                "tenant_id": self.tenant_id, "business_id": self.business_id,
                "period_start": since, "period_end": now,
                "sample_size": len(events), "dry_run": is_dry,
                "backfilled_events": backfilled,
                "recommendations": [self._draft_dict(item) for item in drafts],
                "stored_recommendation_ids": [str(item.id) for item in stored],
            }

    def _store_campaign_funnels(self, uow, *, campaign_events, feedback_events,
                                since: datetime, until: datetime) -> None:
        campaign_ids = {event.campaign_id for event in campaign_events}
        campaign_ids.update(event.campaign_id for event in feedback_events if event.campaign_id)
        summaries = []
        for campaign_id in campaign_ids:
            enrolled = {str(event.enrollment_id) for event in campaign_events
                        if event.campaign_id == campaign_id
                        and event.event_type == "campaign.enrolled"
                        and event.enrollment_id is not None}
            rows = [event for event in feedback_events if event.campaign_id == campaign_id]
            contacted = {str(event.lead_id) for event in rows
                         if event.event_type == "campaign_step_sent" and event.lead_id}
            replied = {str(event.lead_id) for event in rows
                       if event.event_type == "reply_received" and event.lead_id}
            booked = {str(event.lead_id) for event in rows
                      if event.event_type == "appointment_booked" and event.lead_id}
            enrolled_count = len(enrolled)
            contacted_count = len(contacted)
            summary = {"campaign_id": str(campaign_id), "leads_enrolled": enrolled_count,
                       "leads_contacted": contacted_count, "leads_replied": len(replied),
                       "leads_booked": len(booked),
                       "conversion_rate": len(booked) / max(1, enrolled_count)}
            summaries.append(summary)
        business_average = (sum(row["conversion_rate"] for row in summaries) / len(summaries)
                            if summaries else 0.0)
        for summary in summaries:
            summary["business_average_conversion_rate"] = business_average
            summary["underperforming"] = (
                summary["leads_enrolled"] >= self.minimum_sample
                and summary["conversion_rate"] < business_average
            )
            campaign_id = summary["campaign_id"]
            uow.optimization.add_metric(
                tenant_id=self.tenant_id, business_id=self.business_id,
                metric_key=f"campaign-funnel:{campaign_id}:{since.date().isoformat()}",
                metric_type="campaign_funnel",
                segment={"campaign_id": campaign_id},
                sample_size=summary["leads_enrolled"], metric_values=summary,
                period_start=since, period_end=until,
            )

    def _store_segment_metrics(self, uow, *, feedback_events,
                               since: datetime, until: datetime) -> None:
        allowed_sources = ALLOWED_SOURCES
        allowed_industries = ALLOWED_INDUSTRIES
        snapshots = {}
        booked, replied, opted_out = set(), set(), set()
        for event in feedback_events:
            if event.lead_id is None:
                continue
            key = str(event.lead_id)
            if event.event_type == "lead_scored":
                source = str(event.decision.get("source", "other")).casefold()
                industry = str(event.decision.get("industry", "unknown")).casefold()
                country = str(event.decision.get("country", "unknown")).upper()
                source = source if source in allowed_sources else "other"
                industry = industry if industry in allowed_industries else "other"
                country = country if len(country) == 2 and country.isalpha() else "unknown"
                snapshots[key] = (source, industry, country)
            elif event.event_type == "appointment_booked":
                booked.add(key)
            elif event.event_type == "reply_received":
                replied.add(key)
            elif event.event_type == "opted_out":
                opted_out.add(key)
        grouped = {}
        for lead_key, segment in snapshots.items():
            grouped.setdefault(segment, set()).add(lead_key)
        business_rate = len(set(snapshots) & booked) / max(1, len(snapshots))
        for segment, lead_keys in grouped.items():
            booked_count = len(lead_keys & booked)
            values = {
                "lead_count": len(lead_keys),
                "replied": len(lead_keys & replied),
                "booked": booked_count,
                "opted_out": len(lead_keys & opted_out),
                "conversion_rate": booked_count / max(1, len(lead_keys)),
                "business_average_conversion_rate": business_rate,
                "high_performing": booked_count / max(1, len(lead_keys)) > business_rate,
            }
            source, industry, country = segment
            uow.optimization.add_metric(
                tenant_id=self.tenant_id, business_id=self.business_id,
                metric_key=(f"audience-segment:{source}:{industry}:{country}:"
                            f"{since.date().isoformat()}"),
                metric_type="audience_segment",
                segment={"source": source, "industry": industry, "country": country},
                sample_size=len(lead_keys), metric_values=values,
                period_start=since, period_end=until,
            )

    def metrics(self, *, limit: int = 100) -> list[dict]:
        with self._uow_factory() as uow:
            rows = uow.optimization.metrics(tenant_id=self.tenant_id,
                                            business_id=self.business_id, limit=limit)
            return [self._model_dict(row) for row in rows]

    def recommend_next_actions(self, lead_id: UUID) -> dict:
        """Rank actions from persisted lead state after deriving allowed channels via policy."""
        with self._uow_factory() as uow:
            lead = uow.leads.get(tenant_id=self.tenant_id,
                                 business_id=self.business_id, lead_id=lead_id)
            if lead is None:
                raise LookupError("lead not found in trusted business scope")
            contact = uow.contacts.get(tenant_id=self.tenant_id, contact_id=lead.contact_id)
            tenant = uow.tenants.get(tenant_id=self.tenant_id)
            business = uow.businesses.get(tenant_id=self.tenant_id,
                                          business_id=self.business_id)
            if not all((contact, tenant, business)) or contact.business_id != self.business_id:
                raise LookupError("lead contact is unavailable in trusted business scope")
            events = uow.optimization.feedback(
                tenant_id=self.tenant_id, business_id=self.business_id, limit=50000,
            )
            lead_events = [event for event in events if event.lead_id == lead_id]
            state = self._lead_state(lead, lead_events)
            allowed = {"close_lead", "escalate_to_human"}
            channel_actions = {"sms": "send_sms", "email": "send_email",
                               "whatsapp": "send_whatsapp"}
            policy = PolicyService()
            preferences = ContactPreferenceService()
            for channel, action in channel_actions.items():
                if not preferences.destination(contact, channel):
                    continue
                decision = policy.evaluate(
                    uow, tenant=tenant, business=business, contact=contact,
                    channel=channel, direction="outbound", lead_id=lead_id,
                )
                if decision.allowed:
                    allowed.add(action)
            if any(action in allowed for action in channel_actions.values()):
                allowed.add("nurture_with_follow_up")
            available_slots = uow.appointment_slots.list_available(
                tenant_id=self.tenant_id, business_id=self.business_id,
                after=self._now(),
            )
            if state == "lead_replied_positive" and available_slots:
                allowed.add("propose_appointment")
            if state in {"appointment_proposed", "appointment_confirmed"} and any(
                action in allowed for action in channel_actions.values()
            ):
                allowed.add("send_reminder")
            ranked = self.next_best_action.recommend(
                state=state, feedback_events=lead_events, allowed_actions=allowed,
            )
            if ranked:
                last_correlation = lead_events[-1].correlation_id if lead_events else f"lead:{lead_id}"
                day_key = self._now().date().isoformat()
                self.feedback.capture(
                    uow=uow, tenant_id=self.tenant_id, business_id=self.business_id,
                    event_key=f"action-recommended:{lead_id}:{state}:{day_key}",
                    correlation_id=last_correlation, event_type="action_recommended",
                    outcome="recommended", lead_id=lead_id,
                    decision={"state": state,
                              "top_actions": [row["action"] for row in ranked]},
                    event_metadata={"action_count": len(ranked)},
                )
            return {"lead_id": lead_id, "state": state, "actions": ranked}

    @staticmethod
    def _lead_state(lead, events) -> str:
        if lead.status == "opted_out" or any(event.event_type == "opted_out" for event in events):
            return "opted_out"
        if any(event.event_type == "appointment_no_show" for event in events):
            return "appointment_no_show"
        if lead.status in {"booked", "converted"} or any(
            event.event_type == "appointment_booked" for event in events
        ):
            return "appointment_confirmed"
        if any(event.event_type == "appointment_proposed" for event in events):
            return "appointment_proposed"
        replies = [event for event in events if event.event_type == "reply_received"]
        if replies:
            return ("lead_replied_positive" if replies[-1].outcome in
                    {"positive", "book_appointment", "callback"}
                    else "lead_replied_negative")
        if lead.status == "closed":
            return "lead_replied_negative"
        if any(event.event_type == "campaign_step_sent" for event in events):
            return "lead_contacted_no_reply"
        return "new_lead"

    def recommendations(self, *, status: str | None = "pending", limit: int = 100) -> list[dict]:
        with self._uow_factory() as uow:
            rows = uow.optimization.recommendations(
                tenant_id=self.tenant_id, business_id=self.business_id,
                status=status, limit=limit,
            )
            return [self._model_dict(row) for row in rows]

    def audit(self, *, limit: int = 100) -> list[dict]:
        with self._uow_factory() as uow:
            rows = uow.optimization.audit(tenant_id=self.tenant_id,
                                          business_id=self.business_id, limit=limit)
            return [self._model_dict(row) for row in rows]

    def approve(self, principal: AuthenticatedPrincipal, recommendation_id: UUID) -> dict:
        self._authorize(principal, "optimization_approver")
        with self._uow_factory() as uow:
            row = uow.optimization.recommendation(
                tenant_id=self.tenant_id, business_id=self.business_id,
                recommendation_id=recommendation_id,
            )
            if row is None or row.status != "pending":
                raise LookupError("pending recommendation not found")
            if row.recommendation_type == "audience_expansion":
                row.status, row.reviewed_by, row.reviewed_at = (
                    "approved", principal.principal_id, self._now(),
                )
                uow.optimization.add_audit(
                    tenant_id=self.tenant_id, business_id=self.business_id,
                    recommendation_id=row.id, action="audience_expansion_approved",
                    actor_id=principal.principal_id,
                    details={"change": row.change, "confidence": row.confidence,
                             "expected_impact": row.expected_impact,
                             "note": "Advisory only; campaign audience changes remain an explicit business action."},
                )
                metrics.event("workflow", "optimization_review", "approved")
                return {"recommendation": self._model_dict(row), "feature_flag": None,
                        "requires_campaign_review": True}
            if row.recommendation_type not in FLAG_KEYS:
                raise ValueError("recommendation cannot change policy or compliance rules")
            flag_key = FLAG_KEYS[row.recommendation_type]
            prior = uow.optimization.feature_flag(
                tenant_id=self.tenant_id, business_id=self.business_id, flag_key=flag_key,
            )
            previous = ({"enabled": prior.enabled, "rollout_percentage": prior.rollout_percentage,
                         "config": prior.config} if prior else None)
            config = dict(row.change)
            config["approved_at"] = self._now().isoformat()
            config["recommendation_id"] = str(row.id)
            config["previous"] = previous
            flag = uow.optimization.put_feature_flag(
                tenant_id=self.tenant_id, business_id=self.business_id,
                flag_key=flag_key, enabled=True, rollout_percentage=10, config=config,
            )
            row.status, row.reviewed_by, row.reviewed_at = "applied", principal.principal_id, self._now()
            uow.optimization.add_audit(
                tenant_id=self.tenant_id, business_id=self.business_id,
                recommendation_id=row.id, action="approved_and_staged",
                actor_id=principal.principal_id,
                details={"flag_key": flag_key, "rollout_percentage": 10,
                         "change": row.change, "confidence": row.confidence,
                         "expected_impact": row.expected_impact},
            )
            metrics.event("workflow", "optimization_review", "approved")
            return {"recommendation": self._model_dict(row), "feature_flag": self._model_dict(flag)}

    def reject(self, principal: AuthenticatedPrincipal, recommendation_id: UUID,
               rationale: str) -> dict:
        self._authorize(principal, "optimization_approver")
        rationale = rationale.strip()
        if not rationale or len(rationale) > 1000:
            raise ValueError("rejection rationale must contain 1-1000 characters")
        with self._uow_factory() as uow:
            row = uow.optimization.recommendation(
                tenant_id=self.tenant_id, business_id=self.business_id,
                recommendation_id=recommendation_id,
            )
            if row is None or row.status != "pending":
                raise LookupError("pending recommendation not found")
            row.status, row.reviewed_by, row.reviewed_at = "rejected", principal.principal_id, self._now()
            uow.optimization.add_audit(
                tenant_id=self.tenant_id, business_id=self.business_id,
                recommendation_id=row.id, action="rejected", actor_id=principal.principal_id,
                details={"rationale": rationale},
            )
            metrics.event("workflow", "optimization_review", "rejected")
            return self._model_dict(row)

    def capture_manual_feedback(self, *, event_key: str, correlation_id: str,
                                event_type: str, outcome: str,
                                decision: dict, event_metadata: dict,
                                lead_id: UUID | None = None,
                                campaign_id: UUID | None = None,
                                appointment_id: UUID | None = None,
                                principal: AuthenticatedPrincipal | None = None):
        with self._uow_factory() as uow:
            contact_id = None
            if event_type == "appointment_no_show":
                requested_lead_id = lead_id
                if appointment_id is None:
                    raise ValueError("appointment ID is required for no-show feedback")
                appointment = uow.appointments.get_for_tenant(
                    tenant_id=self.tenant_id, business_id=self.business_id,
                    appointment_id=appointment_id,
                )
                now = self._now()
                ends_at = appointment.ends_at if appointment else now
                if ends_at.tzinfo is None:
                    ends_at = ends_at.replace(tzinfo=UTC)
                if appointment is None or appointment.status != "confirmed" or ends_at > now:
                    raise LookupError("eligible confirmed appointment not found")
                if lead_id is not None and appointment.campaign_enrollment_id is None:
                    raise ValueError("appointment is not linked to the supplied lead")
                if campaign_id is not None and appointment.campaign_id != campaign_id:
                    raise ValueError("appointment is not linked to the supplied campaign")
                lead_id = None
                if appointment.campaign_enrollment_id and appointment.campaign_id:
                    enrollment = uow.campaign_enrollments.get(
                        tenant_id=self.tenant_id, business_id=self.business_id,
                        campaign_id=appointment.campaign_id,
                        enrollment_id=appointment.campaign_enrollment_id,
                    )
                    lead_id = enrollment.lead_id if enrollment else None
                if requested_lead_id is not None and lead_id != requested_lead_id:
                    raise ValueError("appointment is not linked to the supplied lead")
                campaign_id = appointment.campaign_id
                contact_id = appointment.contact_id
                event_key = f"appointment-no-show:{appointment.id}"
                correlation_id = appointment.correlation_id or correlation_id
                outcome = "no_show"
            if lead_id is not None and uow.leads.get(
                tenant_id=self.tenant_id, business_id=self.business_id, lead_id=lead_id,
            ) is None:
                raise LookupError("lead not found in trusted business scope")
            if campaign_id is not None and uow.campaigns.get(
                tenant_id=self.tenant_id, business_id=self.business_id, campaign_id=campaign_id,
            ) is None:
                raise LookupError("campaign not found in trusted business scope")
            row = self.feedback.capture(
                uow=uow, tenant_id=self.tenant_id, business_id=self.business_id,
                event_key=event_key, correlation_id=correlation_id,
                event_type=event_type, outcome=outcome, decision=decision,
                event_metadata=event_metadata, lead_id=lead_id,
                campaign_id=campaign_id, contact_id=contact_id,
            )
            uow.optimization.add_audit(
                tenant_id=self.tenant_id, business_id=self.business_id,
                action="manual_feedback_submitted",
                actor_id=principal.principal_id if principal else "system",
                details={"feedback_event_id": str(row.id), "event_type": event_type,
                         "outcome": outcome},
            )
            return row

    def monitor_rollout(self, *, flag_key: str, minimum_sample: int = 20) -> dict:
        """Advance 10→50→100%; revert the previous config if conversion degrades."""
        if flag_key not in FLAG_KEYS.values():
            raise ValueError("unknown optimization feature flag")
        with self._uow_factory() as uow:
            flag = uow.optimization.feature_flag(
                tenant_id=self.tenant_id, business_id=self.business_id, flag_key=flag_key,
            )
            if flag is None or not flag.enabled:
                return {"status": "inactive"}
            events = uow.optimization.feedback(
                tenant_id=self.tenant_id, business_id=self.business_id,
                since=datetime.fromisoformat(flag.config["approved_at"]), limit=50000,
            )
            def cohort_key(event):
                lead_key = str(event.lead_id)
                return (lead_key if flag_key == "optimization.lead_scoring" else
                        f"{event.campaign_id}:{lead_key}")

            exposures = {cohort_key(event): bool(event.decision.get("applied"))
                         for event in events
                         if event.event_type == "feature_flag_exposure"
                         and event.lead_id is not None
                         and event.decision.get("flag_key") == flag_key}
            booked = {cohort_key(event) for event in events
                      if event.event_type == "appointment_booked" and event.lead_id is not None
                      and (flag_key == "optimization.lead_scoring" or event.campaign_id is not None)}
            treatment = {lead for lead, applied in exposures.items() if applied}
            control = {lead for lead, applied in exposures.items() if not applied}
            if len(treatment) < minimum_sample:
                return {"status": "insufficient_sample", "sample_size": len(treatment)}
            current_rate = len(treatment & booked) / len(treatment)
            baseline_value = flag.config.get("baseline_conversion_rate")
            if baseline_value is None:
                if len(control) < minimum_sample:
                    return {"status": "insufficient_control_sample", "sample_size": len(control)}
                baseline_value = len(control & booked) / len(control)
            baseline = float(baseline_value)
            if baseline > 0 and current_rate < baseline * 0.9:
                active_recommendation_id = flag.config.get("recommendation_id")
                previous = flag.config.get("previous")
                flag.enabled = bool(previous and previous["enabled"])
                flag.rollout_percentage = int(previous["rollout_percentage"]) if previous else 0
                flag.config = dict(previous["config"]) if previous else {}
                if active_recommendation_id:
                    try:
                        recommendation = uow.optimization.recommendation(
                            tenant_id=self.tenant_id, business_id=self.business_id,
                            recommendation_id=UUID(str(active_recommendation_id)),
                        )
                    except ValueError:
                        recommendation = None
                    if recommendation is not None:
                        recommendation.status = "rolled_back"
                uow.optimization.add_audit(
                    tenant_id=self.tenant_id, business_id=self.business_id,
                    action="automatic_rollback", actor_id="optimization_monitor",
                    details={"flag_key": flag_key, "baseline": baseline,
                             "observed": current_rate, "sample_size": len(treatment)},
                )
                metrics.event("workflow", "optimization_rollout", "rolled_back")
                return {"status": "rolled_back", "observed_conversion_rate": current_rate}
            if flag.rollout_percentage >= 100:
                return {"status": "complete", "rollout_percentage": 100,
                        "observed_conversion_rate": current_rate}
            next_percentage = 50 if flag.rollout_percentage == 10 else 100
            flag.rollout_percentage = next_percentage
            flag.config = dict(flag.config) | {"baseline_conversion_rate": current_rate}
            uow.optimization.add_audit(
                tenant_id=self.tenant_id, business_id=self.business_id,
                action="rollout_advanced", actor_id="optimization_monitor",
                details={"flag_key": flag_key, "rollout_percentage": next_percentage,
                         "observed_conversion_rate": current_rate,
                         "sample_size": len(treatment)},
            )
            metrics.event("workflow", "optimization_rollout", "advanced")
            return {"status": "advanced", "rollout_percentage": next_percentage,
                    "observed_conversion_rate": current_rate}

    def monitor_active_rollouts(self, *, minimum_sample: int = 20) -> list[dict]:
        with self._uow_factory() as uow:
            flags = uow.optimization.feature_flags(
                tenant_id=self.tenant_id, business_id=self.business_id,
            )
            keys = [row.flag_key for row in flags
                    if row.enabled and row.flag_key in FLAG_KEYS.values()]
        return [{"flag_key": key,
                 **self.monitor_rollout(flag_key=key, minimum_sample=minimum_sample)}
                for key in keys]

    @staticmethod
    def _authorize(principal: AuthenticatedPrincipal, role: str) -> None:
        if not isinstance(principal, AuthenticatedPrincipal) or role not in principal.roles:
            raise PermissionError("optimization operation denied")

    @staticmethod
    def _draft_dict(draft) -> dict:
        return {"recommendation_type": draft.recommendation_type, "change": draft.change,
                "confidence": draft.confidence, "expected_impact": draft.expected_impact,
                "sample_size": draft.sample_size, "rationale": draft.rationale}

    @staticmethod
    def _model_dict(row) -> dict:
        return {key: value for key, value in row.model_dump().items()}
