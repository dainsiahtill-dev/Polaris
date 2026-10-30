"""Compatibility imports for the retired ``polaris_cli`` module.

The canonical CLI host is :mod:`polaris.delivery.cli.__main__`.  This module
keeps a narrow compatibility surface for historical imports, but it does not
own parser definitions, command dispatch, role execution, or workflow routing.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs.runtime import KernelFileSystem

_DEFAULT_PM_CONTRACTS_FILE = "runtime/contracts/pm_tasks.contract.json"


def create_parser() -> argparse.ArgumentParser:
    """Return the canonical Polaris CLI parser."""
    from polaris.delivery.cli.__main__ import create_parser as canonical_create_parser

    return canonical_create_parser()


def _resolve_workspace(workspace: str) -> str:
    """Resolve a workspace path using the canonical CLI resolver."""
    from polaris.delivery.cli.__main__ import _resolve_workspace as canonical_resolve_workspace

    return str(canonical_resolve_workspace(workspace))


def _ensure_cli_runtime_bindings() -> None:
    """Install canonical CLI runtime bindings for historical callers."""
    from polaris.delivery.cli.__main__ import _bootstrap_runtime

    _bootstrap_runtime()


def _bind_workspace_environment(workspace: str) -> None:
    """Bind workspace environment variables through the canonical CLI helper."""
    from polaris.delivery.cli.__main__ import _bind_workspace_env

    _bind_workspace_env(Path(_resolve_workspace(workspace)))


def _kernel_fs_for_workspace(workspace: str) -> KernelFileSystem:
    """Build a workspace-scoped KernelOne filesystem adapter."""
    return KernelFileSystem(workspace, LocalFileSystemAdapter())


def _default_workflow_run_id() -> str:
    """Return a timestamped workflow run id for compatibility imports."""
    return datetime.now(timezone.utc).strftime("cli-%Y%m%d%H%M%S")


def _read_workspace_json(workspace: str, relative_path: str) -> dict[str, Any]:
    """Read a workspace-relative JSON object with explicit UTF-8 handling."""
    fs = _kernel_fs_for_workspace(workspace)
    logical_path = str(relative_path or "").strip()
    if not logical_path:
        raise SystemExit("--contracts-file is required")
    try:
        raw = fs.workspace_read_text(logical_path, encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Workflow contract file not found: {logical_path}. "
            "Generate PM contracts first or pass --contracts-file explicitly."
        ) from exc
    except ValueError as exc:
        raise SystemExit(f"Unsupported workflow contract path: {logical_path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Workflow contract file is not valid JSON: {logical_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Workflow contract file must contain a JSON object: {logical_path}")
    return payload


def _serialize_workflow_submission(submission: Any) -> dict[str, Any]:
    """Project a workflow submission object into a stable JSON-safe dict."""
    details = getattr(submission, "details", {})
    return {
        "submitted": bool(getattr(submission, "submitted", False)),
        "status": str(getattr(submission, "status", "") or "").strip(),
        "workflow_id": str(getattr(submission, "workflow_id", "") or "").strip(),
        "workflow_run_id": str(getattr(submission, "workflow_run_id", "") or "").strip(),
        "error": str(getattr(submission, "error", "") or "").strip(),
        "details": dict(details) if isinstance(details, dict) else {},
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Compatibility entry point delegated to the canonical CLI host."""
    from polaris.delivery.cli.__main__ import main as canonical_main

    return canonical_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
