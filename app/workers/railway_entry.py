"""Railway process entry point for region-scoped Celery workers and Beat."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile


def _materialize_railway_secrets() -> None:
    """Move Railway's injected DB URLs into private read-only files for settings."""
    for variable in ("DATABASE_URL", "REDIS_URL"):
        file_variable = f"{variable}_FILE"
        value = os.environ.pop(variable, None)
        if not value or os.environ.get(file_variable):
            continue
        fd, path = tempfile.mkstemp(prefix="agent-platform-", suffix=".secret",
                                    dir=os.environ.get("TMPDIR", "/tmp"))
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
                secret_file.write(value)
                secret_file.flush()
                os.fsync(secret_file.fileno())
            os.chmod(path, stat.S_IRUSR)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            Path(path).unlink(missing_ok=True)
            raise
        os.environ[file_variable] = path


def _worker_args(environ: dict[str, str] | None = None) -> list[str]:
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
    if process_type not in {"worker", "beat"}:
        raise SystemExit("usage: python -m app.workers.railway_entry [worker|beat]")
    if process_type == "worker":
        args = _worker_args()
    else:
        args = ["-A", "app.queue.celery:celery_app", "beat", "--loglevel=INFO"]
    os.execv(sys.executable, [sys.executable, "-m", "celery", *args])


if __name__ == "__main__":
    main()
