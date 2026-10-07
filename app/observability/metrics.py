"""Small dependency-free Prometheus registry with bounded label values."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from threading import Lock


_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_LABEL_VALUE = re.compile(r"^[a-zA-Z0-9_./{}:-]{1,100}$")
_ALLOWED_LABELS = {
    "method", "route", "status", "component", "operation", "outcome", "queue_domain",
    "channel", "provider", "tenant_bucket", "business_bucket", "campaign_bucket",
    "direction", "classification", "source", "task_status",
}
_ALLOWED_COMPONENTS = {"http", "outreach", "crm", "workflow", "test"}
_ALLOWED_QUEUE_DOMAINS = {"inbound", "outreach", "scheduling", "escalation", "analytics"}
_ALLOWED_OUTCOMES = {
    "success", "failure", "failed", "provider", "succeeded", "attempted", "scheduled",
    "created", "reused", "queued", "retrying", "dead_letter",
    "allowed", "blocked", "rejected", "opt_out", "healthy", "unhealthy",
    "positive", "negative", "unknown", "booked", "completed", "dry_run",
    "generated", "approved", "rejected", "rolled_back", "advanced",
}
_ALLOWED_OPERATIONS = {
    "request", "unhandled_exception", "send_attempt", "failure", "retry",
    "sync_attempt", "sync", "sync_request", "execution", "delivery_receipt", "scrape",
    "enqueue", "dispatch", "redrive", "replay", "dead_letter",
    "intake", "appointment_booking", "outreach_delivery", "reply", "opt_out",
    "conversion", "policy_decision", "provider_call", "queue_wait",
    "queue_execution", "queue_depth", "provider_latency", "worker_heartbeat",
    "booking_confirmation", "lead_conversion",
    "optimization_recommendation", "optimization_review", "optimization_rollout",
}
_ALLOWED_CHANNELS = {"sms", "email", "voice", "call", "chat", "voicemail", "whatsapp", "unknown"}
_ALLOWED_PROVIDERS = {
    "twilio", "sendgrid", "smtp", "google_calendar", "hubspot", "salesforce", "crm", "unknown",
}
_ALLOWED_DIRECTIONS = {"inbound", "outbound"}
_ALLOWED_CLASSIFICATIONS = {"positive", "negative", "opt_out", "book_appointment", "callback", "unknown"}
_ALLOWED_SOURCES = {"inbound", "outreach", "other"}
_ALLOWED_TASK_STATUSES = {"pending", "retrying", "running", "dead_letter", "succeeded"}
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsRegistry:
    """Process-local counters and latency histograms; never labels tenant data."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float | int]] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._otel_counters: dict[str, object] = {}
        self._otel_histograms: dict[str, object] = {}

    def attach_meter(self, meter) -> None:
        """Attach OTel instruments while retaining the protected Prometheus scrape."""
        counter_names = {
            "platform_operations_total", "queue_tasks_total", "http_requests_total",
            "inbound_intake_total", "appointment_bookings_total",
            "appointment_proposals_total",
            "outreach_deliveries_total", "campaign_replies_total",
            "outreach_opt_outs_total", "lead_conversions_total",
            "policy_decisions_total", "provider_calls_total",
        }
        histogram_names = {
            "http_request_duration_seconds", "queue_task_wait_seconds",
            "queue_task_execution_seconds", "provider_request_duration_seconds",
        }
        self._otel_counters = {name: meter.create_counter(name) for name in counter_names}
        self._otel_histograms = {
            name: meter.create_histogram(name, unit="s") for name in histogram_names
        }

    @staticmethod
    def _labels(**labels: str) -> tuple[tuple[str, str], ...]:
        clean = []
        for key, value in labels.items():
            if (key not in _ALLOWED_LABELS or not _LABEL_VALUE.fullmatch(value)):
                raise ValueError("metric labels must use bounded identifier values")
            if key == "component" and value not in _ALLOWED_COMPONENTS:
                raise ValueError("metric component must be from the bounded set")
            if key == "queue_domain" and value not in _ALLOWED_QUEUE_DOMAINS:
                raise ValueError("metric queue domain must be from the bounded set")
            if key == "outcome" and value not in _ALLOWED_OUTCOMES:
                raise ValueError("metric outcome must be from the bounded set")
            if key == "operation" and value not in _ALLOWED_OPERATIONS:
                raise ValueError("metric operation must be from the bounded set")
            if key == "status" and (not value.isdigit() or not 100 <= int(value) <= 599):
                raise ValueError("metric status must be an HTTP status code")
            if key == "method" and value not in {
                "get", "post", "put", "patch", "delete", "head", "options", "other",
            }:
                raise ValueError("metric method must be from the bounded set")
            if key == "route" and (not value.startswith("/") and value != "dynamic_route"):
                raise ValueError("metric route must be a route template")
            if key == "channel" and value not in _ALLOWED_CHANNELS:
                raise ValueError("metric channel must be from the bounded set")
            if key == "provider" and value not in _ALLOWED_PROVIDERS:
                raise ValueError("metric provider must be from the bounded set")
            if key == "direction" and value not in _ALLOWED_DIRECTIONS:
                raise ValueError("metric direction must be from the bounded set")
            if key == "classification" and value not in _ALLOWED_CLASSIFICATIONS:
                raise ValueError("metric classification must be from the bounded set")
            if key == "source" and value not in _ALLOWED_SOURCES:
                raise ValueError("metric source must be from the bounded set")
            if key == "task_status" and value not in _ALLOWED_TASK_STATUSES:
                raise ValueError("metric task status must be from the bounded set")
            if key.endswith("_bucket") and (not value.isdigit() or int(value) >= 64):
                raise ValueError("metric scope bucket is outside the bounded range")
            clean.append((key, value))
        return tuple(sorted(clean))

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        if not _NAME.fullmatch(name) or amount < 0:
            raise ValueError("invalid metric counter")
        bounded_labels = self._labels(**labels)
        with self._lock:
            self._counters[(name, bounded_labels)] += amount
        instrument = self._otel_counters.get(name)
        if instrument is not None:
            instrument.add(amount, dict(bounded_labels))

    def observe(self, name: str, value: float, **labels: str) -> None:
        if not _NAME.fullmatch(name) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid metric observation")
        bounded_labels = self._labels(**labels)
        with self._lock:
            histogram = self._histograms.setdefault(
                (name, bounded_labels), [0] * len(_BUCKETS) + [0, 0.0],
            )
            for index, bound in enumerate(_BUCKETS):
                if value <= bound:
                    histogram[index] += 1
            histogram[len(_BUCKETS)] += 1
            histogram[len(_BUCKETS) + 1] += value
        instrument = self._otel_histograms.get(name)
        if instrument is not None:
            instrument.record(value, dict(bounded_labels))

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        if not _NAME.fullmatch(name) or not math.isfinite(value):
            raise ValueError("invalid metric gauge")
        with self._lock:
            self._gauges[(name, self._labels(**labels))] = value

    def queue_depth(self, *, domain: str, status: str, value: int) -> None:
        self.set_gauge("queue_depth", float(value), queue_domain=domain, task_status=status)

    def appointment_booking(self, *, source: str, tenant_id: str, business_id: str) -> None:
        self.increment("appointment_bookings_total", source=source,
                       **self.scoped_labels(tenant_id=tenant_id, business_id=business_id))

    def appointment_proposal(self, *, source: str, tenant_id: str, business_id: str) -> None:
        self.increment("appointment_proposals_total", source=source,
                       **self.scoped_labels(tenant_id=tenant_id, business_id=business_id))

    def outreach_delivery(self, *, channel: str, outcome: str,
                          tenant_id: str, business_id: str) -> None:
        self.increment("outreach_deliveries_total", channel=channel, outcome=outcome,
                       **self.scoped_labels(tenant_id=tenant_id, business_id=business_id))

    def campaign_reply(self, *, channel: str, classification: str, campaign_id: str) -> None:
        self.increment("campaign_replies_total", channel=channel,
                       classification=classification,
                       campaign_bucket=self.scope_bucket(campaign_id, buckets=32))

    def opt_out(self, *, channel: str, tenant_id: str, business_id: str) -> None:
        self.increment("outreach_opt_outs_total", channel=channel,
                       **self.scoped_labels(tenant_id=tenant_id, business_id=business_id))

    def lead_conversion(self, *, source: str, tenant_id: str, business_id: str) -> None:
        self.increment("lead_conversions_total", source=source,
                       **self.scoped_labels(tenant_id=tenant_id, business_id=business_id))

    @staticmethod
    def scope_bucket(value: str, *, buckets: int = 32) -> str:
        """Stable, non-reversible tenant/business bucket for bounded aggregation."""
        if not isinstance(value, str) or not value or not 1 <= buckets <= 64:
            raise ValueError("scope bucket input is invalid")
        digest = hashlib.blake2s(value.encode("utf-8"), digest_size=4).digest()
        return f"{int.from_bytes(digest, 'big') % buckets:02d}"

    def scoped_labels(self, *, tenant_id: str, business_id: str) -> dict[str, str]:
        return {
            "tenant_bucket": self.scope_bucket(tenant_id, buckets=32),
            "business_bucket": self.scope_bucket(business_id, buckets=64),
        }

    def intake(self, *, channel: str, outcome: str, tenant_id: str, business_id: str) -> None:
        self.increment("inbound_intake_total", channel=channel, outcome=outcome,
                       **self.scoped_labels(tenant_id=tenant_id, business_id=business_id))

    def policy_decision(self, *, channel: str, direction: str, allowed: bool,
                        tenant_id: str, business_id: str) -> None:
        self.increment(
            "policy_decisions_total", channel=channel, direction=direction,
            outcome="allowed" if allowed else "blocked",
            **self.scoped_labels(tenant_id=tenant_id, business_id=business_id),
        )

    def provider_call(self, *, provider: str, operation: str, outcome: str,
                      duration_seconds: float, tenant_id: str, business_id: str) -> None:
        scope = self.scoped_labels(tenant_id=tenant_id, business_id=business_id)
        self.increment("provider_calls_total", provider=provider, operation=operation,
                       outcome=outcome, **scope)
        self.observe("provider_request_duration_seconds", duration_seconds,
                     provider=provider, operation=operation)

    def event(self, component: str, operation: str, outcome: str) -> None:
        """Record bounded application outcomes such as sends, retries, and failures."""
        self.increment("platform_operations_total", component=component,
                       operation=operation, outcome=outcome)

    def queue_event(self, domain: str, operation: str, outcome: str) -> None:
        """Record queue lifecycle metrics without tenant or aggregate labels."""
        self.increment("queue_tasks_total", queue_domain=domain,
                       operation=operation, outcome=outcome)

    def render(self) -> str:
        with self._lock:
            counters = [(name, labels, value)
                        for (name, labels), value in self._counters.items()]
            histograms = {key: tuple(values) for key, values in self._histograms.items()}
            gauges = dict(self._gauges)
        lines = []
        for name in sorted({item[0] for item in counters}):
            lines.extend((f"# HELP {name} Bounded application observations.", f"# TYPE {name} counter"))
            for metric_name, labels, value in sorted(item for item in counters if item[0] == name):
                label_text = _format_labels(labels)
                lines.append(f"{metric_name}{label_text} {value}")
        histogram_names = sorted({name for name, _ in histograms})
        for name in histogram_names:
            lines.extend((f"# HELP {name} Application latency distribution.", f"# TYPE {name} histogram"))
        for (name, labels), values in sorted(histograms.items()):
            label_text = _format_labels(labels)
            cumulative = 0
            for index, bound in enumerate(_BUCKETS):
                cumulative += int(values[index])
                suffix = "," if label_text else ""
                lines.append(f'{name}_bucket{{{label_text[1:-1]}{suffix}le="{bound:g}"}} {cumulative}')
            count = int(values[len(_BUCKETS)])
            suffix = "," if label_text else ""
            lines.append(f'{name}_bucket{{{label_text[1:-1]}{suffix}le="+Inf"}} {count}')
            lines.append(f"{name}_count{label_text} {count}")
            lines.append(f"{name}_sum{label_text} {float(values[len(_BUCKETS) + 1]):.9g}")
        gauge_names = sorted({name for name, _ in gauges})
        for name in gauge_names:
            lines.extend((f"# HELP {name} Current platform health observation.", f"# TYPE {name} gauge"))
        for (name, labels), value in sorted(gauges.items()):
            label_text = _format_labels(labels)
            lines.append(f"{name}{label_text} {value:g}")
        return "\n".join(lines) + "\n"


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    values = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + values + "}"


metrics = MetricsRegistry()
