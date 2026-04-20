"""PM Contract Store Module.

This module handles PM payload, contract, and result persistence.
Designed to be testable as pure functions with minimal side effects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def _get_fs_adapter():
    return get_default_adapter()


def _build_kernel_fs_for_path(path: str) -> KernelFileSystem | None:
    raw = str(path or "").strip()
    if not raw:
        return None

    target = Path(raw).expanduser()
    candidates: list[Path] = []
    if target.is_absolute():
        resolved = target.resolve()
        lower_parts = [part.lower() for part in resolved.parts]
        for marker in (".polaris", ".polaris", ".polaris-cache", ".polaris-cache", "runtime"):
            if marker not in lower_parts:
                continue
            index = lower_parts.index(marker)
            if index <= 0:
                continue
            candidates.append(Path(*resolved.parts[:index]).resolve())
    else:
        candidates.append(Path.cwd().resolve())

    cwd = Path.cwd().resolve()
    if cwd not in candidates:
        candidates.append(cwd)

    for workspace in candidates:
        try:
            fs = KernelFileSystem(str(workspace), _get_fs_adapter())
            fs.to_logical_path(raw)
            return fs
        except (ValueError, OSError, RuntimeError):
            continue
    return None


def write_json_atomic(path: str, data: Any) -> None:
    """Write JSON data atomically to path.

    Creates parent directories if needed and writes to temp file first
    to ensure atomicity on both Windows and Unix.

    Args:
        path: Target file path
        data: JSON-serializable data
    """
    if not path:
        return
    fs = _build_kernel_fs_for_path(path)
    if fs is None:
        raise ValueError(f"Path is outside KernelFileSystem managed roots: {path}")
    logical_path = fs.to_logical_path(path)
    fs.write_json(logical_path, data, indent=2, ensure_ascii=False)


def read_json_safe(path: str) -> dict[str, Any] | None:
    """Read JSON file safely, returning None on failure.

    Args:
        path: File path to read

    Returns:
        Parsed JSON dict or None if read fails
    """
    if not path:
        return None

    fs = _build_kernel_fs_for_path(path)
    if fs is None:
        return None

    try:
        logical_path = fs.to_logical_path(path)
        if not fs.exists(logical_path):
            return None
        payload = fs.read_json(logical_path)
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return None


def persist_pm_payload(
    *,
    normalized: dict[str, Any],
    pm_out_full: str,
    run_pm_tasks: str,
) -> None:
    """Write PM payload to canonical runtime and run-scoped contract paths.

    Args:
        normalized: Normalized PM payload
        pm_out_full: Path to write PM output (runtime/contracts/pm_tasks.contract.json)
        run_pm_tasks: Path to write run-scoped contract (run_dir/contracts/pm_tasks.contract.json)
    """
    for path in (pm_out_full, run_pm_tasks):
        if not path:
            continue
        write_json_atomic(path, normalized)


def ensure_engine_dispatch_contracts(
    *,
    normalized: dict[str, Any],
    run_pm_tasks: str,
    runtime_pm_tasks_full: str,
    runtime_plan_full: str,
) -> None:
    """Ensure engine dispatch prerequisites are persisted in canonical runtime paths.

    Args:
        normalized: Normalized PM payload
        run_pm_tasks: Run-scoped contract path
        runtime_pm_tasks_full: Runtime contract path
        runtime_plan_full: Runtime plan path
    """
    from polaris.cells.docs.court_workflow.public.service import ensure_plan_file

    for path in (runtime_pm_tasks_full, run_pm_tasks):
        if not path:
            continue
        write_json_atomic(path, normalized)

    if runtime_plan_full:
        ensure_plan_file(runtime_plan_full, auto_continue=True)


def persist_director_result(
    *,
    director_result: dict[str, Any],
    run_director_result: str,
) -> None:
    """Persist director execution result.

    Args:
        director_result: Director result data
        run_director_result: Path to write director result
    """
    if not run_director_result:
        return
    write_json_atomic(run_director_result, director_result)


def load_director_result(
    *,
    run_director_result: str,
) -> dict[str, Any] | None:
    """Load director result from file.

    Args:
        run_director_result: Path to director result file

    Returns:
        Director result dict or None if not found
    """
    return read_json_safe(run_director_result)


def persist_pm_state(
    *,
    pm_state: dict[str, Any],
    pm_state_full: str,
) -> None:
    """Persist PM state to file.

    Args:
        pm_state: PM state data
        pm_state_full: Path to PM state file
    """
    if not pm_state_full:
        return
    write_json_atomic(pm_state_full, pm_state)


def load_pm_state(
    *,
    pm_state_full: str,
) -> dict[str, Any] | None:
    """Load PM state from file.

    Args:
        pm_state_full: Path to PM state file

    Returns:
        PM state dict or None if not found
    """
    return read_json_safe(pm_state_full)


def merge_director_result_into_pm_state(
    pm_state: dict[str, Any],
    director_result: dict[str, Any] | None,
) -> None:
    """Merge director execution result into PM state.

    This is an in-place mutation of pm_state.

    Args:
        pm_state: PM state dict to merge into
        director_result: Director result dict to merge from
    """
    if not isinstance(pm_state, dict) or not isinstance(director_result, dict):
        return

    status = str(director_result.get("status") or director_result.get("director_status") or "").strip().lower()
    if status:
        pm_state["last_director_status"] = status

    task_id = str(director_result.get("task_id") or "").strip()
    if task_id:
        pm_state["last_director_task_id"] = task_id

    successes = director_result.get("successes")
    if successes is not None:
        pm_state["last_director_successes"] = successes

    failures = director_result.get("failures")
    if failures is not None:
        pm_state["last_director_failures"] = failures

    blocked = director_result.get("blocked")
    if blocked is not None:
        pm_state["last_director_blocked"] = blocked

    summary = director_result.get("summary")
    if summary and isinstance(summary, str):
        pm_state["last_director_summary"] = summary

    errors = director_result.get("errors")
    if errors and isinstance(errors, list):
        pm_state["last_director_errors"] = errors[:5]

    hard_failure = director_result.get("hard_failure")
    if hard_failure is not None:
        pm_state["last_director_hard_failure"] = bool(hard_failure)

    pm_state["last_director_run_id"] = director_result.get("run_id", "")

    task_results = director_result.get("task_results")
    if isinstance(task_results, list):
        pm_state["last_director_task_results"] = task_results


def build_fallback_director_result_from_summary(
    summary_payload: Any,
    *,
    run_id: str,
    hard_failure: bool,
) -> dict[str, Any]:
    """Build a fallback director result from a summary payload.

    Args:
        summary_payload: Summary data (often from PM)
        run_id: Run identifier
        hard_failure: Whether this is a hard failure

    Returns:
        Fallback director result dict
    """
    if not isinstance(summary_payload, dict):
        return {}

    successes = _coerce_non_negative_int(summary_payload.get("successes")) or 0
    total = _coerce_non_negative_int(summary_payload.get("total")) or 0
    failures = _coerce_non_negative_int(summary_payload.get("failures")) or 0
    blocked = _coerce_non_negative_int(summary_payload.get("blocked")) or 0
    dispatch_blocked = bool(summary_payload.get("dispatch_blocked"))
    dispatch_anomaly = str(summary_payload.get("dispatch_anomaly") or "").strip()

    if total == 0:
        total = successes + failures + blocked

    status = "completed"
    if dispatch_blocked or failures > 0 or blocked > 0 or hard_failure:
        status = "failed"

    result: dict[str, Any] = {
        "status": status,
        "run_id": run_id,
        "successes": successes,
        "failures": failures,
        "blocked": blocked,
        "total": total,
        "hard_failure": hard_failure,
    }

    if dispatch_blocked:
        result["dispatch_blocked"] = True
    if dispatch_anomaly:
        result["dispatch_anomaly"] = dispatch_anomaly

    return result


def _coerce_non_negative_int(value: Any) -> int | None:
    """Coerce value to non-negative integer.

    Args:
        value: Value to coerce

    Returns:
        Non-negative integer or None
    """
    if value is None:
        return None
    try:
        result = int(value)
        if result < 0:
            return None
        return result
    except (ValueError, TypeError):
        return None


def safe_payload_digest(payload: Any) -> str:
    """Generate a safe digest from payload for caching/logging.

    Args:
        payload: Any JSON-serializable payload

    Returns:
        MD5 hex digest (truncated to 16 chars)
    """
    import hashlib
    import json

    try:
        normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    except (RuntimeError, ValueError):
        return "invalid"


def trim_str_list(values: Any, limit: int = 20) -> list[str]:
    """Trim and validate a list of strings.

    Args:
        values: Input values (list or string)
        limit: Maximum number of items to return

    Returns:
        List of trimmed strings
    """
    if isinstance(values, str):
        values = [v.strip() for v in values.split(",") if v.strip()]
    elif isinstance(values, list):
        values = [str(v).strip() for v in values if v]
    else:
        return []

    return values[:limit]


def normalize_file_plan_entry(file_plan: Any) -> dict[str, Any] | None:
    """Normalize a file plan entry to standard format.

    Args:
        file_plan: File plan entry (dict or string)

    Returns:
        Normalized file plan dict or None
    """
    if isinstance(file_plan, dict):
        return {
            "path": str(file_plan.get("path") or "").strip(),
            "action": str(file_plan.get("action") or "modify").strip().lower(),
            "content": file_plan.get("content"),
        }
    if isinstance(file_plan, str):
        path = file_plan.strip()
        if not path:
            return None
        action = "modify"
        if path.startswith("+"):
            path = path[1:].strip()
            action = "create"
        elif path.startswith("-"):
            path = path[1:].strip()
            action = "delete"
        return {"path": path, "action": action}
    return None


__all__ = [
    "build_fallback_director_result_from_summary",
    "ensure_engine_dispatch_contracts",
    "load_director_result",
    "load_pm_state",
    "merge_director_result_into_pm_state",
    "normalize_file_plan_entry",
    "persist_director_result",
    "persist_pm_payload",
    "persist_pm_state",
    "read_json_safe",
    "safe_payload_digest",
    "trim_str_list",
    "write_json_atomic",
]
