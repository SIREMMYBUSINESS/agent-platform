"""Provider-independent CRM contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class CRMProvider(Protocol):
    """Minimal CRM adapter; implementations own SDK and transport details."""

    provider_key: str

    def create_contact(self, contact: Mapping[str, object], *, tenant_id: str,
                       business_id: str, idempotency_key: str) -> Mapping[str, object]: ...

    def update_contact(self, external_id: str, contact: Mapping[str, object], *,
                       tenant_id: str, business_id: str,
                       idempotency_key: str) -> Mapping[str, object]: ...

    def get_contact(self, external_id: str, *, tenant_id: str,
                    business_id: str) -> Mapping[str, object] | None: ...

    def create_activity(self, activity: Mapping[str, object], *, tenant_id: str,
                        business_id: str, idempotency_key: str) -> Mapping[str, object]: ...

    def create_conversation_note(self, external_contact_id: str | None,
                                 note: Mapping[str, object], *,
                                 tenant_id: str, business_id: str,
                                 idempotency_key: str) -> Mapping[str, object]: ...


__all__ = ["CRMProvider"]
