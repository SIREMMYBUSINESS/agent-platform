"""JSON formatter for request and application diagnostic logs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.observability.request_context import current_request_context


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = current_request_context.get()
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if context is not None:
            payload.update(request_id=context.request_id, correlation_id=context.request_id,
                           trace_id=context.trace_id, span_id=context.span_id)
            if getattr(context, "tenant_id", None):
                payload["tenant_id"] = context.tenant_id
            if getattr(context, "business_id", None):
                payload["business_id"] = context.business_id
        else:
            payload["correlation_id"] = "unavailable"
        for field in ("method", "route", "status_code", "duration_ms", "component",
                      "operation", "outcome", "error_type", "tenant_id", "business_id",
                      "task_id", "provider", "channel", "workflow_state"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_structured_logging(level: str = "INFO") -> None:
    logger = logging.getLogger("app.observability")
    logger.setLevel(level)
    logger.propagate = False
    if not any(getattr(handler, "_platform_json_handler", False)
               for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredFormatter())
        handler._platform_json_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
