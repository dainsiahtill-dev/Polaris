"""CI/CD Integration for Benchmark Reporting.

This module provides structured reporting, regression detection,
and alerting capabilities for benchmark pipelines.

Modules
-------
structs : Core data structures for benchmark reports.
guard : Regression detection and pipeline fail-fast guard.
storage : Baseline database for storing and loading metrics.
formatters : Output formatters (JSON, Prometheus, JUnit XML).
alerts : Alert dispatch system for CI/CD pipelines.

Example
-------
    from polaris.kernelone.benchmark.reporting import (
        BenchmarkReporter,
        RegressionGuard,
        PrometheusFormatter,
    )

    # Generate structured report
    reporter = BenchmarkReporter(output_dir="reports")
    report = reporter.generate_report(suite_result)

    # Check for regressions
    guard = RegressionGuard(
        baseline_path="reports/baselines",
        thresholds=[
            RegressionThreshold(
                metric_name="latency_p50",
                increase_threshold_percent=10.0,
                decrease_threshold_percent=5.0,
            ),
        ],
    )
    alerts = guard.check(current_metrics)

    # Format for Prometheus
    formatter = PrometheusFormatter()
    prom_output = formatter.format(report)
"""

from __future__ import annotations

from polaris.kernelone.benchmark.reporting.alerts import (
    AlertChannel,
    AlertDispatcher,
    BenchmarkAlert,
    SlackFormatter,
    TeamsFormatter,
)
from polaris.kernelone.benchmark.reporting.guard import (
    RegressionGuard,
    RegressionThreshold,
)
from polaris.kernelone.benchmark.reporting.storage import (
    BenchmarkDB,
)
from polaris.kernelone.benchmark.reporting.structs import (
    AlertSeverity,
    AlertStatus,
    BenchmarkReport,
    JUnitCase,
    JUnitReport,
    PrometheusFormatter,
    RegressionAlert,
    ReportFormat,
    ReportSummary,
)

__all__ = [
    # Alerts
    "AlertChannel",
    "AlertDispatcher",
    "AlertSeverity",
    "AlertStatus",
    "BenchmarkAlert",
    # Storage
    "BenchmarkDB",
    "BenchmarkReport",
    "JUnitCase",
    "JUnitReport",
    "PrometheusFormatter",
    "RegressionAlert",
    "RegressionGuard",
    # Guard
    "RegressionThreshold",
    # Structs
    "ReportFormat",
    "ReportSummary",
    "SlackFormatter",
    "TeamsFormatter",
]
