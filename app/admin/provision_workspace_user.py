"""Provision an invited workspace identity and role-bound tenant membership."""

from __future__ import annotations

import argparse
import os
import sys
from uuid import uuid4

from app.auth.workspace_roles import WORKSPACE_ROLES
from app.persistence.models import TenantMembership, WorkspaceUser
from app.persistence.session import control_session_factory, session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.config import settings


def provision_workspace_user(
    *, tenant_id: str, email: str, role: str, display_name: str = "",
) -> tuple[str, str]:
    normalized_email = email.strip().casefold()
    if not tenant_id.strip():
        raise ValueError("tenant ID cannot be blank")
    if role not in WORKSPACE_ROLES:
        raise ValueError("unsupported workspace role")
    if not normalized_email or len(normalized_email) > 254 or "@" not in normalized_email:
        raise ValueError("a valid email address is required")
    if os.environ.get("WORKSPACE_USER_PROVISIONING_ENABLED", "").strip().lower() not in {
        "1", "true", "yes",
    }:
        raise ValueError("set WORKSPACE_USER_PROVISIONING_ENABLED=true to enable this operation")

    directory_factory = control_session_factory if settings.tenant_region_routing_enabled else session_factory
    with UnitOfWork(directory_factory) as uow:
        if settings.tenant_region_routing_enabled:
            assignment = uow.tenant_database_assignments.get(tenant_id=tenant_id)
            if assignment is None or assignment.status != "active":
                raise ValueError("tenant is not active in the workspace directory")
            workspace_label = tenant_id
        else:
            tenant = uow.tenants.get(tenant_id=tenant_id)
            if tenant is None:
                raise ValueError("tenant does not exist")
            workspace_label = tenant.name

        user = uow.workspace_identity.get_user_by_email(normalized_email)
        if user is None:
            user = WorkspaceUser(
                id=uuid4(), email=normalized_email,
                display_name=display_name.strip()[:160],
                status="invited",
            )
            uow.workspace_identity.create_user(user)
        elif user.status == "disabled":
            raise ValueError("this workspace identity is disabled")
        elif display_name.strip() and not user.display_name:
            user.display_name = display_name.strip()[:160]

        existing = uow.workspace_identity.get_membership_for_user_tenant(
            user_id=user.id, tenant_id=tenant_id,
        )
        if existing is not None:
            raise ValueError("this email already has an active membership in the tenant")
        pending = uow.session.exec(
            __import__("sqlmodel").select(TenantMembership).where(
                TenantMembership.user_id == user.id,
                TenantMembership.tenant_id == tenant_id,
            )
        ).first()
        if pending is not None:
            if pending.status == "suspended":
                raise ValueError("a suspended membership must be reviewed by an administrator")
            raise ValueError("an invitation already exists for this email and tenant")

        membership = TenantMembership(
            id=uuid4(), user_id=user.id, tenant_id=tenant_id,
            workspace_label=workspace_label, role=role, status="invited",
        )
        uow.workspace_identity.create_membership(membership)
        return str(user.id), str(membership.id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Provision an invited email identity for an existing Agent Platform tenant."
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=sorted(WORKSPACE_ROLES), default="owner")
    parser.add_argument("--display-name", default="")
    args = parser.parse_args(argv)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("requires an interactive terminal; do not run from CI or captured deployment logs")
    print("This creates an invitation only; it does not send an email or deploy any service.")
    confirmation = input(
        f"Invite {args.email!r} to tenant {args.tenant_id!r} as {args.role}? [y/N] "
    ).strip().lower()
    if confirmation not in {"y", "yes"}:
        print("No membership created.", file=sys.stderr)
        return 1

    try:
        user_id, membership_id = provision_workspace_user(
            tenant_id=args.tenant_id, email=args.email,
            role=args.role, display_name=args.display_name,
        )
    except ValueError as exc:
        parser.error(str(exc))

    print("Workspace invitation provisioned.")
    print(f"User ID: {user_id}")
    print(f"Membership ID: {membership_id}")
    print("The user can request a passwordless sign-in link using the invited email address.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
