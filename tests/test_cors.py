from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import install_cors_middleware


def test_cors_allows_configured_ui_origin_and_authorization_preflight():
    api = FastAPI()
    install_cors_middleware(api, ["https://ui.example.com"])

    @api.get("/resource")
    def read_resource():
        return {"ok": True}

    with TestClient(api) as client:
        preflight = client.options(
            "/resource",
            headers={
                "Origin": "https://ui.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        allowed = client.get("/resource", headers={"Origin": "https://ui.example.com"})
        rejected = client.get("/resource", headers={"Origin": "https://other.example.com"})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://ui.example.com"
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()
    assert allowed.headers["access-control-allow-origin"] == "https://ui.example.com"
    assert "access-control-allow-origin" not in rejected.headers
