"""Tenant/business-scoped static FAQ knowledge adapter."""
from __future__ import annotations

import json
import re

from app.integrations.provider_support import ProviderConfigurationError, TenantConfigResolver


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


class ConfiguredFAQKnowledgeAdapter:
    """Resolve deterministic FAQ entries from trusted business configuration.

    faq_entries_json is a JSON array of objects with an answer and either an
    exact question or configured keywords. Ambiguous matches fall back to staff.
    """

    def __init__(self, resolver: TenantConfigResolver):
        self._resolver = resolver

    def answer(self, *, tenant_id: str, business_id: str, question: str) -> str | None:
        config = self._resolver.resolve(tenant_id=tenant_id, business_id=business_id)
        raw = config.get("faq_entries_json", "").strip()
        if not raw:
            return None
        try:
            entries = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raise ProviderConfigurationError("faq_entries_json is invalid") from None
        if not isinstance(entries, list):
            raise ProviderConfigurationError("faq_entries_json must be an array")
        normalized_question = _normalize(question)
        matches = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("answer"), str):
                raise ProviderConfigurationError("FAQ entries require an answer string")
            exact = entry.get("question")
            keywords = entry.get("keywords", [])
            if exact is not None and not isinstance(exact, str):
                raise ProviderConfigurationError("FAQ question must be a string")
            if not isinstance(keywords, list) or any(not isinstance(word, str) for word in keywords):
                raise ProviderConfigurationError("FAQ keywords must be strings")
            exact_match = bool(exact and _normalize(exact) == normalized_question)
            keyword_match = any(
                _normalize(keyword) and _normalize(keyword) in normalized_question
                for keyword in keywords
            )
            if (exact_match or keyword_match) and entry["answer"].strip():
                matches.append(entry["answer"].strip())
        return matches[0] if len(matches) == 1 else None


__all__ = ["ConfiguredFAQKnowledgeAdapter"]
