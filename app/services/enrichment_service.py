"""Optional lead enrichment adapters and failure-isolated orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


ENRICHMENT_FIELDS = (
    "company_info",
    "public_profile_data",
    "website_metadata",
    "custom_business_context",
    "lead_fit_signal",
)


class EnrichmentAdapter(Protocol):
    name: str

    def enrich(self, *, contact: Any, business: Any) -> dict[str, Any] | None: ...


class NoopEnrichmentAdapter:
    """Default adapter: imports remain useful without external providers."""

    name = "none"

    def enrich(self, *, contact: Any, business: Any) -> dict[str, Any] | None:
        return None


@dataclass(frozen=True)
class EnrichmentResult:
    values: dict[str, Any] | None
    provider_name: str
    failed: bool = False


class EnrichmentService:
    def __init__(self, adapter: EnrichmentAdapter | None = None) -> None:
        self._adapter = adapter or NoopEnrichmentAdapter()

    def collect(self, *, contact: Any, business: Any) -> EnrichmentResult:
        try:
            result = self._adapter.enrich(contact=contact, business=business)
            if result is None:
                return EnrichmentResult(None, self._adapter.name)
            values = {
                field: result.get(field, {})
                for field in ENRICHMENT_FIELDS
            }
            if any(not isinstance(value, dict) for value in values.values()):
                raise ValueError("enrichment adapter returned invalid structured fields")
            return EnrichmentResult(values, self._adapter.name)
        except Exception:
            # Provider outages must not reject the lead import. Avoid exposing
            # provider exception details, which may contain secrets or PII.
            return EnrichmentResult(None, self._adapter.name, failed=True)


__all__ = ["EnrichmentAdapter", "EnrichmentResult", "EnrichmentService", "NoopEnrichmentAdapter"]
