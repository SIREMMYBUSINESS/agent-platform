"""Request correlation helpers for structured logs and W3C trace context."""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4


_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    trace_id: str
    span_id: str


current_request_context: ContextVar[RequestContext | None] = ContextVar(
    "current_request_context", default=None,
)


def create_request_context(request_id: str | None, traceparent: str | None) -> RequestContext:
    request = request_id if request_id and _REQUEST_ID.fullmatch(request_id) else uuid4().hex
    match = _TRACEPARENT.fullmatch(traceparent or "")
    trace_id = match.group(1) if match and set(match.group(1)) != {"0"} else uuid4().hex
    span_id = uuid4().hex[:16]
    return RequestContext(request, trace_id, span_id)


def traceparent_header(context: RequestContext) -> str:
    return f"00-{context.trace_id}-{context.span_id}-01"
