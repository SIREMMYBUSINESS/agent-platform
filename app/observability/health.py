"""Bounded dependency and worker health probes."""
from __future__ import annotations

from sqlalchemy import text

from app.observability.metrics import metrics


QUEUE_DOMAINS = ("inbound", "outreach", "scheduling", "escalation", "analytics")


def check_components(*, session_factory, redis_url: str, celery_app) -> dict[str, object]:
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
    # Provider probes are opt-in because they can incur cost or mutate provider state.
    components["providers"] = "not_probed"
    healthy = components["database"] == "healthy" and components["redis"] == "healthy"
    return {"status": "healthy" if healthy else "degraded", "components": components}
