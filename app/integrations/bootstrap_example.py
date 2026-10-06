"""Example worker provider bootstrap using protected mounted secret files.

Set WORKER_BOOTSTRAP_MODULE=app.integrations.bootstrap_example and
PROVIDER_SECRETS_DIR to a read-only secret volume. Each SHA-256 hashed
tenant/business JSON file contains provider selectors and provider credentials.
"""
from app.config import settings
from app.integrations.factory import ProviderAdapterFactory, TenantScopedMessageSender
from app.integrations.persistence_audit import SQLProviderAuditSink
from app.integrations.provider_support import DirectoryTenantConfigResolver


def build_message_sender():
    if settings.provider_secrets_dir is None:
        raise RuntimeError("PROVIDER_SECRETS_DIR is required for provider delivery")
    resolver = DirectoryTenantConfigResolver(settings.provider_secrets_dir)
    factory = ProviderAdapterFactory(configs=resolver, audit_sink=SQLProviderAuditSink())
    return TenantScopedMessageSender(factory)


__all__ = ["build_message_sender"]
