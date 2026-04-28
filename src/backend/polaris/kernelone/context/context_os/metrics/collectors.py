"""Metrics Collectors: collect Prometheus metrics for ContextOS 3.0.

This module provides metric collectors for various ContextOS components.
Metrics follow the naming convention: `contextos_<category>_<metric_name>`

Key Design Principle:
    "Metrics should be lightweight and non-blocking."
    Metric collection must not impact pipeline performance.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricValue:
    """A single metric value with labels."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "labels": self.labels,
            "timestamp": self.timestamp,
        }


class MetricsCollector:
    """Lightweight metrics collector for ContextOS 3.0.

    Collects metrics for:
    - Content store operations
    - Multi-resolution store
    - Phase detection
    - Attention scoring
    - Decision log
    - Budget utilization

    Usage:
        collector = MetricsCollector()
        collector.record_phase_transition("intake", "planning")
        collector.record_attention_score(0.75)
        metrics = collector.collect()
    """

    def __init__(self) -> None:
        # Gauges (current values)
        self._gauges: dict[str, float] = defaultdict(float)
        # Counters (monotonically increasing)
        self._counters: dict[str, int] = defaultdict(int)
        # Histograms (distributions)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        # Labels
        self._labels: dict[str, dict[str, str]] = defaultdict(dict)

    # ------------------------------------------------------------------
    # Content Store Metrics
    # ------------------------------------------------------------------

    def record_content_store_entries(self, count: int) -> None:
        """Record content store entry count."""
        self._gauges["contextos_content_store_entries"] = float(count)

    def record_content_store_bytes(self, bytes_count: int) -> None:
        """Record content store byte count."""
        self._gauges["contextos_content_store_bytes"] = float(bytes_count)

    def record_content_store_hit(self) -> None:
        """Record content store cache hit."""
        self._counters["contextos_content_store_hits"] += 1

    def record_content_store_miss(self) -> None:
        """Record content store cache miss."""
        self._counters["contextos_content_store_misses"] += 1

    # ------------------------------------------------------------------
    # Multi-Resolution Store Metrics
    # ------------------------------------------------------------------

    def record_multi_resolution_count(self, level: str, count: int) -> None:
        """Record multi-resolution store count by level."""
        self._gauges[f"contextos_multi_resolution_count_{level}"] = float(count)

    def record_multi_resolution_eviction(self) -> None:
        """Record multi-resolution store eviction."""
        self._counters["contextos_multi_resolution_evictions"] += 1

    # ------------------------------------------------------------------
    # Phase Detection Metrics
    # ------------------------------------------------------------------

    def record_phase_transition(self, from_phase: str, to_phase: str) -> None:
        """Record phase transition."""
        self._counters["contextos_phase_transitions_total"] += 1
        # Record by transition type
        key = f"contextos_phase_transition_{from_phase}_to_{to_phase}"
        self._counters[key] += 1

    def record_phase_duration(self, phase: str, duration_seconds: float) -> None:
        """Record phase duration."""
        self._histograms[f"contextos_phase_duration_{phase}"].append(duration_seconds)
        self._histograms["contextos_phase_duration_all"].append(duration_seconds)

    # ------------------------------------------------------------------
    # Attention Scoring Metrics
    # ------------------------------------------------------------------

    def record_attention_score(self, score: float) -> None:
        """Record attention score."""
        self._histograms["contextos_attention_score_distribution"].append(score)

    def record_attention_candidates_ranked(self, count: int) -> None:
        """Record number of candidates ranked."""
        self._counters["contextos_attention_candidates_ranked"] += count

    def record_attention_candidates_selected(self, count: int) -> None:
        """Record number of candidates selected."""
        self._counters["contextos_attention_candidates_selected"] += count

    # ------------------------------------------------------------------
    # Decision Log Metrics
    # ------------------------------------------------------------------

    def record_decision_log_entry(self, decision_type: str) -> None:
        """Record decision log entry."""
        self._counters["contextos_decision_log_entries_total"] += 1
        self._counters[f"contextos_decision_log_{decision_type}"] += 1

    # ------------------------------------------------------------------
    # Budget Metrics
    # ------------------------------------------------------------------

    def record_budget_utilization(self, ratio: float) -> None:
        """Record budget utilization ratio."""
        self._histograms["contextos_budget_utilization_ratio"].append(ratio)

    def record_budget_overrun(self) -> None:
        """Record budget overrun."""
        self._counters["contextos_budget_overruns"] += 1

    # ------------------------------------------------------------------
    # Pipeline Metrics
    # ------------------------------------------------------------------

    def record_pipeline_stage_duration(self, stage: str, duration_ms: float) -> None:
        """Record pipeline stage duration."""
        self._histograms[f"contextos_pipeline_stage_duration_{stage}"].append(duration_ms)

    def record_pipeline_projection_duration(self, duration_ms: float) -> None:
        """Record total projection duration."""
        self._histograms["contextos_pipeline_projection_duration"].append(duration_ms)

    # ------------------------------------------------------------------
    # Graph Propagation Metrics
    # ------------------------------------------------------------------

    def record_graph_propagation_events(self, count: int) -> None:
        """Record number of events in graph propagation."""
        self._gauges["contextos_graph_propagation_events"] = float(count)

    def record_graph_propagation_edges(self, count: int) -> None:
        """Record number of edges in graph."""
        self._gauges["contextos_graph_propagation_edges"] = float(count)

    def record_graph_propagation_iterations(self, count: int) -> None:
        """Record number of propagation iterations."""
        self._histograms["contextos_graph_propagation_iterations"].append(count)

    def record_graph_propagation_duration(self, duration_ms: float) -> None:
        """Record graph propagation duration."""
        self._histograms["contextos_graph_propagation_duration"].append(duration_ms)

    # ------------------------------------------------------------------
    # Memory Metrics
    # ------------------------------------------------------------------

    def record_memory_recall_count(self, count: int) -> None:
        """Record number of memories recalled."""
        self._counters["contextos_memory_recall_total"] += count

    def record_memory_injection_allowed(self) -> None:
        """Record memory injection allowed."""
        self._counters["contextos_memory_injection_allowed"] += 1

    def record_memory_injection_rejected(self) -> None:
        """Record memory injection rejected."""
        self._counters["contextos_memory_injection_rejected"] += 1

    def record_memory_conflict_detected(self) -> None:
        """Record memory conflict detected."""
        self._counters["contextos_memory_conflict_detected"] += 1

    # ------------------------------------------------------------------
    # Predictive Compression Metrics
    # ------------------------------------------------------------------

    def record_prediction_strategy(self, strategy: str) -> None:
        """Record prediction strategy used."""
        self._counters[f"contextos_prediction_strategy_{strategy}"] += 1

    def record_prediction_confidence(self, confidence: float) -> None:
        """Record prediction confidence."""
        self._histograms["contextos_prediction_confidence"].append(confidence)

    # ------------------------------------------------------------------
    # Embedding Metrics
    # ------------------------------------------------------------------

    def record_embedding_cache_hit(self) -> None:
        """Record embedding cache hit."""
        self._counters["contextos_embedding_cache_hits"] += 1

    def record_embedding_cache_miss(self) -> None:
        """Record embedding cache miss."""
        self._counters["contextos_embedding_cache_misses"] += 1

    def record_embedding_computation_duration(self, duration_ms: float) -> None:
        """Record embedding computation duration."""
        self._histograms["contextos_embedding_computation_duration"].append(duration_ms)

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def collect(self) -> dict[str, Any]:
        """Collect all metrics.

        Returns:
            Dictionary with gauges, counters, and histograms
        """
        # Calculate histogram statistics
        histogram_stats: dict[str, dict[str, float]] = {}
        for name, values in self._histograms.items():
            if not values:
                continue
            sorted_values = sorted(values)
            histogram_stats[name] = {
                "count": len(values),
                "sum": sum(values),
                "min": min(values),
                "max": max(values),
                "mean": sum(values) / len(values),
                "p50": sorted_values[len(sorted_values) // 2],
                "p90": sorted_values[int(len(sorted_values) * 0.9)],
                "p99": sorted_values[int(len(sorted_values) * 0.99)],
            }

        return {
            "gauges": dict(self._gauges),
            "counters": dict(self._counters),
            "histograms": histogram_stats,
            "timestamp": time.time(),
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._gauges.clear()
        self._counters.clear()
        self._histograms.clear()
