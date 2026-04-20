"""Failure hops building and loading functions.

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import TypeAlias

from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.utils.time_utils import utc_now_iso

logger = logging.getLogger(__name__)


# Type alias for event dictionaries
EventDict: TypeAlias = dict[str, Any]
RefsDict: TypeAlias = dict[str, Any]


def _get_dict(event: EventDict, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Safely get a dict value from a dict, returning default if None or not a dict."""
    value = event.get(key)
    if isinstance(value, dict):
        return value
    return default if default is not None else {}


# Backward compatibility alias
_utc_now_iso = utc_now_iso


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_failed_observation(event: EventDict) -> bool:
    if not isinstance(event, dict):
        return False
    if str(event.get("kind") or "") != "observation":
        return False
    if event.get("ok") is False:
        return True
    if event.get("error"):
        return True
    output = _get_dict(event, "output")
    if output.get("ok") is False:
        return True
    return bool(output.get("error"))


def _collect_events(
    events_path: str,
    *,
    run_id: str,
    event_seq_start: int,
    event_seq_end: int,
) -> list[EventDict]:
    path = Path(events_path)
    if not path.exists():
        return []

    items: list[EventDict] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
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

            refs = _get_dict(event, "refs")
            ref_run_id = str(refs.get("run_id") or "").strip()
            if run_id and ref_run_id and ref_run_id != run_id:
                continue
            items.append(event)

    items.sort(key=lambda item: _safe_int(item.get("seq"), 0))
    return items


def _extract_tool_paths(event: EventDict) -> dict[str, str]:
    keys = (
        "tool_stdout_path",
        "tool_stderr_path",
        "tool_error_path",
        "stdout_path",
        "stderr_path",
        "error_path",
    )
    output = _get_dict(event, "output")
    meta = _get_dict(event, "meta")

    paths: dict[str, str] = {}
    for key in keys:
        value = output.get(key)
        if not value:
            value = meta.get(key)
        if isinstance(value, str) and value.strip():
            paths[key] = value.strip()

    raw_from_meta = meta.get("raw_output_paths")
    if isinstance(raw_from_meta, dict):
        for key in ("tool_stdout_path", "tool_stderr_path", "tool_error_path"):
            value = raw_from_meta.get(key)  # type: ignore[union-attr]
            if isinstance(value, str) and value.strip() and key not in paths:
                paths[key] = value.strip()

    return paths


def _derive_failure_code(event: EventDict, fallback_failure_code: str) -> str:
    if fallback_failure_code:
        return fallback_failure_code

    output = _get_dict(event, "output")
    for key in ("failure_code", "error_code"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    err = event.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()

    output_err = output.get("error")
    if isinstance(output_err, str) and output_err.strip():
        return output_err.strip()

    return "UNKNOWN_FAILURE"


def _build_hop3(event: EventDict) -> dict[str, Any]:
    output = _get_dict(event, "output")
    paths = _extract_tool_paths(event)
    if paths:
        return {
            "source": "artifact_paths",
            "tool": output.get("tool") or event.get("name") or "",
            "paths": paths,
        }

    output_error = output.get("error")
    if isinstance(output_error, str) and output_error.strip():
        return {
            "source": "event_output",
            "tool": output.get("tool") or event.get("name") or "",
            "error": output_error.strip(),
        }

    event_error = event.get("error")
    if isinstance(event_error, str) and event_error.strip():
        return {
            "source": "event_error",
            "tool": output.get("tool") or event.get("name") or "",
            "error": event_error.strip(),
        }

    return {
        "source": "none",
        "tool": output.get("tool") or event.get("name") or "",
        "error": "No raw output captured",
        "missing_artifacts": True,
    }


def build_failure_hops(
    events_path: str,
    run_id: str,
    event_seq_start: int = 0,
    event_seq_end: int = 0,
) -> dict[str, Any]:
    """Build failure hops for a run.

    Args:
        events_path: 事件文件路径
        run_id: 运行 ID
        event_seq_start: 事件序列起始
        event_seq_end: 事件序列结束 (0 表示不限制)

    Returns:
        失败定位 hops 数据
    """
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "event_span": {
            "seq_start": int(event_seq_start or 0),
            "seq_end": int(event_seq_end or 0),
        },
        "ready": True,
        "has_failure": False,
        "failure_code": "",
        "failure_event_seq": None,
        "hop1_phase": None,
        "hop2_evidence": None,
        "hop3_tool_output": None,
    }

    events = _collect_events(
        events_path,
        run_id=str(run_id or "").strip(),
        event_seq_start=int(event_seq_start or 0),
        event_seq_end=int(event_seq_end or 0),
    )
    failed_events = [event for event in events if _is_failed_observation(event)]
    if not failed_events:
        return payload

    failure_event = failed_events[-1]
    failure_seq = _safe_int(failure_event.get("seq"), 0)
    refs = _get_dict(failure_event, "refs")

    related_action_seq: int | None = None
    failure_name = str(failure_event.get("name") or "")
    for event in reversed(events):
        seq = _safe_int(event.get("seq"), 0)
        if seq >= failure_seq:
            continue
        if str(event.get("kind") or "") != "action":
            continue
        if failure_name and str(event.get("name") or "") != failure_name:
            continue
        related_action_seq = seq
        break

    payload["has_failure"] = True
    payload["failure_event_seq"] = failure_seq
    payload["failure_code"] = _derive_failure_code(failure_event, "")
    payload["hop1_phase"] = {
        "phase": refs.get("phase") or "unknown",
        "seq": failure_seq,
        "actor": failure_event.get("actor") or "",
        "name": failure_event.get("name") or "",
        "summary": failure_event.get("summary") or "",
    }
    payload["hop2_evidence"] = {
        "task_id": refs.get("task_id"),
        "task_fingerprint": refs.get("task_fingerprint"),
        "run_id": refs.get("run_id") or run_id,
        "pm_iteration": refs.get("pm_iteration"),
        "director_iteration": refs.get("director_iteration"),
        "trajectory_path": refs.get("trajectory_path"),
        "evidence_path": refs.get("evidence_path"),
        "files": refs.get("files") if isinstance(refs.get("files"), list) else [],
        "related_action_seq": related_action_seq,
        "failure_event_seq": failure_seq,
    }
    payload["hop3_tool_output"] = _build_hop3(failure_event)
    if bool(payload["hop3_tool_output"].get("missing_artifacts")):
        payload["ready"] = False

    return payload


def load_failure_hops(
    runtime_root: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Load failure hops from file.

    Args:
        runtime_root: Runtime 根目录
        run_id: 运行 ID

    Returns:
        失败定位 hops 数据，如果不存在则返回 None
    """
    runtime_path = Path(runtime_root)
    hops_path = runtime_path / "artifacts" / "runs" / run_id / "failure_hops.json"

    if not hops_path.exists():
        return None

    try:
        with open(hops_path, encoding="utf-8") as f:
            return json.load(f)
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to load failure hops from %s: %s", hops_path, exc)
        return None


def save_failure_hops(
    runtime_root: str,
    run_id: str,
    hops_data: dict[str, Any],
) -> bool:
    """Save failure hops to file.

    Args:
        runtime_root: Runtime 根目录
        run_id: 运行 ID
        hops_data: 失败定位 hops 数据

    Returns:
        是否保存成功
    """
    runtime_path = Path(runtime_root)
    hops_path = runtime_path / "artifacts" / "runs" / run_id / "failure_hops.json"

    try:
        content = json.dumps(hops_data, ensure_ascii=False, indent=2)
        write_text_atomic(str(hops_path), content, encoding="utf-8")
        return True
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to save failure hops for run_id=%s: %s", run_id, exc)
        return False


def analyze_failure_chain(
    events: list[EventDict],
) -> dict[str, Any]:
    """Analyze failure chain from events.

    Args:
        events: 事件列表

    Returns:
        失败链分析结果
    """
    failures: list[dict[str, Any]] = []
    tool_errors: list[dict[str, Any]] = []
    verification_failures: list[dict[str, Any]] = []

    for event in events:
        event_type = str(event.get("event_type") or "")
        action = _get_dict(event, "action")

        if action:
            result = str(action.get("result") or "")

            if result == "failure" or event_type == "task_failed":
                failures.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "event_type": event_type,
                        "error": action.get("error") or "",
                    }
                )

            if event_type == "tool_execution" and result == "failure":
                resource = _get_dict(event, "resource")
                tool_errors.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "tool": resource.get("path") if resource else "",
                        "error": action.get("error") or "",
                    }
                )

            if event_type == "verification" and result == "failure":
                data = _get_dict(event, "data")
                verification_failures.append(
                    {
                        "timestamp": event.get("timestamp"),
                        "check": data.get("check") if data else "",
                        "error": action.get("error") or "",
                    }
                )

    return {
        "total_failures": len(failures),
        "tool_errors": len(tool_errors),
        "verification_failures": len(verification_failures),
        "failures": failures,
        "tool_errors_detail": tool_errors,
        "verification_failures_detail": verification_failures,
    }
