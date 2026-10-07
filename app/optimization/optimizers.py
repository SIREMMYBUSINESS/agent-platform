"""Explainable analysis helpers; all inputs are already tenant/business scoped."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import atanh, erf, sqrt
from typing import Iterable
from app.optimization.segments import normalized_segment


@dataclass(frozen=True)
class RecommendationDraft:
    recommendation_type: str
    change: dict
    confidence: float
    expected_impact: float
    sample_size: int
    rationale: str


def _correlation(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x == 0 or variance_y == 0:
        return 0.0
    return covariance / sqrt(variance_x * variance_y)


def _normal_confidence(z_score: float) -> float:
    """Two-sided normal approximation expressed as confidence in a difference."""
    return erf(abs(z_score) / sqrt(2))


def _rate_difference_confidence(a: int, n_a: int, b: int, n_b: int) -> float:
    p_a = min(1.0, max(0.0, a / max(1, n_a)))
    p_b = min(1.0, max(0.0, b / max(1, n_b)))
    standard_error = sqrt(p_a * (1 - p_a) / max(1, n_a) +
                          p_b * (1 - p_b) / max(1, n_b))
    return 0.0 if standard_error == 0 else _normal_confidence((p_a - p_b) / standard_error)


class LeadScoringOptimizer:
    signals = ("fit_score", "urgency_score", "intent_score")

    def recommend(self, events, *, minimum_sample: int = 30,
                  confidence_threshold: float = 0.75) -> RecommendationDraft | None:
        outcomes = {}
        snapshots = {}
        for event in events:
            lead = str(event.lead_id or event.decision.get("lead_key", ""))
            if not lead:
                continue
            if event.event_type == "lead_scored":
                snapshots[lead] = event.decision
            elif event.event_type == "appointment_booked":
                outcomes[lead] = 1.0
            elif event.event_type in {"opted_out", "contact_resolved_without_escalation"}:
                outcomes.setdefault(lead, 0.0)
        points = [(snapshots[key], outcome) for key, outcome in outcomes.items() if key in snapshots]
        if len(points) < minimum_sample:
            return None
        correlations = {
            signal: _correlation([float(point.get(signal, 0)) for point, _ in points],
                                 [outcome for _, outcome in points])
            for signal in self.signals
        }
        ranked = sorted(correlations, key=lambda key: correlations[key], reverse=True)
        strongest, weakest = ranked[0], ranked[-1]
        strongest_r = max(-0.9999, min(0.9999, correlations[strongest]))
        weakest_r = max(-0.9999, min(0.9999, correlations[weakest]))
        z_score = abs(atanh(strongest_r) - atanh(weakest_r)) * sqrt((len(points) - 3) / 2)
        confidence = _normal_confidence(z_score)
        signal_confidence = abs(correlations[strongest] - correlations[weakest])
        if confidence < confidence_threshold or signal_confidence < 0.05:
            return None
        weights = {"fit_score": 0.4, "urgency_score": 0.3, "intent_score": 0.3}
        weights[strongest] += 0.05
        weights[weakest] -= 0.05
        return RecommendationDraft(
            "lead_scoring_weights", {"weights": weights}, confidence,
            max(0.0, (correlations[strongest] - correlations[weakest]) * 0.05),
            len(points),
            f"{strongest} correlates more strongly with booking than {weakest}; "
            f"observed correlations {correlations}.",
        )


class MessageVariantOptimizer:
    def recommend(self, events, *, minimum_sample: int = 30,
                  confidence_threshold: float = 0.75) -> list[RecommendationDraft]:
        groups = defaultdict(lambda: defaultdict(lambda: {"sent": 0, "reply": 0, "positive": 0, "opt_out": 0}))
        for event in events:
            decision = event.decision
            variant = decision.get("variant")
            if not variant:
                continue
            group = str(decision.get("test_group", "all"))
            campaign = str(event.campaign_id or decision.get("campaign_key", "unknown"))
            key = (campaign, str(decision.get("step", "all")), group)
            data = groups[key][str(variant)]
            if event.event_type == "campaign_step_sent":
                data["sent"] += 1
            elif event.event_type == "reply_received":
                data["reply"] += 1
                data["positive"] += int(event.outcome in {"positive", "appointment_booked"})
            elif event.event_type == "opted_out":
                data["opt_out"] += 1
        drafts = []
        for (campaign, step, group), variants in groups.items():
            eligible = {name: row for name, row in variants.items() if row["sent"] >= minimum_sample}
            if len(eligible) < 2:
                continue
            rates = {name: (row["reply"] + 1) / (row["sent"] + 2) -
                     0.5 * row["opt_out"] / max(1, row["sent"])
                     for name, row in eligible.items()}
            ordered = sorted(rates, key=rates.get, reverse=True)
            winner, runner_up = ordered[0], ordered[1]
            n = sum(row["sent"] for row in eligible.values())
            confidence = _rate_difference_confidence(
                eligible[winner]["reply"], eligible[winner]["sent"],
                eligible[runner_up]["reply"], eligible[runner_up]["sent"],
            )
            if confidence < confidence_threshold:
                continue
            drafts.append(RecommendationDraft(
                "message_variant_weights",
                {"campaign_id": campaign, "step": step, "test_group": group,
                 "weights": {winner: 80, runner_up: 20}},
                confidence, rates[winner] - rates[runner_up], n,
                f"Variant {winner} leads within isolated group {group}; adjusted reply rates {rates}.",
            ))
        return drafts


class TimingOptimizer:
    def recommend(self, events, *, minimum_sample: int = 30,
                  confidence_threshold: float = 0.75) -> list[RecommendationDraft]:
        groups = defaultdict(lambda: defaultdict(lambda: {"sent": 0, "opened": 0, "replied": 0}))
        for event in events:
            decision = event.decision
            hour = decision.get("sent_hour")
            if hour is None:
                continue
            segment = str(decision.get("segment", "default"))
            row = groups[segment][int(hour)]
            if event.event_type == "campaign_step_sent":
                row["sent"] += 1
            elif event.event_type == "message_opened":
                row["opened"] += 1
            elif event.event_type == "reply_received":
                row["replied"] += 1
        drafts = []
        for segment, hours in groups.items():
            eligible = {hour: row for hour, row in hours.items() if row["sent"] >= minimum_sample}
            if len(eligible) < 2:
                continue
            rates = {hour: (row["opened"] + row["replied"] + 1) / (row["sent"] + 2)
                     for hour, row in eligible.items()}
            best, baseline = max(rates, key=rates.get), min(rates, key=rates.get)
            n = sum(row["sent"] for row in eligible.values())
            best_row, baseline_row = eligible[best], eligible[baseline]
            confidence = _rate_difference_confidence(
                best_row["opened"] + best_row["replied"], best_row["sent"],
                baseline_row["opened"] + baseline_row["replied"], baseline_row["sent"],
            )
            if best != baseline and confidence >= confidence_threshold:
                drafts.append(RecommendationDraft(
                    "send_time", {"segment": segment, "send_hour": best}, confidence,
                    rates[best] - rates[baseline], n,
                    f"Hour {best} has the strongest smoothed open/reply rate for segment {segment}: {rates}.",
                ))
        return drafts


class AudienceSegmentOptimizer:
    """Produce human-reviewable expansion insights from unique lead outcomes."""

    def recommend(self, events, *, minimum_sample: int = 30,
                  confidence_threshold: float = 0.75) -> list[RecommendationDraft]:
        snapshots = {}
        outcomes = {}
        for event in events:
            lead_id = str(event.lead_id or "")
            if not lead_id:
                continue
            if event.event_type == "lead_scored":
                segment = normalized_segment(
                    source=event.decision.get("source", "other"),
                    industry=event.decision.get("industry", "unknown"),
                    country=event.decision.get("country", "unknown"),
                )
                snapshots[lead_id] = tuple(
                    part.split("=", 1)[1] for part in segment.split(";")
                )
            elif event.event_type == "appointment_booked":
                outcomes[lead_id] = 1
            elif event.event_type in {"opted_out", "contact_resolved_without_escalation"}:
                outcomes.setdefault(lead_id, 0)
        grouped = defaultdict(lambda: [0, 0])
        for lead_id, segment in snapshots.items():
            if lead_id not in outcomes:
                continue
            grouped[segment][0] += outcomes[lead_id]
            grouped[segment][1] += 1
        drafts = []
        for segment, (booked, count) in grouped.items():
            if count < minimum_sample:
                continue
            remainder = [(other_booked, other_count)
                         for other_segment, (other_booked, other_count) in grouped.items()
                         if other_segment != segment]
            other_booked = sum(item[0] for item in remainder)
            other_count = sum(item[1] for item in remainder)
            if other_count < minimum_sample:
                continue
            confidence = _rate_difference_confidence(booked, count, other_booked, other_count)
            rate, baseline = booked / count, other_booked / other_count
            if rate <= baseline or confidence < confidence_threshold:
                continue
            source, industry, country = segment
            drafts.append(RecommendationDraft(
                "audience_expansion",
                {"source": source, "industry": industry, "country": country,
                 "conversion_rate": rate, "comparison_rate": baseline,
                 "suggestion": "review_similar_audience_expansion"},
                confidence, rate - baseline, count + other_count,
                f"Segment {segment} converted at {rate:.3f} versus {baseline:.3f} for other scored leads; review similar audiences.",
            ))
        return drafts


class NextBestActionEngine:
    default_actions = {
        "new_lead": ("send_sms", "send_email", "send_whatsapp", "escalate_to_human"),
        "lead_contacted_no_reply": ("nurture_with_follow_up", "send_email", "send_sms", "close_lead"),
        "lead_replied_positive": ("propose_appointment", "send_sms", "escalate_to_human"),
        "lead_replied_negative": ("close_lead", "nurture_with_follow_up"),
        "appointment_proposed": ("send_reminder", "escalate_to_human"),
        "appointment_confirmed": ("send_reminder", "nurture_with_follow_up"),
        "appointment_no_show": ("send_sms", "send_email", "escalate_to_human"),
        "opted_out": ("close_lead",),
    }

    def recommend(self, *, state: str, feedback_events=(), allowed_actions: Iterable[str] | None = None):
        if state not in self.default_actions:
            raise ValueError("unsupported next-best-action state")
        allowed = set(allowed_actions) if allowed_actions is not None else set(self.default_actions[state])
        if state == "opted_out":
            allowed.intersection_update({"close_lead"})
        observations = defaultdict(lambda: [0, 0])
        for event in feedback_events:
            action = event.decision.get("action")
            if action in allowed and event.decision.get("state") == state:
                observations[action][0] += 1
                observations[action][1] += int(event.outcome == "appointment_booked")
        ranked = []
        for action in self.default_actions[state]:
            if action not in allowed:
                continue
            count, conversions = observations[action]
            rate = (conversions + 1) / (count + 2)
            ranked.append({"action": action, "predicted_conversion_rate": rate,
                           "sample_size": count})
        return sorted(ranked, key=lambda row: (-row["predicted_conversion_rate"], row["action"]))[:3]


class RoutingOptimizer:
    def recommend(self, events, *, minimum_sample: int = 30,
                  confidence_threshold: float = 0.75) -> RecommendationDraft | None:
        outcomes = [event for event in events if event.event_type in {
            "contact_escalated", "contact_resolved_without_escalation",
        }]
        # Creation and resolution are separate feedback records for one ticket.
        # Keep the latest record per ticket so resolved escalations are not
        # counted twice in both the sample size and failure rate.
        tickets = {}
        for index, event in enumerate(outcomes):
            ticket_id = event.decision.get("ticket_id")
            key = str(ticket_id) if ticket_id else f"event:{index}"
            tickets[key] = event
        outcomes = list(tickets.values())
        if len(outcomes) < minimum_sample:
            return None
        escalated = [event for event in outcomes if event.event_type == "contact_escalated"]
        failed = sum(event.outcome in {"unresolved", "timeout", "failed"} for event in escalated)
        fail_rate = failed / max(1, len(escalated))
        direction = -5 if fail_rate > 0.25 else 5 if fail_rate < 0.05 else 0
        standard_error = sqrt(0.15 * 0.85 / max(1, len(escalated)))
        confidence = _normal_confidence((fail_rate - 0.15) / standard_error) if standard_error else 0.0
        if direction == 0 or confidence < confidence_threshold:
            return None
        return RecommendationDraft(
            "escalation_threshold", {"threshold_delta": direction}, confidence,
            abs(fail_rate - 0.15), len(outcomes),
            f"Escalation unresolved/timeout rate is {fail_rate:.3f} across {len(escalated)} escalations.",
        )
