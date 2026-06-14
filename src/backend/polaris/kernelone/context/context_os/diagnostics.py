"""Process-local ContextOS diagnostics registry."""

from __future__ import annotations

import hashlib
import json
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
            "details": {
                "workspace": workspace_key,
                "last_projection_report": None,
                "projection_report_digest": "",
                "receipt_refs": [],
                "receipt_ref_count": 0,
                "replay_command": "python -m polaris.delivery.cli.tools.contextos_replay --workspace <workspace>",
                "replay_ready": False,
            },
            "evidence": [],
        }
    receipt_refs = _collect_receipt_refs(report)
    projection_digest = _stable_digest(report)
    latest_projection_id = str(report.get("projection_id") or "").strip()
    latest_run_id = str(report.get("run_id") or "").strip()
    latest_turn_id = str(report.get("turn_id") or "").strip()
    replay_ready = bool(projection_digest and latest_projection_id and latest_run_id and latest_turn_id)
    return {
        "state": "available",
        "ok": True,
        "details": {
            "workspace": workspace_key,
            "last_projection_report": report,
            "projection_report_digest": projection_digest,
            "latest_projection_id": latest_projection_id,
            "latest_run_id": latest_run_id,
            "latest_turn_id": latest_turn_id,
            "receipt_refs": receipt_refs,
            "receipt_ref_count": len(receipt_refs),
            "replay_command": f"python -m polaris.delivery.cli.tools.contextos_replay --workspace {workspace_key}",
            "replay_ready": replay_ready,
        },
        "evidence": ["ContextOS ProjectionReport", "contextos_replay CLI"],
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


def _stable_digest(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(payload).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def _collect_receipt_refs(payload: Any) -> list[str]:
    refs: set[str] = set()
    canonical_keys = {"receipt_refs", "runtime_receipt_refs", "cognitive_runtime_receipt_ids"}

    def _visit(value: Any, *, canonical: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                _visit(child, canonical=canonical or key in canonical_keys)
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                _visit(child, canonical=canonical)
            return
        if canonical and isinstance(value, str):
            token = value.strip()
            if token:
                refs.add(token)

    _visit(payload)
    return sorted(refs)


__all__ = [
    "get_contextos_diagnostics",
    "record_contextos_projection_report",
    "reset_contextos_diagnostics",
]
