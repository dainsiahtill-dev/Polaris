"""Report generation and formatting for holographic benchmark results."""

from __future__ import annotations

from polaris.kernelone.benchmark.holographic.config import HolographicSuiteResult


def format_suite_report(suite: HolographicSuiteResult) -> str:
    """Format a suite result as a human-readable summary string."""
    lines = [
        f"Holographic Benchmark Suite: {suite.run_id}",
        f"Timestamp: {suite.timestamp_utc}",
        f"Total Cases: {suite.total_cases}",
        f"  Passed:  {suite.passed}",
        f"  Failed:  {suite.failed}",
        f"  Skipped: {suite.skipped}",
        f"  Errored: {suite.errored}",
        "",
        "Per-case results:",
    ]
    for result in suite.results:
        status_icon = "OK" if result.status.value == "passed" else result.status.value.upper()
        lines.append(f"  [{status_icon}] {result.case_id} — {result.duration_ms:.2f} ms")
        if result.failures:
            for failure in result.failures:
                lines.append(f"      ! {failure}")
        if result.message:
            lines.append(f"      > {result.message}")
    return "\n".join(lines)
