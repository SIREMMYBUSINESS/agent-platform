"""Example worker provider bootstrap using protected mounted secret files.

Set WORKER_BOOTSTRAP_MODULE=app.integrations.bootstrap_example and
PROVIDER_SECRETS_DIR to a read-only secret volume. Each SHA-256 hashed
tenant/business JSON file contains provider selectors and provider credentials.
"""
from app.config import settings
from app.integrations.factory import ProviderAdapterFactory, TenantScopedMessageSender
from app.integrations.persistence_audit import SQLProviderAuditSink
from app.integrations.provider_support import DirectoryTenantConfigResolver
from app.integrations.knowledge import ConfiguredFAQKnowledgeAdapter
from app.services.faq_service import FAQService


def build_message_sender():
    if settings.provider_secrets_dir is None:
        raise RuntimeError("PROVIDER_SECRETS_DIR is required for provider delivery")
    resolver = DirectoryTenantConfigResolver(settings.provider_secrets_dir)
    factory = ProviderAdapterFactory(configs=resolver, audit_sink=SQLProviderAuditSink())
    return TenantScopedMessageSender(factory)


def build_calendar_provider():
    if settings.provider_secrets_dir is None:
        raise RuntimeError("PROVIDER_SECRETS_DIR is required for calendar delivery")
    resolver = DirectoryTenantConfigResolver(settings.provider_secrets_dir)
    factory = ProviderAdapterFactory(configs=resolver, audit_sink=SQLProviderAuditSink())
    # The adapter itself resolves each request using its tenant/business scope.
    return _TenantScopedCalendarProvider(factory)


def build_crm_provider():
    if settings.provider_secrets_dir is None:
        raise RuntimeError("PROVIDER_SECRETS_DIR is required for CRM synchronization")
    resolver = DirectoryTenantConfigResolver(settings.provider_secrets_dir)
    factory = ProviderAdapterFactory(configs=resolver, audit_sink=SQLProviderAuditSink())
    return _TenantScopedCRMProvider(factory)


def build_faq_service():
    if settings.provider_secrets_dir is None:
        raise RuntimeError("PROVIDER_SECRETS_DIR is required for business FAQ configuration")
    resolver = DirectoryTenantConfigResolver(settings.provider_secrets_dir)
    return FAQService(knowledge_adapter=ConfiguredFAQKnowledgeAdapter(resolver))


class _TenantScopedCalendarProvider:
    def __init__(self, factory):
        self._factory = factory

    def create_event(self, **kwargs):
        return self._factory.build(
            "calendar", tenant_id=kwargs["tenant_id"], business_id=kwargs["business_id"],
        ).create_event(**kwargs)

    def cancel_event(self, **kwargs):
        return self._factory.build(
            "calendar", tenant_id=kwargs["tenant_id"], business_id=kwargs["business_id"],
        ).cancel_event(**kwargs)

    def availability(self, **kwargs):
        return self._factory.build(
            "calendar", tenant_id=kwargs["tenant_id"], business_id=kwargs["business_id"],
        ).availability(**kwargs)


class _TenantScopedCRMProvider:
    def __init__(self, factory):
        self._factory = factory
        self.provider_key = "tenant_scoped_crm"

    def __getattr__(self, name):
        if name not in {"create_contact", "update_contact", "get_contact",
                        "create_activity", "create_conversation_note"}:
            raise AttributeError(name)
        def call(*args, **kwargs):
            provider = self._factory.build(
                "crm", tenant_id=kwargs["tenant_id"], business_id=kwargs["business_id"],
            )
            return getattr(provider, name)(*args, **kwargs)
        return call


def configure_worker_handlers(registry):
    """Runtime constructs built-in handlers with these scoped provider factories."""
    return None


__all__ = ["build_message_sender", "build_calendar_provider", "build_crm_provider",
           "build_faq_service",
           "configure_worker_handlers"]
