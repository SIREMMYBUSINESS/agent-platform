"""OpenAPI documentation for behavior implemented outside route signatures.

Request correlation and bearer-auth failures are handled by middleware and
dependencies, so FastAPI cannot infer them from the routes. This module only
documents that existing behavior; it never changes how requests are served.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}

_REQUEST_ID_PATTERN = "^[A-Za-z0-9._-]{1,100}$"
_TRACEPARENT_PATTERN = "^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"

_PARAMETERS: dict[str, dict[str, Any]] = {
    "CorrelationId": {
        "name": "X-Correlation-ID",
        "in": "header",
        "required": False,
        "description": (
            "Optional caller-supplied request ID (X-Request-ID is accepted as a "
            "fallback). A missing or malformed value is replaced with a generated "
            "one. The effective ID is echoed in the X-Correlation-ID response header."
        ),
        "schema": {"type": "string", "pattern": _REQUEST_ID_PATTERN, "title": "X-Correlation-ID"},
    },
    "TraceParent": {
        "name": "traceparent",
        "in": "header",
        "required": False,
        "description": "Optional W3C trace context. A malformed value starts a new trace.",
        "schema": {"type": "string", "pattern": _TRACEPARENT_PATTERN, "title": "traceparent"},
    },
}

_HEADERS: dict[str, dict[str, Any]] = {
    "X-Correlation-ID": {
        "description": "Effective request ID for this response; quote it when reporting problems.",
        "schema": {"type": "string", "pattern": _REQUEST_ID_PATTERN},
    },
    "X-Request-ID": {
        "description": "Same value as X-Correlation-ID.",
        "schema": {"type": "string", "pattern": _REQUEST_ID_PATTERN},
    },
    "traceparent": {
        "description": "W3C trace context for this request.",
        "schema": {"type": "string", "pattern": _TRACEPARENT_PATTERN},
    },
}

_ERROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["detail"],
    "properties": {"detail": {"type": "string", "title": "Detail"}},
    "title": "ErrorResponse",
}


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
    }


_HOME_REGION_HEADER: dict[str, Any] = {
    "X-Tenant-Home-Region": {
        "description": "Region that serves this tenant; retry the request against that region.",
        "schema": {"type": "string"},
    },
}

# Operations that are not behind the bearer-auth dependency but still enforce
# tenant home-region routing. tests/test_openapi_contract_regional.py checks
# these sets against the endpoint source so they cannot silently go stale.
HOME_REGION_GUARDED_OPERATIONS = frozenset({
    ("post", "/webhooks/call/twiml"),
    ("post", "/webhooks/email/inbound"),
    ("post", "/webhooks/sms"),
    ("post", "/webhooks/twilio/{tenant_id}/{business_id}"),
})
LOCAL_TOKEN_ROUTING_OPERATIONS = frozenset({
    ("post", "/appointments/{appointment_id}/cancel"),
    ("post", "/contact/appointments/cancel"),
    ("post", "/contact/appointments/confirm"),
    ("post", "/workflows/confirm-appointment"),
})

_REGION_MISDIRECTED = {
    "description": (
        "The tenant's home region is not this deployment (only returned when tenant "
        "region routing is enabled). Retry against the region in X-Tenant-Home-Region."
    ),
    "headers": _HOME_REGION_HEADER,
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
}
_TENANT_DATA_UNAVAILABLE = _error_response(
    "Tenant directory or tenant data is unavailable, including while the tenant is "
    "migrating (only returned when tenant region routing is enabled)"
)
_TOKEN_ROUTING_UNAVAILABLE = _error_response(
    "Contact-token routing is unavailable while tenant region routing is enabled"
)
_TENANT_DENIED = _error_response("Tenant access denied")

_AUTH_RESPONSES: dict[str, dict[str, Any]] = {
    "401": _error_response("Missing or invalid API key"),
    "403": _error_response("Tenant, data-residency or role access denied"),
}


def _declares_header(parameters: list[dict[str, Any]], name: str) -> bool:
    return any(
        item.get("in") == "header" and str(item.get("name", "")).lower() == name.lower()
        for item in parameters
    )


def apply_contract(schema: dict[str, Any]) -> dict[str, Any]:
    """Add correlation headers and auth failure responses to a generated schema."""
    components = schema.setdefault("components", {})
    components.setdefault("schemas", {}).setdefault("ErrorResponse", _ERROR_SCHEMA)
    parameters = components.setdefault("parameters", {})
    for key, parameter in _PARAMETERS.items():
        parameters.setdefault(key, parameter)

    for path, item in schema.get("paths", {}).items():
        shared = item.get("parameters", [])
        for method, operation in item.items():
            if method not in _HTTP_METHODS:
                continue
            refs = operation.setdefault("parameters", [])
            for key, parameter in _PARAMETERS.items():
                if _declares_header(shared, parameter["name"]) or _declares_header(refs, parameter["name"]):
                    continue  # keep a route's own explicit definition
                ref = {"$ref": f"#/components/parameters/{key}"}
                if ref not in refs:
                    refs.append(ref)
            responses = operation.setdefault("responses", {})
            key = (method, path)
            if operation.get("security"):
                for status, response in _AUTH_RESPONSES.items():
                    responses.setdefault(status, response)
                responses.setdefault("421", _REGION_MISDIRECTED)
                responses.setdefault("503", _TENANT_DATA_UNAVAILABLE)
            if key in HOME_REGION_GUARDED_OPERATIONS:
                responses.setdefault("403", _TENANT_DENIED)
                responses.setdefault("421", _REGION_MISDIRECTED)
                responses.setdefault("503", _TENANT_DATA_UNAVAILABLE)
            if key in LOCAL_TOKEN_ROUTING_OPERATIONS:
                responses.setdefault("503", _TOKEN_ROUTING_UNAVAILABLE)
            for response in responses.values():
                headers = response.setdefault("headers", {})
                for name, header in _HEADERS.items():
                    headers.setdefault(name, header)
    return schema


def install_openapi_contract(app: FastAPI) -> None:
    """Wrap ``app.openapi`` so the documented schema includes the contract."""
    generate = app.openapi

    def openapi_with_contract() -> dict[str, Any]:
        if app.openapi_schema is None:
            apply_contract(generate())
        return app.openapi_schema

    app.openapi = openapi_with_contract  # type: ignore[method-assign]
