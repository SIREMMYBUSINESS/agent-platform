"""Single-process APScheduler runner for queue polling and periodic jobs."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import os

from apscheduler.events import EVENT_JOB_ERROR
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import settings
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.poller import DatabaseQueuePoller
from app.workers.runtime import runner


logger = logging.getLogger("app.observability")


class SingleProcessWorker:
    def __init__(self, *, uow_factory=None, now_provider=None) -> None:
        self._uow_factory = uow_factory or (lambda: UnitOfWork(session_factory))
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._region_id = settings.deployment_region_id
        self._worker_id = os.environ.get("RAILWAY_SERVICE_ID", "agent-platform-worker")[:120]
        self._poller = DatabaseQueuePoller(
            uow_factory=self._uow_factory,
            runner=runner,
            now_provider=self._now,
        )

    def _heartbeat(self, *, started_at: datetime | None = None) -> None:
        with self._uow_factory() as uow:
            uow.worker_heartbeats.touch(
                worker_id=self._worker_id,
                region_id=self._region_id,
                now=self._now(),
                started_at=started_at,
            )

    def _stop_heartbeat(self) -> None:
        with self._uow_factory() as uow:
            uow.worker_heartbeats.stop(worker_id=self._worker_id, now=self._now())

    def _guarded(self, name: str, callback) -> None:
        try:
            callback()
        except Exception as exc:
            logger.error("scheduled worker job failed", extra={
                "component": "workflow", "operation": name,
                "outcome": "failed", "error_type": type(exc).__name__,
            })

    def _poll(self) -> None:
        self._poller.run_once(limit=1)
        # Only refresh the liveness marker after a queue poll succeeds. The
        # /ready probe therefore detects a stalled consumer loop as well as a
        # dead worker process.
        self._heartbeat()

    def _daily_optimization(self) -> None:
        from app.workers.optimization_batch import run_daily_optimization
        run_daily_optimization()

    def run(self) -> None:
        from apscheduler.schedulers.base import SchedulerNotRunningError
        from app.regions.catalog import get_region

        get_region(self._region_id)
        started_at = self._now()
        self._heartbeat(started_at=started_at)
        scheduler = BlockingScheduler(
            timezone="UTC",
            executors={"default": ThreadPoolExecutor(max_workers=3)},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30},
        )
        scheduler.add_job(
            lambda: self._guarded("queue_poll", self._poll),
            "interval", seconds=5, id="postgres_queue_poll", next_run_time=started_at,
        )
        scheduler.add_job(
            lambda: self._guarded("daily_optimization", self._daily_optimization),
            "cron", hour=3, minute=0, id="daily_optimization",
        )
        scheduler.add_listener(
            lambda event: logger.error("scheduler job raised an error", extra={
                "component": "workflow", "operation": "scheduler",
                "outcome": "failed", "error_type": type(event.exception).__name__,
            }) if event.exception else None,
            EVENT_JOB_ERROR,
        )
        try:
            logger.info("PostgreSQL worker starting", extra={
                "component": "workflow", "operation": "startup", "outcome": "started",
                "region_id": self._region_id,
            })
            scheduler.start()
        finally:
            try:
                scheduler.shutdown(wait=True)
            except SchedulerNotRunningError:
                pass
            try:
                self._stop_heartbeat()
            except Exception as exc:
                logger.error("worker heartbeat shutdown failed", extra={
                    "component": "workflow", "operation": "shutdown",
                    "outcome": "failed", "error_type": type(exc).__name__,
                })


def run_worker() -> None:
    SingleProcessWorker().run()


__all__ = ["SingleProcessWorker", "run_worker"]
