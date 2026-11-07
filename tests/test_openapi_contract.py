"""The documented OpenAPI contract must match behavior implemented outside route signatures."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.regions.schemas import RegionPolicyOverrideView, TenantRegionConfigView

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}


def _operations(spec):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_every_operation_documents_correlation_and_trace_headers():
    spec = app.openapi()
    for path, method, operation in _operations(spec):
        refs = {p.get("$ref") for p in operation["parameters"] if "$ref" in p}
        assert "#/components/parameters/CorrelationId" in refs, (path, method)
        assert "#/components/parameters/TraceParent" in refs, (path, method)
        for status, response in operation["responses"].items():
            assert {"X-Correlation-ID", "X-Request-ID", "traceparent"} <= set(response["headers"]), (
                path, method, status
            )


def test_secured_operations_document_auth_failures_and_public_ones_do_not():
    spec = app.openapi()
    secured = public = 0
    for path, method, operation in _operations(spec):
        if operation.get("security"):
            secured += 1
            assert {"401", "403"} <= set(operation["responses"]), (path, method)
        else:
            public += 1
            assert "401" not in operation["responses"], (path, method)
    assert secured and public
    assert spec["components"]["schemas"]["ErrorResponse"]["required"] == ["detail"]


def test_contract_does_not_duplicate_existing_parameters_or_responses():
    spec = app.openapi()
    assert app.openapi() is spec
    voice = spec["paths"]["/voice/calls"]["post"]
    names = [p["name"] for p in voice["parameters"] if "name" in p]
    assert names.count("Idempotency-Key") == 1
    assert voice["responses"]["202"]["content"]["application/json"]["schema"]


def test_contract_preserves_explicit_header_and_parameter_definitions():
    from app.openapi_contract import apply_contract

    schema = {
        "paths": {
            "/example": {
                "parameters": [{"name": "X-Correlation-ID", "in": "header", "schema": {"type": "integer"}}],
                "get": {
                    "responses": {
                        "200": {
                            "description": "ok",
                            "headers": {"X-Correlation-ID": {"description": "route-specific"}},
                        }
                    }
                },
            }
        }
    }
    apply_contract(schema)
    operation = schema["paths"]["/example"]["get"]
    all_parameters = schema["paths"]["/example"]["parameters"] + operation["parameters"]
    assert sum(p.get("name") == "X-Correlation-ID" for p in all_parameters) == 1
    assert operation["responses"]["200"]["headers"]["X-Correlation-ID"]["description"] == "route-specific"


def test_region_views_publish_typed_ids_and_timestamps():
    schemas = app.openapi()["components"]["schemas"]
    override = schemas["RegionPolicyOverrideView"]["properties"]
    assert override["id"]["format"] == "uuid"
    assert override["created_at"]["format"] == "date-time"
    approved = schemas["TenantRegionConfigView"]["properties"]["approved_at"]
    assert {"type": "null"} in approved["anyOf"]
    assert any(option.get("format") == "date-time" for option in approved["anyOf"])


def test_region_view_json_wire_format_is_unchanged():
    identifier = uuid4()
    stamp = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    override = RegionPolicyOverrideView(
        id=identifier, region_id="eu-west-1", override_rules={}, approved_by="p", created_at=stamp
    )
    assert override.model_dump(mode="json")["id"] == str(identifier)
    assert override.model_dump(mode="json")["created_at"] == "2026-01-02T03:04:05Z"
    config = TenantRegionConfigView(
        tenant_id="t", region_id="eu-west-1", country_code="DE", timezone="Europe/Berlin",
        language="de", data_residency_region="eu-west-1", allowed_storage_regions=[],
        provider_preferences={}, compliance_profile={}, allowed_channels=[],
        local_phone_route_configured=False, contact_preferences={}, active=True,
        approval_status="approved", approved_by=None, approved_at=None
    )
    assert config.model_dump(mode="json")["approved_at"] is None


def test_documented_correlation_behavior_matches_middleware():
    with TestClient(app) as client:
        echoed = client.get("/health", headers={"X-Correlation-ID": "client-id.1"})
        replaced = client.get("/health", headers={"X-Correlation-ID": "bad id!"})
    assert echoed.headers["X-Correlation-ID"] == "client-id.1"
    assert echoed.headers["X-Request-ID"] == "client-id.1"
    assert replaced.headers["X-Correlation-ID"] != "bad id!"
    assert len(replaced.headers["X-Correlation-ID"]) == 32
    assert echoed.headers["traceparent"].startswith("00-")
