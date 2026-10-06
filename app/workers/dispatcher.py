"""Celery Beat entry point that drains the durable database outbox."""

from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.celery import celery_app
from app.queue.dispatcher import DurableQueueDispatcher


@celery_app.task(name="agent_platform.queue.dispatch_due", acks_late=True)
def dispatch_due() -> dict[str, int]:
    dispatcher = DurableQueueDispatcher(
        uow_factory=lambda: UnitOfWork(session_factory), publisher=celery_app,
    )
    return {"dispatched": dispatcher.dispatch_due()}
