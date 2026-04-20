"""Tests for benchmark reporting structures.

These tests verify the data models used for CI/CD integration,
including report generation, formatting, and serialization.
"""

from __future__ import annotations

import json

from polaris.kernelone.benchmark.reporting.structs import (
    AlertSeverity,
    BenchmarkReport,
    BenchmarkResult,
    JUnitReport,
    PrometheusFormatter,
    RegressionAlert,
    ReportSummary,
    create_report,
)


class TestBenchmarkResult:
    """Tests for BenchmarkResult dataclass."""

    def test_create_basic_result(self) -> None:
        """Test creating a basic benchmark result."""
        result = BenchmarkResult(
            case_id="test_case",
            passed=True,
            score=0.95,
            duration_ms=1500,
        )

        assert result.case_id == "test_case"
        assert result.passed is True
        assert result.score == 0.95
        assert result.duration_ms == 1500
        assert result.p50_ms == 0.0
        assert result.p90_ms == 0.0
        assert result.p99_ms == 0.0

    def test_create_result_with_latency(self) -> None:
        """Test creating a result with latency metrics."""
        result = BenchmarkResult(
            case_id="latency_test",
            passed=True,
            score=0.90,
            duration_ms=2000,
            p50_ms=120.0,
            p90_ms=180.0,
            p99_ms=250.0,
            mode="agentic",
            role="director",
        )

        assert result.p50_ms == 120.0
        assert result.p90_ms == 180.0
        assert result.p99_ms == 250.0
        assert result.mode == "agentic"
        assert result.role == "director"

    def test_score_clamping(self) -> None:
        """Test that scores are clamped to 0.0-1.0 range."""
        result_low = BenchmarkResult(
            case_id="test",
            passed=False,
            score=-0.5,  # Should be clamped to 0.0
            duration_ms=100,
        )
        assert result_low.score == 0.0

        result_high = BenchmarkResult(
            case_id="test",
            passed=True,
            score=1.5,  # Should be clamped to 1.0
            duration_ms=100,
        )
        assert result_high.score == 1.0

    def test_to_dict_roundtrip(self) -> None:
        """Test serialization and deserialization roundtrip."""
        original = BenchmarkResult(
            case_id="roundtrip_test",
            passed=True,
            score=0.85,
            duration_ms=1800,
            p50_ms=100.0,
            mode="strategy",
        )

        data = original.to_dict()
        restored = BenchmarkResult.from_dict(data)

        assert restored.case_id == original.case_id
        assert restored.passed == original.passed
        assert restored.score == original.score
        assert restored.duration_ms == original.duration_ms
        assert restored.p50_ms == original.p50_ms


class TestRegressionAlert:
    """Tests for RegressionAlert dataclass."""

    def test_create_regression_alert(self) -> None:
        """Test creating a regression alert."""
        alert = RegressionAlert(
            metric_name="latency_p50",
            previous_value=120.0,
            current_value=145.0,
            change_percent=20.83,
            severity=AlertSeverity.WARNING,
            threshold_percent=10.0,
        )

        assert alert.metric_name == "latency_p50"
        assert alert.previous_value == 120.0
        assert alert.current_value == 145.0
        assert alert.severity == AlertSeverity.WARNING

    def test_severity_from_string(self) -> None:
        """Test creating alert with string severity."""
        alert = RegressionAlert(
            metric_name="accuracy",
            previous_value=0.95,
            current_value=0.88,
            change_percent=-7.37,
            severity=AlertSeverity.CRITICAL,
            threshold_percent=5.0,
        )

        assert alert.severity == AlertSeverity.CRITICAL

    def test_to_dict_roundtrip(self) -> None:
        """Test serialization and deserialization."""
        original = RegressionAlert(
            metric_name="test_metric",
            previous_value=100.0,
            current_value=120.0,
            change_percent=20.0,
            severity=AlertSeverity.CRITICAL,
            threshold_percent=10.0,
            case_id="test_case",
            message="Test regression",
        )

        data = original.to_dict()
        restored = RegressionAlert.from_dict(data)

        assert restored.metric_name == original.metric_name
        assert restored.previous_value == original.previous_value
        assert restored.current_value == original.current_value
        assert restored.change_percent == original.change_percent
        assert restored.severity == original.severity


class TestReportSummary:
    """Tests for ReportSummary dataclass."""

    def test_create_summary(self) -> None:
        """Test creating a report summary."""
        summary = ReportSummary(
            total_benchmarks=10,
            passed=8,
            failed=2,
            regressions_detected=1,
            overall_score=85.0,
            pass_rate=0.8,
            wall_time_ms=5000,
        )

        assert summary.total_benchmarks == 10
        assert summary.passed == 8
        assert summary.failed == 2
        assert summary.regressions_detected == 1
        assert summary.overall_score == 85.0
        assert summary.pass_rate == 0.8

    def test_is_healthy_no_issues(self) -> None:
        """Test health check with no issues."""
        summary = ReportSummary(
            total_benchmarks=10,
            passed=10,
            failed=0,
            regressions_detected=0,
            overall_score=100.0,
        )
        assert summary.is_healthy is True

    def test_is_healthy_with_failures(self) -> None:
        """Test health check with failures."""
        summary = ReportSummary(
            total_benchmarks=10,
            passed=9,
            failed=1,
            regressions_detected=0,
            overall_score=90.0,
        )
        assert summary.is_healthy is False

    def test_is_healthy_with_regressions(self) -> None:
        """Test health check with regressions but no failures."""
        summary = ReportSummary(
            total_benchmarks=10,
            passed=10,
            failed=0,
            regressions_detected=1,
            overall_score=100.0,
        )
        assert summary.is_healthy is False


class TestBenchmarkReport:
    """Tests for BenchmarkReport dataclass."""

    def test_create_report(self) -> None:
        """Test creating a complete benchmark report with explicit summary."""
        benchmarks = [
            BenchmarkResult(
                case_id="case_1",
                passed=True,
                score=0.95,
                duration_ms=1000,
            ),
            BenchmarkResult(
                case_id="case_2",
                passed=False,
                score=0.60,
                duration_ms=800,
            ),
        ]

        regressions = [
            RegressionAlert(
                metric_name="latency_p50",
                previous_value=120.0,
                current_value=145.0,
                change_percent=20.83,
                severity=AlertSeverity.WARNING,
                threshold_percent=10.0,
            ),
        ]

        # Note: When creating BenchmarkReport directly, summary defaults to zeros
        # Use create_report() for automatic summary calculation
        report = BenchmarkReport(
            report_version="1.0",
            benchmarks=tuple(benchmarks),
            regressions=tuple(regressions),
            summary=ReportSummary(
                total_benchmarks=2,
                passed=1,
                failed=1,
                regressions_detected=1,
                overall_score=77.5,
            ),
            environment={"branch": "main"},
            run_id="run_001",
            suite_name="test_suite",
        )

        assert report.report_version == "1.0"
        assert len(report.benchmarks) == 2
        assert len(report.regressions) == 1
        assert report.summary.total_benchmarks == 2
        assert report.summary.passed == 1
        assert report.summary.failed == 1
        assert report.summary.regressions_detected == 1

    def test_to_json(self) -> None:
        """Test JSON serialization."""
        result = BenchmarkResult(
            case_id="json_test",
            passed=True,
            score=0.90,
            duration_ms=500,
        )

        report = BenchmarkReport(
            benchmarks=(result,),
            summary=ReportSummary(
                total_benchmarks=1,
                passed=1,
                failed=0,
                regressions_detected=0,
                overall_score=90.0,
            ),
        )

        json_str = report.to_json()
        parsed = json.loads(json_str)

        assert parsed["summary"]["total_benchmarks"] == 1
        assert parsed["benchmarks"][0]["case_id"] == "json_test"

    def test_to_jsonl(self) -> None:
        """Test JSON Lines serialization."""
        benchmarks = [
            BenchmarkResult(case_id="case_1", passed=True, score=0.9, duration_ms=100),
            BenchmarkResult(case_id="case_2", passed=False, score=0.7, duration_ms=200),
        ]

        report = BenchmarkReport(
            benchmarks=tuple(benchmarks),
            summary=ReportSummary(
                total_benchmarks=2,
                passed=1,
                failed=1,
                regressions_detected=0,
                overall_score=80.0,
            ),
        )

        jsonl_str = report.to_jsonl()
        lines = jsonl_str.split("\n")

        assert len(lines) == 3  # 2 benchmarks + 1 summary

        for line in lines[:-1]:  # Benchmark lines
            parsed = json.loads(line)
            assert "case_id" in parsed
            assert "type" not in parsed  # Benchmark lines don't have type

        summary_line = json.loads(lines[-1])
        assert summary_line["type"] == "summary"

    def test_from_dict(self) -> None:
        """Test deserialization from dictionary."""
        data = {
            "report_version": "1.0",
            "benchmarks": [
                {"case_id": "test", "passed": True, "score": 0.95, "duration_ms": 100},
            ],
            "regressions": [],
            "summary": {
                "total_benchmarks": 1,
                "passed": 1,
                "failed": 0,
                "regressions_detected": 0,
                "overall_score": 95.0,
            },
            "run_id": "test_run",
            "suite_name": "test",
        }

        report = BenchmarkReport.from_dict(data)
        assert report.report_version == "1.0"
        assert len(report.benchmarks) == 1
        assert report.summary.overall_score == 95.0


class TestCreateReport:
    """Tests for the create_report convenience function."""

    def test_create_report_basic(self) -> None:
        """Test basic report creation."""
        benchmarks = [
            BenchmarkResult(case_id="test_1", passed=True, score=0.95, duration_ms=100),
            BenchmarkResult(case_id="test_2", passed=True, score=0.90, duration_ms=200),
        ]

        report = create_report(benchmarks)

        assert report.summary.total_benchmarks == 2
        assert report.summary.passed == 2
        assert report.summary.failed == 0
        # (0.95 + 0.90) / 2 * 100 = 92.5
        assert report.summary.overall_score == 92.5
        assert report.summary.pass_rate == 1.0

    def test_create_report_with_regressions(self) -> None:
        """Test report creation with regression alerts."""
        benchmarks = [
            BenchmarkResult(case_id="test_1", passed=True, score=1.0, duration_ms=100),
        ]

        regressions = [
            RegressionAlert(
                metric_name="latency",
                previous_value=100.0,
                current_value=120.0,
                change_percent=20.0,
                severity=AlertSeverity.WARNING,
                threshold_percent=10.0,
            ),
        ]

        report = create_report(benchmarks, regressions=regressions)

        assert report.summary.regressions_detected == 1
        assert len(report.regressions) == 1


class TestPrometheusFormatter:
    """Tests for Prometheus metrics formatting."""

    def test_format_basic_report(self) -> None:
        """Test formatting a basic report."""
        result = BenchmarkResult(
            case_id="prom_test",
            passed=True,
            score=0.95,
            duration_ms=1500,
            p50_ms=120.0,
            p90_ms=180.0,
            p99_ms=250.0,
        )

        report = BenchmarkReport(
            benchmarks=(result,),
            summary=ReportSummary(
                total_benchmarks=1,
                passed=1,
                failed=0,
                regressions_detected=0,
                overall_score=95.0,
                pass_rate=1.0,  # passed/total = 1/1
            ),
            run_id="test_run",
            suite_name="prometheus_test",
        )

        formatter = PrometheusFormatter()
        output = formatter.format(report)

        # Check for expected metric lines
        # pass_rate is 1.0 (pass_rate=passed/total=1/1)
        assert 'benchmark_pass_rate{suite="prometheus_test"} 1.0' in output
        assert 'benchmark_overall_score{suite="prometheus_test"} 95' in output
        assert 'benchmark_passed{name="prom_test"' in output
        assert 'benchmark_score{name="prom_test"' in output
        assert 'benchmark_latency_p50_ms{name="prom_test"' in output

    def test_escape_label_values(self) -> None:
        """Test escaping of special characters in labels."""
        formatter = PrometheusFormatter()

        escaped = formatter._escape_label('test"value\nwith\\special')
        assert "\\" in escaped
        assert '"' not in escaped or "\\" in escaped


class TestJUnitReport:
    """Tests for JUnit XML report generation."""

    def test_from_benchmark_report(self) -> None:
        """Test converting BenchmarkReport to JUnitReport."""
        benchmarks = [
            BenchmarkResult(
                case_id="junit_case_1",
                passed=True,
                score=0.95,
                duration_ms=1000,
                mode="agentic",
                role="director",
            ),
            BenchmarkResult(
                case_id="junit_case_2",
                passed=False,
                score=0.50,
                duration_ms=500,
                mode="agentic",
                role="director",
            ),
        ]

        report = BenchmarkReport(
            benchmarks=tuple(benchmarks),
            summary=ReportSummary(
                total_benchmarks=2,
                passed=1,
                failed=1,
                regressions_detected=0,
                overall_score=72.5,
            ),
            suite_name="junit_test",
        )

        junit = JUnitReport.from_benchmark_report(report)

        assert junit.name == "junit_test"
        assert junit.tests == 2
        assert junit.failures == 1
        assert len(junit.cases) == 2

    def test_junit_xml_output(self) -> None:
        """Test JUnit XML generation."""
        benchmarks = [
            BenchmarkResult(
                case_id="xml_test",
                passed=True,
                score=1.0,
                duration_ms=100,
            ),
        ]

        report = BenchmarkReport(
            benchmarks=tuple(benchmarks),
            summary=ReportSummary(
                total_benchmarks=1,
                passed=1,
                failed=0,
                regressions_detected=0,
                overall_score=100.0,
            ),
        )

        junit = JUnitReport.from_benchmark_report(report)
        xml = junit.to_xml()

        assert '<?xml version="1.0"' in xml
        assert "testsuite" in xml
        assert "xml_test" in xml
        assert 'tests="1"' in xml
        assert 'failures="0"' in xml
