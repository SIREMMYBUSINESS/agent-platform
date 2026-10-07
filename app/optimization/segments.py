"""Bounded, privacy-safe audience segments shared by feedback and routing."""
from __future__ import annotations

import re
from typing import Any


ALLOWED_SOURCES = {"inbound", "outreach", "referral", "web", "partner", "manual"}
ALLOWED_INDUSTRIES = {
    "healthcare", "legal", "real_estate", "technology", "finance", "retail",
    "education", "hospitality", "home_services", "other", "unknown",
}
_COUNTRY_CODE = re.compile(r"^[A-Z]{2}$")


def normalized_segment(*, source: Any = "manual", industry: Any = "unknown",
                       country: Any = "unknown") -> str:
    source_value = str(source or "manual").casefold()
    industry_value = str(industry or "unknown").casefold().replace(" ", "_")
    country_value = str(country or "unknown").strip().upper()
    if source_value not in ALLOWED_SOURCES:
        source_value = "other"
    if industry_value not in ALLOWED_INDUSTRIES:
        industry_value = "other"
    if not _COUNTRY_CODE.fullmatch(country_value):
        country_value = "unknown"
    return f"source={source_value};industry={industry_value};country={country_value}"


def segment_for_lead(uow, *, tenant_id: str, business_id: str, lead_id) -> str:
    """Resolve only tenant-scoped, allowlisted lead attributes for timing."""
    lead = uow.leads.get(tenant_id=tenant_id, business_id=business_id, lead_id=lead_id)
    if lead is None:
        return normalized_segment()
    source = uow.lead_sources.get(tenant_id=tenant_id, business_id=business_id,
                                  source_id=lead.source_id)
    enrichment = uow.lead_enrichments.latest(
        tenant_id=tenant_id, business_id=business_id, lead_id=lead.id,
    )
    company = getattr(enrichment, "company_info", {}) if enrichment else {}
    return normalized_segment(
        source=getattr(source, "source_type", "manual"),
        industry=company.get("industry", "unknown") if isinstance(company, dict) else "unknown",
        country=company.get("country", "unknown") if isinstance(company, dict) else "unknown",
    )
