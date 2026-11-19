"""Register, move or disable the dialed number a business answers on (voice gateway directory).

Examples:
  python -m scripts.provision_voice_number --number +15550001111 --tenant-id T --business-id B
  python -m scripts.provision_voice_number --number +15550001111 --tenant-id T --business-id B --disable
  python -m scripts.provision_voice_number --number +15550001111 --tenant-id T2 --business-id B2 --reassign
"""

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from typing import Any

from app.services.voice_number_resolver import SCOPE_ID_RE

E164_RE = re.compile(r"^\+[1-9][0-9]{6,14}$")


class ProvisionError(Exception):
    """The request is invalid or unsafe; nothing was changed."""


def provision_number(
    *,
    number: str,
    tenant_id: str,
    business_id: str,
    disable: bool = False,
    reassign: bool = False,
    directory_uow_factory: Callable[[], Any],
    business_uow_factory: Callable[[], Any],
) -> str:
    if not E164_RE.fullmatch(number):
        raise ProvisionError("number must be E.164, for example +15550001111")
    if not SCOPE_ID_RE.fullmatch(tenant_id) or not SCOPE_ID_RE.fullmatch(business_id):
        raise ProvisionError("tenant and business ids must be 1-64 characters of letters, digits, '_' or '-'")

    # The business must exist under that tenant in the runtime database.
    with business_uow_factory() as uow:
        if uow.businesses.get(tenant_id=tenant_id, business_id=business_id) is None:
            raise ProvisionError("business not found under that tenant")

    with directory_uow_factory() as uow:
        routes = uow.voice_number_routes
        existing = routes.get(e164=number)
        status = "disabled" if disable else "active"
        if existing is None:
            action = "created"
        elif (existing.tenant_id, existing.business_id) != (tenant_id, business_id):
            # Never move a number between businesses by accident.
            if not reassign:
                raise ProvisionError("number is already assigned to a different business; pass --reassign to move it")
            action = "reassigned"
        elif existing.status == status:
            return "unchanged"
        else:
            action = "disabled" if disable else "enabled"
        routes.upsert(e164=number, tenant_id=tenant_id, business_id=business_id, status=status)
        uow.commit()
    return action


def main(
    argv: Sequence[str] | None = None,
    *,
    directory_uow_factory: Callable[[], Any] | None = None,
    business_uow_factory: Callable[[], Any] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--number", required=True, help="dialed number in E.164 format")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--disable", action="store_true", help="stop routing calls for this number")
    parser.add_argument("--reassign", action="store_true", help="allow moving the number to another business")
    args = parser.parse_args(argv)

    try:
        if directory_uow_factory is None or business_uow_factory is None:
            from fastapi import HTTPException

            from app.auth.dependencies import get_auth_uow_factory
            from app.persistence.session import session_factory
            from app.persistence.unit_of_work import UnitOfWork

            try:
                directory_uow_factory = directory_uow_factory or get_auth_uow_factory()
            except HTTPException as exc:
                raise ProvisionError(str(exc.detail)) from None
            business_uow_factory = business_uow_factory or (lambda: UnitOfWork(session_factory))
        action = provision_number(
            number=args.number, tenant_id=args.tenant_id, business_id=args.business_id,
            disable=args.disable, reassign=args.reassign,
            directory_uow_factory=directory_uow_factory, business_uow_factory=business_uow_factory,
        )
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{action}: {args.number} -> business {args.business_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
