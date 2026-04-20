"""Regression Detection and Pipeline Fail-Fast Guard.

This module provides the RegressionGuard class for detecting
performance regressions and deciding whether to fail the CI/CD pipeline.

Design Principles
----------------
- Fail-Fast: Critical regressions immediately block pipelines
- Configurable Thresholds: Per-metric thresholds for different alert levels
- Baseline Comparison: All metrics compared against stored baselines

Example
-------
    from polaris.kernelone.benchmark.reporting import (
        RegressionGuard,
        RegressionThreshold,
        BenchmarkDB,
    )

    # Load baseline from storage
    db = BenchmarkDB("reports/baselines")
    baseline = db.load_baseline("main")

    # Configure thresholds
    thresholds = [
        RegressionThreshold(
            metric_name="latency_p50",
            increase_threshold_percent=10.0,  # 10% latency increase is regression
            decrease_threshold_percent=5.0,   # 5% accuracy drop is regression
        ),
    ]

    # Check for regressions
    guard = RegressionGuard(baseline=baseline, thresholds=thresholds)
    alerts = guard.check({
        "latency_p50": 135.0,  # vs baseline 120.0
        "accuracy": 0.88,      # vs baseline 0.90
    })

    if guard.should_fail_pipeline(alerts):
        print("PIPELINE FAILED: Critical regressions detected")
        for alert in alerts:
            print(f"  - {alert.metric_name}: {alert.change_percent:+.1f}%")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.reporting.structs import (
    AlertSeverity,
    RegressionAlert,
)

# ------------------------------------------------------------------
# Threshold Configuration
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class RegressionThreshold:
    """Configuration for regression detection on a single metric.

    Attributes:
        metric_name: Name of the metric to monitor.
        increase_threshold_percent: Percentage increase that triggers alert.
            For latency/duration metrics, this is a regression.
        decrease_threshold_percent: Percentage decrease that triggers alert.
            For accuracy/throughput metrics, this is a regression.
        absolute_threshold: Optional absolute threshold (e.g., max allowed latency).
        severity_for_above_20: Severity level when change exceeds 20%.
    """

    metric_name: str
    increase_threshold_percent: float
    decrease_threshold_percent: float
    absolute_threshold: float | None = None
    severity_for_above_20: AlertSeverity = AlertSeverity.CRITICAL

    def __post_init__(self) -> None:
        if isinstance(self.severity_for_above_20, str):
            object.__setattr__(self, "severity_for_above_20", AlertSeverity(self.severity_for_above_20))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "increase_threshold_percent": round(self.increase_threshold_percent, 2),
            "decrease_threshold_percent": round(self.decrease_threshold_percent, 2),
            "absolute_threshold": self.absolute_threshold,
            "severity_for_above_20": self.severity_for_above_20.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionThreshold:
        severity = data.get("severity_for_above_20", "critical")
        if isinstance(severity, str):
            severity = AlertSeverity(severity)
        return cls(
            metric_name=data.get("metric_name", ""),
            increase_threshold_percent=data.get("increase_threshold_percent", 10.0),
            decrease_threshold_percent=data.get("decrease_threshold_percent", 5.0),
            absolute_threshold=data.get("absolute_threshold"),
            severity_for_above_20=severity,
        )


# ------------------------------------------------------------------
# Regression Guard
# ------------------------------------------------------------------


class RegressionGuard:
    """Regression detection guard for CI/CD pipelines.

    This class compares current metrics against baselines and
    generates alerts when thresholds are exceeded.

    Attributes:
        baseline: The baseline metrics to compare against.
        thresholds: Configuration for each monitored metric.
        absolute_thresholds: Optional absolute limits.

    Example
    -------
        guard = RegressionGuard(
            baseline={"latency_p50": 120.0, "accuracy": 0.95},
            thresholds=[
                RegressionThreshold(
                    metric_name="latency_p50",
                    increase_threshold_percent=10.0,
                    decrease_threshold_percent=0.0,
                ),
            ],
        )
        alerts = guard.check({"latency_p50": 145.0})
    """

    def __init__(
        self,
        baseline: dict[str, float],
        thresholds: list[RegressionThreshold],
        absolute_thresholds: dict[str, tuple[float, float] | float] | None = None,
    ) -> None:
        """Initialize the regression guard.

        Args:
            baseline: Baseline metrics as metric_name -> value.
            thresholds: List of threshold configurations.
            absolute_thresholds: Optional absolute limits.
                Can be a single value (max) or tuple (min, max).
        """
        self._baseline = dict(baseline)
        self._thresholds: dict[str, RegressionThreshold] = {t.metric_name: t for t in thresholds}
        self._absolute_thresholds = absolute_thresholds or {}

    @classmethod
    def from_baseline_path(
        cls,
        baseline_path: str,
        thresholds: list[RegressionThreshold],
    ) -> RegressionGuard:
        """Create guard from a baseline JSON file.

        Args:
            baseline_path: Path to baseline JSON file.
            thresholds: List of threshold configurations.

        Returns:
            RegressionGuard instance with loaded baseline.
        """
        path = Path(baseline_path)
        if not path.exists():
            return cls(baseline={}, thresholds=thresholds)

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        baseline = data.get("metrics", {})
        return cls(baseline=baseline, thresholds=thresholds)

    @property
    def baseline(self) -> dict[str, float]:
        """Return a copy of the baseline metrics."""
        return dict(self._baseline)

    @property
    def thresholds(self) -> dict[str, RegressionThreshold]:
        """Return a copy of the threshold configurations."""
        return dict(self._thresholds)

    def check(self, current: dict[str, float]) -> list[RegressionAlert]:
        """Check current metrics against baseline and thresholds.

        Args:
            current: Current metric values as metric_name -> value.

        Returns:
            List of regression alerts for metrics that exceeded thresholds.
        """
        alerts: list[RegressionAlert] = []

        for metric_name, current_value in current.items():
            alert = self._check_metric(metric_name, current_value)
            if alert:
                alerts.append(alert)

        # Check absolute thresholds
        for metric_name, current_value in current.items():
            absolute_alert = self._check_absolute_threshold(metric_name, current_value)
            if absolute_alert and not any(a.metric_name == metric_name for a in alerts):
                alerts.append(absolute_alert)

        return alerts

    def _check_metric(self, metric_name: str, current_value: float) -> RegressionAlert | None:
        """Check a single metric against baseline and threshold."""
        if metric_name not in self._baseline:
            return None

        if metric_name not in self._thresholds:
            return None

        baseline_value = self._baseline[metric_name]
        threshold = self._thresholds[metric_name]

        # Avoid division by zero
        if baseline_value == 0:
            if current_value != 0:
                change_percent = 100.0 if current_value > 0 else -100.0
            else:
                return None
        else:
            change_percent = ((current_value - baseline_value) / baseline_value) * 100

        # Check for increase (latency regression)
        if change_percent > threshold.increase_threshold_percent:
            severity = threshold.severity_for_above_20
            if abs(change_percent) <= 20:
                severity = AlertSeverity.WARNING

            return RegressionAlert(
                metric_name=metric_name,
                previous_value=baseline_value,
                current_value=current_value,
                change_percent=change_percent,
                severity=severity,
                threshold_percent=threshold.increase_threshold_percent,
                message=f"{metric_name} increased by {change_percent:.1f}% (threshold: {threshold.increase_threshold_percent:.1f}%)",
            )

        # Check for decrease (accuracy/quality regression)
        if change_percent < -threshold.decrease_threshold_percent:
            severity = AlertSeverity.WARNING
            if abs(change_percent) >= 20:
                severity = AlertSeverity.CRITICAL

            return RegressionAlert(
                metric_name=metric_name,
                previous_value=baseline_value,
                current_value=current_value,
                change_percent=change_percent,
                severity=severity,
                threshold_percent=threshold.decrease_threshold_percent,
                message=f"{metric_name} decreased by {abs(change_percent):.1f}% (threshold: {threshold.decrease_threshold_percent:.1f}%)",
            )

        return None

    def _check_absolute_threshold(
        self,
        metric_name: str,
        current_value: float,
    ) -> RegressionAlert | None:
        """Check absolute threshold limits."""
        if metric_name not in self._absolute_thresholds:
            return None

        threshold_spec = self._absolute_thresholds[metric_name]

        # Single value means maximum
        if isinstance(threshold_spec, (int, float)):
            if current_value > threshold_spec:
                return RegressionAlert(
                    metric_name=metric_name,
                    previous_value=0.0,
                    current_value=current_value,
                    change_percent=0.0,
                    severity=AlertSeverity.CRITICAL,
                    threshold_percent=0.0,
                    message=f"{metric_name} ({current_value}) exceeds absolute limit ({threshold_spec})",
                )
        # Tuple means (min, max)
        elif isinstance(threshold_spec, tuple) and len(threshold_spec) == 2:
            min_val, max_val = threshold_spec
            if current_value < min_val or current_value > max_val:
                return RegressionAlert(
                    metric_name=metric_name,
                    previous_value=0.0,
                    current_value=current_value,
                    change_percent=0.0,
                    severity=AlertSeverity.CRITICAL,
                    threshold_percent=0.0,
                    message=f"{metric_name} ({current_value}) outside absolute range [{min_val}, {max_val}]",
                )

        return None

    def should_fail_pipeline(self, alerts: list[RegressionAlert]) -> bool:
        """Determine if the pipeline should be failed.

        The pipeline should fail if any regression has CRITICAL severity.

        Args:
            alerts: List of regression alerts.

        Returns:
            True if pipeline should fail, False otherwise.
        """
        return any(alert.severity == AlertSeverity.CRITICAL for alert in alerts)

    def get_alert_summary(self, alerts: list[RegressionAlert]) -> dict[str, Any]:
        """Generate a summary of regression alerts.

        Args:
            alerts: List of regression alerts.

        Returns:
            Dictionary with alert summary statistics.
        """
        return {
            "total_alerts": len(alerts),
            "critical": sum(1 for a in alerts if a.severity == AlertSeverity.CRITICAL),
            "warning": sum(1 for a in alerts if a.severity == AlertSeverity.WARNING),
            "should_fail": self.should_fail_pipeline(alerts),
            "metrics": {a.metric_name: a.change_percent for a in alerts},
        }


# ------------------------------------------------------------------
# Default Thresholds
# ------------------------------------------------------------------


def get_default_thresholds() -> list[RegressionThreshold]:
    """Get the default regression thresholds.

    These thresholds are based on typical CI/CD performance budgets.
    """
    return [
        # Latency metrics (increases are bad)
        RegressionThreshold(
            metric_name="latency_p50",
            increase_threshold_percent=10.0,
            decrease_threshold_percent=0.0,
        ),
        RegressionThreshold(
            metric_name="latency_p90",
            increase_threshold_percent=15.0,
            decrease_threshold_percent=0.0,
        ),
        RegressionThreshold(
            metric_name="latency_p99",
            increase_threshold_percent=20.0,
            decrease_threshold_percent=0.0,
        ),
        # Score metrics (decreases are bad)
        RegressionThreshold(
            metric_name="score",
            increase_threshold_percent=0.0,
            decrease_threshold_percent=5.0,
        ),
        RegressionThreshold(
            metric_name="pass_rate",
            increase_threshold_percent=0.0,
            decrease_threshold_percent=2.0,
        ),
        # Duration (increases are bad)
        RegressionThreshold(
            metric_name="duration_ms",
            increase_threshold_percent=20.0,
            decrease_threshold_percent=0.0,
        ),
    ]
