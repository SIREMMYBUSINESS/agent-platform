"""Pure threshold evaluation helpers shared by alert tests and integrations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertThresholds:
    queue_depth: int = 100
    worker_error_ratio: float = 0.05
    policy_block_ratio: float = 0.25
    provider_error_ratio: float = 0.05
    appointment_confirmation_drop: float = 0.5
    conversion_drop: float = 0.5
    dead_letter_depth: int = 10


def evaluate_alerts(*, queue_depth: int, worker_errors: int, worker_tasks: int,
                    policy_blocks: int, policy_decisions: int, provider_errors: int,
                    provider_calls: int, confirmation_rate: float, baseline_confirmation_rate: float,
                    conversion_rate: float, baseline_conversion_rate: float,
                    dead_letter_depth: int,
                    thresholds: AlertThresholds = AlertThresholds()) -> dict[str, bool]:
    ratio = lambda numerator, denominator: numerator / denominator if denominator > 0 else 0.0
    return {
        "high_queue_depth": queue_depth >= thresholds.queue_depth,
        "high_worker_error_rate": ratio(worker_errors, worker_tasks) >= thresholds.worker_error_ratio,
        "policy_block_surge": ratio(policy_blocks, policy_decisions) >= thresholds.policy_block_ratio,
        "provider_api_failures": ratio(provider_errors, provider_calls) >= thresholds.provider_error_ratio,
        "appointment_confirmation_drop": baseline_confirmation_rate > 0 and
            confirmation_rate < baseline_confirmation_rate * thresholds.appointment_confirmation_drop,
        "conversion_rate_anomaly": baseline_conversion_rate > 0 and
            conversion_rate < baseline_conversion_rate * thresholds.conversion_drop,
        "dead_letter_depth": dead_letter_depth >= thresholds.dead_letter_depth,
    }


__all__ = ["AlertThresholds", "evaluate_alerts"]
