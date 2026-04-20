"""Output Formatters for Benchmark Reports.

This module provides various output formatters for benchmark reports,
including JSON, JSON Lines, Prometheus, and JUnit XML formats.

Example
-------
    from polaris.kernelone.benchmark.reporting import (
        BenchmarkReport,
        JSONFormatter,
        PrometheusFormatter,
        JUnitXMLFormatter,
    )

    report = create_report(benchmarks=[...])

    # JSON output
    json_fmt = JSONFormatter()
    print(json_fmt.format(report))

    # Prometheus metrics
    prom_fmt = PrometheusFormatter()
    print(prom_fmt.format(report))

    # JUnit XML for CI
    junit_fmt = JUnitXMLFormatter()
    print(junit_fmt.format(report))

    # Write to file
    formatter = MultiFormatFormatter("reports/output")
    formatter.write(report, format="json")
    formatter.write(report, format="prometheus")
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.reporting.structs import (
    BenchmarkReport,
    JUnitReport,
    PrometheusFormatter,
    ReportFormat,
)

# ------------------------------------------------------------------
# JSON Formatter
# ------------------------------------------------------------------


class JSONFormatter:
    """JSON formatter for benchmark reports.

    Produces pretty-printed JSON output with all report data.
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format report as JSON string.

        Args:
            report: The benchmark report to format.

        Returns:
            JSON string.
        """
        return report.to_json()

    def format_compact(self, report: BenchmarkReport) -> str:
        """Format report as compact JSON (no pretty printing).

        Args:
            report: The benchmark report to format.

        Returns:
            Compact JSON string.
        """
        return json.dumps(report.to_dict(), separators=(",", ":"), ensure_ascii=False)


# ------------------------------------------------------------------
# JSON Lines Formatter
# ------------------------------------------------------------------


class JSONLinesFormatter:
    """JSON Lines formatter for streaming log integration.

    Produces one JSON object per line, suitable for log aggregation
    systems like ELK Stack or Google Cloud Logging.
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format report as JSON Lines.

        Each line is a complete JSON object representing either:
        - A benchmark result
        - A regression alert
        - A summary

        Args:
            report: The benchmark report to format.

        Returns:
            JSON Lines string.
        """
        return report.to_jsonl()

    def format_events(self, report: BenchmarkReport) -> list[dict[str, Any]]:
        """Format report as list of event dictionaries.

        Args:
            report: The benchmark report to format.

        Returns:
            List of event dictionaries.
        """
        events: list[dict[str, Any]] = []

        for bench in report.benchmarks:
            events.append(
                {
                    "type": "benchmark",
                    **bench.to_dict(),
                }
            )

        for regression in report.regressions:
            events.append(
                {
                    "type": "regression",
                    **regression.to_dict(),
                }
            )

        events.append(
            {
                "type": "summary",
                **report.summary.to_dict(),
            }
        )

        return events


# ------------------------------------------------------------------
# JUnit XML Formatter
# ------------------------------------------------------------------


class JUnitXMLFormatter:
    """JUnit XML formatter for CI/CD integration.

    Produces JUnit XML format compatible with Jenkins, GitHub Actions,
    and other CI/CD systems.
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format report as JUnit XML.

        Args:
            report: The benchmark report to format.

        Returns:
            JUnit XML string.
        """
        junit_report = JUnitReport.from_benchmark_report(report)
        return junit_report.to_xml()

    def format_to_file(self, report: BenchmarkReport, output_path: str) -> None:
        """Write JUnit XML to file.

        Args:
            report: The benchmark report to format.
            output_path: Path to write the XML file.
        """
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.format(report), encoding="utf-8")


# ------------------------------------------------------------------
# Summary Text Formatter
# ------------------------------------------------------------------


class SummaryFormatter:
    """Plain text summary formatter for human-readable output.

    Produces a formatted summary suitable for console output or logs.
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format report as human-readable summary.

        Args:
            report: The benchmark report to format.

        Returns:
            Formatted text summary.
        """
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append(f"BENCHMARK REPORT: {report.suite_name}")
        lines.append("=" * 60)
        lines.append(f"Run ID: {report.run_id or 'N/A'}")
        lines.append(f"Generated: {report.generated_at}")
        lines.append("")

        # Summary section
        summary = report.summary
        lines.append("SUMMARY")
        lines.append("-" * 40)
        lines.append(f"  Total:    {summary.total_benchmarks}")
        lines.append(f"  Passed:    {summary.passed} ({summary.pass_rate:.1%})")
        lines.append(f"  Failed:    {summary.failed}")
        lines.append(f"  Score:     {summary.overall_score:.1f}/100")
        lines.append(f"  Regressions: {summary.regressions_detected}")
        lines.append(f"  Wall Time: {summary.wall_time_ms}ms")
        lines.append("")

        # Individual benchmarks
        if report.benchmarks:
            lines.append("BENCHMARK RESULTS")
            lines.append("-" * 40)
            for bench in report.benchmarks:
                status = "PASS" if bench.passed else "FAIL"
                lines.append(f"  [{status}] {bench.case_id}")
                lines.append(f"          Score: {bench.score:.1%} | Duration: {bench.duration_ms}ms")
                if bench.role:
                    lines.append(f"          Role: {bench.role} | Mode: {bench.mode}")
                lines.append("")

        # Regressions
        if report.regressions:
            lines.append("REGRESSION ALERTS")
            lines.append("-" * 40)
            for reg in report.regressions:
                severity = reg.severity.value.upper()
                lines.append(f"  [{severity}] {reg.metric_name}")
                lines.append(
                    f"          {reg.previous_value:.2f} -> {reg.current_value:.2f} ({reg.change_percent:+.1f}%)"
                )
                lines.append(f"          Threshold: {reg.threshold_percent:.1f}%")
                lines.append("")

        # Health status
        lines.append("=" * 60)
        if summary.is_healthy:
            lines.append("STATUS: HEALTHY - Pipeline can proceed")
        else:
            lines.append("STATUS: UNHEALTHY - Pipeline should fail")
        lines.append("=" * 60)

        return "\n".join(lines)


# ------------------------------------------------------------------
# Markdown Formatter
# ------------------------------------------------------------------


class MarkdownFormatter:
    """Markdown formatter for documentation and pull requests.

    Produces GitHub-flavored markdown suitable for PR comments
    and documentation.
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format report as Markdown.

        Args:
            report: The benchmark report to format.

        Returns:
            Markdown string.
        """
        lines: list[str] = []

        # Header
        lines.append(f"# Benchmark Report: {report.suite_name}")
        lines.append("")
        lines.append(f"**Run ID:** `{report.run_id or 'N/A'}`  ")
        lines.append(f"**Generated:** {report.generated_at}  ")
        lines.append("")

        # Summary table
        summary = report.summary
        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total | {summary.total_benchmarks} |")
        lines.append(f"| Passed | {summary.passed} |")
        lines.append(f"| Failed | {summary.failed} |")
        lines.append(f"| Pass Rate | {summary.pass_rate:.1%} |")
        lines.append(f"| Overall Score | {summary.overall_score:.1f}/100 |")
        lines.append(f"| Regressions | {summary.regressions_detected} |")
        lines.append(f"| Wall Time | {summary.wall_time_ms}ms |")
        lines.append("")

        # Health badge
        if summary.is_healthy:
            lines.append("## Status: PASS")
        else:
            lines.append("## Status: FAIL")
        lines.append("")

        # Individual results
        if report.benchmarks:
            lines.append("## Results")
            lines.append("")
            lines.append("| Case | Status | Score | Duration | Mode |")
            lines.append("|------|--------|-------|----------|------|")
            for bench in report.benchmarks:
                status = "PASS" if bench.passed else "FAIL"
                badge = f"![{status}](https://img.shields.io/badge/{status}-{'success' if bench.passed else 'failed'}-lightgrey)"
                lines.append(
                    f"| `{bench.case_id}` | {badge} | {bench.score:.1%} | {bench.duration_ms}ms | {bench.mode} |"
                )
            lines.append("")

        # Regressions
        if report.regressions:
            lines.append("## Regression Alerts")
            lines.append("")
            lines.append("| Metric | Previous | Current | Change | Severity |")
            lines.append("|--------|---------|---------|--------|----------|")
            for reg in report.regressions:
                lines.append(
                    f"| {reg.metric_name} | {reg.previous_value:.2f} | {reg.current_value:.2f} "
                    f"| {reg.change_percent:+.1f}% | {reg.severity.value} |"
                )
            lines.append("")

        return "\n".join(lines)


# ------------------------------------------------------------------
# CSV Formatter
# ------------------------------------------------------------------


class CSVFormatter:
    """CSV formatter for data analysis.

    Produces CSV output suitable for Excel, Google Sheets,
    and data analysis tools.
    """

    def format(self, report: BenchmarkReport) -> str:
        """Format report as CSV.

        Args:
            report: The benchmark report to format.

        Returns:
            CSV string.
        """
        lines: list[str] = [
            "case_id,passed,score,duration_ms,p50_ms,p90_ms,p99_ms,mode,role",
        ]

        for bench in report.benchmarks:
            lines.append(
                f"{self._escape_csv(bench.case_id)},"
                f"{'TRUE' if bench.passed else 'FALSE'},"
                f"{bench.score:.4f},"
                f"{bench.duration_ms},"
                f"{bench.p50_ms:.2f},"
                f"{bench.p90_ms:.2f},"
                f"{bench.p99_ms:.2f},"
                f"{self._escape_csv(bench.mode)},"
                f"{self._escape_csv(bench.role)}"
            )

        return "\n".join(lines)

    def _escape_csv(self, value: str) -> str:
        """Escape a value for CSV format."""
        if not value:
            return ""
        if "," in value or '"' in value or "\n" in value:
            escaped = value.replace('"', '""')
            return f'"{escaped}"'
        return value


# ------------------------------------------------------------------
# Multi-Format Writer
# ------------------------------------------------------------------


@dataclass
class MultiFormatWriter:
    """Multi-format report writer.

    Writes benchmark reports in multiple formats to disk.
    """

    output_dir: str
    formats: tuple[ReportFormat, ...] = (ReportFormat.JSON,)

    def __post_init__(self) -> None:
        self._output_dir = Path(self.output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._formatters: dict[ReportFormat, object] = {
            ReportFormat.JSON: JSONFormatter(),
            ReportFormat.JSON_LINES: JSONLinesFormatter(),
            ReportFormat.PROMETHEUS: PrometheusFormatter(),
            ReportFormat.JUNIT_XML: JUnitXMLFormatter(),
        }

    def write(self, report: BenchmarkReport, format: ReportFormat | str = ReportFormat.JSON) -> dict[str, str]:
        """Write report in specified format(s).

        Args:
            report: The benchmark report to write.
            format: Format to write, or tuple of formats.

        Returns:
            Dictionary mapping format to output path.
        """
        if isinstance(format, str):
            format = ReportFormat(format)

        formats_to_write = (format,) if isinstance(format, ReportFormat) else format

        outputs: dict[str, str] = {}
        for fmt in formats_to_write:
            path = self._write_single(report, fmt)
            if path:
                outputs[fmt.value] = path

        return outputs

    def _write_single(self, report: BenchmarkReport, fmt: ReportFormat) -> str | None:
        """Write report in a single format."""
        formatter = self._formatters.get(fmt)
        if not formatter:
            return None

        # Determine extension
        extensions: dict[ReportFormat, str] = {
            ReportFormat.JSON: ".json",
            ReportFormat.JSON_LINES: ".jsonl",
            ReportFormat.PROMETHEUS: ".prom",
            ReportFormat.JUNIT_XML: ".xml",
        }
        ext = extensions.get(fmt, ".txt")

        # Build filename
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"benchmark_{report.suite_name}_{report.run_id or 'run'}_{timestamp}{ext}"
        filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)

        path = self._output_dir / filename

        # Format and write
        if fmt in (ReportFormat.JSON, ReportFormat.JSON_LINES, ReportFormat.PROMETHEUS, ReportFormat.JUNIT_XML):
            content = formatter.format(report)  # type: ignore
        else:
            return None

        path.write_text(content, encoding="utf-8")
        return str(path)
