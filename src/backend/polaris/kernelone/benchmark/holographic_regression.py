"""Regression helpers for holographic benchmark suite."""

from __future__ import annotations

from dataclasses import dataclass

from polaris.kernelone.benchmark.reporting.guard import (
    RegressionGuard,
    RegressionThreshold,
)
from polaris.kernelone.benchmark.reporting.structs import RegressionAlert


@dataclass(frozen=True, kw_only=True)
class DeltaJudgement:
    """Single metric regression outcome."""

    metric_name: str
    baseline: float
    current: float
    delta_percent: float
    warning: bool
    failed: bool


def evaluate_delta(
    *,
    metric_name: str,
    baseline: float,
    current: float,
    warning_threshold_percent: float = 5.0,
    fail_threshold_percent: float = 10.0,
) -> DeltaJudgement:
    """Evaluate warning/fail flags for a metric versus baseline."""
    delta_percent = (0.0 if current == 0 else 100.0) if baseline == 0 else (current - baseline) / baseline * 100.0
    warning = delta_percent > warning_threshold_percent
    failed = delta_percent > fail_threshold_percent
    return DeltaJudgement(
        metric_name=metric_name,
        baseline=baseline,
        current=current,
        delta_percent=delta_percent,
        warning=warning,
        failed=failed,
    )


def guard_regressions(
    *,
    baseline: dict[str, float],
    current: dict[str, float],
    warning_threshold_percent: float = 5.0,
    fail_threshold_percent: float = 10.0,
) -> list[RegressionAlert]:
    """Run RegressionGuard with uniform thresholds across metrics."""
    thresholds = [
        RegressionThreshold(
            metric_name=name,
            increase_threshold_percent=warning_threshold_percent,
            decrease_threshold_percent=warning_threshold_percent,
        )
        for name in baseline
    ]
    guard = RegressionGuard(baseline=baseline, thresholds=thresholds)
    alerts = guard.check(current)
    filtered: list[RegressionAlert] = []
    for alert in alerts:
        if (
            abs(alert.change_percent) >= fail_threshold_percent
            or abs(alert.change_percent) >= warning_threshold_percent
        ):
            filtered.append(alert)
    return filtered
