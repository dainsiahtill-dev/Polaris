"""Median helper with a bug for the benchmark fixture."""

from __future__ import annotations


def median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle]
