"""One-off provider-free queue smoke check for an isolated staging database."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import time
from urllib.parse import urlsplit
from uuid import uuid4


def _require_staging_opt_in(environment: str, opt_in: str) -> None:
    if environment.strip().lower() != "staging":
        raise RuntimeError("staging queue smoke refused: ENVIRONMENT must be staging")
    if opt_in.strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError(
            "staging queue smoke refused: set STAGING_QUEUE_SMOKE_ENABLED=true for this run"
        )


def _require_neon_database(database_url: str) -> None:
    hostname = urlsplit(database_url).hostname or ""
    if not hostname.lower().endswith(".neon.tech"):
        raise RuntimeError("staging queue smoke refused: database host must be a Neon host")


def run(*, timeout_seconds: int = 180, poll_interval_seconds: float = 3) -> None:
    """Seed a synthetic chat contact, enqueue inbound work, and await the worker."""
    _require_staging_opt_in(
        os.environ.get("ENVIRONMENT", ""),
        os.environ.get("STAGING_QUEUE_SMOKE_ENABLED", ""),
    )
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("timeout and poll interval must be positive")

    from app.config import settings
    from app.persistence.models import Business, Contact, Tenant
    from app.persistence.session import session_factory
    from app.persistence.unit_of_work import UnitOfWork
    from app.queue.contracts import QueueDomain, QueueTaskSubmission
    from app.queue.service import QueueTaskService
    from app.security import AuthenticatedPrincipal

    _require_neon_database(settings.database_url)

    suffix = uuid4().hex
    tenant_id = f"staging-queue-smoke-{suffix}"
    business_id = f"staging-queue-smoke-{suffix}"
    contact_id = f"staging-queue-smoke-{suffix}"
    region_id = settings.database_region_id or settings.deployment_region_id
    now = datetime.now(UTC)

    # This isolated operational command creates only synthetic, contact-free
    # demo records. Keep the seed transaction separate so the worker can read it.
    with session_factory() as session:
        session.add(Tenant(
            id=tenant_id, name="Staging queue smoke", region="EU",
            default_region_id=region_id,
        ))
        session.flush()
        session.add(Business(
            id=business_id, tenant_id=tenant_id, name="Staging queue smoke",
            vertical="general", region="EU", region_id=region_id,
        ))
        session.flush()
        session.add(Contact(
            id=contact_id, tenant_id=tenant_id, business_id=business_id,
            full_name="Synthetic staging contact", consent_status="unknown",
            preferred_channel="chat",
        ))
        session.commit()

    principal = AuthenticatedPrincipal(
        principal_id=f"staging-smoke:{suffix}", tenant_id=tenant_id,
        roles=("inbound_writer",), auth_source="staging_smoke",
        issued_at=now,
    )
    submission = QueueTaskSubmission(
        domain=QueueDomain.INBOUND, task_name="inbound.process",
        business_id=business_id, idempotency_key=f"staging-smoke:{suffix}",
        aggregate_type="contact", aggregate_id=contact_id,
        payload={
            "contact_id": contact_id,
            "channel": "chat",
            "text": "Provider-free staging queue smoke check",
            "contact_name": "Synthetic staging contact",
        },
        correlation_id=f"staging-smoke:{suffix}", available_at=now,
        max_attempts=1,
    )
    task = QueueTaskService(
        uow_factory=lambda: UnitOfWork(session_factory),
    ).enqueue(principal, submission)

    deadline = time.monotonic() + timeout_seconds
    while True:
        with UnitOfWork(session_factory) as uow:
            current = uow.queue_tasks.get(
                tenant_id=tenant_id, business_id=business_id, task_id=task.id,
            )
            if current is None:
                raise RuntimeError("staging queue smoke task disappeared")
            status = current.status
            error_code = current.error_code
            attempts = current.attempts
        if status == "succeeded":
            print(
                f"STAGING_QUEUE_SMOKE_OK task_id={task.id} "
                f"status={status} attempts={attempts}"
            )
            return
        if status == "dead_letter":
            raise RuntimeError(
                f"staging queue smoke failed task_id={task.id} "
                f"status={status} error_code={error_code or 'unknown'}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"staging queue smoke timed out task_id={task.id} "
                f"last_status={status} attempts={attempts}"
            )
        time.sleep(min(poll_interval_seconds, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    run()
