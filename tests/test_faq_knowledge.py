import json

import pytest

from app.integrations.knowledge import ConfiguredFAQKnowledgeAdapter
from app.integrations.provider_support import MappingTenantConfig, ProviderConfigurationError


def test_faq_adapter_uses_exact_tenant_business_configuration():
    resolver = MappingTenantConfig({
        ("tenant-a", "business-a"): {
            "faq_entries_json": json.dumps([
                {"question": "What are your hours?", "answer": "9 AM to 5 PM."},
                {"keywords": ["parking"], "answer": "Street parking is available."},
            ]),
        },
        ("tenant-b", "business-a"): {
            "faq_entries_json": json.dumps([
                {"question": "What are your hours?", "answer": "Different business hours."},
            ]),
        },
    })
    adapter = ConfiguredFAQKnowledgeAdapter(resolver)

    assert adapter.answer(
        tenant_id="tenant-a", business_id="business-a", question="What are your hours?",
    ) == "9 AM to 5 PM."
    assert adapter.answer(
        tenant_id="tenant-b", business_id="business-a", question="What are your hours?",
    ) == "Different business hours."
    assert adapter.answer(
        tenant_id="tenant-a", business_id="business-a", question="Is there parking?",
    ) == "Street parking is available."
    assert adapter.answer(
        tenant_id="tenant-a", business_id="business-a", question="Do you validate transit passes?",
    ) is None


def test_invalid_faq_configuration_fails_closed():
    adapter = ConfiguredFAQKnowledgeAdapter(MappingTenantConfig({
        ("tenant-a", "business-a"): {"faq_entries_json": "not-json"},
    }))
    with pytest.raises(ProviderConfigurationError):
        adapter.answer(tenant_id="tenant-a", business_id="business-a", question="hours?")
