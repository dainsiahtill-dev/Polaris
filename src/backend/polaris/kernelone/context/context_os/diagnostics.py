"""Process-local ContextOS diagnostics registry."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any

_LOCK = Lock()
_LAST_PROJECTION_REPORTS: dict[str, dict[str, Any]] = {}


def record_contextos_projection_report(workspace: str | None, report: Any) -> None:
    """Record the latest projection report for a workspace."""

    if report is None:
        return
    payload = report.to_dict() if hasattr(report, "to_dict") else report
    if not isinstance(payload, dict):
        return
    workspace_key = _workspace_key(workspace)
    with _LOCK:
        _LAST_PROJECTION_REPORTS[workspace_key] = deepcopy(payload)


def get_contextos_diagnostics(workspace: str | None) -> dict[str, Any]:
    """Return side-effect-free ContextOS diagnostics for a workspace."""

    workspace_key = _workspace_key(workspace)
    with _LOCK:
        report = deepcopy(_LAST_PROJECTION_REPORTS.get(workspace_key))
    if report is None:
        return {
            "state": "no_projection",
            "ok": True,
            "details": {"workspace": workspace_key, "last_projection_report": None},
            "evidence": [],
        }
    return {
        "state": "available",
        "ok": True,
        "details": {"workspace": workspace_key, "last_projection_report": report},
        "evidence": ["ContextOS ProjectionReport"],
    }


def reset_contextos_diagnostics() -> None:
    """Clear process-local diagnostics. Intended for tests."""

    with _LOCK:
        _LAST_PROJECTION_REPORTS.clear()


def _workspace_key(workspace: str | None) -> str:
    token = str(workspace or ".").strip()
    if not token:
        token = "."
    try:
        return str(Path(token).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return token


__all__ = [
    "get_contextos_diagnostics",
    "record_contextos_projection_report",
    "reset_contextos_diagnostics",
]
