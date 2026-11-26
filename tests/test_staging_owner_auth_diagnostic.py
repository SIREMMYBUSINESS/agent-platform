from fastapi.testclient import TestClient

from app.main import app


def test_staging_owner_auth_diagnostic_reports_registered_route_without_route_object_assumptions():
    response = TestClient(app).get("/diagnostics/staging-owner-auth")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "effective_environment",
        "bypass_enabled",
        "route_registered",
        "running_revision",
    }
    assert payload["route_registered"] is True
    assert isinstance(payload["bypass_enabled"], bool)
