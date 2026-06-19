"""Deterministic empty-task recovery for the PM planning pipeline.

Extracted from ``orchestration_engine``. When the PM role yields zero tasks
while requirements are non-empty, these helpers synthesize a deterministic
requirements fallback contract (grounded in bounded workspace files) and run
the PM task quality gate over the repaired contract.

Bodies are byte-for-byte identical to the original ``orchestration_engine``
definitions and are re-exported from that module to preserve the canonical
import path.
"""

from __future__ import annotations

import os
from typing import Any, cast

from polaris.cells.orchestration.pm_planning.public.service import (
    autofix_pm_contract_for_quality,
    evaluate_pm_task_quality,
)
from polaris.delivery.cli.pm.tasks_utils import (
    build_requirements_fallback_payload,
)

# Constants
_PM_TASK_QUALITY_MODE_ENV = "KERNELONE_PM_TASK_QUALITY_MODE"
_PM_TASK_QUALITY_RETRIES_ENV = "KERNELONE_PM_TASK_QUALITY_RETRIES"
_PM_TASK_QUALITY_MODES = {"off", "warn", "strict"}
_PM_TASK_QUALITY_DEFAULT_MODE = "strict"
_FALLBACK_WORKSPACE_FILE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
}

_FALLBACK_WORKSPACE_SKIP_DIRS = {
    ".git",
    ".polaris",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}


def _extract_normalized_tasks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        return []
    return cast("list[dict[str, Any]]", raw_tasks)


def _collect_workspace_file_candidates(workspace_full: str, limit: int = 256) -> list[str]:
    """Collect bounded workspace file paths for deterministic PM fallback grounding."""
    root = os.path.abspath(str(workspace_full or "").strip())
    if not root or not os.path.isdir(root):
        return []
    selected: list[str] = []
    for current_dir, dir_names, file_names in os.walk(root):
        dir_names[:] = [
            name for name in sorted(dir_names) if name not in _FALLBACK_WORKSPACE_SKIP_DIRS and not name.startswith(".")
        ]
        rel_dir = os.path.relpath(current_dir, root)
        depth = 0 if rel_dir == "." else len(rel_dir.split(os.sep))
        if depth >= 6:
            dir_names[:] = []
        for file_name in sorted(file_names):
            if len(selected) >= limit:
                return selected
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in _FALLBACK_WORKSPACE_FILE_EXTENSIONS:
                continue
            full_path = os.path.join(current_dir, file_name)
            rel_path = os.path.relpath(full_path, root).replace(os.sep, "/")
            selected.append(rel_path)
    return selected


def _apply_requirements_fallback_for_empty_tasks(
    *,
    exit_code: int,
    normalized: dict[str, Any],
    normalized_tasks: list[dict[str, Any]],
    requirements: str,
    iteration: int,
    timestamp: str,
    plan_text: str,
    docs_stage: dict[str, Any],
    run_id: str,
    workspace_files: list[str] | None = None,
) -> tuple[int, dict[str, Any], list[dict[str, Any]], bool]:
    """Recover an empty PM task contract from requirements when possible."""
    if not str(requirements or "").strip() or len(normalized_tasks) > 0:
        return exit_code, normalized, normalized_tasks, False

    original_exit_code = int(exit_code)
    original_notes = str(normalized.get("notes") or "").strip()
    raw_original_warnings = normalized.get("schema_warnings")
    original_warnings: list[str] = (
        [str(item) for item in raw_original_warnings if str(item).strip()]
        if isinstance(raw_original_warnings, list)
        else []
    )

    fallback_payload = build_requirements_fallback_payload(
        requirements=requirements,
        iteration=iteration,
        timestamp=timestamp,
        plan_text=plan_text,
        docs_stage=docs_stage,
        workspace_files=workspace_files,
    )
    if not isinstance(fallback_payload, dict):
        return exit_code, normalized, normalized_tasks, False

    fallback_payload["run_id"] = run_id
    fallback_payload["pm_iteration"] = iteration
    fallback_tasks = _extract_normalized_tasks(fallback_payload)

    if original_notes:
        fallback_notes = str(fallback_payload.get("notes") or "").strip()
        recovery_label = "Recovered PM parse context"
        if (
            "invoke failed" in str(original_notes).lower()
            or "provider invocation failed" in str(original_notes).lower()
        ):
            recovery_label = "Original PM provider failure context"
        fallback_payload["notes"] = "; ".join(
            part
            for part in (
                fallback_notes,
                f"{recovery_label}: {original_notes}",
            )
            if part
        )

    if original_exit_code != 0 or original_warnings:
        fallback_warnings = []
        raw_fallback_warnings = fallback_payload.get("schema_warnings")
        if isinstance(raw_fallback_warnings, list):
            fallback_warnings = [str(item) for item in raw_fallback_warnings if str(item).strip()]
        fallback_warnings.extend(original_warnings)
        if original_exit_code != 0:
            fallback_warnings.append(
                f"PM planning failed with exit code {original_exit_code}; deterministic requirements fallback used."
            )
        fallback_payload["schema_warnings"] = fallback_warnings
        fallback_payload["schema_warning_count"] = len(fallback_warnings)

    recovered_exit_code = 0 if fallback_tasks else exit_code
    return recovered_exit_code, fallback_payload, fallback_tasks, bool(fallback_tasks)


def _resolve_outer_pm_task_quality_mode() -> str:
    raw = str(os.environ.get(_PM_TASK_QUALITY_MODE_ENV, _PM_TASK_QUALITY_DEFAULT_MODE) or "").strip().lower()
    if raw not in _PM_TASK_QUALITY_MODES:
        return _PM_TASK_QUALITY_DEFAULT_MODE
    return raw


def _apply_quality_gate_to_requirements_fallback(
    *,
    normalized: dict[str, Any],
    workspace_full: str,
    docs_stage: dict[str, Any],
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    autofix_stats = autofix_pm_contract_for_quality(normalized, workspace_full=workspace_full)
    quality_report = evaluate_pm_task_quality(
        normalized,
        docs_stage=docs_stage if isinstance(docs_stage, dict) else {},
        workspace_full=workspace_full,
    )
    quality_mode = _resolve_outer_pm_task_quality_mode()
    normalized["quality_gate"] = {
        "mode": quality_mode,
        "attempt": "requirements_fallback",
        "max_attempts": "requirements_fallback",
        "passed": bool(quality_report.get("ok")),
        "score": int(quality_report.get("score") or 0),
        "summary": str(quality_report.get("summary") or "").strip(),
        "critical_issue_count": len(quality_report.get("critical_issues") or []),
        "warning_count": len(quality_report.get("warnings") or []),
    }

    if not bool(quality_report.get("ok")):
        raw_schema_warnings = normalized.get("schema_warnings")
        schema_warnings = (
            [str(item) for item in raw_schema_warnings if str(item).strip()]
            if isinstance(raw_schema_warnings, list)
            else []
        )
        for item in quality_report.get("critical_issues") or []:
            token = str(item).strip()
            if token:
                schema_warnings.append(f"PM quality issue: {token}")
        normalized["schema_warnings"] = schema_warnings
        normalized["schema_warning_count"] = len(schema_warnings)
        normalized["terminal_error_code"] = "PM_TASK_QUALITY_FAILED"
        normalized["terminal_error"] = str(quality_report.get("summary") or "").strip()

    exit_code = 0 if bool(quality_report.get("ok")) or quality_mode in {"off", "warn"} else 1
    quality_payload = {
        "autofix_stats": autofix_stats,
        "quality": {
            "ok": bool(quality_report.get("ok")),
            "score": int(quality_report.get("score") or 0),
            "summary": str(quality_report.get("summary") or "").strip(),
            "critical_issues": list(quality_report.get("critical_issues") or [])[:8],
            "warnings": list(quality_report.get("warnings") or [])[:8],
        },
    }
    return exit_code, _extract_normalized_tasks(normalized), quality_payload
