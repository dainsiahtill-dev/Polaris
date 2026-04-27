"""QA Service - Quality Assurance as a Service.

This module implements the QA/Auditor role as a proper service in the Polaris v2 architecture.
Responsible for reviewing code, running tests, and ensuring quality standards.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType
from polaris.kernelone.events.typed import (
    AuditCompleted as TypedAuditCompleted,
    get_default_adapter as get_typed_adapter,
)

logger = logging.getLogger(__name__)


@dataclass
class QAConfig:
    """Configuration for QA Service."""

    workspace: str
    enable_auto_audit: bool = True
    min_test_coverage: float = 0.7


@dataclass
class AuditResult:
    """Result of a quality audit."""

    audit_id: str
    target: str  # file, task, or project
    verdict: str  # PASS, FAIL, NEEDS_REVIEW
    issues: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PathSecurityError(Exception):
    """Raised when a path security violation is detected."""

    pass


class QAService:
    """QA Service - audits code and ensures quality standards.

    Responsibilities:
    - Review code changes
    - Run tests and check coverage
    - Verify acceptance criteria
    - Report quality issues
    - Block/fail tasks that don't meet standards
    """

    # Maximum allowed path length
    _MAX_PATH_LENGTH = 4096

    # Valid filename pattern: no path separators, no null bytes
    _VALID_FILENAME_PATTERN = re.compile(r"^[^\\/\x00]+$")

    def __init__(
        self,
        config: QAConfig,
        message_bus: MessageBus | None = None,
    ) -> None:
        self.config = config
        self._workspace = Path(config.workspace).resolve()
        self._bus = message_bus or MessageBus()
        self._audits: dict[str, AuditResult] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    async def _emit_typed_event(self, event: Any) -> None:
        """Emit a typed event through the adapter if available."""
        adapter = get_typed_adapter()
        if adapter is not None:
            try:
                await adapter.emit_to_both(event)
            except (RuntimeError, ValueError) as exc:
                logger.debug("Failed to emit typed event %s: %s", event.event_name, exc)

    async def start(self) -> None:
        """Start QA Service."""
        self._running = True
        self._task = asyncio.create_task(self._main_loop())

        # Subscribe to relevant messages
        await self._bus.subscribe(MessageType.TASK_COMPLETED, self._on_task_completed)
        await self._bus.subscribe(MessageType.FILE_WRITTEN, self._on_file_written)

        logger.info("[QA Service] Started for workspace: %s", self.config.workspace)

    async def stop(self) -> None:
        """Stop QA Service."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("[QA Service] Stopped")

    def _validate_path(self, filepath: str) -> Path:
        """Validate and sanitize a file path to prevent directory traversal attacks.

        This method ensures that:
        1. The path does not contain null bytes
        2. The path does not contain path traversal sequences (../)
        3. The resolved path is within the workspace directory
        4. The path length is within reasonable limits

        Args:
            filepath: The relative or absolute file path to validate

        Returns:
            Resolved Path object within the workspace

        Raises:
            PathSecurityError: If the path is invalid or attempts path traversal
            ValueError: If the path format is invalid
        """
        if not filepath:
            raise ValueError("File path cannot be empty")

        if len(filepath) > self._MAX_PATH_LENGTH:
            raise ValueError(f"Path exceeds maximum length of {self._MAX_PATH_LENGTH}")

        # Check for null bytes
        if "\x00" in filepath:
            raise PathSecurityError("Path contains null bytes")

        # Normalize the path and resolve to absolute
        try:
            # First, treat as relative to workspace
            full_path = Path(filepath).resolve() if os.path.isabs(filepath) else (self._workspace / filepath).resolve()
        except (OSError, ValueError) as e:
            raise PathSecurityError(f"Invalid path format: {e}") from e

        # Security check: ensure the resolved path is within workspace
        # This prevents path traversal attacks like ../../../etc/passwd
        try:
            full_path.relative_to(self._workspace)
        except ValueError as e:
            raise PathSecurityError(f"Path traversal detected: {filepath!r} resolves outside workspace") from e

        # Additional check: verify the path is a file or doesn't exist yet
        # (we allow non-existent paths for new files)
        if full_path.exists() and full_path.is_dir():
            raise PathSecurityError(f"Path is a directory, not a file: {filepath}")

        return full_path

    def _is_safe_filename(self, filename: str) -> bool:
        """Check if a filename is safe (no path separators).

        Args:
            filename: The filename to check

        Returns:
            True if the filename is safe, False otherwise
        """
        if not filename or filename in (".", ".."):
            return False
        return bool(self._VALID_FILENAME_PATTERN.match(filename))

    async def audit_task(self, task_id: str, task_subject: str, changed_files: list[str]) -> AuditResult:
        """Audit a completed task.

        Args:
            task_id: The task identifier
            task_subject: The task subject/title
            changed_files: List of changed file paths (relative to workspace)

        Returns:
            AuditResult containing the audit verdict and issues found
        """
        logger.info("[QA Service] Auditing task: %s", task_subject)

        issues = []
        valid_files = []
        security_violations = []

        # Validate and filter file paths
        for filepath in changed_files:
            try:
                validated_path = self._validate_path(filepath)
                valid_files.append((filepath, validated_path))
            except (PathSecurityError, ValueError) as e:
                security_violations.append(
                    {
                        "file": filepath,
                        "severity": "error",
                        "message": f"Security violation: {e}",
                    }
                )

        # Add security violations to issues
        issues.extend(security_violations)

        metrics = {
            "files_audited": len(valid_files),
            "files_rejected": len(security_violations),
            "issues_found": 0,
        }

        # Check each validated file
        for filepath, full_path in valid_files:
            if not full_path.exists():
                issues.append(
                    {
                        "file": filepath,
                        "severity": "warning",
                        "message": "File does not exist",
                    }
                )
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                issues.append(
                    {
                        "file": filepath,
                        "severity": "error",
                        "message": f"Failed to read file: {e}",
                    }
                )
                continue

            # Basic checks
            if len(content) == 0:
                issues.append(
                    {
                        "file": filepath,
                        "severity": "error",
                        "message": "File is empty",
                    }
                )

            # Check for common issues
            if filepath.endswith(".py"):
                py_issues = self._check_python_file(filepath, content)
                issues.extend(py_issues)

        metrics["issues_found"] = len(issues)

        # Determine verdict
        errors = [i for i in issues if i.get("severity") == "error"]
        verdict = "PASS" if not errors else "FAIL"

        audit = AuditResult(
            audit_id=f"audit-{len(self._audits) + 1}",
            target=task_id,
            verdict=verdict,
            issues=issues,
            metrics=metrics,
        )
        self._audits[audit.audit_id] = audit

        logger.info("[QA Service] Audit complete: %s (%s issues)", verdict, len(issues))

        # Emit typed event
        typed_event = TypedAuditCompleted.create(
            audit_id=audit.audit_id,
            target=task_id,
            verdict=verdict,
            issue_count=len(issues),
            workspace=str(self._workspace),
        )
        await self._emit_typed_event(typed_event)

        # Broadcast legacy audit result
        await self._bus.broadcast(
            MessageType.AUDIT_COMPLETED,
            "qa",
            {
                "audit_id": audit.audit_id,
                "target": task_id,
                "verdict": verdict,
                "issue_count": len(issues),
            },
        )

        return audit

    async def run_tests(self, test_path: str | None = None) -> dict[str, Any]:
        """Run test suite."""
        logger.info("[QA Service] Running tests...")

        # This would integrate with pytest or other test runners
        # For now, return a placeholder result
        result = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "coverage": 0.0,
        }

        return result

    def _check_python_file(self, filepath: str, content: str) -> list[dict]:
        """Check Python file for common issues."""
        issues = []

        # Check for syntax errors
        try:
            compile(content, filepath, "exec")
        except SyntaxError as e:
            issues.append(
                {
                    "file": filepath,
                    "severity": "error",
                    "message": f"Syntax error: {e}",
                    "line": e.lineno,
                }
            )

        # Check for debt markers (warnings)
        for i, line in enumerate(content.split("\n"), 1):
            if "TODO" in line or "FIXME" in line:
                issues.append(
                    {
                        "file": filepath,
                        "severity": "warning",
                        "message": f"TODO/FIXME found: {line.strip()}",
                        "line": i,
                    }
                )

        return issues

    async def _main_loop(self) -> None:
        """Main processing loop."""
        while self._running:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError) as e:
                logger.error("[QA Service] Error in main loop: %s", e)

    async def _on_task_completed(self, message: Message) -> None:
        """Handle task completion for auto-audit."""
        if not self.config.enable_auto_audit:
            return

        task_id = message.payload.get("task_id")
        task_subject = message.payload.get("subject", "Unknown")
        changed_files = message.payload.get("changed_files", [])

        # Auto-audit the task
        if task_id:
            await self.audit_task(task_id, task_subject, changed_files)

    async def _on_file_written(self, message: Message) -> None:
        """Handle file write notifications."""
        filepath = message.payload.get("path")
        if filepath:
            logger.info("[QA Service] File written: %s", filepath)

    def get_status(self) -> dict[str, Any]:
        """Get QA Service status."""
        return {
            "running": self._running,
            "workspace": str(self._workspace),
            "audits": len(self._audits),
            "audit_ids": list(self._audits.keys()),
        }
