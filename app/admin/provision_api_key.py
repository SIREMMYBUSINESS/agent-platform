"""Provision a tenant-scoped dashboard key from an interactive admin terminal.

This command intentionally requires a TTY. Never invoke it from a CI job,
application startup, or a command whose stdout is captured in deployment logs.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import os
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from app.auth.api_keys import generate_api_key, hash_api_key


def _positive_days(value: str) -> int:
    try:
        days = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of days") from None
    if not 1 <= days <= 3650:
        raise argparse.ArgumentTypeError("must be between 1 and 3650 days")
    return days


def provision_dashboard_key(*, tenant_id: str, expires_in_days: int = 90) -> tuple[str, str, datetime]:
    """Atomically persist the dashboard's tenant-scoped read grant."""
    from app.persistence import UnitOfWork, session_factory
    from app.persistence.models import ApiKey, ApiKeyGrant
    from app.config import settings

    if not tenant_id.strip():
        raise ValueError("tenant ID cannot be blank")
    if not 1 <= expires_in_days <= 3650:
        raise ValueError("expiry must be between 1 and 3650 days")
    if os.environ.get("ENVIRONMENT", "").strip().lower() != "staging":
        raise ValueError("staging key provisioning refused: ENVIRONMENT must be staging")
    if os.environ.get("STAGING_CREDENTIAL_PROVISIONING_ENABLED", "").strip().lower() not in {
        "1", "true", "yes",
    }:
        raise ValueError("set STAGING_CREDENTIAL_PROVISIONING_ENABLED=true for this operation")
    database_host = urlsplit(settings.database_url).hostname or ""
    if not database_host.lower().endswith(".neon.tech"):
        raise ValueError("staging key provisioning refused: database host must be Neon")

    raw_key = generate_api_key()
    principal_id = str(uuid4())
    issued_at = datetime.now(UTC)
    expires_at = issued_at + timedelta(days=expires_in_days)

    with UnitOfWork(session_factory) as uow:
        tenant = uow.tenants.get(tenant_id=tenant_id)
        if tenant is None:
            raise ValueError(f"No existing tenant has ID {tenant_id!r}; no key was created.")
        uow.session.add_all([
            ApiKey(
                id=principal_id,
                tenant_id=tenant.id,
                key_hash=hash_api_key(raw_key),
                revoked=False,
                issued_at=issued_at,
                expires_at=expires_at,
            ),
            ApiKeyGrant(
                principal_id=principal_id,
                tenant_id=tenant.id,
                roles=["lead_reader", "conversation_reader"],
                enabled=True,
                expires_at=expires_at,
            ),
        ])

    return tenant.name, raw_key, expires_at


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create one expiring, tenant-scoped dashboard API key with read-only lead and conversation access."
    )
    parser.add_argument("--tenant-id", required=True, help="ID of an existing tenant")
    parser.add_argument(
        "--expires-in-days", type=_positive_days, default=90,
        help="key and grant lifetime in days (default: 90; maximum: 3650)",
    )
    args = parser.parse_args(argv)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("requires an interactive terminal; do not run from CI or captured deployment logs")

    confirmation = input(
        f"Create a {args.expires_in_days}-day dashboard read key for tenant {args.tenant_id!r}? [y/N] "
    ).strip().lower()
    if confirmation not in {"y", "yes"}:
        print("No key created.", file=sys.stderr)
        return 1

    try:
        tenant_name, raw_key, expires_at = provision_dashboard_key(
            tenant_id=args.tenant_id,
            expires_in_days=args.expires_in_days,
        )
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Provisioned tenant: {tenant_name} ({args.tenant_id})")
    print("Grants: lead_reader, conversation_reader")
    print(f"Expires (UTC): {expires_at.isoformat()}")
    print("Copy this key now; it will not be shown again:")
    print(raw_key)
    print("Deliver it to the tenant through a secure channel. Do not paste it into chat, logs, or source control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
