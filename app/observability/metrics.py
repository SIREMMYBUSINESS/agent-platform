"""Small dependency-free Prometheus registry with bounded label values."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from threading import Lock


_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_LABEL_VALUE = re.compile(r"^[a-zA-Z0-9_./{}:-]{1,100}$")
_ALLOWED_LABELS = {"method", "route", "status", "component", "operation", "outcome"}
_ALLOWED_COMPONENTS = {"http", "outreach", "crm", "workflow", "test"}
_ALLOWED_OUTCOMES = {
    "success", "failure", "failed", "provider", "succeeded", "attempted", "scheduled",
}
_ALLOWED_OPERATIONS = {
    "request", "unhandled_exception", "send_attempt", "failure", "retry",
    "sync_attempt", "sync", "sync_request", "execution", "delivery_receipt", "scrape",
}
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsRegistry:
    """Process-local counters and latency histograms; never labels tenant data."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
        self._histograms: dict[str, list[float | int]] = {}

    @staticmethod
    def _labels(**labels: str) -> tuple[tuple[str, str], ...]:
        clean = []
        for key, value in labels.items():
            if (key not in _ALLOWED_LABELS or not _LABEL_VALUE.fullmatch(value)):
                raise ValueError("metric labels must use bounded identifier values")
            if key == "component" and value not in _ALLOWED_COMPONENTS:
                raise ValueError("metric component must be from the bounded set")
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
            clean.append((key, value))
        return tuple(sorted(clean))

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        if not _NAME.fullmatch(name) or amount < 0:
            raise ValueError("invalid metric counter")
        with self._lock:
            self._counters[(name, self._labels(**labels))] += amount

    def observe(self, name: str, value: float) -> None:
        if not _NAME.fullmatch(name) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid metric observation")
        with self._lock:
            histogram = self._histograms.setdefault(name, [0] * len(_BUCKETS) + [0, 0.0])
            for index, bound in enumerate(_BUCKETS):
                if value <= bound:
                    histogram[index] += 1
            histogram[len(_BUCKETS)] += 1
            histogram[len(_BUCKETS) + 1] += value

    def event(self, component: str, operation: str, outcome: str) -> None:
        """Record bounded application outcomes such as sends, retries, and failures."""
        self.increment("platform_operations_total", component=component,
                       operation=operation, outcome=outcome)

    def render(self) -> str:
        with self._lock:
            counters = [(name, labels, value)
                        for (name, labels), value in self._counters.items()]
            histograms = {name: tuple(values) for name, values in self._histograms.items()}
        lines = [
            "# HELP platform_operations_total Completed application operations by bounded category.",
            "# TYPE platform_operations_total counter",
        ]
        for name, labels, value in sorted(counters):
            label_text = _format_labels(labels)
            lines.append(f"{name}{label_text} {value}")
        for name, values in sorted(histograms.items()):
            lines.extend((f"# TYPE {name} histogram",))
            cumulative = 0
            for bound in _BUCKETS:
                index = _BUCKETS.index(bound)
                cumulative += int(values[index])
                lines.append(f'{name}_bucket{{le="{bound:g}"}} {cumulative}')
            count = int(values[len(_BUCKETS)])
            lines.append(f'{name}_bucket{{le="+Inf"}} {count}')
            lines.append(f"{name}_count {count}")
            lines.append(f"{name}_sum {float(values[len(_BUCKETS) + 1]):.9g}")
        return "\n".join(lines) + "\n"


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    values = ",".join(f'{key}="{value}"' for key, value in labels)
    return "{" + values + "}"


metrics = MetricsRegistry()
