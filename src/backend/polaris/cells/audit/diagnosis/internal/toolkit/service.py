"""Unified audit command service for CLI and agents.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。

Refactoring note
----------------
The original ``_run_offline_command`` and ``_run_online_command`` functions were
200-line if-elif chains that both had to be modified whenever a new command was
added.  They have been replaced by two command-handler registries:

    _OFFLINE_HANDLERS: Dict[str, Callable] — one entry per offline command
    _ONLINE_HANDLERS:  Dict[str, Callable] — one entry per online command

Each handler is a focused private function that receives ``(params, *, ...)``
and returns a data-only payload (not wrapped in an envelope).  The dispatcher
wraps the payload and handles all cross-cutting concerns (error envelopes,
mode selection, exception mapping) in one place.

Adding a new command now requires:
1.  Adding its name to SUPPORTED_COMMANDS.
2.  Implementing one ``_offline_<cmd>`` and/or ``_online_<cmd>`` function.
3.  Registering it in ``_OFFLINE_HANDLERS`` / ``_ONLINE_HANDLERS``.

No changes to ``run_audit_command`` or the envelope helpers are needed.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from polaris.cells.audit.diagnosis.internal.diagnosis_engine import AuditDiagnosisEngine
from polaris.cells.audit.diagnosis.internal.usecases import AuditUseCaseFacade
from polaris.kernelone.audit import KernelAuditEventType, validate_run_id
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.utils.time_utils import utc_now_iso

from .hops import build_failure_hops, load_failure_hops
from .triage import build_triage_bundle

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2.1"
SUPPORTED_MODES = {"auto", "online", "offline"}
SUPPORTED_COMMANDS = {
    "triage",
    "hops",
    "diagnose",
    "scan",
    "check-region",
    "trace",
    "verify-chain",
    "tail",
    "export",
    "corruption",
    "stats",
}


# ═══════════════════════════════════════════════════════════════════════════════
# Pure utility helpers (no side effects, easily unit-tested)
# ═══════════════════════════════════════════════════════════════════════════════


# Backward compatibility alias
_utc_now_iso = utc_now_iso


def _error_item(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": str(code or "unknown_error"),
        "message": str(message or "").strip() or "unknown error",
    }
    if details:
        payload["details"] = details
    return payload


def _build_envelope(
    *,
    command: str,
    status: str,
    mode: str,
    data: Any,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": status,
        "mode": mode,
        "generated_at": _utc_now_iso(),
        "data": data,
        "errors": errors or [],
    }


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _normalize_status(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"ok", "success"}:
        return "success"
    if token in {"not_found", "missing"}:
        return "not_found"
    if token in {"partial"}:
        return "partial"
    if token in {"error", "failed", "failure"}:
        return "error"
    return "success"


def _coerce_total_events(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("total_events") or 0)
    except (TypeError, ValueError):
        return 0


def _augment_verify_chain_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    total_events = _coerce_total_events(normalized)
    has_events = total_events > 0
    normalized["has_events"] = has_events
    normalized["empty_chain"] = not has_events
    return normalized


def _build_verify_chain_strict_error(
    *,
    command: str,
    mode: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    total_events = _coerce_total_events(payload)
    return _build_envelope(
        command=command,
        status="error",
        mode=mode,
        data=payload,
        errors=[
            _error_item(
                "insufficient_audit_data",
                "strict_non_empty requires at least one audit event",
                {"total_events": total_events},
            )
        ],
    )


def _resolve_backend_base_url(base_url: str | None = None) -> str:
    if base_url and str(base_url).strip():
        return str(base_url).strip().rstrip("/")
    port = os.environ.get("KERNELONE_BACKEND_PORT", "49977")
    return f"http://127.0.0.1:{port}"


def _resolve_runtime_root_from_backend(base_url: str) -> Path | None:
    """Try backend runtime layout endpoint as a non-authoritative hint."""
    token = str(os.environ.get("KERNELONE_BACKEND_TOKEN") or os.environ.get("KERNELONE_TOKEN") or "").strip()
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"{base_url}/runtime/storage-layout",
            headers=headers,
            timeout=3,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        runtime_root = str(payload.get("runtime_root") or "").strip()
        if not runtime_root:
            return None
        return Path(runtime_root).resolve()
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to resolve runtime_root from backend /status: %s", exc)
        return None


def resolve_runtime_root(
    *,
    runtime_root: Path | str | None = None,
    workspace: str | None = None,
    base_url: str | None = None,
) -> Path | None:
    """Resolve runtime root with strict precedence."""
    if runtime_root:
        return Path(runtime_root).resolve()

    env_root = str(os.environ.get("KERNELONE_RUNTIME_BASE") or "").strip()
    if env_root:
        return Path(env_root).resolve()

    backend_base = _resolve_backend_base_url(base_url)
    from_backend = _resolve_runtime_root_from_backend(backend_base)
    if from_backend:
        return from_backend

    workspace_path = str(workspace or os.environ.get("KERNELONE_WORKSPACE") or os.getcwd())
    try:
        roots = resolve_storage_roots(workspace_path)
        return Path(roots.runtime_root).resolve()
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to resolve runtime_root via storage roots for workspace=%s: %s", workspace_path, exc)
        return None


def _parse_iso8601(value: str | None) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    return datetime.fromisoformat(token.replace("Z", "+00:00"))


def _resolve_failure_hops_events_path(runtime_root: Path) -> Path | None:
    candidates = (
        runtime_root / "events" / "runtime.events.jsonl",
        runtime_root / "events" / "events.jsonl",
        runtime_root / "events.jsonl",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _discover_journal_run_dirs(runtime_root: Path) -> list[Path]:
    """Discover all journal run directories under runtime_root.

    Returns run directories sorted by mtime descending (newest first).
    """
    runs_root = runtime_root / "runs"
    if not runs_root.is_dir():
        return []
    dirs: list[tuple[float, Path]] = []
    for d in runs_root.iterdir():
        if not d.is_dir():
            continue
        try:
            mtime = d.stat().st_mtime
        except OSError:
            mtime = 0.0
        dirs.append((mtime, d))
    dirs.sort(key=lambda x: x[0], reverse=True)
    return [d for _, d in dirs]


def _resolve_journal_events_path(run_dir: Path) -> Path | None:
    """Resolve the normalized journal events file for a run directory.

    Checks preference order: norm > enriched > raw.
    """
    logs_dir = run_dir / "logs"
    if not logs_dir.is_dir():
        return None
    candidates = [
        logs_dir / "journal.norm.jsonl",
        logs_dir / "journal.enriched.jsonl",
        logs_dir / "journal.raw.jsonl",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def load_journal_events(run_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Load the most recent *limit* events from a journal file.

    Returns events in ascending time order.
    """
    journal_path = _resolve_journal_events_path(run_dir)
    if journal_path is None or not journal_path.exists():
        return []
    all_events: list[dict[str, Any]] = []
    try:
        with open(journal_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    all_events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    # Return the most recent *limit* events
    start = max(0, len(all_events) - limit)
    return all_events[start:]


def discover_strategy_receipts(runtime_root: Path) -> list[Path]:
    """Discover all strategy receipt JSON files under runtime_root."""
    receipts_root = runtime_root / "strategy_runs"
    if not receipts_root.is_dir():
        return []
    receipts: list[tuple[float, Path]] = []
    for p in receipts_root.glob("*.json"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        receipts.append((mtime, p))
    receipts.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in receipts]


def _normalize_hops_payload(payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "schema_version": int(payload.get("schema_version", 2) or 2),
        "run_id": str(payload.get("run_id") or run_id),
        "generated_at": str(payload.get("generated_at") or _utc_now_iso()),
        "ready": bool(payload.get("ready", False)),
        "has_failure": bool(payload.get("has_failure", False)),
        "failure_code": str(payload.get("failure_code") or ""),
        "failure_event_seq": payload.get("failure_event_seq"),
        "hop1_phase": payload.get("hop1_phase"),
        "hop2_evidence": payload.get("hop2_evidence"),
        "hop3_tool_output": payload.get("hop3_tool_output"),
    }


def _coerce_int(value: Any, *, minimum: int = 0, maximum: int = 10_000) -> int:
    """Coerce *value* to an int clamped within [minimum, maximum].

    Raises:
        ValueError: if *value* cannot be converted to an integer.
            Propagates intentionally so the caller can surface a structured
            ``invalid_input`` error envelope rather than silently using a
            default that would produce incorrect audit output.
    """
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected an integer, got {type(value).__name__!r}: {value!r}") from exc
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _parse_line_range(value: Any) -> tuple[int, int] | None:
    token = str(value or "").strip()
    if not token:
        return None
    parts = token.split("-", 1)
    if len(parts) != 2:
        raise ValueError("lines must be in <start>-<end> format")
    start = _coerce_int(parts[0], minimum=1)
    end = _coerce_int(parts[1], minimum=start)
    return (start, end)


def _build_diagnosis_engine(runtime_root: Path, workspace: str) -> AuditDiagnosisEngine:
    return AuditDiagnosisEngine(runtime_root=runtime_root, workspace=workspace)


def _get_audit_facade(runtime_root: Path) -> AuditUseCaseFacade:
    return AuditUseCaseFacade(runtime_root=runtime_root)


def _parse_event_type(value: str | None) -> KernelAuditEventType | None:
    token = str(value or "").strip()
    if not token:
        return None
    return KernelAuditEventType(token)


def _parse_event_types(value: Any) -> list[KernelAuditEventType] | None:
    token = str(value or "").strip()
    if not token:
        return None
    return [KernelAuditEventType(part.strip()) for part in token.split(",") if part.strip()]


# ═══════════════════════════════════════════════════════════════════════════════
# Offline command handlers
# Each returns a (status, data) tuple: status is a _normalize_status token,
# data is a plain dict that the dispatcher wraps in an envelope.
# Raise ValueError for user-visible bad-input problems.
# ═══════════════════════════════════════════════════════════════════════════════


def _offline_triage(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    bundle = build_triage_bundle(
        workspace=workspace,
        run_id=params.get("run_id"),
        task_id=params.get("task_id"),
        trace_id=params.get("trace_id"),
        runtime_root=runtime_root,
    )
    return _normalize_status(bundle.get("status")), bundle


def _offline_hops(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    run_id = str(params.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    if not validate_run_id(run_id):
        raise ValueError(f"invalid run_id format: {run_id}")

    loaded = load_failure_hops(str(runtime_root), run_id)
    if loaded:
        return "success", _normalize_hops_payload(loaded, run_id)

    events_path = _resolve_failure_hops_events_path(runtime_root)
    if not events_path:
        return "not_found", {}

    built = build_failure_hops(
        str(events_path),
        run_id=run_id,
        event_seq_start=0,
        event_seq_end=0,
    )
    if str(built.get("failure_code") or "").strip().lower() == "build_error":
        raise RuntimeError(f"Failed to build failure hops from events stream: {events_path}")
    return "success", _normalize_hops_payload(built, run_id)


def _offline_diagnose(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    if not (params.get("run_id") or params.get("task_id") or params.get("error_message")):
        raise ValueError("run_id, task_id, or error_message is required")
    engine = _build_diagnosis_engine(runtime_root, workspace)
    payload = engine.analyze_failure(
        run_id=params.get("run_id"),
        task_id=params.get("task_id"),
        error_hint=params.get("error_message"),
        time_range=str(params.get("time_range") or "1h"),
        depth=_coerce_int(params.get("depth") or 3, minimum=1, maximum=3),
    )
    return "success", payload


def _offline_scan(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    engine = _build_diagnosis_engine(runtime_root, workspace)
    payload = engine.scan_project(
        scope=str(params.get("scope") or "full"),
        focus=str(params.get("focus") or "").strip() or None,
        max_files=_coerce_int(params.get("max_files") or 800, minimum=1, maximum=5000),
        max_findings=_coerce_int(params.get("max_findings") or 300, minimum=1, maximum=2000),
    )
    return "success", payload


def _offline_check_region(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    if not (params.get("file_path") or params.get("function_name")):
        raise ValueError("file_path or function_name is required")
    engine = _build_diagnosis_engine(runtime_root, workspace)
    payload = engine.check_region(
        file_path=params.get("file_path"),
        function_name=params.get("function_name"),
        line_range=_parse_line_range(params.get("lines")),
    )
    return "success", payload


def _offline_trace(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    trace_id = str(params.get("trace_id") or "").strip()
    if not trace_id:
        raise ValueError("trace_id is required")
    engine = _build_diagnosis_engine(runtime_root, workspace)
    payload = engine.get_trace(
        trace_id=trace_id,
        limit=_coerce_int(params.get("limit") or 300, minimum=1, maximum=2000),
    )
    status = "success" if int(payload.get("event_count") or 0) > 0 else "not_found"
    return status, payload


def _offline_verify_chain(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    facade = _get_audit_facade(runtime_root)
    result = _augment_verify_chain_payload(facade.verify_chain())
    return "success", result  # strict_non_empty handled by dispatcher post-step


def _offline_tail(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    limit = max(1, int(params.get("limit") or 50))
    failure_only = _coerce_bool(params.get("failure_only"), False)
    event_type = "task_failed" if failure_only else str(params.get("event_type") or "").strip() or None
    facade = _get_audit_facade(runtime_root)
    event_type_enum = _parse_event_type(event_type)
    events = [
        evt.to_dict()
        for evt in facade.query_logs(
            event_type=event_type_enum,
            limit=max(1, int(limit or 50)),
            offset=0,
        )
    ]
    events.sort(key=lambda row: str(row.get("timestamp") or ""))
    return "success", {"events": events, "count": len(events)}


def _offline_export(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    facade = _get_audit_facade(runtime_root)
    export_format = str(params.get("format") or "json").strip().lower()
    start_dt = _parse_iso8601(params.get("start_time"))
    end_dt = _parse_iso8601(params.get("end_time"))
    include_data = _coerce_bool(params.get("include_data"), default=True)
    event_types = _parse_event_types(params.get("event_types"))

    if export_format == "json":
        content = facade.export_json(
            start_time=start_dt,
            end_time=end_dt,
            event_types=event_types,  # type: ignore[arg-type]
            include_data=include_data,
        )
        return "success", {"format": "json", "content": content}

    if export_format != "csv":
        raise ValueError("Unsupported export format. Use json or csv.")

    events = facade.query_logs(
        start_time=start_dt,
        end_time=end_dt,
        limit=10_000,
        offset=0,
    )
    if event_types:
        allowed = {item.value for item in event_types}
        events = [evt for evt in events if evt.event_type.value in allowed]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["event_id", "timestamp", "event_type", "role", "task_id", "resource_path", "operation", "result"])
    for event in events:
        writer.writerow(
            [
                event.event_id,
                event.timestamp.isoformat(),
                event.event_type.value,
                event.source.get("role", ""),
                event.task.get("task_id", ""),
                event.resource.get("path", ""),
                event.resource.get("operation", ""),
                event.action.get("result", ""),
            ]
        )

    return "success", {"format": "csv", "content": buffer.getvalue()}


def _offline_corruption(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    limit = max(1, int(params.get("limit") or 100))
    facade = _get_audit_facade(runtime_root)
    records = facade.get_corruption_log(workspace=workspace, limit=limit)
    return "success", {"records": records, "count": len(records)}


def _offline_stats(
    params: dict[str, Any],
    *,
    runtime_root: Path,
    workspace: str,
) -> tuple[str, dict[str, Any]]:
    start_dt = _parse_iso8601(params.get("start_time"))
    end_dt = _parse_iso8601(params.get("end_time"))
    facade = _get_audit_facade(runtime_root)
    stats = facade.get_stats(start_time=start_dt, end_time=end_dt)
    legacy = {
        "total_events": stats.get("total_events", 0),
        "event_types": stats.get("by_type", {}),
        "sources": stats.get("by_role", {}),
    }
    return "success", {
        "stats": stats,
        "time_range": {
            "start": params.get("start_time"),
            "end": params.get("end_time"),
        },
        "legacy": legacy,
    }


# Registry: command name -> handler callable
# Handler signature: (params, *, runtime_root, workspace) -> (status, data)
_OfflineHandler = Callable[..., tuple[str, dict[str, Any]]]

_OFFLINE_HANDLERS: dict[str, _OfflineHandler] = {
    "triage": _offline_triage,
    "hops": _offline_hops,
    "diagnose": _offline_diagnose,
    "scan": _offline_scan,
    "check-region": _offline_check_region,
    "trace": _offline_trace,
    "verify-chain": _offline_verify_chain,
    "tail": _offline_tail,
    "export": _offline_export,
    "corruption": _offline_corruption,
    "stats": _offline_stats,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Online command handlers
# Each returns a requests.Response; the dispatcher converts to an envelope.
# Raise ValueError for user-visible bad-input problems.
# ═══════════════════════════════════════════════════════════════════════════════


def _online_triage(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    request_payload = {k: params[k] for k in ("run_id", "task_id", "trace_id") if params.get(k)}
    resp = requests.post(f"{base_url}/v2/audit/triage", json=request_payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return {"status": _normalize_status(data.get("status")), "data": data}


def _online_hops(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    run_id = str(params.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("run_id is required")
    resp = requests.get(f"{base_url}/v2/audit/failures/{run_id}/hops", timeout=15)
    if resp.status_code == 404:
        return {"status": "not_found", "data": {}}
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}


def _online_diagnose(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "run_id": params.get("run_id"),
        "task_id": params.get("task_id"),
        "error_message": params.get("error_message"),
        "time_range": params.get("time_range") or "1h",
        "depth": _coerce_int(params.get("depth") or 3, minimum=1, maximum=3),
    }
    if not (request_payload.get("run_id") or request_payload.get("task_id") or request_payload.get("error_message")):
        raise ValueError("run_id, task_id, or error_message is required")
    resp = requests.post(f"{base_url}/v2/audit/analyze-failure", json=request_payload, timeout=30)
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}


def _online_scan(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    request_payload = {
        "scope": str(params.get("scope") or "full"),
        "focus": params.get("focus"),
        "max_files": _coerce_int(params.get("max_files") or 800, minimum=1, maximum=5000),
        "max_findings": _coerce_int(params.get("max_findings") or 300, minimum=1, maximum=2000),
    }
    resp = requests.post(f"{base_url}/v2/audit/scan-project", json=request_payload, timeout=60)
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}


def _online_check_region(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    request_payload = {
        "file_path": params.get("file_path"),
        "function_name": params.get("function_name"),
        "lines": params.get("lines"),
    }
    if not (request_payload.get("file_path") or request_payload.get("function_name")):
        raise ValueError("file_path or function_name is required")
    resp = requests.post(f"{base_url}/v2/audit/check-region", json=request_payload, timeout=30)
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}


def _online_trace(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    trace_id = str(params.get("trace_id") or "").strip()
    if not trace_id:
        raise ValueError("trace_id is required")
    query = {"limit": _coerce_int(params.get("limit") or 300, minimum=1, maximum=2000)}
    resp = requests.get(f"{base_url}/v2/audit/trace/{trace_id}", params=query, timeout=15)
    if resp.status_code == 404:
        return {"status": "not_found", "data": {}}
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}


def _online_verify_chain(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    resp = requests.get(f"{base_url}/v2/audit/verify", timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        payload = {}
    result = _augment_verify_chain_payload(payload)
    return {"status": "success", "data": result}


def _online_tail(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    query: dict[str, Any] = {"limit": max(1, int(params.get("limit") or 50))}
    if _coerce_bool(params.get("failure_only"), False):
        query["event_type"] = "task_failed"
    else:
        event_type = str(params.get("event_type") or "").strip()
        if event_type:
            query["event_type"] = event_type
    resp = requests.get(f"{base_url}/v2/audit/logs", params=query, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    events = payload.get("events", []) if isinstance(payload, dict) else []
    return {
        "status": "success",
        "data": {"events": events, "count": len(events), "pagination": payload.get("pagination", {})},
    }


def _online_export(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "format": str(params.get("format") or "json").strip().lower(),
        "include_data": _coerce_bool(params.get("include_data"), True),
    }
    for key in ("start_time", "end_time", "event_types"):
        value = params.get(key)
        if value:
            query[key] = value
    resp = requests.get(f"{base_url}/v2/audit/export", params=query, timeout=60)
    resp.raise_for_status()
    export_format = query["format"]
    if export_format == "json":
        try:
            content: Any = resp.json()
        except ValueError:
            content = json.loads(resp.text)
    else:
        content = resp.text
    return {"status": "success", "data": {"format": export_format, "content": content}}


def _online_corruption(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    query = {"limit": max(1, int(params.get("limit") or 100))}
    resp = requests.get(f"{base_url}/v2/audit/corruption", params=query, timeout=15)
    resp.raise_for_status()
    records = resp.json()
    return {"status": "success", "data": {"records": records, "count": len(records)}}


def _online_stats(
    params: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    for key in ("start_time", "end_time"):
        value = params.get(key)
        if value:
            query[key] = value
    resp = requests.get(f"{base_url}/v2/audit/stats", params=query, timeout=15)
    resp.raise_for_status()
    return {"status": "success", "data": resp.json()}


# Registry: command name -> handler callable
# Handler signature: (params, *, base_url) -> {"status": str, "data": dict}
_OnlineHandler = Callable[..., dict[str, Any]]

_ONLINE_HANDLERS: dict[str, _OnlineHandler] = {
    "triage": _online_triage,
    "hops": _online_hops,
    "diagnose": _online_diagnose,
    "scan": _online_scan,
    "check-region": _online_check_region,
    "trace": _online_trace,
    "verify-chain": _online_verify_chain,
    "tail": _online_tail,
    "export": _online_export,
    "corruption": _online_corruption,
    "stats": _online_stats,
}


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatchers — thin wrappers that call the registry and build envelopes
# ═══════════════════════════════════════════════════════════════════════════════


def _api_error_to_item(exc: requests.RequestException) -> dict[str, Any]:
    response = getattr(exc, "response", None)
    if response is None:
        return _error_item("api_error", str(exc))
    detail: Any = ""
    try:
        payload = response.json()
        detail = payload.get("detail") or payload if isinstance(payload, dict) else payload
    except ValueError:
        detail = response.text
    return _error_item(
        "api_error",
        f"HTTP {response.status_code}",
        {"detail": detail},
    )


def _run_offline_command(
    *,
    command: str,
    params: dict[str, Any],
    runtime_root: Path,
    workspace: str,
) -> dict[str, Any]:
    handler = _OFFLINE_HANDLERS.get(command)
    if handler is None:
        return _build_envelope(
            command=command,
            status="error",
            mode="offline",
            data={},
            errors=[_error_item("unsupported_command", f"Unsupported command: {command}")],
        )

    status, data = handler(params, runtime_root=runtime_root, workspace=workspace)

    # Post-step: verify-chain strict_non_empty check
    if command == "verify-chain":
        strict_non_empty = _coerce_bool(params.get("strict_non_empty"), False)
        if strict_non_empty and bool(data.get("empty_chain")):
            return _build_verify_chain_strict_error(command=command, mode="offline", payload=data)

    return _build_envelope(command=command, status=status, mode="offline", data=data)


def _run_online_command(
    *,
    command: str,
    params: dict[str, Any],
    base_url: str,
) -> dict[str, Any]:
    handler = _ONLINE_HANDLERS.get(command)
    if handler is None:
        return _build_envelope(
            command=command,
            status="error",
            mode="online",
            data={},
            errors=[_error_item("unsupported_command", f"Unsupported command: {command}")],
        )

    result = handler(params, base_url=base_url)
    status = result["status"]
    data = result["data"]

    # Post-step: verify-chain strict_non_empty check
    if command == "verify-chain":
        strict_non_empty = _coerce_bool(params.get("strict_non_empty"), False)
        if strict_non_empty and bool(data.get("empty_chain")):
            return _build_verify_chain_strict_error(command=command, mode="online", payload=data)

    return _build_envelope(command=command, status=status, mode="online", data=data)


# ═══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════════════


def run_audit_command(
    command: str,
    *,
    params: dict[str, Any] | None = None,
    mode: str = "auto",
    runtime_root: Path | str | None = None,
    workspace: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Run audit command with consistent contract across modes."""
    normalized_command = str(command or "").strip().lower()
    if normalized_command not in SUPPORTED_COMMANDS:
        return _build_envelope(
            command=normalized_command or "unknown",
            status="error",
            mode="unknown",
            data={},
            errors=[_error_item("unsupported_command", f"Unsupported command: {command}")],
        )

    normalized_mode = str(mode or "auto").strip().lower()
    if normalized_mode not in SUPPORTED_MODES:
        return _build_envelope(
            command=normalized_command,
            status="error",
            mode="unknown",
            data={},
            errors=[_error_item("invalid_mode", f"Unsupported mode: {mode}")],
        )

    resolved_base_url = _resolve_backend_base_url(base_url)
    resolved_workspace = str(workspace or os.environ.get("KERNELONE_WORKSPACE") or os.getcwd())
    resolved_runtime_root = resolve_runtime_root(
        runtime_root=runtime_root,
        workspace=resolved_workspace,
        base_url=resolved_base_url,
    )

    runtime_exists = bool(resolved_runtime_root and resolved_runtime_root.exists())
    if normalized_mode == "offline":
        if not runtime_exists:
            return _build_envelope(
                command=normalized_command,
                status="error",
                mode="offline",
                data={},
                errors=[_error_item("runtime_not_found", "runtime_root not found for offline mode")],
            )
        selected_mode = "offline"
    elif normalized_mode == "online":
        selected_mode = "online"
    else:
        selected_mode = "offline" if runtime_exists else "online"

    normalized_params = dict(params or {})
    try:
        if selected_mode == "offline":
            assert resolved_runtime_root is not None  # guarded by runtime_exists
            return _run_offline_command(
                command=normalized_command,
                params=normalized_params,
                runtime_root=resolved_runtime_root,
                workspace=resolved_workspace,
            )

        return _run_online_command(
            command=normalized_command,
            params=normalized_params,
            base_url=resolved_base_url,
        )
    except requests.RequestException as exc:
        return _build_envelope(
            command=normalized_command,
            status="error",
            mode=selected_mode,
            data={},
            errors=[_api_error_to_item(exc)],
        )
    except FileNotFoundError as exc:
        return _build_envelope(
            command=normalized_command,
            status="not_found",
            mode=selected_mode,
            data={},
            errors=[_error_item("not_found", str(exc))],
        )
    except ValueError as exc:
        return _build_envelope(
            command=normalized_command,
            status="error",
            mode=selected_mode,
            data={},
            errors=[_error_item("invalid_input", str(exc))],
        )
    except RuntimeError as exc:
        logger.error("Unhandled exception in run_audit_command(%s): %s", normalized_command, exc, exc_info=True)
        return _build_envelope(
            command=normalized_command,
            status="error",
            mode=selected_mode,
            data={},
            errors=[_error_item("internal_error", str(exc))],
        )


def to_legacy_result(envelope: dict[str, Any]) -> dict[str, Any]:
    """Flatten unified envelope to legacy script-friendly payload."""
    data = envelope.get("data")
    if isinstance(data, dict):
        result: dict[str, Any] = dict(data)
    else:
        result = {"data": data}

    result["schema_version"] = envelope.get("schema_version")
    result["command"] = envelope.get("command")
    result["status"] = envelope.get("status")
    result["mode"] = envelope.get("mode")
    result["generated_at"] = envelope.get("generated_at")

    errors = envelope.get("errors")
    if isinstance(errors, list) and errors:
        result["errors"] = errors
        first = errors[0] if isinstance(errors[0], dict) else {"message": str(errors[0])}
        result["error"] = str(first.get("message") or first)

    return result
