"""Resolve an inbound Twilio number to its provisioned business scope."""
from __future__ import annotations

import re
from collections.abc import Callable

from app.integrations.provider_support import (
    DirectoryTenantConfigResolver,
    ProviderConfigurationError,
    TenantConfigResolver,
)
from app.persistence.unit_of_work import UnitOfWork

_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")


class VoiceNumberLookupAmbiguous(Exception):
    """The same inbound number is configured for multiple business scopes."""


class VoiceNumberLookupService:
    """Find a number in trusted, per-business provider configuration.

    This is intentionally a platform-wide service operation authenticated with a
    dedicated secret. It never uses a caller-selected tenant or business ID.
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        config_resolver: TenantConfigResolver,
    ) -> None:
        self._uow_factory = uow_factory
        self._config_resolver = config_resolver

    def resolve(self, *, dialed_number: str) -> dict[str, str] | None:
        if not isinstance(dialed_number, str) or not _E164.fullmatch(dialed_number):
            raise ValueError("dialed_number must be E.164")

        matches: list[tuple[str, str]] = []
        with self._uow_factory() as uow:
            tenant_ids = uow.tenants.list_ids()
            for tenant_id in tenant_ids:
                for business in uow.businesses.list_for_tenant(tenant_id=tenant_id):
                    try:
                        provider_config = self._config_resolver.resolve(
                            tenant_id=tenant_id,
                            business_id=business.id,
                        )
                    except ProviderConfigurationError:
                        # Businesses without voice credentials have no inbound
                        # number mapping. Their IDs and config remain private.
                        continue

                    configured_number = provider_config.get("from_call", "").strip()
                    if configured_number == dialed_number:
                        matches.append((tenant_id, business.id))

        if len(matches) > 1:
            raise VoiceNumberLookupAmbiguous
        if not matches:
            return None

        tenant_id, business_id = matches[0]
        return {"tenant_id": tenant_id, "business_id": business_id}


__all__ = ["VoiceNumberLookupAmbiguous", "VoiceNumberLookupService"]
