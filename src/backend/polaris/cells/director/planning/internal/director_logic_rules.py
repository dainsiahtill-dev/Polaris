"""Director logic rules (Cell Implementation).

Migrated from ``polaris.cells.director.execution.internal.director_logic_rules``.

Logic extracted from loop-director.py for testability and reusability
across director.planning and director.runtime.

Shared director logic utilities have been extracted to
``polaris.domain.services.director_logic_service``.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
from typing import Any

from polaris.domain.entities import DEFAULT_DEFECT_TICKET_FIELDS
from polaris.domain.services.director_logic_service import (
    extract_defect_ticket,
    parse_acceptance,
    validate_defect_ticket,
)
from polaris.kernelone.utils.json_utils import parse_json_payload

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DEFECT_TICKET_FIELDS",
    "compact_pm_payload",
    "extract_defect_ticket",
    "extract_required_evidence",
    "parse_acceptance",
    "parse_json_payload",
    "validate_defect_ticket",
    "validate_files_to_edit",
    "write_gate_check",
]


def _truncate_text(text: str, max_chars: int) -> str:
    try:
        from polaris.kernelone.runtime.shared_types import truncate_text as _impl
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to import truncate_text, using fallback: %s", exc)
        from polaris.kernelone.runtime.shared_types import truncate_text as _impl

    return _impl(text, max_chars or 800)


def _compact_str(value: Any, max_chars: int) -> str:
    try:
        from polaris.kernelone.runtime.shared_types import compact_str as _impl
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to import compact_str, using fallback: %s", exc)
        from polaris.kernelone.runtime.shared_types import compact_str as _impl

    return _impl(value, max_chars)


def _compact_list(values: Any, max_items: int, max_str_chars: int) -> list[str]:
    items: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str) and item.strip():
                items.append(_truncate_text(item.strip(), max_str_chars))
    elif isinstance(values, str) and values.strip():
        items.append(_truncate_text(values.strip(), max_str_chars))
    if max_items > 0 and len(items) > max_items:
        items = items[:max_items]
    return items


def compact_pm_payload(pm_payload: dict[str, Any] | None, max_chars: int) -> dict[str, Any]:
    """Compacts PM payload to fit into context window via progressive reduction."""
    if not isinstance(pm_payload, dict):
        return {}

    def _fits(candidate: dict[str, Any]) -> bool:
        return max_chars <= 0 or len(json.dumps(candidate, ensure_ascii=False)) <= max_chars

    def build(task_limit: int, list_limit: int, str_limit: int, include_evidence: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if "overall_goal" in pm_payload:
            payload["overall_goal"] = _compact_str(pm_payload.get("overall_goal"), str_limit)
        if "focus" in pm_payload:
            payload["focus"] = _compact_str(pm_payload.get("focus"), str_limit)
        if "notes" in pm_payload:
            payload["notes"] = _compact_str(pm_payload.get("notes"), str_limit)
        tasks_out: list[dict[str, Any]] = []
        tasks = pm_payload.get("tasks")
        if isinstance(tasks, list):
            for task in tasks[: max(task_limit, 0)]:
                if not isinstance(task, dict):
                    continue
                compact_task: dict[str, Any] = {
                    "id": _compact_str(task.get("id"), 120),
                    "title": _compact_str(task.get("title"), str_limit),
                    "goal": _compact_str(task.get("goal"), str_limit),
                    "target_files": _compact_list(task.get("target_files"), list_limit, 200),
                    "scope_paths": _compact_list(task.get("scope_paths"), list_limit, 200),
                    "scope_mode": _compact_str(task.get("scope_mode"), 32),
                    "context_files": _compact_list(task.get("context_files"), list_limit, 200),
                    "constraints": _compact_list(task.get("constraints"), list_limit, str_limit),
                    "acceptance": _compact_list(task.get("acceptance"), list_limit, str_limit),
                    "stop_conditions": _compact_list(task.get("stop_conditions"), list_limit, str_limit),
                }
                if include_evidence:
                    compact_task["required_evidence"] = task.get("required_evidence")
                    compact_task["policy_overrides"] = task.get("policy_overrides")
                tasks_out.append(compact_task)
        payload["tasks"] = tasks_out
        return payload

    # Progressive reduction tiers - explicitly typed to match build() signature
    tiers: list[tuple[int, int, int, bool]] = [
        (3, 8, 320, True),
        (2, 6, 240, True),
        (1, 4, 180, False),
    ]

    for task_limit, list_limit, str_limit, include_evidence in tiers:
        candidate = build(task_limit, list_limit, str_limit, include_evidence)
        if _fits(candidate):
            return candidate

    # Ultra-compact: just task IDs and summary
    task_ids = []
    tasks = pm_payload.get("tasks")
    if isinstance(tasks, list):
        for task in tasks[:2]:
            if isinstance(task, dict) and task.get("id"):
                task_ids.append(_compact_str(task.get("id"), 120))
    summary_limit = min(240, max_chars) if max_chars > 0 else 240
    candidate = {
        "summary": _compact_str(
            pm_payload.get("focus") or pm_payload.get("overall_goal") or "pm_tasks",
            summary_limit,
        ),
        "task_ids": task_ids,
    }
    if _fits(candidate):
        return candidate

    # Last resort: truncate summary to fit
    summary = candidate.get("summary", "")
    if not isinstance(summary, str):
        summary = str(summary)
    overhead = len(json.dumps({"summary": ""}, ensure_ascii=False))
    allowed = max(0, max_chars - overhead) if max_chars > 0 else len(summary)
    return {"summary": summary[:allowed]}


def validate_files_to_edit(files: list[str], workspace: str) -> tuple[bool, list[str], list[str]]:
    """
    Ensure files are readable before edits.
    Returns: (is_valid, missing_files, unreadable_files)
    """
    if not files:
        return True, [], []

    missing: list[str] = []
    unreadable: list[str] = []

    for path in files:
        full_path = os.path.join(workspace, path)
        if not os.path.exists(full_path):
            missing.append(path)
            continue
        if os.path.isdir(full_path):
            # Directory/module scope hints are valid in module mode; skip file-read probe.
            continue
        try:
            with open(full_path, encoding="utf-8") as handle:
                handle.read(1)
        except (RuntimeError, ValueError) as exc:
            logger.debug("Failed to read file %s: %s", path, exc)
            unreadable.append(f"{path} ({exc})")

    is_valid = len(unreadable) == 0
    return is_valid, missing, unreadable


def write_gate_check(
    changed_files: list[str],
    act_files: list[str],
    pm_target_files: list[str] | None = None,
    *,
    require_change: bool = False,
) -> tuple[bool, str]:
    """Enforce changed files are within planner act scope and PM task scope.

    PM scope accepts either exact file paths or module/directory prefixes.
    """

    extensionless_file_names = {
        "dockerfile",
        "makefile",
        "license",
        "readme",
        "procfile",
    }

    def _normalize_scope_path(path: str) -> str:
        normalized = str(path or "").strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized.strip()

    def _is_directory_scope(path: str) -> bool:
        normalized = _normalize_scope_path(path)
        if not normalized:
            return False
        if normalized in (".", "*", "**"):
            return True
        if normalized.endswith("/"):
            return True
        base = normalized.rstrip("/").split("/")[-1]
        if "." in base:
            return False
        return base.lower() not in extensionless_file_names

    def _scope_matches(path: str, scope: str) -> bool:
        normalized_path = _normalize_scope_path(path)
        normalized_scope = _normalize_scope_path(scope)
        if not normalized_path or not normalized_scope:
            return False
        if normalized_scope in (".", "*", "**"):
            return True
        if normalized_path == normalized_scope:
            return True
        if _is_directory_scope(normalized_scope):
            prefix = normalized_scope.rstrip("/")
            return bool(prefix) and normalized_path.startswith(prefix + "/")
        return False

    def _load_companion_allowlist() -> list[str]:
        raw = str(os.environ.get("POLARIS_SCOPE_GATE_COMPANION_ALLOWLIST", "") or "").strip()
        defaults = [
            "tests/**",
            "test/**",
            "__tests__/**",
            "*.md",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "pyproject.toml",
            "poetry.lock",
            "requirements.txt",
            "requirements-*.txt",
        ]
        if not raw:
            return defaults
        parsed = [token.strip().replace("\\", "/").lower() for token in re.split(r"[,\n;]+", raw) if token.strip()]
        return parsed or defaults

    companion_allowlist = _load_companion_allowlist()

    def _is_companion_file(path: str) -> bool:
        normalized = _normalize_scope_path(path).lower()
        if not normalized:
            return False
        base = normalized.split("/")[-1]
        return any(
            fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(base, pattern) for pattern in companion_allowlist
        )

    scope_gate_mode = str(os.environ.get("POLARIS_SCOPE_GATE_MODE", "adaptive") or "").strip().lower()
    enforce_scope = scope_gate_mode in ("strict", "hard", "enforce")
    scope_warnings: list[str] = []

    normalized_changed = {_normalize_scope_path(path) for path in changed_files if str(path).strip()}
    normalized_act = {_normalize_scope_path(path) for path in act_files if str(path).strip()}
    normalized_pm = {_normalize_scope_path(path) for path in (pm_target_files or []) if str(path).strip()}

    if require_change and not normalized_changed:
        expected_scope = sorted(normalized_act or normalized_pm)
        if expected_scope:
            return (
                False,
                f"No files changed; expected edits within scope: {expected_scope}",
            )
        return False, "No files changed during apply"

    if normalized_changed and normalized_act and not normalized_changed.issubset(normalized_act):
        extra = sorted(normalized_changed - normalized_act)
        overlap_with_act = bool(normalized_changed.intersection(normalized_act))
        companion_extra = [path for path in extra if _is_companion_file(path)]
        non_companion_extra = [path for path in extra if path not in companion_extra]
        message = f"Changed files exceed act.files scope: {extra}"
        if not enforce_scope and not overlap_with_act:
            return False, message + " (no overlap with act.files)"
        if enforce_scope and non_companion_extra:
            return (
                False,
                f"Changed files exceed act.files scope: {sorted(non_companion_extra)}",
            )
        if enforce_scope and companion_extra and not non_companion_extra:
            scope_warnings.append(
                "scope expanded (companion files): "
                + f"Changed files exceed act.files scope: {sorted(companion_extra)}"
            )
        else:
            scope_warnings.append(message)

    if (
        normalized_changed
        and normalized_pm
        and not all(any(_scope_matches(path, scope) for scope in normalized_pm) for path in normalized_changed)
    ):
        matched_pm_count = sum(
            1 for path in normalized_changed if any(_scope_matches(path, scope) for scope in normalized_pm)
        )
        extra = sorted(
            path for path in normalized_changed if not any(_scope_matches(path, scope) for scope in normalized_pm)
        )
        companion_extra = [path for path in extra if _is_companion_file(path)]
        non_companion_extra = [path for path in extra if path not in companion_extra]
        message = f"Changed files exceed PM target_files scope: {extra}"
        if not enforce_scope and matched_pm_count == 0:
            return False, message + " (no overlap with PM target_files)"
        if enforce_scope and non_companion_extra:
            return (
                False,
                "Changed files exceed PM target_files scope: " + f"{sorted(non_companion_extra)}",
            )
        if enforce_scope and companion_extra and not non_companion_extra:
            scope_warnings.append(
                "scope expanded (companion files): "
                + "Changed files exceed PM target_files scope: "
                + f"{sorted(companion_extra)}"
            )
        else:
            scope_warnings.append(message)

    if scope_warnings:
        return True, "scope expanded: " + "; ".join(scope_warnings)
    return True, ""


def extract_required_evidence(pm_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(pm_payload, dict):
        return {}
    if isinstance(pm_payload.get("required_evidence"), dict):
        return pm_payload.get("required_evidence")  # type: ignore
    tasks = pm_payload.get("tasks")
    if isinstance(tasks, list):
        for item in tasks:
            if isinstance(item, dict) and isinstance(item.get("required_evidence"), dict):
                return item.get("required_evidence")  # type: ignore
    return {}
