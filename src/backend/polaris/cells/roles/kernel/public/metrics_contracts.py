"""Metrics contracts - public boundary for metrics-related kernel internals.

This module exposes observability metrics utilities from roles.kernel.internal.metrics
for use by other Cells (especially kernelone), following the Public/Internal Fence principle.

Public exports:
- get_dead_loop_metrics: dead loop metrics collector factory
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.metrics import (
    get_dead_loop_metrics,
)

__all__ = [
    "get_dead_loop_metrics",
]
