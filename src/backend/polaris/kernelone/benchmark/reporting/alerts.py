"""Alert Dispatch System for Benchmark Reporting.

This module provides alert handling for CI/CD pipelines,
supporting multiple notification channels and formatting.

Example
-------
    from polaris.kernelone.benchmark.reporting import (
        BenchmarkAlert,
        AlertDispatcher,
        AlertChannel,
    )

    alert = BenchmarkAlert(
        title="Regression Detected",
        description="latency_p50 increased by 15%",
        severity=AlertSeverity.CRITICAL,
        case_id="test_case",
        metrics={"latency_p50": {"previous": 120.0, "current": 138.0}},
    )

    dispatcher = AlertDispatcher()
    dispatcher.register_channel(AlertChannel.SLACK, slack_webhook_url)
    dispatcher.dispatch([alert])
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.reporting.structs import (
    AlertSeverity,
    AlertStatus,
    BenchmarkReport,
    RegressionAlert,
)

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Alert Channel
# ------------------------------------------------------------------


class AlertChannel(Enum):
    """Supported alert notification channels."""

    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    FILE = "file"
    WEBHOOK = "webhook"
    CONSOLE = "console"


# ------------------------------------------------------------------
# Benchmark Alert
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BenchmarkAlert:
    """Benchmark alert for CI/CD notifications.

    Attributes:
        title: Short alert title.
        description: Detailed description.
        severity: Alert severity level.
        case_id: Associated benchmark case (if any).
        metrics: Related metric data.
        timestamp: When alert was created.
        status: Alert lifecycle status.
        url: Optional link to more details.
    """

    title: str
    description: str
    severity: AlertSeverity
    case_id: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: AlertStatus = AlertStatus.TRIGGERED
    url: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            object.__setattr__(self, "severity", AlertSeverity(self.severity))
        if isinstance(self.status, str):
            object.__setattr__(self, "status", AlertStatus(self.status))

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "case_id": self.case_id,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BenchmarkAlert:
        severity = data.get("severity", "warning")
        if isinstance(severity, str):
            severity = AlertSeverity(severity)
        status = data.get("status", "triggered")
        if isinstance(status, str):
            status = AlertStatus(status)
        return cls(
            title=data.get("title", ""),
            description=data.get("description", ""),
            severity=severity,
            case_id=data.get("case_id", ""),
            metrics=data.get("metrics", {}),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            status=status,
            url=data.get("url", ""),
        )


# ------------------------------------------------------------------
# Alert Formatters
# ------------------------------------------------------------------


class SlackFormatter:
    """Slack message formatter for benchmark alerts.

    Formats alerts as Slack Block Kit messages for rich formatting.
    """

    def format(self, alerts: list[BenchmarkAlert], report: BenchmarkReport | None = None) -> dict[str, Any]:
        """Format alerts as Slack message payload.

        Args:
            alerts: List of alerts to format.
            report: Optional report for summary.

        Returns:
            Slack webhook payload.
        """
        blocks: list[dict[str, Any]] = []

        # Header
        if alerts:
            worst = max(alerts, key=lambda a: self._severity_order(a.severity))
            emoji = self._severity_emoji(worst.severity)
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} Benchmark Alert: {worst.title}",
                        "emoji": True,
                    },
                }
            )
        else:
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "Benchmark Report: All Passed",
                        "emoji": True,
                    },
                }
            )

        blocks.append({"type": "divider"})

        # Summary if report provided
        if report:
            summary = report.summary

            blocks.append(
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Total:*\n{summary.total_benchmarks}"},
                        {"type": "mrkdwn", "text": f"*Passed:*\n{summary.passed}"},
                        {"type": "mrkdwn", "text": f"*Failed:*\n{summary.failed}"},
                        {"type": "mrkdwn", "text": f"*Regressions:*\n{summary.regressions_detected}"},
                        {"type": "mrkdwn", "text": f"*Score:*\n{summary.overall_score:.1f}/100"},
                    ],
                }
            )

        blocks.append({"type": "divider"})

        # Individual alerts
        for alert in alerts:
            fields: list[dict[str, str]] = [
                {"type": "mrkdwn", "text": f"*Severity:*\n{alert.severity.value.upper()}"},
            ]

            if alert.case_id:
                fields.append({"type": "mrkdwn", "text": f"*Case:*\n`{alert.case_id}`"})

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*{alert.description}*"},
                    "fields": fields,
                }
            )

        return {"blocks": blocks}

    def _severity_order(self, severity: AlertSeverity) -> int:
        """Return numeric order for severity comparison."""
        return {"critical": 3, "warning": 2, "info": 1}.get(severity.value, 0)

    def _severity_emoji(self, severity: AlertSeverity) -> str:
        """Return emoji for severity."""
        return {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(severity.value, "⚪")

    def _severity_color(self, severity: AlertSeverity) -> str:
        """Return color hex for severity."""
        return {"critical": "#FF0000", "warning": "#FFA500", "info": "#00AAFF"}.get(severity.value, "#AAAAAA")


class TeamsFormatter:
    """Microsoft Teams message formatter for benchmark alerts.

    Formats alerts as Teams Adaptive Cards for rich notifications.
    """

    def format(self, alerts: list[BenchmarkAlert], report: BenchmarkReport | None = None) -> dict[str, Any]:
        """Format alerts as Teams Adaptive Card payload.

        Args:
            alerts: List of alerts to format.
            report: Optional report for summary.

        Returns:
            Teams Adaptive Card payload.
        """
        # Determine overall status
        if not alerts:
            status_text = "All Passed"
        elif any(a.severity == AlertSeverity.CRITICAL for a in alerts):
            status_text = "Critical Alerts"
        else:
            status_text = "Warnings"

        # Build Adaptive Card
        card: dict[str, Any] = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "Container",
                    "style": "emphasis",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "Benchmark Report",
                            "weight": "Bolder",
                            "size": "Large",
                        },
                        {
                            "type": "TextBlock",
                            "text": status_text,
                            "color": "Attention" if alerts else "Good",
                        },
                    ],
                },
            ],
        }

        # Add summary if report provided
        if report:
            summary = report.summary
            card["body"].append(
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "Total", "value": str(summary.total_benchmarks)},
                        {"title": "Passed", "value": str(summary.passed)},
                        {"title": "Failed", "value": str(summary.failed)},
                        {"title": "Regressions", "value": str(summary.regressions_detected)},
                        {"title": "Score", "value": f"{summary.overall_score:.1f}/100"},
                    ],
                }
            )

        # Add alert details
        for alert in alerts:
            card["body"].append(
                {
                    "type": "Container",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": f"{alert.severity.value.upper()}: {alert.title}",
                            "weight": "Bolder",
                            "color": "Attention" if alert.severity == AlertSeverity.CRITICAL else "Warning",
                        },
                        {
                            "type": "TextBlock",
                            "text": alert.description,
                            "wrap": True,
                        },
                    ],
                }
            )

        return card


# ------------------------------------------------------------------
# Alert Dispatcher
# ------------------------------------------------------------------


class AlertDispatcher:
    """Dispatch benchmark alerts to multiple notification channels.

    Supports Slack, Teams, Email, Webhook, File, and Console outputs.

    Example
    -------
        dispatcher = AlertDispatcher()

        # Register channels
        dispatcher.register_channel(AlertChannel.SLACK, slack_url)
        dispatcher.register_channel(AlertChannel.FILE, "alerts.json")

        # Dispatch alerts
        dispatcher.dispatch(alerts)
    """

    def __init__(self) -> None:
        """Initialize the dispatcher."""
        self._channels: dict[AlertChannel, str] = {}
        self._formatters: dict[AlertChannel, object] = {
            AlertChannel.SLACK: SlackFormatter(),
            AlertChannel.TEAMS: TeamsFormatter(),
        }

    def register_channel(self, channel: AlertChannel, config: str) -> None:
        """Register a notification channel.

        Args:
            channel: The channel type.
            config: Channel-specific configuration (URL, path, etc.).
        """
        self._channels[channel] = config

    def unregister_channel(self, channel: AlertChannel) -> bool:
        """Unregister a notification channel.

        Args:
            channel: The channel to remove.

        Returns:
            True if removed, False if not found.
        """
        if channel in self._channels:
            del self._channels[channel]
            return True
        return False

    def dispatch(
        self,
        alerts: list[BenchmarkAlert],
        report: BenchmarkReport | None = None,
    ) -> dict[AlertChannel, bool]:
        """Dispatch alerts to all registered channels.

        Args:
            alerts: List of alerts to dispatch.
            report: Optional report for context.

        Returns:
            Dictionary mapping channel to success status.
        """
        results: dict[AlertChannel, bool] = {}

        for channel, config in self._channels.items():
            success = self._dispatch_to_channel(channel, config, alerts, report)
            results[channel] = success

        return results

    def _dispatch_to_channel(
        self,
        channel: AlertChannel,
        config: str,
        alerts: list[BenchmarkAlert],
        report: BenchmarkReport | None,
    ) -> bool:
        """Dispatch to a single channel."""
        try:
            if channel == AlertChannel.CONSOLE:
                return self._dispatch_console(alerts)
            elif channel == AlertChannel.FILE:
                return self._dispatch_file(config, alerts)
            elif channel == AlertChannel.WEBHOOK:
                return self._dispatch_webhook(config, alerts, report)
            elif channel == AlertChannel.SLACK:
                return self._dispatch_slack(config, alerts, report)
            elif channel == AlertChannel.TEAMS:
                return self._dispatch_teams(config, alerts, report)
            elif channel == AlertChannel.EMAIL:
                return self._dispatch_email(config, alerts)
            return False
        except (RuntimeError, ValueError):
            _logger.debug("Alert dispatch to channel %s failed", channel)
            return False

    def _dispatch_console(self, alerts: list[BenchmarkAlert]) -> bool:
        """Print alerts to console."""
        for alert in alerts:
            emoji = {"critical": "🔴", "warning": "🟡", "info": "ℹ️"}.get(alert.severity.value, "⚪")
            print(f"{emoji} [{alert.severity.value.upper()}] {alert.title}")
            print(f"   {alert.description}")
            if alert.case_id:
                print(f"   Case: {alert.case_id}")
        return True

    def _dispatch_file(self, path: str, alerts: list[BenchmarkAlert]) -> bool:
        """Write alerts to JSON file."""
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alerts": [a.to_dict() for a in alerts],
        }

        path_obj.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True

    def _dispatch_webhook(
        self,
        url: str,
        alerts: list[BenchmarkAlert],
        report: BenchmarkReport | None,
    ) -> bool:
        """Send alerts to a generic webhook."""
        import urllib.request

        payload = {
            "alerts": [a.to_dict() for a in alerts],
            "report": report.to_dict() if report else None,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200

    def _dispatch_slack(
        self,
        webhook_url: str,
        alerts: list[BenchmarkAlert],
        report: BenchmarkReport | None,
    ) -> bool:
        """Send alerts to Slack."""
        import urllib.request

        formatter = self._formatters.get(AlertChannel.SLACK)
        if not formatter:
            return False

        payload = formatter.format(alerts, report)  # type: ignore
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200
        except (RuntimeError, ValueError):
            _logger.debug("Slack webhook dispatch failed for %d alerts", len(alerts))
            return False

    def _dispatch_teams(
        self,
        webhook_url: str,
        alerts: list[BenchmarkAlert],
        report: BenchmarkReport | None,
    ) -> bool:
        """Send alerts to Microsoft Teams."""
        import urllib.request

        formatter = self._formatters.get(AlertChannel.TEAMS)
        if not formatter:
            return False

        payload = formatter.format(alerts, report)  # type: ignore
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status == 200
        except (RuntimeError, ValueError):
            _logger.debug("Teams webhook dispatch failed for %d alerts", len(alerts))
            return False

    def _dispatch_email(self, config: str, alerts: list[BenchmarkAlert]) -> bool:
        """Send alerts via email.

        Config should be in format: smtp://user:pass@host:port?from=addr&to=addr
        """
        import smtplib
        import urllib.parse
        from email.message import EmailMessage

        parsed = urllib.parse.urlparse(config)
        if parsed.scheme != "smtp":
            return False

        # Parse query parameters
        params = dict(urllib.parse.parse_qsl(parsed.query))
        from_addr = params.get("from", "")
        to_addr = params.get("to", "")

        if not from_addr or not to_addr:
            return False

        # Build email
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Subject"] = f"Benchmark Alert: {len(alerts)} issues detected"

        body = "\n".join(f"[{a.severity.value.upper()}] {a.title}\n{a.description}\n" for a in alerts)
        msg.set_content(body)

        # Send email
        host = parsed.hostname or "localhost"
        port = parsed.port or 587
        user = parsed.username or ""
        password = parsed.password or ""

        try:
            with smtplib.SMTP(host, port, timeout=10) as server:
                server.starttls()
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
            return True
        except (RuntimeError, ValueError):
            _logger.debug("Email dispatch failed for %d alerts to %s", len(alerts), host)
            return False


# ------------------------------------------------------------------
# Convenience Functions
# ------------------------------------------------------------------


def create_alerts_from_regressions(
    regressions: list[RegressionAlert],
    case_id: str = "",
) -> list[BenchmarkAlert]:
    """Create BenchmarkAlert objects from RegressionAlert list.

    Args:
        regressions: List of regression alerts.
        case_id: Optional case ID to associate.

    Returns:
        List of BenchmarkAlert objects.
    """
    alerts: list[BenchmarkAlert] = []

    for reg in regressions:
        alert = BenchmarkAlert(
            title=f"Regression: {reg.metric_name}",
            description=reg.message or f"{reg.metric_name} changed by {reg.change_percent:+.1f}%",
            severity=reg.severity,
            case_id=case_id or reg.case_id,
            metrics={
                "metric_name": reg.metric_name,
                "previous_value": reg.previous_value,
                "current_value": reg.current_value,
                "change_percent": reg.change_percent,
                "threshold_percent": reg.threshold_percent,
            },
        )
        alerts.append(alert)

    return alerts


def create_alerts_from_report(report: BenchmarkReport) -> list[BenchmarkAlert]:
    """Create alerts from a complete benchmark report.

    Args:
        report: The benchmark report.

    Returns:
        List of alerts.
    """
    alerts: list[BenchmarkAlert] = []

    # Create alerts for failed benchmarks
    for bench in report.benchmarks:
        if not bench.passed:
            alerts.append(
                BenchmarkAlert(
                    title=f"Failed: {bench.case_id}",
                    description=f"Benchmark failed with score {bench.score:.1%}",
                    severity=AlertSeverity.CRITICAL,
                    case_id=bench.case_id,
                    metrics={"score": bench.score, "duration_ms": bench.duration_ms},
                )
            )

    # Convert regressions to alerts
    alerts.extend(create_alerts_from_regressions(list(report.regressions)))

    return alerts
