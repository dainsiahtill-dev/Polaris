"""Statistical utilities for holographic benchmark cases."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class SampleStats:
    """Statistical summary after warmup removal and outlier filtering."""

    count_raw: int
    count_filtered: int
    mean: float
    std_dev: float
    p50: float
    p90: float
    p99: float
    min_value: float
    max_value: float
    coefficient_of_variation: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "count_raw": self.count_raw,
            "count_filtered": self.count_filtered,
            "mean": self.mean,
            "std_dev": self.std_dev,
            "p50": self.p50,
            "p90": self.p90,
            "p99": self.p99,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "cv": self.coefficient_of_variation,
        }


def strip_warmup(samples: list[float], warmup_rounds: int) -> list[float]:
    """Drop warmup samples from the front of a sequence."""
    if warmup_rounds <= 0:
        return list(samples)
    if len(samples) <= warmup_rounds:
        return []
    return list(samples[warmup_rounds:])


def percentile(samples: list[float], ratio: float) -> float:
    """Compute percentile using nearest-rank behavior."""
    if not samples:
        return 0.0
    ratio = min(max(ratio, 0.0), 1.0)
    sorted_values = sorted(samples)
    index = math.ceil(ratio * len(sorted_values)) - 1
    index = min(max(index, 0), len(sorted_values) - 1)
    return float(sorted_values[index])


def iqr_filter(samples: list[float], multiplier: float = 1.5) -> list[float]:
    """Apply IQR outlier filter and return inlier values only."""
    if len(samples) < 4:
        return list(samples)
    sorted_values = sorted(samples)
    q1 = percentile(sorted_values, 0.25)
    q3 = percentile(sorted_values, 0.75)
    iqr = q3 - q1
    if iqr == 0:
        return list(sorted_values)
    lower = q1 - multiplier * iqr
    upper = q3 + multiplier * iqr
    return [value for value in sorted_values if lower <= value <= upper]


def summarize_samples(
    samples: list[float],
    *,
    warmup_rounds: int = 0,
    iqr_multiplier: float = 1.5,
) -> SampleStats:
    """Compute robust stats with warmup stripping and IQR filtering."""
    without_warmup = strip_warmup(samples, warmup_rounds)
    filtered = iqr_filter(without_warmup, multiplier=iqr_multiplier)
    values = filtered if filtered else without_warmup
    if not values:
        return SampleStats(
            count_raw=len(samples),
            count_filtered=0,
            mean=0.0,
            std_dev=0.0,
            p50=0.0,
            p90=0.0,
            p99=0.0,
            min_value=0.0,
            max_value=0.0,
            coefficient_of_variation=0.0,
        )
    mean = float(statistics.mean(values))
    std_dev = float(statistics.stdev(values)) if len(values) > 1 else 0.0
    cv = std_dev / mean if mean > 0 else 0.0
    return SampleStats(
        count_raw=len(samples),
        count_filtered=len(values),
        mean=mean,
        std_dev=std_dev,
        p50=percentile(values, 0.50),
        p90=percentile(values, 0.90),
        p99=percentile(values, 0.99),
        min_value=min(values),
        max_value=max(values),
        coefficient_of_variation=cv,
    )


def ks_uniform_statistic(samples: list[float]) -> float:
    """Kolmogorov-Smirnov statistic against U(0,1) after min-max scaling."""
    if len(samples) < 2:
        return 0.0
    minimum = min(samples)
    maximum = max(samples)
    if math.isclose(minimum, maximum):
        return 1.0
    normalized = sorted((value - minimum) / (maximum - minimum) for value in samples)
    count = len(normalized)
    max_gap = 0.0
    for index, value in enumerate(normalized, start=1):
        cdf_upper = index / count
        cdf_lower = (index - 1) / count
        max_gap = max(max_gap, abs(cdf_upper - value), abs(value - cdf_lower))
    return max_gap
