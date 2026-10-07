"""Low-cardinality gauges sampled from the durable queue ledger."""
from sqlalchemy import func, select

from app.observability.health import QUEUE_DOMAINS
from app.observability.metrics import metrics
from app.persistence.models import QueueTaskRecord
from app.persistence.models import OptimizationAuditLog, OptimizationMetric


TASK_STATES = ("pending", "queued", "running", "retrying", "succeeded", "dead_letter")


def refresh_queue_depths(session_factory) -> None:
    session = None
    try:
        session = session_factory()
        rows = session.exec(select(
            QueueTaskRecord.domain, QueueTaskRecord.status, func.count(),
        ).group_by(QueueTaskRecord.domain, QueueTaskRecord.status)).all()
        counts = {(domain, status): int(count) for domain, status, count in rows}
        for domain in QUEUE_DOMAINS:
            for status in TASK_STATES:
                metrics.queue_depth(domain=domain, status=status,
                                    value=counts.get((domain, status), 0))
    except Exception:
        # Metrics collection cannot make the scrape or application unavailable.
        metrics.set_gauge("queue_metrics_collection_healthy", 0.0)
    else:
        metrics.set_gauge("queue_metrics_collection_healthy", 1.0)
    finally:
        if session is not None:
            session.close()


def refresh_optimization_health(session_factory) -> None:
    """Expose last durable batch status without tenant/business label leakage."""
    session = None
    try:
        session = session_factory()
        rows = session.exec(select(OptimizationMetric).where(
            OptimizationMetric.metric_type.in_(
                ("feedback_batch", "optimization_batch_failure")),
        ).order_by(OptimizationMetric.created_at.desc())).all()
        latest = {}
        for row in rows:
            latest.setdefault((row.tenant_id, row.business_id), row)
        for (tenant_id, business_id), row in latest.items():
            metrics.set_gauge(
                "optimization_batch_failed",
                1.0 if row.metric_type == "optimization_batch_failure" else 0.0,
                tenant_bucket=metrics.scope_bucket(tenant_id, buckets=32),
                business_bucket=metrics.scope_bucket(business_id, buckets=64),
            )
        rollouts = session.exec(select(OptimizationAuditLog).where(
            OptimizationAuditLog.action.in_(
                ("automatic_rollback", "rollout_advanced")),
        ).order_by(OptimizationAuditLog.created_at.desc())).all()
        latest_rollout = {}
        for row in rollouts:
            latest_rollout.setdefault((row.tenant_id, row.business_id), row)
        for (tenant_id, business_id), row in latest_rollout.items():
            metrics.set_gauge(
                "optimization_rollout_rollback",
                1.0 if row.action == "automatic_rollback" else 0.0,
                tenant_bucket=metrics.scope_bucket(tenant_id, buckets=32),
                business_bucket=metrics.scope_bucket(business_id, buckets=64),
            )
    except Exception:
        metrics.set_gauge("optimization_metrics_collection_healthy", 0.0)
    else:
        metrics.set_gauge("optimization_metrics_collection_healthy", 1.0)
    finally:
        if session is not None:
            session.close()
