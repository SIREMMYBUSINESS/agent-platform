"""Railway entry point for the single PostgreSQL-backed worker process."""
from __future__ import annotations

import os
import sys

from app.runtime_entry import materialize_database_url


def _materialize_railway_secrets() -> None:
    """Move Railway's injected database URL into a private read-only file."""
    materialize_database_url()


def _worker_args(environ: dict[str, str] | None = None) -> list[str]:
    """Legacy Celery argument builder retained for older local tooling only."""
    env = os.environ if environ is None else environ
    from app.queue.routing import worker_pool_assumptions
    from app.config import settings
    from app.regions.catalog import get_region

    domain_capacity = worker_pool_assumptions()
    configured_domains = env.get("WORKER_DOMAINS", "").strip()
    domains = ([item.strip() for item in configured_domains.split(",") if item.strip()]
               if configured_domains else list(domain_capacity))
    if not domains or len(set(domains)) != len(domains) or set(domains) - set(domain_capacity):
        raise ValueError("WORKER_DOMAINS must be a unique comma-separated list of worker domains")

    region_id = env.get("DEPLOYMENT_REGION_ID", settings.deployment_region_id)
    get_region(region_id)
    queues = {f"agent.{domain}" for domain in domains}
    queues.update(f"agent.{region_id}.{domain}" for domain in domains)
    if "inbound" in domains:
        queues.add("agent.control")
    requested_concurrency = env.get("WORKER_CONCURRENCY")
    default_concurrency = sum(domain_capacity[domain] for domain in domains)
    try:
        concurrency = int(requested_concurrency or default_concurrency)
    except ValueError:
        raise ValueError("WORKER_CONCURRENCY must be a positive integer") from None
    if not 1 <= concurrency <= 64:
        raise ValueError("WORKER_CONCURRENCY must be between 1 and 64")
    return ["-A", "app.queue.celery:celery_app", "worker", "--loglevel=INFO",
            f"--concurrency={concurrency}", f"--queues={','.join(sorted(queues))}"]


def main() -> None:
    _materialize_railway_secrets()
    process_type = sys.argv[1] if len(sys.argv) > 1 else "worker"
    if process_type != "worker":
        raise SystemExit("usage: python -m app.workers.railway_entry [worker]")
    from app.workers.scheduler import run_worker
    run_worker()


if __name__ == "__main__":
    main()
