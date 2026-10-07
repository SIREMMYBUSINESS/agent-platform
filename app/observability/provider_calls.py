"""Tracing, timing, and structured outcomes for provider adapter boundaries."""
from __future__ import annotations

import logging
import time

from app.observability.metrics import metrics
from app.observability.telemetry import start_span


logger = logging.getLogger("app.observability")


def call_provider(*, kind: str, provider: str, operation: str, callback, **kwargs):
    tenant_id, business_id = kwargs.get("tenant_id"), kwargs.get("business_id")
    if not tenant_id or not business_id:
        raise ValueError("provider calls require trusted tenant and business scope")
    if provider not in {"twilio", "sendgrid", "smtp", "google_calendar", "hubspot", "salesforce", "crm"}:
        provider = "unknown"
    started = time.perf_counter()
    outcome = "failure"
    try:
        with start_span(f"provider.{kind}.{operation}", attributes={
            "provider.type": provider, "provider.operation": operation,
            "tenant.id": str(tenant_id), "business.id": str(business_id),
        }):
            result = callback(**kwargs)
        outcome = "success"
        return result
    finally:
        metrics.provider_call(provider=provider, operation="provider_call", outcome=outcome,
                              duration_seconds=time.perf_counter() - started,
                              tenant_id=str(tenant_id), business_id=str(business_id))
        logger.info("provider call completed", extra={
            "tenant_id": str(tenant_id), "business_id": str(business_id),
            "provider": provider, "component": "workflow",
            "operation": "sync_attempt", "outcome": outcome,
        })
