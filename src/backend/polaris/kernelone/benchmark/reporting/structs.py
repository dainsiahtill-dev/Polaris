"""Structured Data Models for Benchmark Reporting.

This module provides immutable dataclasses for CI/CD-integrated
benchmark reports, regression alerts, and Prometheus metrics.

Design Principles
----------------
- frozen=True: Immutable data carriers for thread safety
- kw_only=True: Explicit keyword-only arguments
- Complete type hints: All fields annotated

Example
-------
    report = BenchmarkReport(
        report_version="1.0",
        environment={"branch": "main", "commit": "abc123"},
        benchmarks=[
            BenchmarkResult(
                case_id="test_case",
                passed=True,
                score=0.95,
                duration_ms=1500,
                p50_ms=120.0,
                p90_ms=200.0,
                p99_ms=350.0,
            ),
        ],
        regressions=[],
        summary=ReportSummary(
            total_benchmarks=1,
            passed=1,
            failed=0,
            regressions_detected=0,
            overall_score=95.0,
        ),
    )
    print(report.to_json())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeAlias

# ------------------------------------------------------------------
# Enums
# ------------------------------------------------------------------


class ReportFormat(Enum):
    """Supported output formats for benchmark reports."""

    JSON_LINES = "jsonl"
    JSON = "json"
    JUNIT_XML = "junit_xml"
    PROMETHEUS = "prometheus"


class AlertSeverity(Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(Enum):
    """Alert lifecycle status."""

    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


# ------------------------------------------------------------------
# Type Aliases
# ------------------------------------------------------------------

MetricName: TypeAlias = str
MetricValue: TypeAlias = float


# ------------------------------------------------------------------
# Core Report Structures
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BenchmarkResult:
    """Result of a single benchmark case.

    Attributes:
        case_id: Unique identifier for this benchmark case.
        passed: Whether the benchmark passed.
        score: Overall score (0.0-1.0).
        duration_ms: Execution time in milliseconds.
        p50_ms: 50th percentile latency in milliseconds.
        p90_ms: 90th percentile latency in milliseconds.
        p99_ms: 99th percentile latency in milliseconds.
        timestamp: ISO timestamp of the execution.
        mode: Benchmark mode (agentic/strategy/context).
        role: The role that was benchmarked.
        metadata: Additional case metadata.
    """

    case_id: str
    passed: bool
    score: float
    duration_ms: int
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p99_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mode: str = "agentic"
    role: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "score", max(0.0, min(1.0, float(self.score))))
        object.__setattr__(self, "duration_ms", max(0, int(self.duration_ms)))
        object.__setattr__(self, "p50_ms", max(0.0, float(self.p50_ms)))
        object.__setattr__(self, "p90_ms", max(0.0, float(self.p90_ms)))
        object.__setattr__(self, "p99_ms", max(0.0, float(self.p99_ms)))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": round(self.score, 4),
            "duration_ms": self.duration_ms,
            "p50_ms": round(self.p50_ms, 2),
            "p90_ms": round(self.p90_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "timestamp": self.timestamp,
            "mode": self.mode,
            "role": self.role,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkResult:
        return cls(
            case_id=data.get("case_id", ""),
            passed=bool(data.get("passed", False)),
            score=data.get("score", 0.0),
            duration_ms=data.get("duration_ms", 0),
            p50_ms=data.get("p50_ms", 0.0),
            p90_ms=data.get("p90_ms", 0.0),
            p99_ms=data.get("p99_ms", 0.0),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            mode=data.get("mode", "agentic"),
            role=data.get("role", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True, kw_only=True)
class RegressionAlert:
    """Regression alert for detected performance degradation.

    Attributes:
        metric_name: Name of the metric that regressed.
        previous_value: Baseline value.
        current_value: Current measured value.
        change_percent: Percentage change from baseline.
        severity: Alert severity level.
        threshold_percent: The threshold that was exceeded.
        case_id: Optional associated benchmark case.
        message: Human-readable alert message.
    """

    metric_name: str
    previous_value: float
    current_value: float
    change_percent: float
    severity: AlertSeverity
    threshold_percent: float
    case_id: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            object.__setattr__(self, "severity", AlertSeverity(self.severity))
        object.__setattr__(self, "change_percent", round(float(self.change_percent), 2))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "previous_value": round(self.previous_value, 4),
            "current_value": round(self.current_value, 4),
            "change_percent": self.change_percent,
            "severity": self.severity.value,
            "threshold_percent": round(self.threshold_percent, 2),
            "case_id": self.case_id,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegressionAlert:
        severity = data.get("severity", "warning")
        if isinstance(severity, str):
            severity = AlertSeverity(severity)
        return cls(
            metric_name=data.get("metric_name", ""),
            previous_value=data.get("previous_value", 0.0),
            current_value=data.get("current_value", 0.0),
            change_percent=data.get("change_percent", 0.0),
            severity=severity,
            threshold_percent=data.get("threshold_percent", 10.0),
            case_id=data.get("case_id", ""),
            message=data.get("message", ""),
        )


@dataclass(frozen=True, kw_only=True)
class ReportSummary:
    """Summary statistics for a benchmark report.

    Attributes:
        total_benchmarks: Total number of benchmark cases.
        passed: Number of cases that passed.
        failed: Number of cases that failed.
        regressions_detected: Number of regressions detected.
        overall_score: Overall score (0-100).
        pass_rate: Pass rate as a fraction (0.0-1.0).
        wall_time_ms: Total wall-clock time in milliseconds.
    """

    total_benchmarks: int
    passed: int
    failed: int
    regressions_detected: int
    overall_score: float
    pass_rate: float = 0.0
    wall_time_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "total_benchmarks", max(0, int(self.total_benchmarks)))
        object.__setattr__(self, "passed", max(0, int(self.passed)))
        object.__setattr__(self, "failed", max(0, int(self.failed)))
        object.__setattr__(self, "regressions_detected", max(0, int(self.regressions_detected)))
        object.__setattr__(self, "overall_score", max(0.0, min(100.0, float(self.overall_score))))
        object.__setattr__(self, "pass_rate", max(0.0, min(1.0, float(self.pass_rate))))
        object.__setattr__(self, "wall_time_ms", max(0, int(self.wall_time_ms)))

    @property
    def is_healthy(self) -> bool:
        """Check if the benchmark suite is healthy (no failures or regressions)."""
        return self.failed == 0 and self.regressions_detected == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_benchmarks": self.total_benchmarks,
            "passed": self.passed,
            "failed": self.failed,
            "regressions_detected": self.regressions_detected,
            "overall_score": round(self.overall_score, 2),
            "pass_rate": round(self.pass_rate, 4),
            "wall_time_ms": self.wall_time_ms,
            "is_healthy": self.is_healthy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportSummary:
        return cls(
            total_benchmarks=data.get("total_benchmarks", 0),
            passed=data.get("passed", 0),
            failed=data.get("failed", 0),
            regressions_detected=data.get("regressions_detected", 0),
            overall_score=data.get("overall_score", 0.0),
            pass_rate=data.get("pass_rate", 0.0),
            wall_time_ms=data.get("wall_time_ms", 0),
        )


@dataclass(frozen=True, kw_only=True)
class BenchmarkReport:
    """Complete benchmark report for CI/CD integration.

    This is the root container for all benchmark results and
    regression alerts in a single report.

    Attributes:
        report_version: Report schema version.
        generated_at: ISO timestamp when report was generated.
        environment: Environment variables and metadata.
        benchmarks: List of individual benchmark results.
        regressions: List of detected regression alerts.
        summary: Summary statistics.
        run_id: Unique identifier for this run.
        suite_name: Name of the benchmark suite.
    """

    report_version: str = "1.0"
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    environment: dict[str, str] = field(default_factory=dict)
    benchmarks: tuple[BenchmarkResult, ...] = field(default_factory=tuple)
    regressions: tuple[RegressionAlert, ...] = field(default_factory=tuple)
    summary: ReportSummary = field(
        default_factory=lambda: ReportSummary(
            total_benchmarks=0,
            passed=0,
            failed=0,
            regressions_detected=0,
            overall_score=0.0,
        )
    )
    run_id: str = ""
    suite_name: str = "benchmark"

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", dict(self.environment or {}))
        object.__setattr__(
            self,
            "benchmarks",
            tuple(
                b if isinstance(b, BenchmarkResult) else BenchmarkResult.from_dict(b) for b in (self.benchmarks or ())
            ),
        )
        object.__setattr__(
            self,
            "regressions",
            tuple(
                r if isinstance(r, RegressionAlert) else RegressionAlert.from_dict(r) for r in (self.regressions or ())
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "generated_at": self.generated_at,
            "environment": self.environment,
            "benchmarks": [b.to_dict() for b in self.benchmarks],
            "regressions": [r.to_dict() for r in self.regressions],
            "summary": self.summary.to_dict(),
            "run_id": self.run_id,
            "suite_name": self.suite_name,
        }

    def to_json(self) -> str:
        """Serialize report as formatted JSON string."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def to_jsonl(self) -> str:
        """Serialize as JSON Lines format (one event per line).

        This format is suitable for log aggregation systems like
        ELK Stack or Google Cloud Logging.
        """
        lines: list[str] = []
        for bench in self.benchmarks:
            lines.append(json.dumps(bench.to_dict(), ensure_ascii=False))
        for regression in self.regressions:
            lines.append(json.dumps({"type": "regression", **regression.to_dict()}, ensure_ascii=False))
        lines.append(json.dumps({"type": "summary", **self.summary.to_dict()}, ensure_ascii=False))
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkReport:
        summary = data.get("summary", {})
        if isinstance(summary, dict):
            summary = ReportSummary.from_dict(summary)
        return cls(
            report_version=data.get("report_version", "1.0"),
            generated_at=data.get("generated_at", datetime.now(timezone.utc).isoformat()),
            environment=data.get("environment", {}),
            benchmarks=tuple(data.get("benchmarks", ())),
            regressions=tuple(data.get("regressions", ())),
            summary=summary,
            run_id=data.get("run_id", ""),
            suite_name=data.get("suite_name", "benchmark"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> BenchmarkReport:
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)


# ------------------------------------------------------------------
# Prometheus Formatter
# ------------------------------------------------------------------


class PrometheusFormatter:
    """Prometheus metrics formatter for benchmark reports.

    Formats benchmark results as Prometheus exposition format
    for scraping by Prometheus servers.

    Example
    -------
        formatter = PrometheusFormatter()
        output = formatter.format(report)
        # Output:
        # benchmark_passed{name="test_case"} 1
        # benchmark_score{name="test_case"} 0.95
        # benchmark_latency_p50_ms{name="test_case"} 120.0
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format benchmark report as Prometheus metrics.

        Args:
            report: The benchmark report to format.

        Returns:
            Prometheus exposition format string.
        """
        lines: list[str] = [
            "# HELP benchmark_report_generated_at Timestamp of report generation",
            "# TYPE benchmark_report_generated_at gauge",
            f'benchmark_report_generated_at{{run_id="{self._escape_label(report.run_id)}"}} 1',
            "",
        ]

        # Summary metrics
        lines.extend(
            [
                "# HELP benchmark_total Total number of benchmark cases",
                "# TYPE benchmark_total gauge",
                f'benchmark_total{{suite="{self._escape_label(report.suite_name)}"}} {report.summary.total_benchmarks}',
                "",
                "# HELP benchmark_passed_total Total number of passed benchmarks",
                "# TYPE benchmark_passed_total gauge",
                f'benchmark_passed_total{{suite="{self._escape_label(report.suite_name)}"}} {report.summary.passed}',
                "",
                "# HELP benchmark_failed_total Total number of failed benchmarks",
                "# TYPE benchmark_failed_total gauge",
                f'benchmark_failed_total{{suite="{self._escape_label(report.suite_name)}"}} {report.summary.failed}',
                "",
                "# HELP benchmark_pass_rate Pass rate as a fraction",
                "# TYPE benchmark_pass_rate gauge",
                f'benchmark_pass_rate{{suite="{self._escape_label(report.suite_name)}"}} {report.summary.pass_rate}',
                "",
                "# HELP benchmark_overall_score Overall score (0-100)",
                "# TYPE benchmark_overall_score gauge",
                f'benchmark_overall_score{{suite="{self._escape_label(report.suite_name)}"}} {report.summary.overall_score}',
                "",
                "# HELP benchmark_regressions_total Total number of regressions detected",
                "# TYPE benchmark_regressions_total gauge",
                f'benchmark_regressions_total{{suite="{self._escape_label(report.suite_name)}"}} {report.summary.regressions_detected}',
            ]
        )

        # Individual benchmark metrics
        for bench in report.benchmarks:
            labels = self._build_labels(bench)

            lines.extend(
                [
                    "",
                    "# HELP benchmark_passed Whether benchmark passed (1=yes, 0=no)",
                    "# TYPE benchmark_passed gauge",
                    f"benchmark_passed{labels} {1 if bench.passed else 0}",
                    "",
                    "# HELP benchmark_score Benchmark score (0.0-1.0)",
                    "# TYPE benchmark_score gauge",
                    f"benchmark_score{labels} {bench.score}",
                    "",
                    "# HELP benchmark_duration_ms Benchmark duration in milliseconds",
                    "# TYPE benchmark_duration_ms gauge",
                    f"benchmark_duration_ms{labels} {bench.duration_ms}",
                ]
            )

            # Latency metrics
            if bench.p50_ms > 0:
                lines.extend(
                    [
                        "",
                        "# HELP benchmark_latency_p50_ms 50th percentile latency",
                        "# TYPE benchmark_latency_p50_ms gauge",
                        f"benchmark_latency_p50_ms{labels} {bench.p50_ms}",
                        "",
                        "# HELP benchmark_latency_p90_ms 90th percentile latency",
                        "# TYPE benchmark_latency_p90_ms gauge",
                        f"benchmark_latency_p90_ms{labels} {bench.p90_ms}",
                        "",
                        "# HELP benchmark_latency_p99_ms 99th percentile latency",
                        "# TYPE benchmark_latency_p99_ms gauge",
                        f"benchmark_latency_p99_ms{labels} {bench.p99_ms}",
                    ]
                )

        # Regression metrics
        for reg in report.regressions:
            reg_labels = f'{{metric="{self._escape_label(reg.metric_name)}",severity="{reg.severity.value}"}}'
            lines.extend(
                [
                    "",
                    "# HELP benchmark_regression_change_percent Percentage change from baseline",
                    "# TYPE benchmark_regression_change_percent gauge",
                    f"benchmark_regression_change_percent{reg_labels} {reg.change_percent}",
                ]
            )

        return "\n".join(lines)

    def _escape_label(self, value: str) -> str:
        """Escape special characters in Prometheus label values."""
        return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    def _build_labels(self, bench: BenchmarkResult) -> str:
        """Build Prometheus label string for a benchmark."""
        parts = [
            f'name="{self._escape_label(bench.case_id)}"',
            f'mode="{self._escape_label(bench.mode)}"',
        ]
        if bench.role:
            parts.append(f'role="{self._escape_label(bench.role)}"')
        return "{" + ",".join(parts) + "}"


# ------------------------------------------------------------------
# JUnit XML Formatter
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class JUnitCase:
    """JUnit test case representation."""

    name: str
    classname: str
    time: float
    passed: bool
    failure_message: str = ""
    failure_type: str = ""
    system_out: str = ""


@dataclass(frozen=True, kw_only=True)
class JUnitReport:
    """JUnit XML report structure."""

    name: str
    tests: int
    failures: int
    errors: int
    skipped: int
    time: float
    cases: tuple[JUnitCase, ...] = field(default_factory=tuple)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    hostname: str = "localhost"

    def to_xml(self) -> str:
        """Generate JUnit XML format string."""
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<testsuite name="{self._escape_xml(self.name)}" '
            f'tests="{self.tests}" failures="{self.failures}" '
            f'errors="{self.errors}" skipped="{self.skipped}" '
            f'time="{self.time:.3f}" '
            f'timestamp="{self._escape_xml(self.timestamp)}" '
            f'hostname="{self._escape_xml(self.hostname)}">',
        ]

        for case in self.cases:
            attrs = f'name="{self._escape_xml(case.name)}" classname="{self._escape_xml(case.classname)}" time="{case.time:.3f}"'
            if case.passed:
                lines.append(f"  <testcase {attrs}/>")
            else:
                lines.append(f"  <testcase {attrs}>")
                if case.failure_message:
                    lines.append(
                        f'    <failure type="{self._escape_xml(case.failure_type)}" '
                        f'message="{self._escape_xml(case.failure_message)}"/>'
                    )
                if case.system_out:
                    lines.append(f"    <system-out><![CDATA[{case.system_out}]]></system-out>")
                lines.append("  </testcase>")

        lines.append("</testsuite>")
        return "\n".join(lines)

    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters."""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    @classmethod
    def from_benchmark_report(cls, report: BenchmarkReport) -> JUnitReport:
        """Convert BenchmarkReport to JUnitReport."""
        cases: list[JUnitCase] = []
        total_time = 0.0

        for bench in report.benchmarks:
            time_sec = bench.duration_ms / 1000.0
            total_time += time_sec

            case = JUnitCase(
                name=bench.case_id,
                classname=f"benchmark.{bench.mode}.{bench.role or 'default'}",
                time=time_sec,
                passed=bench.passed,
                failure_message="" if bench.passed else f"Score: {bench.score:.2%} (threshold: 75%)",
                failure_type="AssertionError" if not bench.passed else "",
            )
            cases.append(case)

        # Add regression failures
        for reg in report.regressions:
            total_time += 0.001
            case = JUnitCase(
                name=f"regression:{reg.metric_name}",
                classname="benchmark.regressions",
                time=0.001,
                passed=False,
                failure_message=reg.message or f"{reg.metric_name} regressed by {reg.change_percent:.1f}%",
                failure_type="RegressionError",
            )
            cases.append(case)

        failures = sum(1 for c in cases if not c.passed)
        return cls(
            name=report.suite_name,
            tests=len(cases),
            failures=failures,
            errors=0,
            skipped=0,
            time=total_time,
            cases=tuple(cases),
            timestamp=report.generated_at,
        )


# ------------------------------------------------------------------
# Convenience Factory
# ------------------------------------------------------------------


def create_report(
    benchmarks: list[BenchmarkResult],
    *,
    environment: dict[str, str] | None = None,
    regressions: list[RegressionAlert] | None = None,
    run_id: str = "",
    suite_name: str = "benchmark",
) -> BenchmarkReport:
    """Create a benchmark report from a list of results.

    Args:
        benchmarks: List of benchmark results.
        environment: Optional environment metadata.
        regressions: Optional list of regression alerts.
        run_id: Optional run identifier.
        suite_name: Optional suite name.

    Returns:
        Complete BenchmarkReport instance.
    """
    total = len(benchmarks)
    passed = sum(1 for b in benchmarks if b.passed)
    failed = total - passed
    regressions_detected = len(regressions or [])
    overall_score = (sum(b.score for b in benchmarks) / total * 100) if total > 0 else 0.0
    pass_rate = passed / total if total > 0 else 0.0

    summary = ReportSummary(
        total_benchmarks=total,
        passed=passed,
        failed=failed,
        regressions_detected=regressions_detected,
        overall_score=overall_score,
        pass_rate=pass_rate,
    )

    return BenchmarkReport(
        report_version="1.0",
        environment=environment or {},
        benchmarks=tuple(benchmarks),
        regressions=tuple(regressions or []),
        summary=summary,
        run_id=run_id,
        suite_name=suite_name,
    )
