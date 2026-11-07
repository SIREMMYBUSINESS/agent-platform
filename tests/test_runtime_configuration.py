from fastapi.testclient import TestClient

import app.main as main_module


def test_runtime_configuration_reports_only_nonsecret_settings_without_database_access(monkeypatch):
    monkeypatch.setattr(main_module.settings, "deployment_region_id", "eu-west-1")
    monkeypatch.setattr(main_module.settings, "database_region_id", "eu-west-1")
    monkeypatch.setattr(main_module.settings, "tenant_region_routing_enabled", False)
    monkeypatch.setattr(main_module.settings, "environment", "development")

    def unexpected_database_access():
        raise AssertionError("configuration reporting must not open a database")

    monkeypatch.setattr(main_module, "session_factory", unexpected_database_access)
    response = TestClient(main_module.app).get("/health/runtime")
    assert response.status_code == 200
    assert response.json() == {
        "environment": "development",
        "deployment_region_id": "eu-west-1",
        "database_region_id": "eu-west-1",
        "tenant_region_routing_enabled": False,
    }
