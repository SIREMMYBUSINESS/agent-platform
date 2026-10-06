"""Stable message-sequence A/B assignment and conversion comparison."""

from dataclasses import dataclass
import hashlib
from typing import Mapping


@dataclass(frozen=True)
class VariantResult:
    variant: str
    delivered: int
    conversions: int
    conversion_rate: float


class SequenceExperimentService:
    @staticmethod
    def assign_variant(*, tenant_id: str, business_id: str, campaign_id: str,
                       lead_id: str, experiment_id: str,
                       weights: Mapping[str, int]) -> str:
        """Assign deterministically per lead so retries preserve the same arm."""
        normalized = sorted((name, weight) for name, weight in weights.items())
        if not normalized or any(not name or weight < 1 for name, weight in normalized):
            raise ValueError("experiment variants require positive weights")
        total = sum(weight for _, weight in normalized)
        digest = hashlib.sha256("|".join((tenant_id, business_id, campaign_id,
                                            lead_id, experiment_id)).encode()).digest()
        point = int.from_bytes(digest[:8], "big") % total
        for name, weight in normalized:
            if point < weight:
                return name
            point -= weight
        raise AssertionError("weighted variant selection failed")

    @staticmethod
    def compare(results: Mapping[str, tuple[int, int]]) -> tuple[VariantResult, ...]:
        """Return delivered-to-conversion rates; zero-delivery arms remain visible."""
        output = []
        for variant, (delivered, conversions) in sorted(results.items()):
            if delivered < 0 or conversions < 0 or conversions > delivered:
                raise ValueError("conversion counts must be between zero and delivered")
            output.append(VariantResult(
                variant=variant, delivered=delivered, conversions=conversions,
                conversion_rate=conversions / delivered if delivered else 0.0,
            ))
        return tuple(output)


__all__ = ["SequenceExperimentService", "VariantResult"]
