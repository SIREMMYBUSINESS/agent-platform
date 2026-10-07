"""Worker runtime configuration and queue execution entry point."""

import importlib
from uuid import UUID

from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.runtime import QueueTaskRunner, TaskHandlerRegistry


from app.workers.handlers import build_default_handlers

bootstrap = None
if settings.worker_bootstrap_module:
    bootstrap = importlib.import_module(settings.worker_bootstrap_module)
message_sender_factory = getattr(bootstrap, "build_message_sender", None) if bootstrap else None
message_sender = message_sender_factory() if callable(message_sender_factory) else None
calendar_provider_factory = getattr(bootstrap, "build_calendar_provider", None) if bootstrap else None
calendar_provider = calendar_provider_factory() if callable(calendar_provider_factory) else None
crm_provider_factory = getattr(bootstrap, "build_crm_provider", None) if bootstrap else None
crm_provider = crm_provider_factory() if callable(crm_provider_factory) else None
faq_service_factory = getattr(bootstrap, "build_faq_service", None) if bootstrap else None
faq_service = faq_service_factory() if callable(faq_service_factory) else None
handlers = build_default_handlers(
    message_sender=message_sender, calendar_provider=calendar_provider,
    crm_provider=crm_provider, faq_service=faq_service,
)


def register_handler(domain: str, task_name: str, handler) -> None:
    handlers.register(domain, task_name, handler)


def replace_handler(domain: str, task_name: str, handler) -> None:
    handlers.replace(domain, task_name, handler)


if bootstrap is not None:
    configure = getattr(bootstrap, "configure_worker_handlers", None)
    if not callable(configure):
        raise RuntimeError("worker bootstrap module must define configure_worker_handlers(registry)")
    configure(handlers)

runner = QueueTaskRunner(
    uow_factory=lambda: UnitOfWork(session_factory), handlers=handlers,
)


def execute_domain_task(domain: str, task_id: str) -> dict:
    return runner.execute(UUID(task_id), expected_domain=domain)


__all__ = ["execute_domain_task", "handlers", "register_handler", "replace_handler", "runner"]
