"""Daily optimization batch and operator CLI.

Run locally with: python -m app.workers.optimization_batch --tenant-id tenant-1 --dry-run
"""
from __future__ import annotations

import argparse
import json

from app.config import settings
from app.optimization.orchestrator import FeedbackLoopOrchestrator
from app.persistence.session import session_factory
from app.persistence.unit_of_work import UnitOfWork
from app.queue.celery import celery_app


def build_orchestrator() -> FeedbackLoopOrchestrator:
    return FeedbackLoopOrchestrator(
        uow_factory=lambda: UnitOfWork(session_factory),
        minimum_sample=settings.optimization_min_sample_size,
        confidence_threshold=settings.optimization_confidence_threshold,
        dry_run=settings.optimization_dry_run,
    )


@celery_app.task(name="agent_platform.optimization.run_daily")
def run_daily_optimization() -> dict:
    results = build_orchestrator().run_daily()
    return {"businesses_processed": len(results),
            "recommendations": sum(len(row["recommendations"]) for row in results)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze tenant feedback and generate optimization recommendations")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--business-id")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    results = build_orchestrator().run_tenant(
        args.tenant_id, business_id=args.business_id,
        dry_run=True if args.dry_run else None, days=args.days,
    )
    print(json.dumps(results, default=str, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
