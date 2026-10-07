"""OpenTelemetry SDK configuration and bounded manual instrumentation helpers."""
from __future__ import annotations

from contextlib import contextmanager
import os
from threading import Lock
from typing import Iterator

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import get_global_textmap, set_global_textmap
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


_lock = Lock()
_configured = False
_TRACER_NAME = "agent_platform"


def configure_telemetry(*, service_name: str = "agent-platform",
                        environment: str = "development", endpoint: str | None = None,
                        sample_rate: float = 0.1, metrics_registry=None) -> None:
    """Configure SDK once per process. Export is optional and uses OTLP/HTTP."""
    global _configured
    with _lock:
        if _configured:
            return
        endpoint = (endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
        resource = Resource.create({
            "service.name": service_name,
            "deployment.environment.name": environment,
        })
        sampler = ParentBased(TraceIdRatioBased(sample_rate))
        tracer_provider = TracerProvider(resource=resource, sampler=sampler)
        metric_readers = []
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            base = endpoint.rstrip("/")
            tracer_provider.add_span_processor(BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{base}/v1/traces"),
            ))
            metric_readers.append(PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
            ))
        trace.set_tracer_provider(tracer_provider)
        otel_metrics.set_meter_provider(MeterProvider(
            resource=resource, metric_readers=metric_readers,
        ))
        set_global_textmap(TraceContextTextMapPropagator())
        if metrics_registry is not None:
            metrics_registry.attach_meter(otel_metrics.get_meter(_TRACER_NAME))
        _configured = True


def extract_context(carrier: dict[str, str] | None = None) -> Context:
    return get_global_textmap().extract(carrier or {})


def inject_context(carrier: dict[str, str] | None = None) -> dict[str, str]:
    result = dict(carrier or {})
    get_global_textmap().inject(result)
    return result


def span_context_fields(span: Span | None = None) -> tuple[str, str] | None:
    span = span or trace.get_current_span()
    context = span.get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


@contextmanager
def start_span(name: str, *, parent_context: Context | None = None,
               attributes: dict[str, str | int | float | bool] | None = None) -> Iterator[Span]:
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(
        name, context=parent_context, attributes=attributes or {}, record_exception=True,
    ) as current:
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise


__all__ = ["configure_telemetry", "extract_context", "inject_context", "span_context_fields", "start_span"]
