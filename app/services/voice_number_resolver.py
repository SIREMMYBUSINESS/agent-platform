"""Resolve a dialed number to its tenant business scope for the voice gateway.

Fails closed: any missing, disabled, malformed or inconsistent mapping is "not found".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Ids are embedded in a signed stream URL path by the gateway, so they must be opaque and URL-safe.
SCOPE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VoiceNumberNotFound(Exception):
    """The number has no usable mapping."""


@dataclass(frozen=True)
class ResolvedVoiceNumber:
    tenant_id: str
    business_id: str
    voice_calls_per_minute: int


class VoiceNumberResolver:
    def __init__(
        self,
        *,
        directory_uow_factory: Callable[[], Any],
        business_uow_factory: Callable[[], Any],
        region_guard: Callable[[str], None],
    ) -> None:
        self._directory_uow_factory = directory_uow_factory
        self._business_uow_factory = business_uow_factory
        self._region_guard = region_guard

    def resolve(self, e164: str) -> ResolvedVoiceNumber:
        with self._directory_uow_factory() as uow:
            route = uow.voice_number_routes.get_active(e164=e164)
            if route is None:
                raise VoiceNumberNotFound
            tenant_id, business_id = route.tenant_id, route.business_id
        if not SCOPE_ID_RE.fullmatch(tenant_id) or not SCOPE_ID_RE.fullmatch(business_id):
            raise VoiceNumberNotFound
        # Raises HTTP 403/421/503 for disabled, foreign-region or migrating tenants.
        self._region_guard(tenant_id)
        # The business must exist under that tenant in the runtime database.
        with self._business_uow_factory() as uow:
            business = uow.businesses.get(tenant_id=tenant_id, business_id=business_id)
            if business is None:
                raise VoiceNumberNotFound
            business_limit = business.voice_calls_per_minute
        return ResolvedVoiceNumber(
            tenant_id=tenant_id, business_id=business_id,
            voice_calls_per_minute=business_limit,
        )


__all__ = ["ResolvedVoiceNumber", "SCOPE_ID_RE", "VoiceNumberNotFound", "VoiceNumberResolver"]
