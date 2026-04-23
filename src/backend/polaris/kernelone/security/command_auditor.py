"""Structured command audit layer on top of dangerous pattern detection.

This module provides a severity-aware command auditing system that builds on
the canonical pattern detection in ``dangerous_patterns.py``. It maps matched
patterns to severity levels and produces actionable audit results with
configurable block/warn/log thresholds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Final

from polaris.kernelone.security.dangerous_patterns import (
    _DANGEROUS_PATTERNS,
    is_dangerous_command,
)


class SeverityLevel(str, Enum):
    """Severity levels for command audit events."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class CommandAuditEvent:
    """A single matched pattern event during command auditing."""

    timestamp: str
    command_text: str
    matched_pattern: str
    severity: SeverityLevel
    suggested_action: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandAuditResult:
    """Result of auditing a command for dangerous patterns."""

    is_dangerous: bool
    severity: SeverityLevel | None
    events: tuple[CommandAuditEvent, ...]
    recommendation: str


@dataclass(frozen=True)
class AuditConfig:
    """Configuration for command audit thresholds and custom patterns.

    Args:
        block_threshold: Minimum severity that triggers a ``block``
            recommendation. Defaults to ``SeverityLevel.HIGH``.
        warn_threshold: Minimum severity that triggers a ``warn``
            recommendation. Defaults to ``SeverityLevel.MEDIUM``.
        custom_patterns: Additional regex patterns to evaluate alongside
            the canonical patterns. Each tuple is ``(pattern, severity)``.
    """

    block_threshold: SeverityLevel = SeverityLevel.HIGH
    warn_threshold: SeverityLevel = SeverityLevel.MEDIUM
    custom_patterns: tuple[tuple[str, SeverityLevel], ...] = field(
        default_factory=tuple,
    )


# Mapping of canonical dangerous patterns to severity levels.
# Patterns are matched in order; the first match wins.
_SEVERITY_MAP: Final[list[tuple[re.Pattern[str], SeverityLevel]]] = [
    # CRITICAL: filesystem destruction, device wiping
    (re.compile(r"rm\s+-rf\s+[/~]", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"rm\s+-rf\s+\$HOME", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"rm\s+-rf\s+\*", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"rm\s+-rf\s+\.", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"mkfs\.", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"format\s+[a-z]:", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"dd\s+if=/dev/", re.IGNORECASE), SeverityLevel.CRITICAL),
    # HIGH: remote code execution, shell injection
    (re.compile(r"curl.*\|.*sh", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r"wget.*\|.*sh", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r"powershell.*-enc", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r":\(\)\s*\{.*\|.*&.*\}", re.IGNORECASE), SeverityLevel.HIGH),
    # MEDIUM: dynamic code evaluation
    (re.compile(r"eval\s*\(", re.IGNORECASE), SeverityLevel.MEDIUM),
    (re.compile(r"exec\s*\(", re.IGNORECASE), SeverityLevel.MEDIUM),
    (re.compile(r"os\.system", re.IGNORECASE), SeverityLevel.MEDIUM),
    (re.compile(r"subprocess\.call", re.IGNORECASE), SeverityLevel.MEDIUM),
    (re.compile(r"__import__\('os'\)", re.IGNORECASE), SeverityLevel.MEDIUM),
    # LOW: permission changes, sensitive file access
    (re.compile(r"chmod\s+-R\s+777", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"chown\s+-R", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"chmod\s+000", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"/etc/passwd", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"/etc/shadow", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"~/.ssh", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"\.env", re.IGNORECASE), SeverityLevel.LOW),
    (re.compile(r"\.env\.local", re.IGNORECASE), SeverityLevel.LOW),
    # Remaining dangerous patterns default to HIGH if not matched above
    (re.compile(r"rm\s+-rf", re.IGNORECASE), SeverityLevel.CRITICAL),
    (re.compile(r"del\s+/[fqs]\s+", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r"rmdir\s+/[s]", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r"cmd\.exe\s+/c", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r"bash\s+-c", re.IGNORECASE), SeverityLevel.HIGH),
    (re.compile(r"sh\s+-c", re.IGNORECASE), SeverityLevel.HIGH),
]


def _severity_for_match(command: str) -> SeverityLevel | None:
    """Determine the highest severity for a matched pattern in *command*.

    Patterns are evaluated in priority order (CRITICAL first). The first
    match wins so that the most severe classification is returned.
    """
    for pattern, severity in _SEVERITY_MAP:
        if pattern.search(command):
            return severity
    return None


def _all_matched_events(
    command: str,
    custom_patterns: tuple[tuple[str, SeverityLevel], ...],
) -> list[CommandAuditEvent]:
    """Return all matched pattern events for *command*.

    Canonical patterns are evaluated first, followed by any custom patterns
    provided in the audit configuration.
    """
    events: list[CommandAuditEvent] = []
    timestamp = datetime.now(timezone.utc).isoformat()

    # Evaluate canonical patterns with severity mapping
    for raw_pattern in _DANGEROUS_PATTERNS:
        compiled = re.compile(raw_pattern, re.IGNORECASE)
        match = compiled.search(command)
        if match:
            severity = _severity_for_match(command) or SeverityLevel.HIGH
            events.append(
                CommandAuditEvent(
                    timestamp=timestamp,
                    command_text=command,
                    matched_pattern=raw_pattern,
                    severity=severity,
                    suggested_action=_action_for_severity(severity),
                    metadata={"match_span": match.span()},
                ),
            )

    # Evaluate custom patterns
    for raw_pattern, severity in custom_patterns:
        compiled = re.compile(raw_pattern, re.IGNORECASE)
        match = compiled.search(command)
        if match:
            events.append(
                CommandAuditEvent(
                    timestamp=timestamp,
                    command_text=command,
                    matched_pattern=raw_pattern,
                    severity=severity,
                    suggested_action=_action_for_severity(severity),
                    metadata={
                        "match_span": match.span(),
                        "custom": True,
                    },
                ),
            )

    return events


def _action_for_severity(severity: SeverityLevel) -> str:
    """Return the default suggested action for a given severity."""
    if severity == SeverityLevel.CRITICAL:
        return "block"
    if severity == SeverityLevel.HIGH:
        return "block"
    if severity == SeverityLevel.MEDIUM:
        return "warn"
    return "log"


def _recommendation_for_result(
    max_severity: SeverityLevel | None,
    config: AuditConfig,
) -> str:
    """Determine the final recommendation based on severity and config."""
    if max_severity is None:
        return "log"

    severity_order = [
        SeverityLevel.LOW,
        SeverityLevel.MEDIUM,
        SeverityLevel.HIGH,
        SeverityLevel.CRITICAL,
    ]

    max_index = severity_order.index(max_severity)
    block_index = severity_order.index(config.block_threshold)
    warn_index = severity_order.index(config.warn_threshold)

    if max_index >= block_index:
        return "block"
    if max_index >= warn_index:
        return "warn"
    return "log"


class CommandAuditor:
    """Audits shell commands and code snippets for dangerous patterns.

    Usage::

        auditor = CommandAuditor()
        result = auditor.audit("rm -rf /")
        assert result.recommendation == "block"
    """

    def audit(
        self,
        command: str,
        config: AuditConfig | None = None,
    ) -> CommandAuditResult:
        """Audit a command for dangerous patterns.

        Args:
            command: The command string or code snippet to audit.
            config: Optional audit configuration. Uses defaults if omitted.

        Returns:
            A ``CommandAuditResult`` containing danger assessment,
            severity, matched events, and a recommendation.
        """
        cfg = config or AuditConfig()

        # Delegate to canonical detection as required
        dangerous = is_dangerous_command(command)

        events = _all_matched_events(command, cfg.custom_patterns)

        if not dangerous and not events:
            return CommandAuditResult(
                is_dangerous=False,
                severity=None,
                events=(),
                recommendation="log",
            )

        if not events:
            # is_dangerous_command detected something but no specific
            # pattern matched (edge case). Treat as HIGH.
            max_severity: SeverityLevel | None = SeverityLevel.HIGH
        else:
            severity_order = {
                SeverityLevel.LOW: 1,
                SeverityLevel.MEDIUM: 2,
                SeverityLevel.HIGH: 3,
                SeverityLevel.CRITICAL: 4,
            }
            max_severity = max(
                events,
                key=lambda e: severity_order[e.severity],
            ).severity

        recommendation = _recommendation_for_result(max_severity, cfg)

        return CommandAuditResult(
            is_dangerous=True,
            severity=max_severity,
            events=tuple(events),
            recommendation=recommendation,
        )


__all__ = [
    "AuditConfig",
    "CommandAuditEvent",
    "CommandAuditResult",
    "CommandAuditor",
    "SeverityLevel",
]
