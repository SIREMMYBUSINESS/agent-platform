import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, parse_qs
from uuid import uuid4

from fastapi.testclient import TestClient
from starlette.datastructures import FormData
import pytest
from twilio.request_validator import RequestValidator

from app.config import settings
from app.integrations.provider_support import (
    DirectoryTenantConfigResolver, ProviderConfigurationError,
)
from app.integrations.sms.twilio_webhooks import (
    build_status_callback_url, validate_twilio_signature,
)
from app.integrations.sms.twilio import TwilioMessageSender
from app.observability.logging import StructuredFormatter
from app.main import app
from app.routes.provider_webhooks import get_outreach_service


def _scope_file(root: Path, tenant_id: str, business_id: str, values: dict[str, str]) -> Path:
    digest = hashlib.sha256(f"{tenant_id}\0{business_id}".encode()).hexdigest()
    path = root / f"{digest}.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    path.chmod(0o400)
    return path


def test_directory_secret_resolver_is_scope_hashed_and_fails_closed(tmp_path):
    _scope_file(tmp_path, "tenant-a", "business-a", {"auth_token": "do-not-log"})
    resolver = DirectoryTenantConfigResolver(tmp_path)
    assert resolver.resolve(tenant_id="tenant-a", business_id="business-a")["auth_token"] == "do-not-log"
    with pytest.raises(ProviderConfigurationError):
        resolver.resolve(tenant_id="tenant-b", business_id="business-a")


@pytest.mark.parametrize("suffix", (".json", ""))
def test_directory_secret_resolver_accepts_render_safe_scope_filename(tmp_path, suffix):
    digest = hashlib.sha256(b"tenant-a\0business-a").hexdigest()
    path = tmp_path / f"scope-{digest}{suffix}"
    path.write_text(json.dumps({"auth_token": "render-secret"}), encoding="utf-8")
    path.chmod(0o400)

    resolver = DirectoryTenantConfigResolver(tmp_path)
    assert resolver.resolve(tenant_id="tenant-a", business_id="business-a")["auth_token"] == "render-secret"


def test_structured_provider_config_rejection_logs_safe_reason_code(monkeypatch, caplog):
    import app.routes.provider_ingress as ingress
    from fastapi import HTTPException

    class InvalidResolver:
        def __init__(self, _): pass
        def resolve(self, *, tenant_id, business_id):
            raise ProviderConfigurationError("provider configuration is invalid")

    monkeypatch.setattr(ingress.settings, "provider_secrets_dir", Path("/tmp/secrets"))
    monkeypatch.setattr(ingress, "DirectoryTenantConfigResolver", InvalidResolver)
    with caplog.at_level(logging.WARNING, logger="app.observability"):
        with pytest.raises(HTTPException):
            ingress._scoped_config("tenant-a", "business-a")

    record = next(record for record in caplog.records if record.msg == "provider webhook scope configuration unavailable")
    structured = json.loads(StructuredFormatter().format(record))
    assert structured["reason_code"] == "scope_file_invalid"


@pytest.mark.skipif(__import__("os").name == "nt", reason="Windows ACLs are not represented by POSIX mode bits")
def test_directory_secret_resolver_rejects_overly_permissive_secret_file(tmp_path):
    path = _scope_file(tmp_path, "tenant-a", "business-a", {"auth_token": "private"})
    path.chmod(0o644)
    resolver = DirectoryTenantConfigResolver(tmp_path)
    with pytest.raises(ProviderConfigurationError, match="permissions"):
        resolver.resolve(tenant_id="tenant-a", business_id="business-a")


def test_twilio_signature_validates_full_configured_url_and_all_form_fields():
    message_id = uuid4()
    callback = build_status_callback_url(
        "https://api.example.test/webhooks/twilio", tenant_id="tenant-a",
        business_id="business-a", message_id=message_id, attempt=1,
    )
    params = {"MessageSid": "SM" + "a" * 32, "MessageStatus": "delivered",
              "AccountSid": "AC" + "b" * 32, "FutureParameter": ["z", "a"]}
    form = FormData([(key, value) for key, values in params.items()
                     for value in (values if isinstance(values, list) else [values])])
    signature = RequestValidator("secret").compute_signature(callback, form)
    assert validate_twilio_signature(callback_url=callback, parameters=params,
                                     signature=signature, auth_token="secret")
    assert not validate_twilio_signature(callback_url=callback + "/tampered", parameters=params,
                                          signature=signature, auth_token="secret")


def test_twilio_sender_requests_status_callback_and_returns_provider_sid():
    class Transport:
        def __init__(self):
            self.call = None

        def request(self, method, url, *, headers, body=None, timeout=10.0):
            self.call = (method, url, headers, body)
            return 201, {"sid": "SM" + "a" * 32}

    transport = Transport()
    class Audit:
        def emit(self, event):
            pass

    sender = TwilioMessageSender(
        configs=type("Configs", (), {"resolve": lambda *_args, **_kwargs: {
            "account_sid": "AC" + "b" * 32, "auth_token": "secret",
            "from_sms": "+15550000001",
            "status_callback_base_url": "https://api.example.test/webhooks/twilio",
        }})(),
        audit_sink=Audit(), transport=transport,
    )
    message_id = uuid4()
    sid = sender.send(tenant_id="tenant-a", business_id="business-a", channel="sms",
                      recipient="+15559990001", content={"body": "Hello"},
                      idempotency_key="send-1", message_id=message_id, attempt=1)
    fields = parse_qs(transport.call[3].decode())
    assert sid == "SM" + "a" * 32
    assert fields["StatusCallback"] == [build_status_callback_url(
        "https://api.example.test/webhooks/twilio", tenant_id="tenant-a",
        business_id="business-a", message_id=message_id, attempt=1,
    )]


def test_twilio_webhook_requires_signature_and_passes_trusted_scope(tmp_path, monkeypatch):
    tenant_id, business_id, message_id = "tenant-a", "business-a", uuid4()
    config = {
        "account_sid": "AC" + "b" * 32, "auth_token": "secret",
        "status_callback_base_url": "https://api.example.test/webhooks/twilio",
    }
    _scope_file(tmp_path, tenant_id, business_id, config)
    monkeypatch.setattr(settings, "provider_secrets_dir", tmp_path)

    class ReceiptService:
        def __init__(self):
            self.call = None

        def apply_twilio_receipt(self, principal, **kwargs):
            self.call = (principal, kwargs)

    service = ReceiptService()
    app.dependency_overrides[get_outreach_service] = lambda: service
    client = TestClient(app)
    params = {
        "AccountSid": config["account_sid"], "MessageSid": "SM" + "a" * 32,
        "MessageStatus": "delivered", "ErrorCode": "",
        "TenantId": "attacker-controlled-and-ignored",
    }
    callback_url = build_status_callback_url(
        config["status_callback_base_url"], tenant_id=tenant_id,
        business_id=business_id, message_id=message_id, attempt=1,
    )
    form_pairs = list(params.items())
    form = FormData(form_pairs)
    signature = RequestValidator("secret").compute_signature(callback_url, form)
    request_url = f"/webhooks/twilio/{tenant_id}/{business_id}?message_id={message_id}&attempt=1"
    try:
        rejected = client.post(request_url, content=urlencode(form_pairs),
                               headers={"content-type": "application/x-www-form-urlencoded",
                                        "x-twilio-signature": "invalid"})
        accepted = client.post(request_url, content=urlencode(form_pairs),
                               headers={"content-type": "application/x-www-form-urlencoded",
                                        "x-twilio-signature": signature,
                                        "host": "spoofed.example"})
    finally:
        app.dependency_overrides.pop(get_outreach_service, None)
    assert rejected.status_code == 403
    assert accepted.status_code == 204
    principal, payload = service.call
    assert principal.tenant_id == tenant_id
    assert principal.roles == ("delivery_receipt_writer",)
    assert principal.auth_source == "twilio_webhook"
    assert payload["provider_message_id"] == params["MessageSid"]
    assert payload["message_id"] == message_id
    assert payload["attempt"] == 1


def test_readiness_probe_reports_database_unavailable_without_leaking_details(monkeypatch):
    class Session:
        def exec(self, _statement):
            raise RuntimeError("postgres://user:secret@database is unavailable")

        def close(self):
            self.closed = True

    monkeypatch.setattr("app.main.session_factory", lambda: Session())
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "components": {
            "database": "unhealthy", "worker": "unhealthy", "providers": "not_probed",
        },
    }
    assert "secret" not in response.text


def test_readiness_probe_reports_session_factory_failure_as_unavailable(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    def fail_to_create_session():
        raise ModuleNotFoundError("database driver is not installed")

    monkeypatch.setattr(main, "session_factory", fail_to_create_session)
    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "components": {
            "database": "unhealthy", "worker": "unhealthy", "providers": "not_probed",
        },
    }
    assert "database driver" not in response.text


def test_readiness_probe_checks_postgres_with_a_small_select(monkeypatch):
    class Session:
        closed = False

        def exec(self, statement):
            if str(statement) == "SELECT 1":
                self.database_checked = True
            return self

        def one(self):
            return (1,)

        def first(self):
            return object()

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr("app.main.session_factory", lambda: session)
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "components": {
            "database": "healthy", "worker": "healthy", "providers": "not_probed",
        },
    }
    assert session.database_checked
    assert session.closed
