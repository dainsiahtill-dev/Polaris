"""Tests for benchmark alerts module.

Covers AlertChannel, BenchmarkAlert, SlackFormatter, TeamsFormatter,
AlertDispatcher, and convenience functions.
All tests are pure logic (filesystem mocked where needed).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.benchmark.reporting.alerts import (
    AlertChannel,
    AlertDispatcher,
    BenchmarkAlert,
    SlackFormatter,
    TeamsFormatter,
    create_alerts_from_regressions,
    create_alerts_from_report,
)
from polaris.kernelone.benchmark.reporting.structs import (
    AlertSeverity,
    AlertStatus,
    BenchmarkReport,
    BenchmarkResult,
    RegressionAlert,
    ReportSummary,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_alert() -> BenchmarkAlert:
    """Return a sample BenchmarkAlert."""
    return BenchmarkAlert(
        title="Latency Regression",
        description="p50 latency increased by 15%",
        severity=AlertSeverity.WARNING,
        case_id="bench-001",
        metrics={"p50_ms": {"previous": 100.0, "current": 115.0}},
    )


@pytest.fixture
def critical_alert() -> BenchmarkAlert:
    """Return a critical severity BenchmarkAlert."""
    return BenchmarkAlert(
        title="Throughput Collapse",
        description=" throughput dropped by 80%",
        severity=AlertSeverity.CRITICAL,
        case_id="bench-002",
        metrics={"tps": {"previous": 1000.0, "current": 200.0}},
    )


@pytest.fixture
def info_alert() -> BenchmarkAlert:
    """Return an info severity BenchmarkAlert."""
    return BenchmarkAlert(
        title="Metric Change",
        description="minor metric fluctuation",
        severity=AlertSeverity.INFO,
        case_id="bench-003",
    )


@pytest.fixture
def sample_report() -> BenchmarkReport:
    """Return a sample BenchmarkReport with mixed results."""
    return BenchmarkReport(
        report_version="1.0",
        environment={"branch": "main", "commit": "abc123"},
        benchmarks=(
            BenchmarkResult(
                case_id="bench-001",
                passed=True,
                score=0.95,
                duration_ms=1500,
                p50_ms=120.0,
                p90_ms=200.0,
                p99_ms=350.0,
            ),
            BenchmarkResult(
                case_id="bench-002",
                passed=False,
                score=0.45,
                duration_ms=3000,
                p50_ms=500.0,
            ),
        ),
        regressions=(
            RegressionAlert(
                metric_name="latency_p50",
                previous_value=100.0,
                current_value=115.0,
                change_percent=15.0,
                severity=AlertSeverity.WARNING,
                threshold_percent=10.0,
                case_id="bench-001",
                message="p50 latency regressed by 15%",
            ),
        ),
        summary=ReportSummary(
            total_benchmarks=2,
            passed=1,
            failed=1,
            regressions_detected=1,
            overall_score=70.0,
            pass_rate=0.5,
            wall_time_ms=4500,
        ),
    )


# ------------------------------------------------------------------
# AlertChannel Tests
# ------------------------------------------------------------------


class TestAlertChannel:
    """Tests for AlertChannel enum."""

    def test_all_channels_defined(self) -> None:
        expected = {"slack", "teams", "email", "file", "webhook", "console"}
        actual = {c.value for c in AlertChannel}
        assert actual == expected

    def test_slack_value(self) -> None:
        assert AlertChannel.SLACK.value == "slack"

    def test_teams_value(self) -> None:
        assert AlertChannel.TEAMS.value == "teams"

    def test_email_value(self) -> None:
        assert AlertChannel.EMAIL.value == "email"

    def test_file_value(self) -> None:
        assert AlertChannel.FILE.value == "file"

    def test_webhook_value(self) -> None:
        assert AlertChannel.WEBHOOK.value == "webhook"

    def test_console_value(self) -> None:
        assert AlertChannel.CONSOLE.value == "console"


# ------------------------------------------------------------------
# BenchmarkAlert Tests
# ------------------------------------------------------------------


class TestBenchmarkAlert:
    """Tests for BenchmarkAlert dataclass."""

    def test_create_with_all_fields(self, sample_alert: BenchmarkAlert) -> None:
        assert sample_alert.title == "Latency Regression"
        assert sample_alert.description == "p50 latency increased by 15%"
        assert sample_alert.severity == AlertSeverity.WARNING
        assert sample_alert.case_id == "bench-001"
        assert sample_alert.metrics == {"p50_ms": {"previous": 100.0, "current": 115.0}}
        assert sample_alert.status == AlertStatus.TRIGGERED
        assert sample_alert.url == ""

    def test_default_timestamp(self, sample_alert: BenchmarkAlert) -> None:
        assert isinstance(sample_alert.timestamp, str)
        assert "T" in sample_alert.timestamp  # ISO format

    def test_default_status(self) -> None:
        alert = BenchmarkAlert(
            title="Test",
            description="Test description",
            severity=AlertSeverity.INFO,
        )
        assert alert.status == AlertStatus.TRIGGERED

    def test_to_dict_structure(self, sample_alert: BenchmarkAlert) -> None:
        result = sample_alert.to_dict()
        expected_keys = {"title", "description", "severity", "case_id", "metrics", "timestamp", "status", "url"}
        assert set(result.keys()) == expected_keys

    def test_to_dict_severity_is_string(self, sample_alert: BenchmarkAlert) -> None:
        result = sample_alert.to_dict()
        assert result["severity"] == "warning"
        assert isinstance(result["severity"], str)

    def test_from_dict_round_trip(self, sample_alert: BenchmarkAlert) -> None:
        data = sample_alert.to_dict()
        restored = BenchmarkAlert.from_dict(data)
        assert restored.title == sample_alert.title
        assert restored.severity == sample_alert.severity
        assert restored.status == sample_alert.status

    def test_from_dict_with_string_severity(self) -> None:
        data = {
            "title": "Test",
            "description": "Desc",
            "severity": "critical",
            "case_id": "c1",
        }
        alert = BenchmarkAlert.from_dict(data)
        assert alert.severity == AlertSeverity.CRITICAL

    def test_from_dict_with_string_status(self) -> None:
        data = {
            "title": "Test",
            "description": "Desc",
            "severity": "info",
            "status": "acknowledged",
        }
        alert = BenchmarkAlert.from_dict(data)
        assert alert.status == AlertStatus.ACKNOWLEDGED

    def test_from_dict_defaults(self) -> None:
        alert = BenchmarkAlert.from_dict({})
        assert alert.title == ""
        assert alert.severity == AlertSeverity.WARNING
        assert alert.status == AlertStatus.TRIGGERED

    def test_post_init_converts_string_severity(self) -> None:
        alert = BenchmarkAlert(
            title="Test",
            description="Desc",
            severity="critical",  # type: ignore[arg-type]
        )
        assert alert.severity == AlertSeverity.CRITICAL

    def test_post_init_converts_string_status(self) -> None:
        alert = BenchmarkAlert(
            title="Test",
            description="Desc",
            severity=AlertSeverity.INFO,
            status="resolved",  # type: ignore[arg-type]
        )
        assert alert.status == AlertStatus.RESOLVED

    def test_frozen_dataclass(self) -> None:
        alert = BenchmarkAlert(
            title="Test",
            description="Desc",
            severity=AlertSeverity.INFO,
        )
        with pytest.raises(AttributeError):
            alert.title = "Changed"


# ------------------------------------------------------------------
# SlackFormatter Tests
# ------------------------------------------------------------------


class TestSlackFormatter:
    """Tests for SlackFormatter."""

    @pytest.fixture
    def formatter(self) -> SlackFormatter:
        return SlackFormatter()

    def test_format_empty_alerts(self, formatter: SlackFormatter) -> None:
        result = formatter.format([])
        assert "blocks" in result
        assert result["blocks"][0]["text"]["text"] == "Benchmark Report: All Passed"

    def test_format_single_alert(self, formatter: SlackFormatter, sample_alert: BenchmarkAlert) -> None:
        result = formatter.format([sample_alert])
        blocks = result["blocks"]
        assert any("Benchmark Alert" in str(b) for b in blocks)

    def test_format_uses_worst_severity_for_header(
        self,
        formatter: SlackFormatter,
        sample_alert: BenchmarkAlert,
        critical_alert: BenchmarkAlert,
    ) -> None:
        result = formatter.format([sample_alert, critical_alert])
        header_text = result["blocks"][0]["text"]["text"]
        assert "Critical" in header_text or "🔴" in header_text

    def test_format_includes_case_id(self, formatter: SlackFormatter, sample_alert: BenchmarkAlert) -> None:
        result = formatter.format([sample_alert])
        text_blocks = [b for b in result["blocks"] if b.get("type") == "section" and "fields" in b]
        assert any("bench-001" in str(b) for b in text_blocks)

    def test_format_with_report_summary(self, formatter: SlackFormatter, sample_report: BenchmarkReport) -> None:
        result = formatter.format([], report=sample_report)
        blocks_text = str(result["blocks"])
        assert "Total:" in blocks_text
        assert "Passed:" in blocks_text
        assert "Failed:" in blocks_text

    def test_format_multiple_alerts(
        self, formatter: SlackFormatter, sample_alert: BenchmarkAlert, critical_alert: BenchmarkAlert
    ) -> None:
        result = formatter.format([sample_alert, critical_alert])
        assert len(result["blocks"]) > 3  # header, divider, sections, divider

    def test_severity_order_critical_highest(self, formatter: SlackFormatter) -> None:
        assert formatter._severity_order(AlertSeverity.CRITICAL) == 3
        assert formatter._severity_order(AlertSeverity.WARNING) == 2
        assert formatter._severity_order(AlertSeverity.INFO) == 1

    def test_severity_emoji_mapping(self, formatter: SlackFormatter) -> None:
        assert formatter._severity_emoji(AlertSeverity.CRITICAL) == "🔴"
        assert formatter._severity_emoji(AlertSeverity.WARNING) == "🟡"
        assert formatter._severity_emoji(AlertSeverity.INFO) == "ℹ️"

    def test_severity_color_mapping(self, formatter: SlackFormatter) -> None:
        assert formatter._severity_color(AlertSeverity.CRITICAL) == "#FF0000"
        assert formatter._severity_color(AlertSeverity.WARNING) == "#FFA500"
        assert formatter._severity_color(AlertSeverity.INFO) == "#00AAFF"


# ------------------------------------------------------------------
# TeamsFormatter Tests
# ------------------------------------------------------------------


class TestTeamsFormatter:
    """Tests for TeamsFormatter."""

    @pytest.fixture
    def formatter(self) -> TeamsFormatter:
        return TeamsFormatter()

    def test_format_empty_alerts(self, formatter: TeamsFormatter) -> None:
        result = formatter.format([])
        assert result["type"] == "AdaptiveCard"
        assert any("All Passed" in str(item) for item in result["body"])

    def test_format_critical_alerts(self, formatter: TeamsFormatter, critical_alert: BenchmarkAlert) -> None:
        result = formatter.format([critical_alert])
        assert any("Critical Alerts" in str(item) for item in result["body"])

    def test_format_warning_alerts(self, formatter: TeamsFormatter, sample_alert: BenchmarkAlert) -> None:
        result = formatter.format([sample_alert])
        assert any("Warnings" in str(item) for item in result["body"])

    def test_format_with_report_summary(self, formatter: TeamsFormatter, sample_report: BenchmarkReport) -> None:
        result = formatter.format([], report=sample_report)
        body_text = str(result["body"])
        assert "Total" in body_text
        assert "Passed" in body_text

    def test_format_includes_alert_details(self, formatter: TeamsFormatter, sample_alert: BenchmarkAlert) -> None:
        result = formatter.format([sample_alert])
        body_text = str(result["body"])
        assert sample_alert.title in body_text
        assert sample_alert.description in body_text

    def test_format_critical_color_attention(self, formatter: TeamsFormatter, critical_alert: BenchmarkAlert) -> None:
        result = formatter.format([critical_alert])
        body_text = str(result["body"])
        assert "Attention" in body_text

    def test_format_multiple_alerts(
        self, formatter: TeamsFormatter, sample_alert: BenchmarkAlert, critical_alert: BenchmarkAlert
    ) -> None:
        result = formatter.format([sample_alert, critical_alert])
        assert len(result["body"]) > 2


# ------------------------------------------------------------------
# AlertDispatcher Tests
# ------------------------------------------------------------------


class TestAlertDispatcher:
    """Tests for AlertDispatcher."""

    @pytest.fixture
    def dispatcher(self) -> AlertDispatcher:
        return AlertDispatcher()

    def test_register_channel(self, dispatcher: AlertDispatcher) -> None:
        dispatcher.register_channel(AlertChannel.SLACK, "https://hooks.slack.com/test")
        assert AlertChannel.SLACK in dispatcher._channels

    def test_unregister_existing_channel(self, dispatcher: AlertDispatcher) -> None:
        dispatcher.register_channel(AlertChannel.SLACK, "url")
        result = dispatcher.unregister_channel(AlertChannel.SLACK)
        assert result is True
        assert AlertChannel.SLACK not in dispatcher._channels

    def test_unregister_nonexistent_channel(self, dispatcher: AlertDispatcher) -> None:
        result = dispatcher.unregister_channel(AlertChannel.SLACK)
        assert result is False

    def test_dispatch_empty_channels_returns_empty(
        self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert
    ) -> None:
        results = dispatcher.dispatch([sample_alert])
        assert results == {}

    def test_dispatch_console_channel(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.CONSOLE, "")
        with patch("builtins.print") as mock_print:
            results = dispatcher.dispatch([sample_alert])

        assert results[AlertChannel.CONSOLE] is True
        mock_print.assert_called()

    def test_dispatch_file_channel(
        self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert, tmp_path: Path
    ) -> None:
        path = str(tmp_path / "alerts.json")
        dispatcher.register_channel(AlertChannel.FILE, path)
        results = dispatcher.dispatch([sample_alert])

        assert results[AlertChannel.FILE] is True
        assert Path(path).exists()

    def test_dispatch_file_channel_content(
        self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert, tmp_path: Path
    ) -> None:
        path = str(tmp_path / "alerts.json")
        dispatcher.register_channel(AlertChannel.FILE, path)
        dispatcher.dispatch([sample_alert])

        content = Path(path).read_text(encoding="utf-8")
        assert "Latency Regression" in content
        assert "alerts" in content

    def test_dispatch_webhook_mocked(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.WEBHOOK, "http://example.com/webhook")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_response

            results = dispatcher.dispatch([sample_alert])

        assert results[AlertChannel.WEBHOOK] is True

    def test_dispatch_slack_mocked(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.SLACK, "https://hooks.slack.com/test")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_response

            results = dispatcher.dispatch([sample_alert])

        assert results[AlertChannel.SLACK] is True

    def test_dispatch_teams_mocked(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.TEAMS, "https://teams.webhook.office.com/test")
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_urlopen.return_value.__enter__.return_value = mock_response

            results = dispatcher.dispatch([sample_alert])

        assert results[AlertChannel.TEAMS] is True

    def test_dispatch_email_invalid_config(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.EMAIL, "invalid-config")
        results = dispatcher.dispatch([sample_alert])
        assert results[AlertChannel.EMAIL] is False

    def test_dispatch_email_wrong_scheme(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.EMAIL, "http://user:pass@host:587?from=a@b.com&to=c@d.com")
        results = dispatcher.dispatch([sample_alert])
        assert results[AlertChannel.EMAIL] is False

    def test_dispatch_email_missing_addresses(self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert) -> None:
        dispatcher.register_channel(AlertChannel.EMAIL, "smtp://user:pass@host:587")
        results = dispatcher.dispatch([sample_alert])
        assert results[AlertChannel.EMAIL] is False

    def test_dispatch_multiple_channels(
        self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert, tmp_path: Path
    ) -> None:
        dispatcher.register_channel(AlertChannel.CONSOLE, "")
        dispatcher.register_channel(AlertChannel.FILE, str(tmp_path / "alerts.json"))

        with patch("builtins.print"):
            results = dispatcher.dispatch([sample_alert])

        assert len(results) == 2
        assert all(results.values())

    def test_dispatch_with_report(
        self, dispatcher: AlertDispatcher, sample_alert: BenchmarkAlert, sample_report: BenchmarkReport
    ) -> None:
        dispatcher.register_channel(AlertChannel.CONSOLE, "")
        with patch("builtins.print"):
            results = dispatcher.dispatch([sample_alert], report=sample_report)

        assert results[AlertChannel.CONSOLE] is True


# ------------------------------------------------------------------
# create_alerts_from_regressions Tests
# ------------------------------------------------------------------


class TestCreateAlertsFromRegressions:
    """Tests for create_alerts_from_regressions function."""

    def test_empty_regressions_returns_empty(self) -> None:
        result = create_alerts_from_regressions([])
        assert result == []

    def test_single_regression(self) -> None:
        regressions = [
            RegressionAlert(
                metric_name="latency_p50",
                previous_value=100.0,
                current_value=115.0,
                change_percent=15.0,
                severity=AlertSeverity.WARNING,
                threshold_percent=10.0,
                message="p50 latency regressed by 15%",
            ),
        ]
        alerts = create_alerts_from_regressions(regressions)
        assert len(alerts) == 1
        assert alerts[0].title == "Regression: latency_p50"
        assert alerts[0].severity == AlertSeverity.WARNING

    def test_multiple_regressions(self) -> None:
        regressions = [
            RegressionAlert(
                metric_name="latency_p50",
                previous_value=100.0,
                current_value=115.0,
                change_percent=15.0,
                severity=AlertSeverity.WARNING,
                threshold_percent=10.0,
            ),
            RegressionAlert(
                metric_name="throughput",
                previous_value=1000.0,
                current_value=800.0,
                change_percent=-20.0,
                severity=AlertSeverity.CRITICAL,
                threshold_percent=15.0,
            ),
        ]
        alerts = create_alerts_from_regressions(regressions)
        assert len(alerts) == 2
        assert alerts[1].title == "Regression: throughput"

    def test_regression_with_case_id(self) -> None:
        regressions = [
            RegressionAlert(
                metric_name="latency",
                previous_value=100.0,
                current_value=120.0,
                change_percent=20.0,
                severity=AlertSeverity.CRITICAL,
                threshold_percent=10.0,
                case_id="case-001",
            ),
        ]
        alerts = create_alerts_from_regressions(regressions, case_id="override-001")
        assert alerts[0].case_id == "override-001"

    def test_regression_without_message_uses_default(self) -> None:
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
        alerts = create_alerts_from_regressions(regressions)
        assert "latency changed by +20.0%" in alerts[0].description

    def test_regression_metrics_populated(self) -> None:
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
        alerts = create_alerts_from_regressions(regressions)
        metrics = alerts[0].metrics
        assert metrics["metric_name"] == "latency"
        assert metrics["previous_value"] == 100.0
        assert metrics["current_value"] == 120.0
        assert metrics["change_percent"] == 20.0
        assert metrics["threshold_percent"] == 10.0

    def test_regression_uses_regression_case_id_when_no_override(self) -> None:
        regressions = [
            RegressionAlert(
                metric_name="latency",
                previous_value=100.0,
                current_value=120.0,
                change_percent=20.0,
                severity=AlertSeverity.WARNING,
                threshold_percent=10.0,
                case_id="reg-case-001",
            ),
        ]
        alerts = create_alerts_from_regressions(regressions)
        assert alerts[0].case_id == "reg-case-001"


# ------------------------------------------------------------------
# create_alerts_from_report Tests
# ------------------------------------------------------------------


class TestCreateAlertsFromReport:
    """Tests for create_alerts_from_report function."""

    def test_empty_report_returns_empty(self) -> None:
        report = BenchmarkReport(
            benchmarks=(),
            regressions=(),
            summary=ReportSummary(
                total_benchmarks=0,
                passed=0,
                failed=0,
                regressions_detected=0,
                overall_score=0.0,
            ),
        )
        alerts = create_alerts_from_report(report)
        assert alerts == []

    def test_failed_benchmark_creates_alert(self) -> None:
        report = BenchmarkReport(
            benchmarks=(
                BenchmarkResult(
                    case_id="bench-001",
                    passed=False,
                    score=0.45,
                    duration_ms=3000,
                ),
            ),
            regressions=(),
            summary=ReportSummary(
                total_benchmarks=1,
                passed=0,
                failed=1,
                regressions_detected=0,
                overall_score=45.0,
                pass_rate=0.0,
            ),
        )
        alerts = create_alerts_from_report(report)
        assert len(alerts) == 1
        assert alerts[0].title == "Failed: bench-001"
        assert alerts[0].severity == AlertSeverity.CRITICAL

    def test_passed_benchmark_no_alert(self) -> None:
        report = BenchmarkReport(
            benchmarks=(
                BenchmarkResult(
                    case_id="bench-001",
                    passed=True,
                    score=0.95,
                    duration_ms=1500,
                ),
            ),
            regressions=(),
            summary=ReportSummary(
                total_benchmarks=1,
                passed=1,
                failed=0,
                regressions_detected=0,
                overall_score=95.0,
                pass_rate=1.0,
            ),
        )
        alerts = create_alerts_from_report(report)
        assert alerts == []

    def test_mixed_passed_and_failed(self) -> None:
        report = BenchmarkReport(
            benchmarks=(
                BenchmarkResult(case_id="b1", passed=True, score=0.9, duration_ms=1000),
                BenchmarkResult(case_id="b2", passed=False, score=0.4, duration_ms=2000),
                BenchmarkResult(case_id="b3", passed=True, score=0.85, duration_ms=1500),
            ),
            regressions=(),
            summary=ReportSummary(
                total_benchmarks=3,
                passed=2,
                failed=1,
                regressions_detected=0,
                overall_score=71.67,
                pass_rate=2 / 3,
            ),
        )
        alerts = create_alerts_from_report(report)
        assert len(alerts) == 1
        assert alerts[0].case_id == "b2"

    def test_regressions_converted_to_alerts(self, sample_report: BenchmarkReport) -> None:
        alerts = create_alerts_from_report(sample_report)
        regression_alerts = [a for a in alerts if "Regression" in a.title]
        assert len(regression_alerts) == 1
        assert regression_alerts[0].title == "Regression: latency_p50"

    def test_combined_failures_and_regressions(self, sample_report: BenchmarkReport) -> None:
        alerts = create_alerts_from_report(sample_report)
        assert len(alerts) == 2  # 1 failed benchmark + 1 regression

    def test_failed_benchmark_metrics(self) -> None:
        report = BenchmarkReport(
            benchmarks=(
                BenchmarkResult(
                    case_id="bench-001",
                    passed=False,
                    score=0.45,
                    duration_ms=3000,
                ),
            ),
            regressions=(),
            summary=ReportSummary(
                total_benchmarks=1,
                passed=0,
                failed=1,
                regressions_detected=0,
                overall_score=45.0,
                pass_rate=0.0,
            ),
        )
        alerts = create_alerts_from_report(report)
        assert alerts[0].metrics["score"] == 0.45
        assert alerts[0].metrics["duration_ms"] == 3000

    @pytest.mark.parametrize("threshold_percent", [5.0, 10.0, 15.0, 20.0])
    def test_regression_alert_with_various_thresholds(self, threshold_percent: float) -> None:
        report = BenchmarkReport(
            benchmarks=(),
            regressions=(
                RegressionAlert(
                    metric_name="latency",
                    previous_value=100.0,
                    current_value=120.0,
                    change_percent=20.0,
                    severity=AlertSeverity.WARNING,
                    threshold_percent=threshold_percent,
                ),
            ),
            summary=ReportSummary(
                total_benchmarks=0,
                passed=0,
                failed=0,
                regressions_detected=1,
                overall_score=0.0,
            ),
        )
        alerts = create_alerts_from_report(report)
        assert len(alerts) == 1
        assert alerts[0].metrics["threshold_percent"] == threshold_percent
