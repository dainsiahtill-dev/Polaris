"""Tests for holographic regression helpers."""

from __future__ import annotations

from polaris.kernelone.benchmark.holographic_regression import (
    evaluate_delta,
    guard_regressions,
)


def test_evaluate_delta_warning_and_failure_flags() -> None:
    judgement = evaluate_delta(
        metric_name="latency_p99",
        baseline=100.0,
        current=112.0,
        warning_threshold_percent=5.0,
        fail_threshold_percent=10.0,
    )
    assert judgement.warning is True
    assert judgement.failed is True
    assert judgement.delta_percent > 10.0


def test_guard_regressions_detects_breach() -> None:
    alerts = guard_regressions(
        baseline={"latency_p99": 100.0},
        current={"latency_p99": 120.0},
        warning_threshold_percent=5.0,
        fail_threshold_percent=10.0,
    )
    assert alerts
    assert alerts[0].metric_name == "latency_p99"
