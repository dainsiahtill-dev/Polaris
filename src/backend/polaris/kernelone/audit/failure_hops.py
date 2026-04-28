from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from polaris.kernelone.fs.text_ops import ensure_parent_dir, write_json_atomic
from polaris.kernelone.utils.time_utils import utc_now_iso as _utc_now_iso

_logger = logging.getLogger(__name__)


def _emit_audit_internal_failure(error_type: str, error_details: dict) -> None:
    """Attempt to emit internal audit event; degrade gracefully on failure."""
    try:
        from polaris.kernelone.audit.contracts import KernelAuditEventType
        from polaris.kernelone.audit.runtime import KernelAuditRuntime

        runtime = KernelAuditRuntime.get_instance(Path.cwd())
        runtime._emit_internal_event(
            KernelAuditEventType.INTERNAL_AUDIT_FAILURE,
            {"source_module": "failure_hops", "error_type": error_type, **error_details},
        )
    except (RuntimeError, ValueError, TypeError):
        _logger.warning("Audit internal failure (degraded): %s %s", error_type, error_details)


def _safe_int(value: Any, default: int = 0) -> int:
    from polaris.kernelone.runtime.shared_types import safe_int as _impl

    return _impl(value, default)


def _is_dict(value: Any) -> bool:
    """Type guard for dict."""
    return isinstance(value, dict)


def _get_str(event: dict[str, Any], key: str, default: str = "") -> str:
    """Safely get string value from dict."""
    value = event.get(key)
    if value is None:
        return default
    return str(value)


def _get_int(event: dict[str, Any], key: str, default: int = 0) -> int:
    """Safely get int value from dict."""
    value = event.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _is_failed_observation(event: dict[str, Any]) -> bool:
    """Check if event is a failed observation."""
    if str(event.get("kind") or "") != "observation":
        return False
    if event.get("ok") is False:
        return True
    if event.get("error"):
        return True
    output = event.get("output")
    if _is_dict(output):
        if output.get("ok") is False:  # type: ignore[union-attr]
            return True
        if output.get("error"):  # type: ignore[union-attr]
            return True
    return False


def _collect_events(
    events_path: str,
    *,
    run_id: str,
    event_seq_start: int,
    event_seq_end: int,
) -> list[dict[str, Any]]:
    if not events_path or not os.path.exists(events_path):
        return []
    items: list[dict[str, Any]] = []
    with open(events_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (RuntimeError, ValueError) as exc:
                with contextlib.suppress(TypeError):
                    _emit_audit_internal_failure("json_parse_error", {"line_preview": line[:200], "error": str(exc)})
                continue
            if not isinstance(event, dict):
                continue
            seq = _safe_int(event.get("seq"), -1)
            if seq < 0:
                continue
            if event_seq_start > 0 and seq < event_seq_start:
                continue
            if event_seq_end > 0 and seq > event_seq_end:
                continue
            refs = event.get("refs")
            if not _is_dict(refs):
                refs = {}
            ref_run_id = str(refs.get("run_id") or "") if refs else ""  # type: ignore[union-attr]
            if run_id and ref_run_id and ref_run_id != run_id:
                continue
            items.append(event)
    items.sort(key=lambda item: _safe_int(item.get("seq"), 0))
    return items


def _extract_tool_paths(event: dict[str, Any]) -> dict[str, str]:
    keys = (
        "tool_stdout_path",
        "tool_stderr_path",
        "tool_error_path",
        "stdout_path",
        "stderr_path",
        "error_path",
    )
    output = event.get("output")
    if not _is_dict(output):
        output = {}
    meta = event.get("meta")
    if not _is_dict(meta):
        meta = {}
    paths: dict[str, str] = {}
    for key in keys:
        value = output.get(key) if output else None  # type: ignore[union-attr]
        if not value:
            value = meta.get(key) if meta else None  # type: ignore[union-attr]
        if isinstance(value, str) and value.strip():
            paths[key] = value.strip()
    raw_from_meta = meta.get("raw_output_paths") if meta else None  # type: ignore[union-attr]
    if _is_dict(raw_from_meta):
        for key in ("tool_stdout_path", "tool_stderr_path", "tool_error_path"):
            value = raw_from_meta.get(key)  # type: ignore[union-attr]
            if isinstance(value, str) and value.strip() and key not in paths:
                paths[key] = value.strip()
    return paths


def _derive_failure_code(event: dict[str, Any], fallback_failure_code: str) -> str:
    if fallback_failure_code:
        return fallback_failure_code
    output = event.get("output")
    if _is_dict(output):
        for key in ("failure_code", "error_code"):
            value = output.get(key)  # type: ignore[union-attr]
            if isinstance(value, str) and value.strip():
                return value.strip()
    err = event.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if _is_dict(output):
        output_err = output.get("error")  # type: ignore[union-attr]
        if isinstance(output_err, str) and output_err.strip():
            return output_err.strip()
    return "UNKNOWN_FAILURE"


def _build_hop3(event: dict[str, Any]) -> dict[str, Any]:
    output = event.get("output")
    if not _is_dict(output):
        output = {}
    paths = _extract_tool_paths(event)
    if paths:
        return {
            "source": "artifact_paths",
            "tool": output.get("tool") or event.get("name") or "",  # type: ignore[union-attr]
            "paths": paths,
        }
    output_error = output.get("error") if output else None  # type: ignore[union-attr]
    if isinstance(output_error, str) and output_error.strip():
        return {
            "source": "event_output",
            "tool": output.get("tool") or event.get("name") or "",  # type: ignore[union-attr]
            "error": output_error.strip(),
        }
    event_error = event.get("error")
    if isinstance(event_error, str) and event_error.strip():
        return {
            "source": "event_error",
            "tool": output.get("tool") or event.get("name") or "",  # type: ignore[union-attr]
            "error": event_error.strip(),
        }
    return {
        "source": "none",
        "tool": output.get("tool") or event.get("name") or "",  # type: ignore[union-attr]
        "error": "No raw output captured",
        "missing_artifacts": True,
    }


def build_failure_hops(
    events_path: str,
    *,
    run_id: str,
    event_seq_start: int,
    event_seq_end: int,
    fallback_failure_code: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "event_span": {
            "seq_start": event_seq_start,
            "seq_end": event_seq_end,
        },
        "ready": True,
        "has_failure": False,
        "failure_code": fallback_failure_code or "",
        "failure_event_seq": None,
        "hop1_phase": None,
        "hop2_evidence": None,
        "hop3_tool_output": None,
    }

    events = _collect_events(
        events_path,
        run_id=run_id,
        event_seq_start=event_seq_start,
        event_seq_end=event_seq_end,
    )
    failed_events = [event for event in events if _is_failed_observation(event)]
    if not failed_events:
        return payload

    failure_event = failed_events[-1]
    failure_seq = _safe_int(failure_event.get("seq"), 0)
    refs = failure_event.get("refs")
    if not _is_dict(refs):
        refs = {}

    related_action_seq: int | None = None
    related_action_phase = ""
    failure_name = _get_str(failure_event, "name", "")
    for event in reversed(events):
        seq = _safe_int(event.get("seq"), 0)
        if seq >= failure_seq:
            continue
        if str(event.get("kind") or "") != "action":
            continue
        event_name = _get_str(event, "name", "")
        if failure_name and event_name != failure_name:
            continue
        related_action_seq = seq
        action_refs = event.get("refs")
        if _is_dict(action_refs):
            related_action_phase = str(action_refs.get("phase") or "")
        break

    payload["has_failure"] = True
    payload["failure_event_seq"] = failure_seq
    payload["failure_code"] = _derive_failure_code(failure_event, fallback_failure_code)
    phase = refs.get("phase") or related_action_phase or "unknown" if refs else related_action_phase or "unknown"
    payload["hop1_phase"] = {
        "phase": phase,  # type: ignore[union-attr]
        "seq": failure_seq,
        "actor": _get_str(failure_event, "actor", ""),
        "name": failure_name,
        "summary": _get_str(failure_event, "summary", ""),
    }

    # Build hop2_evidence with safe dict access
    hop2_refs: dict[str, Any] = refs if refs else {}  # type: ignore[assignment]
    files_value = hop2_refs.get("files")
    files_list: list[Any] = []
    if isinstance(files_value, list):
        files_list = files_value

    payload["hop2_evidence"] = {
        "task_id": hop2_refs.get("task_id"),
        "task_fingerprint": hop2_refs.get("task_fingerprint"),
        "run_id": hop2_refs.get("run_id") or run_id,
        "pm_iteration": hop2_refs.get("pm_iteration"),
        "director_iteration": hop2_refs.get("director_iteration"),
        "trajectory_path": hop2_refs.get("trajectory_path"),
        "evidence_path": hop2_refs.get("evidence_path"),
        "files": files_list,
        "related_action_seq": related_action_seq,
        "failure_event_seq": failure_seq,
    }
    payload["hop3_tool_output"] = _build_hop3(failure_event)
    if bool(payload["hop3_tool_output"].get("missing_artifacts")):
        payload["ready"] = False
    return payload


def write_failure_index(run_dir: str, payload: dict[str, Any]) -> str:
    if not run_dir:
        return ""
    output_path = os.path.join(run_dir, "failure_hops.json")
    ensure_parent_dir(output_path)
    write_json_atomic(output_path, payload)
    return output_path
