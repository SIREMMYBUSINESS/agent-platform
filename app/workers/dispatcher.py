"""Directly drain due durable work from PostgreSQL."""

from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.poller import DatabaseQueuePoller
from app.workers.runtime import runner


def dispatch_due() -> dict[str, int]:
    poller = DatabaseQueuePoller(
        uow_factory=lambda: UnitOfWork(session_factory), runner=runner,
    )
    return {"processed": poller.run_once()}
