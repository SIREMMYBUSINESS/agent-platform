import json

from app.observability.alerts import AlertThresholds, evaluate_alerts
from app.observability.logging import StructuredFormatter
from app.observability.metrics import MetricsRegistry, metrics
from app.observability.request_context import RequestContext, current_request_context
from app.observability.telemetry import extract_context, inject_context, start_span
from app.observability.health import check_components


def test_business_metrics_emit_bounded_scope_and_channel_labels():
    registry = MetricsRegistry()
    registry.intake(channel="sms", outcome="success", tenant_id="tenant-secret",
                    business_id="business-secret")
    rendered = registry.render()
    assert "inbound_intake_total" in rendered
    assert "tenant_bucket=" in rendered and "business_bucket=" in rendered
    assert "tenant-secret" not in rendered and "business-secret" not in rendered


def test_metric_scope_buckets_have_bounded_cardinality():
    registry = MetricsRegistry()
    buckets = {registry.scope_bucket(f"tenant-{index}") for index in range(1000)}
    assert len(buckets) <= 32


def test_trace_context_propagates_across_task_carrier():
    with start_span("test.parent") as parent:
        carrier = inject_context({})
        parent_trace_id = f"{parent.get_span_context().trace_id:032x}"
    child_context = extract_context(carrier)
    with start_span("test.child", parent_context=child_context) as child:
        assert f"{child.get_span_context().trace_id:032x}" == parent_trace_id


def test_structured_formatter_includes_correlation_and_authoritative_scope():
    import logging

    token = current_request_context.set(RequestContext(
        "corr-123", "a" * 32, "b" * 16, tenant_id="tenant-a", business_id="business-a",
    ))
    try:
        record = logging.LogRecord("test", logging.INFO, "", 0, "safe event", (), None)
        payload = json.loads(StructuredFormatter().format(record))
    finally:
        current_request_context.reset(token)
    assert payload["correlation_id"] == "corr-123"
    assert payload["tenant_id"] == "tenant-a"
    assert payload["business_id"] == "business-a"


def test_alert_threshold_logic_handles_surge_and_zero_baselines():
    alerts = evaluate_alerts(
        queue_depth=101, worker_errors=2, worker_tasks=10,
        policy_blocks=4, policy_decisions=10, provider_errors=1, provider_calls=10,
        confirmation_rate=0.2, baseline_confirmation_rate=1.0,
        conversion_rate=0.1, baseline_conversion_rate=0.8,
        dead_letter_depth=10,
    )
    assert alerts == {
        "high_queue_depth": True, "high_worker_error_rate": True,
        "policy_block_surge": True, "provider_api_failures": True,
        "appointment_confirmation_drop": True, "conversion_rate_anomaly": True,
        "dead_letter_depth": True,
    }
    quiet = evaluate_alerts(
        queue_depth=0, worker_errors=0, worker_tasks=0,
        policy_blocks=0, policy_decisions=0, provider_errors=0, provider_calls=0,
        confirmation_rate=0, baseline_confirmation_rate=0,
        conversion_rate=0, baseline_conversion_rate=0, dead_letter_depth=0,
        thresholds=AlertThresholds(),
    )
    assert not any(quiet.values())


def test_prometheus_histogram_has_single_type_declaration():
    registry = MetricsRegistry()
    registry.observe("queue_task_wait_seconds", 0.2, queue_domain="inbound")
    rendered = registry.render()
    assert rendered.count("# TYPE queue_task_wait_seconds histogram") == 1
    assert 'queue_task_wait_seconds_bucket{queue_domain="inbound",le="+Inf"} 1' in rendered


def test_health_probe_checks_database_redis_and_worker_queues(monkeypatch):
    import redis

    class Session:
        def exec(self, _statement):
            return self

        def one(self):
            return (1,)

        def close(self):
            pass

    class Inspect:
        def active_queues(self):
            return {"worker-1": [{"name": "agent.inbound"}]}

    class Control:
        def inspect(self, timeout):
            assert timeout == 1.0
            return Inspect()

    class RedisClient:
        def ping(self):
            return True

    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: RedisClient())
    result = check_components(session_factory=Session, redis_url="redis://localhost:6379/0",
                              celery_app=type("Celery", (), {"control": Control()})())
    assert result["status"] == "healthy"
    assert result["components"]["database"] == "healthy"
    assert result["components"]["redis"] == "healthy"
    assert result["components"]["celery_workers"] == "healthy"
    assert result["components"]["providers"] == "not_probed"
