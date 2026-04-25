"""Public metrics boundary for ``runtime.task_market`` cell.

External callers (especially ``delivery/``) MUST import from this module
instead of ``polaris.cells.runtime.task_market.internal.metrics``.

Exports
-------
TaskMarketMetrics
    Thread-safe business metrics class exposing Prometheus-format counters,
    latency histograms, and queue depth gauges.
get_task_market_metrics
    Returns the global ``TaskMarketMetrics`` singleton.
"""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.metrics import (
    TaskMarketMetrics,
    get_task_market_metrics,
)

__all__ = [
    "TaskMarketMetrics",
    "get_task_market_metrics",
]
