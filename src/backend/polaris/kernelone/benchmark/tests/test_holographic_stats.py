"""Tests for holographic stats utilities."""

from __future__ import annotations

from polaris.kernelone.benchmark.holographic_stats import (
    iqr_filter,
    ks_uniform_statistic,
    percentile,
    summarize_samples,
)


def test_percentile_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.50) == 3.0
    assert percentile(values, 0.90) == 5.0


def test_iqr_filter_removes_extreme_outlier() -> None:
    values = [1.0, 1.1, 1.2, 1.3, 9.9]
    filtered = iqr_filter(values, multiplier=1.5)
    assert 9.9 not in filtered
    assert len(filtered) == 4


def test_summarize_samples_handles_warmup_and_cv() -> None:
    values = [10.0, 10.0, 1.0, 1.1, 0.9, 1.0, 1.1]
    stats = summarize_samples(values, warmup_rounds=2)
    assert stats.count_raw == 7
    assert stats.count_filtered >= 5
    assert stats.p99 > 0
    assert stats.coefficient_of_variation >= 0


def test_ks_uniform_statistic_range() -> None:
    uniform_like = [index / 100 for index in range(1, 100)]
    ks = ks_uniform_statistic(uniform_like)
    assert 0.0 <= ks <= 1.0
    assert ks < 0.1
