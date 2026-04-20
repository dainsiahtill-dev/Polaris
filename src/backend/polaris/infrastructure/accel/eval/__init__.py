"""infrastructure.accel.eval - Context evaluation metrics.

Public API:
    recall_at_k(expected, predicted, k)
    reciprocal_rank(expected, predicted)
    symbol_hit_rate(expected, observed)
    aggregate_case_metrics(case_metrics)

Note:
    This module is now integrated into the unified benchmark framework via:
    polaris.kernelone.benchmark.adapters.context_adapter
"""

from __future__ import annotations

from .metrics import aggregate_case_metrics, recall_at_k, reciprocal_rank, symbol_hit_rate

__all__ = [
    "aggregate_case_metrics",
    "recall_at_k",
    "reciprocal_rank",
    "symbol_hit_rate",
]
