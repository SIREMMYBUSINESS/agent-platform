from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_inbound_ok():
    response = client.post("/inbound", json={
        "tenant_id": "tenant-1",
        "business_id": "biz-1",
        "contact_id": "contact-1",
        "channel": "chat",
        "contact_name": "Alex",
        "consent_status": "granted",
        "metadata": {"region": "US"},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Alex" in body["agent_response"]
