"""Metrics: Prometheus metrics for ContextOS 3.0 observability.

This module provides metrics collection and export for ContextOS 3.0.
Metrics follow the naming convention: `contextos_<category>_<metric_name>`

Categories:
    - content_store: Content store entries, bytes, hit rate
    - multi_resolution: Multi-resolution store counts and evictions
    - phase_detection: Phase transitions and durations
    - attention_scoring: Attention score distributions
    - decision_log: Decision log entries and types
    - budget: Budget utilization and overruns

Usage:
    from polaris.kernelone.context.context_os.metrics import MetricsCollector

    collector = MetricsCollector()
    collector.record_phase_transition("intake", "planning")
    collector.record_attention_score(0.75)
"""

from .collectors import MetricsCollector
from .exporters import MetricsExporter

__all__ = [
    "MetricsCollector",
    "MetricsExporter",
]
