import json
import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_authenticated_principal
from app.main import app
from app.observability.logging import StructuredFormatter
from app.observability.metrics import MetricsRegistry, metrics
from app.observability.request_context import (
    create_request_context, current_request_context, traceparent_header,
)
from app.security import AuthenticatedPrincipal


def test_metrics_registry_renders_counters_and_bounded_histogram():
    registry = MetricsRegistry()
    registry.event("outreach", "retry", "scheduled")
    registry.observe("test_request_duration_seconds", 0.02)
    registry.observe("test_request_duration_seconds", 0.5)

    rendered = registry.render()
    assert 'platform_operations_total{component="outreach",operation="retry",outcome="scheduled"} 1' in rendered
    assert 'test_request_duration_seconds_bucket{le="0.025"} 1' in rendered
    assert "test_request_duration_seconds_count 2" in rendered
    assert "test_request_duration_seconds_sum 0.52" in rendered
    assert "tenant_id" not in rendered


def test_metrics_registry_rejects_unbounded_metric_names_and_values():
    registry = MetricsRegistry()
    with pytest.raises(ValueError, match="bounded"):
        registry.increment("valid_metric", component="contact@example.com")
    with pytest.raises(ValueError, match="bounded"):
        registry.increment("valid_metric", tenant_id="tenant-a")


def test_request_context_reuses_valid_trace_and_rejects_untrusted_request_id():
    context = create_request_context(
        "bad request id", "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
    )
    assert len(context.request_id) == 32
    assert context.trace_id == "0123456789abcdef0123456789abcdef"
    header = traceparent_header(context)
    assert header.startswith(f"00-{context.trace_id}-")


def test_structured_formatter_emits_trace_context_without_request_payload():
    context = create_request_context("request-1", None)
    token = current_request_context.set(context)
    try:
        record = logging.LogRecord("app.observability", logging.INFO, "", 0,
                                   "request completed", (), None)
        record.method = "GET"
        record.route = "/health"
        record.status_code = 200
        output = json.loads(StructuredFormatter().format(record))
    finally:
        current_request_context.reset(token)
    assert output["request_id"] == "request-1"
    assert output["trace_id"] == context.trace_id
    assert output["route"] == "/health"
    assert "tenant_id" not in output
    assert "Authorization" not in output


def test_metrics_endpoint_requires_metrics_reader_and_returns_prometheus():
    client = TestClient(app)
    assert client.get("/metrics").status_code == 401
    principal = AuthenticatedPrincipal(
        "metrics-agent", "tenant-observability", ("metrics_reader",), "test",
        datetime.now(UTC),
    )
    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    try:
        metrics.event("test", "scrape", "succeeded")
        response = client.get("/metrics")
    finally:
        app.dependency_overrides.pop(get_authenticated_principal, None)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'platform_operations_total{component="test",operation="scrape",outcome="succeeded"} 1' in response.text
    assert "tenant-observability" not in response.text


def test_request_middleware_returns_trace_correlation_headers():
    client = TestClient(app)
    response = client.get("/health", headers={
        "X-Request-ID": "request-test-1",
        "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
    })
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-test-1"
    assert response.headers["traceparent"].startswith(
        "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-",
    )
