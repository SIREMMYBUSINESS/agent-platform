"""421/503 documentation must match the region-routing guards it describes."""

import importlib
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import dependencies as auth_deps
from app.main import app
from app.openapi_contract import HOME_REGION_GUARDED_OPERATIONS, LOCAL_TOKEN_ROUTING_OPERATIONS

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}
ROUTE_MODULES = ("app.routes.provider_ingress", "app.routes.provider_webhooks", "app.routes.workflows")


def _operations(spec):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def _guarded_by(marker: str) -> set[tuple[str, str]]:
    """Find operations whose endpoint calls ``marker`` via operationId -> function name."""
    functions = {}
    for name in ROUTE_MODULES:
        module = importlib.import_module(name)
        functions.update({n: f for n, f in vars(module).items()
                          if inspect.isfunction(f) and f.__module__ == module.__name__})
    found = set()
    for path, method, operation in _operations(app.openapi()):
        matches = [n for n in functions if operation["operationId"].startswith(n + "_")]
        if matches and f"{marker}(" in inspect.getsource(functions[max(matches, key=len)]):
            found.add((method, path))
    return found


def test_guard_lists_match_endpoint_source():
    assert _guarded_by("require_tenant_home_region") == set(HOME_REGION_GUARDED_OPERATIONS)
    assert _guarded_by("_require_local_token_routing") == set(LOCAL_TOKEN_ROUTING_OPERATIONS)


def test_secured_operations_document_421_with_region_header_and_503():
    spec = app.openapi()
    for path, method, operation in _operations(spec):
        if operation.get("security"):
            responses = operation["responses"]
            assert "X-Tenant-Home-Region" in responses["421"]["headers"], (path, method)
            assert "503" in responses, (path, method)


def test_public_guarded_operations_document_their_routing_failures():
    responses = {
        (method, path): operation["responses"] for path, method, operation in _operations(app.openapi())
    }
    for key in HOME_REGION_GUARDED_OPERATIONS:
        assert {"403", "421", "503"} <= set(responses[key]), key
    for key in LOCAL_TOKEN_ROUTING_OPERATIONS:
        assert "503" in responses[key], key
    assert "421" not in responses[("post", "/contact/appointments/confirm")]


def _routing_settings(**overrides):
    values = dict(tenant_region_routing_enabled=True, control_database_url="postgresql://control",
                  database_region_id="us-east-1", deployment_region_id="us-east-1")
    values.update(overrides)
    return SimpleNamespace(**values)


def _fake_directory(monkeypatch, assignment):
    class FakeUow:
        tenant_database_assignments = SimpleNamespace(get=lambda **_: assignment)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(auth_deps, "UnitOfWork", lambda factory: FakeUow())


def test_home_region_guard_returns_documented_421_and_header(monkeypatch):
    monkeypatch.setattr(auth_deps, "settings", _routing_settings())
    _fake_directory(monkeypatch, SimpleNamespace(status="active", home_region_id="eu-west-1"))
    with pytest.raises(HTTPException) as caught:
        auth_deps.require_tenant_home_region("tenant-1")
    assert caught.value.status_code == 421
    assert caught.value.headers["X-Tenant-Home-Region"] == "eu-west-1"


def test_home_region_guard_returns_documented_503_while_migrating(monkeypatch):
    monkeypatch.setattr(auth_deps, "settings", _routing_settings())
    _fake_directory(monkeypatch, SimpleNamespace(status="migrating", home_region_id="us-east-1"))
    with pytest.raises(HTTPException) as caught:
        auth_deps.require_tenant_home_region("tenant-1")
    assert caught.value.status_code == 503


def test_home_region_guard_is_a_no_op_when_routing_is_disabled(monkeypatch):
    monkeypatch.setattr(auth_deps, "settings", _routing_settings(tenant_region_routing_enabled=False))
    assert auth_deps.require_tenant_home_region("tenant-1") is None
