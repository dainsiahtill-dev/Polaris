"""Kernel Internal Components

This module exposes internal kernel components for the roles kernel cell.
"""

from polaris.cells.roles.kernel.internal.metrics import (
    MetricsCollector,
    MetricsSnapshot,
    get_metrics_collector,
    record_cache_stats,
)

__all__ = [
    "MetricsCollector",
    "MetricsSnapshot",
    "get_metrics_collector",
    "record_cache_stats",
]
