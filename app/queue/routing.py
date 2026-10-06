"""Stable domain queues and documented per-process concurrency assumptions."""

from dataclasses import dataclass

from app.queue.contracts import QueueDomain


@dataclass(frozen=True)
class QueueRoute:
    domain: QueueDomain
    queue: str
    task_name: str
    concurrency: int


_ROUTES = {
    QueueDomain.INBOUND: QueueRoute(QueueDomain.INBOUND, "agent.inbound", "agent_platform.worker.inbound", 4),
    QueueDomain.OUTREACH: QueueRoute(QueueDomain.OUTREACH, "agent.outreach", "agent_platform.worker.outreach", 8),
    QueueDomain.SCHEDULING: QueueRoute(QueueDomain.SCHEDULING, "agent.scheduling", "agent_platform.worker.scheduling", 2),
    QueueDomain.ESCALATION: QueueRoute(QueueDomain.ESCALATION, "agent.escalation", "agent_platform.worker.escalation", 2),
    QueueDomain.ANALYTICS: QueueRoute(QueueDomain.ANALYTICS, "agent.analytics", "agent_platform.worker.analytics", 2),
}


def route_for(domain: QueueDomain | str, *, tenant_id: str, business_id: str) -> QueueRoute:
    """Validate task scope and route to a bounded domain pool, not tenant-named queues."""
    try:
        normalized = QueueDomain(domain)
    except ValueError:
        raise ValueError("unsupported queue domain") from None
    if not tenant_id.strip() or not business_id.strip():
        raise ValueError("tenant and business scope are required for task routing")
    return _ROUTES[normalized]


def worker_pool_assumptions() -> dict[str, int]:
    """Starting local concurrency settings; deployment must benchmark its own workload."""
    return {domain.value: route.concurrency for domain, route in _ROUTES.items()}


__all__ = ["QueueRoute", "route_for", "worker_pool_assumptions"]
