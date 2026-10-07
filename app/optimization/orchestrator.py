"""Per-business daily feedback analysis coordinator."""
from datetime import UTC, datetime, timedelta

from app.optimization.service import OptimizationService


class FeedbackLoopOrchestrator:
    def __init__(self, *, uow_factory, minimum_sample: int = 30,
                 confidence_threshold: float = 0.75, dry_run: bool = False):
        self._uow_factory = uow_factory
        self._minimum_sample = minimum_sample
        self._confidence_threshold = confidence_threshold
        self._dry_run = dry_run

    def run_tenant(self, tenant_id: str, *, business_id: str | None = None,
                   dry_run: bool | None = None, days: int = 7) -> list[dict]:
        with self._uow_factory() as uow:
            businesses = uow.businesses.list_for_tenant(tenant_id=tenant_id)
            selected = [row for row in businesses if business_id is None or row.id == business_id]
        if business_id is not None and not selected:
            raise LookupError("business not found in tenant scope")
        results = []
        effective_dry_run = self._dry_run if dry_run is None else dry_run
        for row in selected:
            service = OptimizationService(
                trusted_tenant_id=tenant_id, trusted_business_id=row.id,
                uow_factory=self._uow_factory,
                minimum_sample=self._minimum_sample,
                confidence_threshold=self._confidence_threshold,
                dry_run=self._dry_run,
            )
            try:
                result = service.run_batch(days=days, dry_run=dry_run)
            except Exception as exc:
                occurred_at = datetime.now(UTC)
                with self._uow_factory() as failed_uow:
                    failed_uow.optimization.add_metric(
                        tenant_id=tenant_id, business_id=row.id,
                        metric_key=f"batch-failure:{occurred_at.isoformat()}",
                        metric_type="optimization_batch_failure", segment={},
                        sample_size=0,
                        metric_values={"error_type": type(exc).__name__[:100]},
                        period_start=occurred_at - timedelta(minutes=1),
                        period_end=occurred_at,
                    )
                    failed_uow.optimization.add_audit(
                        tenant_id=tenant_id, business_id=row.id,
                        action="batch_failed", actor_id="optimization_batch",
                        details={"error_type": type(exc).__name__[:100]},
                    )
                raise
            result["rollouts"] = ([] if effective_dry_run else
                                  service.monitor_active_rollouts(minimum_sample=self._minimum_sample))
            results.append(result)
        return results

    def run_daily(self, *, dry_run: bool | None = None, days: int = 7) -> list[dict]:
        with self._uow_factory() as uow:
            tenant_ids = uow.tenants.list_ids()
        results = []
        for tenant_id in tenant_ids:
            results.extend(self.run_tenant(tenant_id, dry_run=dry_run, days=days))
        return results
