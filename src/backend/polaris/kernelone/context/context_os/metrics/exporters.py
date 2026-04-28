"""Metrics Exporters: export Prometheus metrics for ContextOS 3.0.

This module provides exporters for Prometheus metrics.
Metrics can be exported in Prometheus text format or JSON.

Key Design Principle:
    "Metrics should be lightweight and non-blocking."
    Export must not impact pipeline performance.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .collectors import MetricsCollector

logger = logging.getLogger(__name__)


class MetricsExporter:
    """Export metrics in Prometheus text format or JSON.

    Usage:
        collector = MetricsCollector()
        exporter = MetricsExporter(collector)

        # Export in Prometheus text format
        prometheus_text = exporter.export_prometheus()

        # Export in JSON format
        json_data = exporter.export_json()
    """

    def __init__(self, collector: MetricsCollector) -> None:
        self._collector = collector

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format.

        Returns:
            Prometheus text format string
        """
        metrics = self._collector.collect()
        lines: list[str] = []

        # Export gauges
        for name, value in metrics["gauges"].items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        # Export counters
        for name, value in metrics["counters"].items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # Export histograms
        for name, stats in metrics["histograms"].items():
            lines.append(f"# TYPE {name} histogram")
            lines.append(f"{name}_count {stats['count']}")
            lines.append(f"{name}_sum {stats['sum']}")
            lines.append(f'{name}{{quantile="0.5"}} {stats["p50"]}')
            lines.append(f'{name}{{quantile="0.9"}} {stats["p90"]}')
            lines.append(f'{name}{{quantile="0.99"}} {stats["p99"]}')

        return "\n".join(lines) + "\n"

    def export_json(self) -> str:
        """Export metrics in JSON format.

        Returns:
            JSON string
        """
        metrics = self._collector.collect()
        return json.dumps(metrics, indent=2, ensure_ascii=False)

    def export_dict(self) -> dict[str, Any]:
        """Export metrics as dictionary.

        Returns:
            Dictionary with metrics
        """
        return self._collector.collect()
