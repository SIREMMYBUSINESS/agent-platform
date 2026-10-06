"""Application boundary for creating tenant-scoped durable queue records."""

from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.contracts import QueueTaskSubmission, QueueTaskView
from app.queue.service import QueueTaskService
from app.security import AuthenticatedPrincipal


def enqueue_task(principal: AuthenticatedPrincipal,
                 submission: QueueTaskSubmission) -> QueueTaskView:
    return QueueTaskService(
        uow_factory=lambda: UnitOfWork(session_factory),
    ).enqueue(principal, submission)


__all__ = ["enqueue_task"]
