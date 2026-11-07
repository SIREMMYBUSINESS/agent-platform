"""Bounded dependency and worker health probes."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import select
from sqlalchemy import text

from app.observability.metrics import metrics
from app.persistence.models import WorkerHeartbeat


QUEUE_DOMAINS = ("inbound", "outreach", "scheduling", "escalation", "analytics")


def check_components(*, session_factory, control_session_factory=None,
                     worker_stale_seconds: int = 30,
                     redis_url: str | None = None, celery_app=None) -> dict[str, object]:
    components: dict[str, str] = {}
    session = None
    try:
        session = session_factory()
        session.exec(text("SELECT 1")).one()
        components["database"] = "healthy"
    except Exception:
        components["database"] = "unhealthy"
    finally:
        if session is not None:
            session.close()
    if control_session_factory is not None:
        control_session = None
        try:
            control_session = control_session_factory()
            control_session.exec(text("SELECT 1")).one()
            components["directory"] = "healthy"
        except Exception:
            components["directory"] = "unhealthy"
        finally:
            if control_session is not None:
                control_session.close()
    # Preserve the old probe only for callers explicitly using the deprecated API.
    if redis_url is not None and celery_app is not None:
        try:
            import redis
            redis.Redis.from_url(redis_url, socket_connect_timeout=0.5,
                                 socket_timeout=0.5).ping()
            components["redis"] = "healthy"
        except Exception:
            components["redis"] = "unhealthy"
        try:
            replies = celery_app.control.inspect(timeout=1.0).active_queues() or {}
            domains = {domain: False for domain in QUEUE_DOMAINS}
            for queues in replies.values():
                for queue in queues:
                    name = str(queue.get("name", ""))
                    for domain in QUEUE_DOMAINS:
                        if domain in name:
                            domains[domain] = True
            for domain, alive in domains.items():
                metrics.set_gauge("worker_heartbeat", 1.0 if alive else 0.0,
                                  queue_domain=domain)
            components["celery_workers"] = "healthy" if any(domains.values()) else "unhealthy"
        except Exception:
            components["celery_workers"] = "unhealthy"
            for domain in QUEUE_DOMAINS:
                metrics.set_gauge("worker_heartbeat", 0.0, queue_domain=domain)
        healthy = (components["database"] == "healthy"
                   and components["redis"] == "healthy"
                   and components.get("directory", "healthy") == "healthy")
        components["providers"] = "not_probed"
        return {"status": "healthy" if healthy else "degraded", "components": components}

    worker_session = None
    try:
        worker_session = session_factory()
        cutoff = datetime.now(UTC) - timedelta(seconds=worker_stale_seconds)
        heartbeat = worker_session.exec(select(WorkerHeartbeat).where(
            WorkerHeartbeat.status == "running",
            WorkerHeartbeat.heartbeat_at >= cutoff,
        ).limit(1)).first()
        worker_alive = heartbeat is not None
    except Exception:
        worker_alive = False
    finally:
        if worker_session is not None:
            worker_session.close()
    components["worker"] = "healthy" if worker_alive else "unhealthy"
    for domain in QUEUE_DOMAINS:
        metrics.set_gauge("worker_heartbeat", 1.0 if worker_alive else 0.0,
                          queue_domain=domain)
    # Provider probes are opt-in because they can incur cost or mutate provider state.
    components["providers"] = "not_probed"
    healthy = (components["database"] == "healthy" and components["worker"] == "healthy"
               and components.get("directory", "healthy") == "healthy")
    return {"status": "healthy" if healthy else "degraded", "components": components}
