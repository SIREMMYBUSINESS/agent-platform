"""Authenticated staging inbound smoke: API -> worker -> dashboard API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4


def _require_staging_opt_in(environment: str, opt_in: str) -> None:
    if environment.strip().lower() != "staging":
        raise RuntimeError("staging queue smoke refused: ENVIRONMENT must be staging")
    if opt_in.strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError("staging queue smoke refused: set STAGING_QUEUE_SMOKE_ENABLED=true")


def _require_neon_database(database_url: str) -> None:
    hostname = urlsplit(database_url).hostname or ""
    if not hostname.lower().endswith(".neon.tech"):
        raise RuntimeError("staging queue smoke refused: database host must be a Neon host")


def _request_json(url: str, *, key: str, method: str = "GET",
                  body: dict | None = None, idempotency_key: str | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(
            f"staging API {method} {urlsplit(url).path} returned HTTP {exc.code}"
        ) from None


def run(*, timeout_seconds: int = 60, poll_interval_seconds: float = 2) -> None:
    """Seed a valid test workspace and prove inbound work is dashboard-visible."""
    _require_staging_opt_in(os.environ.get("ENVIRONMENT", ""),
                            os.environ.get("STAGING_QUEUE_SMOKE_ENABLED", ""))
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("timeout and poll interval must be positive")

    from app.auth.api_keys import generate_api_key, hash_api_key
    from app.config import settings
    from app.persistence.models import (
        ApiKey, ApiKeyGrant, Business, Contact, Tenant, TenantRegionConfig,
    )
    from app.persistence.session import session_factory
    from app.persistence.unit_of_work import UnitOfWork

    _require_neon_database(settings.database_url)
    suffix = uuid4().hex
    tenant_id = f"staging-queue-smoke-tenant-{suffix}"
    business_id = f"staging-queue-smoke-business-{suffix}"
    contact_id = f"staging-queue-smoke-contact-{suffix}"
    principal_id = f"staging-queue-smoke-key-{suffix}"
    region_id = settings.database_region_id or settings.deployment_region_id
    raw_key = generate_api_key()
    expires_at = datetime.now(UTC) + timedelta(minutes=15)

    # All records are synthetic; region approval and explicit consent satisfy
    # the same policy checks that apply to a real EU chat inbound.
    with session_factory() as session:
        session.add(Tenant(
            id=tenant_id, name="Staging queue smoke", region="EU",
            default_region_id=region_id, supported_region_ids=[region_id],
        ))
        session.flush()
        session.add(Business(
            id=business_id, tenant_id=tenant_id, name="Staging queue smoke",
            vertical="general", region="EU", region_id=region_id,
        ))
        session.flush()
        session.add(Contact(
            id=contact_id, tenant_id=tenant_id, business_id=business_id,
            full_name="Synthetic staging contact", consent_status="granted",
            preferred_channel="chat",
        ))
        session.add(TenantRegionConfig(
            tenant_id=tenant_id, region_id=region_id, country_code="DE",
            timezone="Europe/Berlin", language="en", data_residency_region=region_id,
            allowed_storage_regions=[region_id], allowed_channels=["chat"],
            compliance_profile={"region_group": "EU"}, active=True,
            approval_status="approved", approved_by="staging-smoke",
            approved_at=datetime.now(UTC),
        ))
        session.add(ApiKey(
            id=principal_id, tenant_id=tenant_id, key_hash=hash_api_key(raw_key),
            expires_at=expires_at,
        ))
        session.add(ApiKeyGrant(
            principal_id=principal_id, tenant_id=tenant_id,
            roles=["inbound_writer", "conversation_reader"],
            enabled=True, expires_at=expires_at,
        ))
        session.commit()

    try:
        with UnitOfWork(session_factory) as uow:
            uow.consent_records.add(
                tenant_id=tenant_id, business_id=business_id, contact_id=contact_id,
                channel="chat", status="granted", source="staging_smoke",
                source_reference=f"smoke:{suffix}",
            )

        origin = os.environ.get(
            "STAGING_API_ORIGIN", "https://agent-platform-staging.onrender.com",
        ).rstrip("/")
        queued = _request_json(
            f"{origin}/inbound/queue", key=raw_key, method="POST",
            idempotency_key=f"staging-smoke:{suffix}", body={
                "tenant_id": tenant_id, "business_id": business_id,
                "contact_id": contact_id, "channel": "chat",
                "contact_name": "Synthetic staging contact",
                "text": "What are your opening hours?",
            },
        )
        task_id, correlation_id = queued["id"], queued["correlation_id"]
        deadline = time.monotonic() + timeout_seconds
        last_status = "queued"
        while time.monotonic() < deadline:
            with UnitOfWork(session_factory) as uow:
                task = uow.queue_tasks.get(
                    tenant_id=tenant_id, business_id=business_id, task_id=task_id,
                )
                conversation = uow.conversations.get_by_correlation_id(
                    tenant_id=tenant_id, business_id=business_id,
                    correlation_id=correlation_id,
                )
                if task is None:
                    raise RuntimeError("staging queue smoke task disappeared")
                last_status = task.status
                error_code = task.error_code
                workflow_state = conversation.workflow_state if conversation else None
                conversation_id = str(conversation.id) if conversation else None

            if last_status == "dead_letter":
                raise RuntimeError(
                    f"staging queue smoke dead-lettered task error_code={error_code or 'unknown'}"
                )
            if last_status == "succeeded" and conversation is not None:
                if workflow_state == "blocked":
                    raise RuntimeError("staging inbound workflow completed but conversation stayed blocked")
                listing = _request_json(
                    f"{origin}/conversations?offset=0&limit=100", key=raw_key,
                )
                visible = any(str(item.get("id")) == conversation_id
                              for item in listing.get("items", []))
                if not visible:
                    raise RuntimeError("conversation is missing from dashboard conversation API")
                print(
                    f"STAGING_INBOUND_E2E_OK task_id={task_id} conversation_id={conversation_id} "
                    f"workflow_state={workflow_state} dashboard_visible=true"
                )
                return
            time.sleep(min(poll_interval_seconds, max(0, deadline - time.monotonic())))
        raise TimeoutError(
            f"staging inbound smoke timed out after {timeout_seconds}s "
            f"task_id={task_id} last_status={last_status}"
        )
    finally:
        # The raw key never leaves process memory and the persisted key is revoked.
        with session_factory() as session:
            record = session.get(ApiKey, principal_id)
            if record is not None:
                record.revoked = True
                session.commit()


if __name__ == "__main__":
    run()
