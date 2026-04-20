#!/usr/bin/env python3
"""Audit Agent Toolkit - unified audit helpers for agent callers.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.cells.audit.diagnosis.public import resolve_runtime_root, run_audit_command, to_legacy_result

    return resolve_runtime_root, run_audit_command, to_legacy_result


logger = logging.getLogger(__name__)

_CLI_BINDINGS_READY = False


def _ensure_cli_bindings() -> None:
    """Install minimal Polaris bindings for standalone audit commands."""
    global _CLI_BINDINGS_READY
    if _CLI_BINDINGS_READY:
        return
    from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

    ensure_minimal_kernelone_bindings()
    _CLI_BINDINGS_READY = True


@dataclass
class AuditContext:
    """Audit execution context with runtime auto-resolution."""

    workspace: str = "."
    runtime_root: str | None = None
    mode: str = "auto"
    base_url: str | None = None

    def __post_init__(self) -> None:
        resolve_runtime_root, _, _ = _bootstrap_backend_import_path()
        if self.runtime_root is None:
            resolved = resolve_runtime_root(
                runtime_root=None,
                workspace=self.workspace,
                base_url=self.base_url,
            )
            self.runtime_root = resolved

    def is_offline_available(self) -> bool:
        """Check if offline audit data is available."""
        if not self.runtime_root:
            return False
        runtime_path = Path(self.runtime_root)
        offline_marker = runtime_path / "audit" / ".offline_available"
        return offline_marker.exists()

    def get_audit_root(self) -> str:
        """Get the audit data root directory."""
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        if self.runtime_root:
            return str(Path(self.runtime_root) / "audit")
        return str(Path.cwd() / get_workspace_metadata_dir_name() / "audit")


def get_events(
    workspace: str = ".",
    event_types: list[str] | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get audit events with optional filtering.

    Args:
        workspace: Workspace directory
        event_types: Filter by event types (e.g., ['tool_call', 'tool_result'])
        limit: Maximum number of events to return

    Returns:
        List of audit event dictionaries
    """
    ctx = AuditContext(workspace=workspace)
    _ensure_cli_bindings()
    _, run_audit_command, _ = _bootstrap_backend_import_path()

    result = run_audit_command(
        command="query",
        workspace=workspace,
        event_types=event_types or [],
        limit=limit,
    )
    return result.get("events", [])


def get_stats(workspace: str = ".") -> dict[str, Any]:
    """Get audit statistics.

    Args:
        workspace: Workspace directory

    Returns:
        Dictionary with audit statistics
    """
    ctx = AuditContext(workspace=workspace)
    _ensure_cli_bindings()
    _, run_audit_command, _ = _bootstrap_backend_import_path()

    result = run_audit_command(
        command="stats",
        workspace=workspace,
    )
    return result


def triage(workspace: str = ".") -> dict[str, Any]:
    """Run audit triage to identify issues.

    Args:
        workspace: Workspace directory

    Returns:
        Dictionary with triage results
    """
    ctx = AuditContext(workspace=workspace)
    _ensure_cli_bindings()
    _, run_audit_command, _ = _bootstrap_backend_import_path()

    result = run_audit_command(
        command="triage",
        workspace=workspace,
    )
    return result


def verify(workspace: str = ".") -> dict[str, Any]:
    """Verify audit chain integrity.

    Args:
        workspace: Workspace directory

    Returns:
        Dictionary with verification results
    """
    ctx = AuditContext(workspace=workspace)
    _ensure_cli_bindings()
    _, run_audit_command, _ = _bootstrap_backend_import_path()

    result = run_audit_command(
        command="verify-chain",
        workspace=workspace,
    )
    return result


def get_corruption_log(
    workspace: str = ".",
    since: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get corruption log entries from audit events.

    Args:
        workspace: Workspace directory
        since: Time window (e.g., "24h", "7d")
        limit: Maximum number of entries to return

    Returns:
        List of corruption log entries
    """
    ctx = AuditContext(workspace=workspace)
    _ensure_cli_bindings()
    _, run_audit_command, _ = _bootstrap_backend_import_path()

    result = run_audit_command(
        command="corruption-log",
        workspace=workspace,
        since=since,
        limit=limit,
    )
    return result.get("entries", [])
