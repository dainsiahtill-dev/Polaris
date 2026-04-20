"""Tests for regression detection guard.

These tests verify the RegressionGuard functionality for
detecting performance regressions in CI/CD pipelines.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from polaris.kernelone.benchmark.reporting.guard import (
    RegressionGuard,
    RegressionThreshold,
    get_default_thresholds,
)
from polaris.kernelone.benchmark.reporting.structs import AlertSeverity


class TestRegressionThreshold:
    """Tests for RegressionThreshold configuration."""

    def test_create_latency_threshold(self) -> None:
        """Test creating a latency regression threshold."""
        threshold = RegressionThreshold(
            metric_name="latency_p50",
            increase_threshold_percent=10.0,
            decrease_threshold_percent=0.0,
        )

        assert threshold.metric_name == "latency_p50"
        assert threshold.increase_threshold_percent == 10.0
        assert threshold.decrease_threshold_percent == 0.0
        assert threshold.absolute_threshold is None

    def test_create_score_threshold(self) -> None:
        """Test creating a score regression threshold."""
        threshold = RegressionThreshold(
            metric_name="accuracy",
            increase_threshold_percent=0.0,
            decrease_threshold_percent=5.0,
        )

        assert threshold.decrease_threshold_percent == 5.0

    def test_severity_from_string(self) -> None:
        """Test severity parsing from string."""
        threshold = RegressionThreshold(
            metric_name="test",
            increase_threshold_percent=10.0,
            decrease_threshold_percent=5.0,
            severity_for_above_20=AlertSeverity.CRITICAL,
        )

        assert threshold.severity_for_above_20 == AlertSeverity.CRITICAL

    def test_to_dict_roundtrip(self) -> None:
        """Test serialization roundtrip."""
        original = RegressionThreshold(
            metric_name="test_metric",
            increase_threshold_percent=15.0,
            decrease_threshold_percent=7.0,
            absolute_threshold=500.0,
            severity_for_above_20=AlertSeverity.WARNING,
        )

        data = original.to_dict()
        restored = RegressionThreshold.from_dict(data)

        assert restored.metric_name == original.metric_name
        assert restored.increase_threshold_percent == original.increase_threshold_percent
        assert restored.decrease_threshold_percent == original.decrease_threshold_percent
        assert restored.absolute_threshold == original.absolute_threshold


class TestRegressionGuard:
    """Tests for RegressionGuard class."""

    @pytest.fixture
    def baseline(self) -> dict[str, float]:
        """Fixture providing baseline metrics."""
        return {
            "latency_p50": 120.0,
            "latency_p90": 200.0,
            "latency_p99": 350.0,
            "score": 0.95,
            "pass_rate": 0.90,
        }

    @pytest.fixture
    def thresholds(self) -> list[RegressionThreshold]:
        """Fixture providing default thresholds."""
        return [
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
            RegressionThreshold(
                metric_name="score",
                increase_threshold_percent=0.0,
                decrease_threshold_percent=5.0,
            ),
        ]

    def test_no_regression_healthy(self, baseline: dict[str, float], thresholds: list[RegressionThreshold]) -> None:
        """Test that no alerts are raised when metrics are healthy."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 122.0,  # ~1.7% increase
            "latency_p90": 210.0,  # ~5% increase
            "score": 0.94,  # ~1% decrease
        }

        alerts = guard.check(current)
        assert len(alerts) == 0

    def test_latency_regression_detected(
        self, baseline: dict[str, float], thresholds: list[RegressionThreshold]
    ) -> None:
        """Test detection of latency regression."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 140.0,  # 16.7% increase - exceeds 10% threshold
        }

        alerts = guard.check(current)
        assert len(alerts) == 1

        alert = alerts[0]
        assert alert.metric_name == "latency_p50"
        assert alert.severity == AlertSeverity.WARNING
        assert alert.previous_value == 120.0
        assert alert.current_value == 140.0

    def test_critical_regression_above_20_percent(
        self, baseline: dict[str, float], thresholds: list[RegressionThreshold]
    ) -> None:
        """Test that regressions above 20% are marked as critical."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 150.0,  # 25% increase
        }

        alerts = guard.check(current)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_score_regression_detected(self, baseline: dict[str, float], thresholds: list[RegressionThreshold]) -> None:
        """Test detection of score regression."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "score": 0.88,  # ~7.4% decrease - exceeds 5% threshold
        }

        alerts = guard.check(current)
        assert len(alerts) == 1

        alert = alerts[0]
        assert alert.metric_name == "score"
        assert alert.previous_value == 0.95
        assert alert.current_value == 0.88

    def test_multiple_regressions(self, baseline: dict[str, float], thresholds: list[RegressionThreshold]) -> None:
        """Test detection of multiple simultaneous regressions."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 145.0,  # 20.8% increase
            "latency_p90": 250.0,  # 25% increase
            "score": 0.85,  # 10.5% decrease
        }

        alerts = guard.check(current)
        assert len(alerts) == 3

        metric_names = {a.metric_name for a in alerts}
        assert "latency_p50" in metric_names
        assert "latency_p90" in metric_names
        assert "score" in metric_names

    def test_unknown_metric_no_alert(self, baseline: dict[str, float], thresholds: list[RegressionThreshold]) -> None:
        """Test that unknown metrics don't generate alerts."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "unknown_metric": 1000.0,
        }

        alerts = guard.check(current)
        assert len(alerts) == 0

    def test_metric_not_in_baseline_no_alert(
        self, baseline: dict[str, float], thresholds: list[RegressionThreshold]
    ) -> None:
        """Test that metrics not in baseline don't generate alerts."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 140.0,
            "new_metric": 100.0,  # Not in baseline
        }

        alerts = guard.check(current)
        assert len(alerts) == 1  # Only latency_p50
        assert alerts[0].metric_name == "latency_p50"

    def test_should_fail_pipeline_critical(
        self, baseline: dict[str, float], thresholds: list[RegressionThreshold]
    ) -> None:
        """Test pipeline failure when critical alert present."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 150.0,  # 25% increase -> critical
        }

        alerts = guard.check(current)
        assert guard.should_fail_pipeline(alerts) is True

    def test_should_not_fail_pipeline_warning_only(
        self, baseline: dict[str, float], thresholds: list[RegressionThreshold]
    ) -> None:
        """Test pipeline continues with warnings only."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        current = {
            "latency_p50": 135.0,  # 12.5% increase -> warning
        }

        alerts = guard.check(current)
        assert guard.should_fail_pipeline(alerts) is False

    def test_get_alert_summary(self, baseline: dict[str, float], thresholds: list[RegressionThreshold]) -> None:
        """Test alert summary generation."""
        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        # 20.83% > 20% threshold -> critical
        # 28.57% > 20% threshold -> critical
        current = {
            "latency_p50": 145.0,  # 20.83% increase -> critical
            "latency_p99": 450.0,  # 28.57% increase -> critical
        }

        alerts = guard.check(current)
        summary = guard.get_alert_summary(alerts)

        assert summary["total_alerts"] == 2
        assert summary["critical"] == 2
        assert summary["warning"] == 0
        assert summary["should_fail"] is True
        assert "latency_p50" in summary["metrics"]
        assert "latency_p99" in summary["metrics"]

    def test_absolute_threshold_exceeded(self) -> None:
        """Test absolute threshold checking."""
        guard = RegressionGuard(
            baseline={"latency": 100.0},
            thresholds=[],
            absolute_thresholds={"latency": 200.0},
        )

        current = {"latency": 250.0}
        alerts = guard.check(current)

        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.CRITICAL
        assert "exceeds absolute limit" in alerts[0].message

    def test_absolute_threshold_range(self) -> None:
        """Test absolute threshold with min/max range."""
        guard = RegressionGuard(
            baseline={},
            thresholds=[],
            absolute_thresholds={"accuracy": (0.8, 1.0)},
        )

        current = {"accuracy": 0.75}
        alerts = guard.check(current)

        assert len(alerts) == 1
        assert "outside absolute range" in alerts[0].message

    def test_zero_baseline_handling(self) -> None:
        """Test handling of zero baseline values."""
        baseline = {"metric": 0.0}
        thresholds = [
            RegressionThreshold(
                metric_name="metric",
                increase_threshold_percent=10.0,
                decrease_threshold_percent=5.0,
            ),
        ]

        guard = RegressionGuard(baseline=baseline, thresholds=thresholds)

        # Non-zero current with zero baseline
        current = {"metric": 10.0}
        alerts = guard.check(current)

        # Should not crash, may or may not alert depending on implementation
        assert isinstance(alerts, list)


class TestDefaultThresholds:
    """Tests for default threshold configurations."""

    def test_get_default_thresholds(self) -> None:
        """Test getting default thresholds."""
        thresholds = get_default_thresholds()

        assert len(thresholds) > 0

        # Check latency thresholds exist
        latency_metrics = [t for t in thresholds if "latency" in t.metric_name]
        assert len(latency_metrics) >= 3  # p50, p90, p99

        # Check score threshold exists
        score_thresholds = [t for t in thresholds if t.metric_name == "score"]
        assert len(score_thresholds) == 1

    def test_default_threshold_values(self) -> None:
        """Test default threshold values are reasonable."""
        thresholds = get_default_thresholds()
        threshold_dict = {t.metric_name: t for t in thresholds}

        # Latency thresholds should be more lenient than 10%
        assert threshold_dict["latency_p50"].increase_threshold_percent == 10.0
        assert threshold_dict["latency_p90"].increase_threshold_percent == 15.0
        assert threshold_dict["latency_p99"].increase_threshold_percent == 20.0

        # Score thresholds should alert on decrease
        assert threshold_dict["score"].decrease_threshold_percent == 5.0


class TestRegressionGuardFileOperations:
    """Tests for file-based baseline loading."""

    def test_from_baseline_path(self) -> None:
        """Test creating guard from baseline file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            baseline_path = Path(tmpdir) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "branch": "main",
                        "commit": "abc1234",
                        "metrics": {
                            "latency_p50": 120.0,
                            "score": 0.95,
                        },
                    }
                ),
                encoding="utf-8",
            )

            thresholds = [
                RegressionThreshold(
                    metric_name="latency_p50",
                    increase_threshold_percent=10.0,
                    decrease_threshold_percent=0.0,
                ),
            ]

            guard = RegressionGuard.from_baseline_path(
                str(baseline_path),
                thresholds,
            )

            assert guard.baseline["latency_p50"] == 120.0
            assert guard.baseline["score"] == 0.95

    def test_from_baseline_path_nonexistent(self) -> None:
        """Test creating guard with nonexistent baseline file."""
        thresholds = [
            RegressionThreshold(
                metric_name="test",
                increase_threshold_percent=10.0,
                decrease_threshold_percent=5.0,
            ),
        ]

        guard = RegressionGuard.from_baseline_path(
            "/nonexistent/path/baseline.json",
            thresholds,
        )

        assert guard.baseline == {}

    def test_guard_property_returns_copy(self) -> None:
        """Test that baseline property returns a copy."""
        original = {"latency": 100.0}
        thresholds = [
            RegressionThreshold(
                metric_name="latency",
                increase_threshold_percent=10.0,
                decrease_threshold_percent=0.0,
            ),
        ]

        guard = RegressionGuard(baseline=original, thresholds=thresholds)
        retrieved = guard.baseline

        # Modifying retrieved should not affect original
        retrieved["latency"] = 999.0
        assert guard.baseline["latency"] == 100.0
