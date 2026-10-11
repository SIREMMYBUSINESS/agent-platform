"""Persistence for liveness of the single database-backed worker."""

from datetime import datetime

from sqlmodel import Session

from app.persistence.models import WorkerHeartbeat


class SQLWorkerHeartbeatRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def touch(self, *, worker_id: str, region_id: str, now: datetime,
              started_at: datetime | None = None) -> WorkerHeartbeat:
        row = self._session.get(WorkerHeartbeat, worker_id)
        if row is None:
            row = WorkerHeartbeat(
                worker_id=worker_id,
                region_id=region_id,
                status="running",
                started_at=started_at or now,
                heartbeat_at=now,
            )
            self._session.add(row)
        else:
            row.region_id = region_id
            row.status = "running"
            if started_at is not None:
                row.started_at = started_at
            row.heartbeat_at = now
        self._session.flush()
        return row

    def stop(self, *, worker_id: str, now: datetime) -> bool:
        row = self._session.get(WorkerHeartbeat, worker_id)
        if row is None:
            return False
        row.status = "stopped"
        row.heartbeat_at = now
        self._session.flush()
        return True


__all__ = ["SQLWorkerHeartbeatRepository"]
