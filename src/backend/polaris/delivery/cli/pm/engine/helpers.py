"""Helper utilities for Polaris engine.

This module contains pure utility functions with no side effects,
extracted from polaris_engine.py for maintainability.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from polaris.delivery.cli.pm.task_helpers import normalize_priority
from polaris.delivery.cli.pm.tasks import normalize_task_status
from polaris.delivery.cli.pm.utils import normalize_path_list, normalize_str_list
from polaris.kernelone.runtime.shared_types import normalize_path

logger = logging.getLogger(__name__)

# Constants for scheduling and execution modes
_ALLOWED_EXECUTION_MODES = {"single", "multi"}
_ALLOWED_SCHEDULING_POLICIES = {"fifo", "priority", "dag"}
_TERMINAL_TASK_STATUSES = {"done", "failed", "blocked"}
_DIRECTOR_RESULT_STATUSES = {"success", "needs_continue", "fail", "blocked", "unknown"}
_PHASE_ORDER = {
    "bootstrap": 10,
    "scaffold": 10,
    "core": 20,
    "implementation": 20,
    "integration": 30,
    "verification": 40,
    "qa": 40,
    "polish": 50,
}
_CODE_FILE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hh",
    ".hpp",
    ".cs",
    ".kt",
    ".swift",
    ".php",
    ".rb",
    ".sh",
    ".ps1",
}


def _now_timestamp() -> str:
    """Get current ISO format timestamp (UTC).

    Unified ISO format timestamp for cross-module state comparison.
    """
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_running_status(status: str) -> bool:
    """Check if status indicates active execution."""
    normalized = str(status or "").strip().lower()
    return normalized in {
        "running",
        "planning",
        "dispatching",
        "in_progress",
        "pending",
    }


def _safe_int(value: Any, *, default: int = 0) -> int:
    """Parse integer with fallback default."""
    try:
        return int(value)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse int from %r, using default %d: %s",
            value,
            default,
            exc,
        )
        return int(default)


def _join_non_empty(parts: Any) -> str:
    """Join non-empty string parts with semicolon separator."""
    if not isinstance(parts, (list, tuple, set)):
        return ""
    return "; ".join(str(item).strip() for item in parts if str(item).strip())


def _normalize_failure_detail(value: Any, *, max_len: int = 1200) -> str:
    """Normalize failure detail to a concise string representation."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()[:max_len]
    if isinstance(value, dict):
        preferred_keys = (
            "summary",
            "detail",
            "reason",
            "error",
            "message",
            "diagnostics",
            "failed_gates",
            "missing_evidence",
        )
        parts: list[str] = []
        for key in preferred_keys:
            item = value.get(key)
            if item in (None, "", [], {}):
                continue
            if isinstance(item, list):
                token = ",".join(str(entry).strip() for entry in item if str(entry).strip())
            else:
                token = str(item).strip()
            if token:
                parts.append(f"{key}={token}")
        text = _join_non_empty(parts) or str(value).strip()
        return text[:max_len]
    if isinstance(value, (list, tuple, set)):
        text = _join_non_empty([_normalize_failure_detail(item, max_len=max_len) for item in value])
        return text[:max_len]
    return str(value).strip()[:max_len]


def _env_positive_int(name: str, default: int) -> int:
    """Parse positive integer from environment variable."""
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse positive int from env %s=%r, using default %d: %s",
            name,
            raw,
            default,
            exc,
        )
        return default
    return parsed if parsed > 0 else default


def _env_float(name: str, default: float) -> float:
    """Parse float from environment variable."""
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse float from env %s=%r, using default %s: %s",
            name,
            raw,
            default,
            exc,
        )
        return default
    return parsed


def _env_non_negative_int(name: str, default: int) -> int:
    """Parse non-negative integer from environment variable."""
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to parse non-negative int from env %s=%r, using default %d: %s",
            name,
            raw,
            default,
            exc,
        )
        return default
    return parsed if parsed >= 0 else default


def _is_truthy_env(name: str) -> bool | None:
    """Check if environment variable is truthy/falsy."""
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def _normalize_bool(value: Any, *, default: bool) -> bool:
    """Normalize boolean value from various input types."""
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _dedupe_paths(paths: Any) -> list[str]:
    """Deduplicate path list while preserving order."""
    if not isinstance(paths, (list, tuple, set)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for value in paths:
        path = str(value or "").strip()
        if not path:
            continue
        norm = os.path.normcase(os.path.normpath(path))
        if norm in seen:
            continue
        seen.add(norm)
        result.append(path)
    return result


def _first_existing_file(candidates: Any) -> str:
    """Return first existing file from candidate list."""
    if not isinstance(candidates, (list, tuple, set)):
        return ""
    for item in candidates:
        path = str(item or "").strip()
        if not path:
            continue
        if os.path.isfile(path):
            return path
    return ""


def _phase_rank(task: dict[str, Any]) -> int:
    """Get ordering rank for task phase."""
    phase = str(task.get("phase") or "").strip().lower()
    return int(_PHASE_ORDER.get(phase, 999))


def _task_identity_key(task: dict[str, Any]) -> str:
    """Generate unique identity key for deduplication."""
    fingerprint = str(task.get("fingerprint") or "").strip()
    if fingerprint:
        return f"fp:{fingerprint}"
    task_id = str(task.get("id") or "").strip()
    if task_id:
        return f"id:{task_id}"
    title = str(task.get("title") or task.get("goal") or "").strip().lower()
    targets = "|".join(normalize_path_list(task.get("target_files") or []))
    if title or targets:
        return f"title:{title}|targets:{targets}"
    return ""


def _task_dependency_ids(task: dict[str, Any]) -> list[str]:
    """Extract dependency IDs from task."""
    return normalize_str_list(task.get("dependencies") or task.get("depends_on") or task.get("deps"))


def _collect_completed_task_ids(
    pm_payload: dict[str, Any],
    completion_state: dict[str, Any],
) -> set[str]:
    """Collect IDs of completed tasks from payload and state."""
    completed_ids: set[str] = set()
    tasks = pm_payload.get("tasks") if isinstance(pm_payload, dict) else []
    if isinstance(tasks, list):
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = str(task.get("id") or "").strip()
            if not task_id:
                continue
            status = normalize_task_status(task.get("status"))
            if status == "done" or bool(task.get("completion_lock")):
                completed_ids.add(task_id)

    keys = completion_state.get("keys") if isinstance(completion_state, dict) else set()
    if isinstance(keys, set):
        for key in keys:
            token = str(key or "").strip()
            if token.startswith("id:"):
                task_id = token[3:].strip()
                if task_id:
                    completed_ids.add(task_id)
    return completed_ids


def _order_tasks(
    tasks: Any,
    policy: str,
) -> list[tuple[int, dict[str, Any]]]:
    """Order tasks according to scheduling policy."""
    if not isinstance(tasks, (list, tuple, set)):
        return []
    indexed: list[tuple[int, dict[str, Any]]] = [
        (index, task) for index, task in enumerate(tasks) if isinstance(task, dict)
    ]
    normalized_policy = str(policy or "priority").strip().lower()
    if normalized_policy not in _ALLOWED_SCHEDULING_POLICIES:
        normalized_policy = "priority"
    if normalized_policy == "fifo":
        return indexed
    if normalized_policy == "dag":
        return sorted(
            indexed,
            key=lambda pair: (
                _phase_rank(pair[1]),
                normalize_priority(pair[1].get("priority"), fallback=pair[0] + 1),
                pair[0],
            ),
        )
    return sorted(
        indexed,
        key=lambda pair: (
            normalize_priority(pair[1].get("priority"), fallback=pair[0] + 1),
            pair[0],
        ),
    )


def _build_batches(
    ordered: Any,
    workers: int,
    policy: str,
) -> list[list[dict[str, Any]]]:
    """Build execution batches from ordered tasks."""
    if not isinstance(ordered, (list, tuple, set)):
        return []
    pending: list[tuple[int, dict[str, Any]]] = list(ordered)
    completed_ids: set[str] = set()
    batches: list[list[dict[str, Any]]] = []
    normalized_policy = str(policy or "priority").strip().lower()

    while pending:
        ready: list[tuple[int, dict[str, Any]]] = []
        for item in pending:
            _, task = item
            deps = _task_dependency_ids(task)
            if not deps:
                ready.append(item)
                continue
            if all(str(dep).strip() in completed_ids for dep in deps if str(dep).strip()):
                ready.append(item)

        # Cycle/missing-dependency fallback: force progress with first pending task.
        if not ready:
            ready = sorted(
                pending,
                key=lambda pair: (
                    _phase_rank(pair[1]),
                    normalize_priority(pair[1].get("priority"), fallback=pair[0] + 1),
                    pair[0],
                ),
            )[:1]

        if normalized_policy in ("priority", "dag"):
            ready = sorted(
                ready,
                key=lambda pair: (
                    normalize_priority(pair[1].get("priority"), fallback=pair[0] + 1),
                    pair[0],
                ),
            )

        selected = ready[: max(1, workers)]
        batch: list[dict[str, Any]] = []
        for item in selected:
            pending.remove(item)
            _, task = item
            batch.append(task)
            task_id = str(task.get("id") or "").strip()
            if task_id:
                completed_ids.add(task_id)
        batches.append(batch)

    return batches


def _looks_like_code_file(path: str) -> bool:
    """Check if path looks like a code file."""
    normalized = normalize_path(path)
    if not normalized:
        return False
    _, ext = os.path.splitext(normalized.lower())
    return ext in _CODE_FILE_EXTENSIONS


def _looks_like_test_file(path: str) -> bool:
    """Check if path looks like a test file."""
    normalized = normalize_path(path)
    if not normalized:
        return False
    lower = normalized.lower()
    name = os.path.basename(lower)
    if "/tests/" in lower or lower.startswith("tests/"):
        return True
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if name.endswith("_test.go") or name.endswith("_test.rs"):
        return True
    return bool(".spec." in name or ".test." in name)


def _resolve_workspace_candidate_path(
    workspace_full: str,
    rel_path: str,
) -> Path | None:
    """Resolve relative path within workspace with traversal check."""
    normalized = normalize_path(rel_path)
    if not normalized:
        return None
    root = Path(str(workspace_full or "")).resolve()
    candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Path traversal check failed for workspace %r / rel %r: %s",
            workspace_full,
            rel_path,
            exc,
        )
        return None
    if not candidate.is_file():
        return None
    return candidate


def _count_utf8_lines(path: Path, *, max_lines: int = 20000) -> int:
    """Count UTF-8 lines in file with upper bound."""
    if max_lines <= 0:
        return 0
    line_count = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line_count, _ in enumerate(handle, start=1):
                if line_count >= max_lines:
                    break
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "Failed to read or count lines from %r (max_lines=%d): %s",
            path,
            max_lines,
            exc,
        )
        return 0
    return max(0, int(line_count))


def _estimate_code_lines_from_workspace(
    workspace_full: str,
    candidate_files: Any,
) -> int:
    """Estimate total code lines from workspace files."""
    if not isinstance(candidate_files, (list, tuple, set)):
        return 0
    total = 0
    seen: set[str] = set()
    for rel in candidate_files:
        normalized = normalize_path(rel)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        if not _looks_like_code_file(normalized):
            continue
        resolved = _resolve_workspace_candidate_path(workspace_full, normalized)
        if resolved is None:
            continue
        total += _count_utf8_lines(resolved)
    return max(0, int(total))


__all__ = [
    "_ALLOWED_EXECUTION_MODES",
    "_ALLOWED_SCHEDULING_POLICIES",
    "_CODE_FILE_EXTENSIONS",
    "_DIRECTOR_RESULT_STATUSES",
    "_PHASE_ORDER",
    "_TERMINAL_TASK_STATUSES",
    "_build_batches",
    "_collect_completed_task_ids",
    "_count_utf8_lines",
    "_dedupe_paths",
    "_env_float",
    "_env_non_negative_int",
    "_env_positive_int",
    "_estimate_code_lines_from_workspace",
    "_first_existing_file",
    "_is_running_status",
    "_is_truthy_env",
    "_join_non_empty",
    "_looks_like_code_file",
    "_looks_like_test_file",
    "_normalize_bool",
    "_normalize_failure_detail",
    "_now_timestamp",
    "_order_tasks",
    "_phase_rank",
    "_resolve_workspace_candidate_path",
    "_safe_int",
    "_task_dependency_ids",
    "_task_identity_key",
]
