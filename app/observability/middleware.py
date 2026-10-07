"""HTTP request metrics, trace correlation, and structured access/failure logs."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import metrics
from app.observability.telemetry import configure_telemetry, extract_context, span_context_fields, start_span
from app.config import settings
from app.observability.request_context import (
    create_request_context, current_request_context, traceparent_header,
)


logger = logging.getLogger("app.observability")
configure_telemetry(environment=settings.environment, metrics_registry=metrics)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        context = create_request_context(request.headers.get("x-correlation-id") or
                                         request.headers.get("x-request-id"),
                                         request.headers.get("traceparent"))
        token = current_request_context.set(context)
        request.state.request_id = context.request_id
        started = time.perf_counter()
        response: Response | None = None
        outcome = "success"
        status = 500
        parent = extract_context({"traceparent": request.headers.get("traceparent", "")})
        try:
            with start_span("http.request", parent_context=parent,
                            attributes={"http.request.method": request.method}) as span:
                fields = span_context_fields(span)
                if fields:
                    from dataclasses import replace
                    context = replace(context, trace_id=fields[0], span_id=fields[1])
                    current_request_context.set(context)
                    request.state.request_id = context.request_id
                response = await call_next(request)
                status = response.status_code
                outcome = "failure" if status >= 500 else "success"
                span.set_attribute("http.response.status_code", status)
                span.set_attribute("http.route", _route_name(request))
                return response
        except Exception as exc:
            outcome = "failure"
            metrics.event("http", "unhandled_exception", "failure")
            logger.error("request failed", extra={
                "method": request.method, "route": _route_name(request),
                "status_code": status, "component": "http",
                "operation": "request", "outcome": "failure",
                "error_type": type(exc).__name__,
            })
            raise
        finally:
            elapsed = time.perf_counter() - started
            route = _route_name(request)
            if route != "/metrics":
                metrics.increment("http_requests_total", method=_metric_method(request.method),
                                  route=_metric_route(route), status=str(status))
                metrics.observe("http_request_duration_seconds", elapsed,
                                method=_metric_method(request.method), route=_metric_route(route),
                                status=str(status))
                if outcome == "failure":
                    metrics.event("http", "request", "failure")
            if response is not None:
                response.headers["X-Request-ID"] = context.request_id
                response.headers["X-Correlation-ID"] = context.request_id
                response.headers["traceparent"] = traceparent_header(context)
            logger.info("request completed", extra={
                "method": request.method, "route": route, "status_code": status,
                "duration_ms": round(elapsed * 1000, 3), "component": "http",
                "operation": "request", "outcome": outcome,
            })
            current_request_context.reset(token)


def _route_name(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "/unmatched")


def _metric_route(route: str) -> str:
    # Route templates are safe bounded labels; never use raw URLs or query strings.
    return route if len(route) <= 100 and "{" not in route else "dynamic_route"


def _metric_method(method: str) -> str:
    normalized = method.lower()
    return normalized if normalized in {
        "get", "post", "put", "patch", "delete", "head", "options",
    } else "other"
