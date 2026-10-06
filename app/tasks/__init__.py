"""Task APIs and compatibility exports for the legacy dispatcher."""

from app.tasks.legacy import TaskDispatcher
from app.queue.service import QueueTaskService

__all__ = ["QueueTaskService", "TaskDispatcher"]
