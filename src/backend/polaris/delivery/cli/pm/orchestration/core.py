"""Core orchestration logic for PM workflow."""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from polaris.delivery.cli.pm.utils import read_json_file
from polaris.kernelone.fs.control_flags import stop_requested
from polaris.kernelone.fs.text_ops import read_file_safe, write_json_atomic
from polaris.kernelone.storage.io_paths import resolve_artifact_path

from .architect_stage import ensure_docs_ready
from .directive_processing import (
    _build_architect_plan_from_directive,
    _extract_project_goal_from_directive,
)
from .docs_pipeline import _resolve_docs_stage_context
from .helpers import (
    _load_cli_directive,
    _resolve_pm_doc_stage_mode,
)

logger = logging.getLogger(__name__)


def _append_resolved_artifact_candidate(
    candidates: list[str],
    workspace_full: str,
    cache_root_full: str,
    rel_path: str,
) -> None:
    try:
        candidates.append(resolve_artifact_path(workspace_full, cache_root_full, rel_path))
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to resolve artifact candidate %r: %s", rel_path, exc)


def _first_existing_file(candidates: list[str]) -> str:
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return candidates[0] if candidates else ""


def archive_task_history(
    workspace_full: str,
    cache_root_full: str,
    run_id: str,
    iteration: int,
    normalized: dict[str, Any],
    director_result: dict[str, Any] | None,
    timestamp: str,
) -> None:
    """Archive task history to runtime/state/task_history.state.json.

    Args:
        workspace_full: Full path to workspace
        cache_root_full: Full path to cache root
        run_id: Run identifier
        iteration: PM iteration number
        normalized: Normalized task state dictionary
        director_result: Director execution result (optional)
        timestamp: Archive timestamp
    """
    try:
        task_history_path = resolve_artifact_path(
            workspace_full,
            cache_root_full,
            "runtime/state/task_history.state.json",
        )
        tasks = normalized.get("tasks", []) if isinstance(normalized, dict) else []
        total_tasks = len(tasks) if isinstance(tasks, list) else 0
        successes = 0
        total_executed = 0
        if isinstance(director_result, dict):
            successes = int(director_result.get("successes", 0))
            total_executed = int(director_result.get("total", 0))

        history_record = {
            "round_id": run_id,
            "timestamp": timestamp,
            "pm_iteration": iteration,
            "focus": normalized.get("focus", ""),
            "overall_goal": normalized.get("overall_goal", ""),
            "tasks": tasks,
            "execution_summary": {
                "total_tasks": total_tasks,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "success_rate": (successes / total_executed if total_executed > 0 else 0.0),
            },
            "director_results": {
                "run_id": (director_result.get("run_id", "") if isinstance(director_result, dict) else ""),
                "status": (
                    director_result.get("status", "unknown") if isinstance(director_result, dict) else "unknown"
                ),
                "start_time": (director_result.get("start_time", "") if isinstance(director_result, dict) else ""),
                "end_time": (director_result.get("end_time", "") if isinstance(director_result, dict) else ""),
                "successes": successes,
                "total": total_executed,
            },
            "artifacts": {
                "pm_tasks_path": "runtime/contracts/pm_tasks.contract.json",
                "director_result_path": "runtime/results/director.result.json",
                "events_path": "runtime/events/runtime.events.jsonl",
                "dialogue_path": "runtime/events/dialogue.transcript.jsonl",
            },
        }

        existing_history: dict[str, Any] = {"rounds": []}
        if os.path.isfile(task_history_path):
            try:
                with open(task_history_path, encoding="utf-8") as f:
                    existing_history = json.load(f)
                    if not isinstance(existing_history, dict) or "rounds" not in existing_history:
                        existing_history = {"rounds": []}
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "Failed to load existing task history from %r, starting fresh: %s",
                    task_history_path,
                    exc,
                )
                existing_history = {"rounds": []}

        existing_history["rounds"].append(history_record)
        if len(existing_history["rounds"]) > 100:
            existing_history["rounds"] = existing_history["rounds"][-100:]

        write_json_atomic(task_history_path, existing_history)
        logger.info(f"[history] Archived round {run_id} with {total_tasks} tasks")
    except (RuntimeError, ValueError) as e:
        logger.error(f"[history] Error archiving task history: {e}")


def load_state_and_context(
    workspace_full: str,
    cache_root_full: str,
    args: Any,
    iteration: int,
) -> dict[str, Any]:
    """Load state and context for the iteration.

    Args:
        workspace_full: Full path to workspace
        cache_root_full: Full path to cache root
        args: CLI arguments object
        iteration: Current iteration number

    Returns:
        Dictionary containing loaded state and context
    """
    # Resolve paths
    gap_full = resolve_artifact_path(workspace_full, cache_root_full, args.gap_report_path)
    qa_full = resolve_artifact_path(workspace_full, cache_root_full, args.qa_path)

    plan_candidates: list[str] = []
    _append_resolved_artifact_candidate(plan_candidates, workspace_full, cache_root_full, args.plan_path)
    _append_resolved_artifact_candidate(
        plan_candidates,
        workspace_full,
        cache_root_full,
        "workspace/docs/product/plan.md",
    )
    plan_full = _first_existing_file(plan_candidates)

    req_raw = str(getattr(args, "requirements_path", "") or "").strip()
    req_candidates: list[str] = []
    if req_raw:
        if os.path.isabs(req_raw):
            req_candidates.append(req_raw)
        else:
            _append_resolved_artifact_candidate(req_candidates, workspace_full, cache_root_full, req_raw)
            req_candidates.append(os.path.join(workspace_full, req_raw))
            if req_raw.startswith("docs/"):
                _append_resolved_artifact_candidate(
                    req_candidates,
                    workspace_full,
                    cache_root_full,
                    "workspace/" + req_raw,
                )
    for fallback_req_rel in (
        "workspace/docs/product/requirements.md",
        "workspace/docs/10_requirements.md",
    ):
        _append_resolved_artifact_candidate(
            req_candidates,
            workspace_full,
            cache_root_full,
            fallback_req_rel,
        )
    req_full = _first_existing_file(req_candidates)
    pm_out_full = resolve_artifact_path(workspace_full, cache_root_full, args.pm_out)
    pm_state_full = resolve_artifact_path(workspace_full, cache_root_full, args.state_path)

    run_id = f"pm-{iteration:05d}"

    # Read files
    requirements = read_file_safe(req_full) or ""
    plan_text = read_file_safe(plan_full) or ""
    gap_report = read_file_safe(gap_full) or ""
    last_qa = read_file_safe(qa_full) or ""

    # One-shot directive channel: keeps large inputs in memory and avoids
    # requiring file writes before PM planning starts.
    directive = _load_cli_directive(args)
    directive_goal = _extract_project_goal_from_directive(directive)
    start_from = str(getattr(args, "start_from", "pm") or "pm").strip().lower()
    if directive and start_from == "architect":
        requirements = directive_goal or directive
        synthetic_plan = _build_architect_plan_from_directive(directive_goal or directive)
        if synthetic_plan:
            plan_text = synthetic_plan

    # Plan sanity check
    if plan_text and workspace_full:
        _plan_path_refs = re.findall(
            r"(?:^|\s|`|\()([a-zA-Z0-9_./-]+\.(?:ts|tsx|js|jsx|py|go|java|rs|cpp|c|cs|rb|php|swift|kt))\b",
            plan_text,
        )
        if _plan_path_refs:
            _plan_path_missing = sum(
                1 for p in _plan_path_refs if not os.path.exists(os.path.join(workspace_full, p.lstrip("/")))
            )
            _plan_path_missing_ratio = _plan_path_missing / len(_plan_path_refs)
            if _plan_path_missing_ratio > 0.5:
                _plan_sanity_warning = (
                    "[PLAN_SANITY_WARNING] The paths referenced in contracts/plan.md mostly do not exist in the project "
                    f"({_plan_path_missing}/{len(_plan_path_refs)} missing). "
                    "These are likely template example paths, NOT real project paths. "
                    "You MUST ignore all file paths from contracts/plan.md. "
                    "Derive tasks solely from the requirements below and the actual project structure.\n\n"
                )
                plan_text = _plan_sanity_warning + plan_text

    # Load state
    pm_state = read_json_file(pm_state_full) or {}
    if getattr(args, "clear_spin_guard", False):
        pm_state.pop("pm_spin_guard_active", None)
        pm_state.pop("pm_spin_guard_reason", None)
        pm_state["pm_no_progress_count"] = 0
        pm_state["last_updated_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_json_atomic(pm_state_full, pm_state)

    # Load last tasks
    last_tasks = read_json_file(pm_out_full)

    docs_stage: dict[str, Any] = {
        "enabled": False,
        "mode": _resolve_pm_doc_stage_mode(),
    }
    if start_from == "pm":
        requirements, plan_text, docs_stage = _resolve_docs_stage_context(
            workspace_full=workspace_full,
            cache_root_full=cache_root_full,
            iteration=iteration,
            last_tasks=last_tasks,
            requirements=requirements,
            plan_text=plan_text,
        )

    return {
        "requirements": requirements,
        "plan_text": plan_text,
        "gap_report": gap_report,
        "last_qa": last_qa,
        "last_tasks": last_tasks,
        "pm_state": pm_state,
        "pm_state_full": pm_state_full,
        "plan_full": plan_full,
        "pm_out_full": pm_out_full,
        "run_id": run_id,
        "docs_stage": docs_stage,
    }


def check_spin_guard(pm_state: dict[str, Any]) -> str | None:
    """Check if spin guard is active.

    Args:
        pm_state: PM state dictionary

    Returns:
        Reason string if spin guard is active, None otherwise
    """
    if bool(pm_state.get("pm_spin_guard_active")):
        return str(pm_state.get("pm_spin_guard_reason") or "spin_guard_active").strip()
    return None


def check_stop_conditions(
    workspace_full: str,
    pm_state: dict[str, Any],
    consecutive_failures: int,
    consecutive_blocked: int,
    args: Any,
) -> int | None:
    """Check stop conditions.

    Args:
        workspace_full: Full path to workspace
        pm_state: PM state dictionary
        consecutive_failures: Count of consecutive failures
        consecutive_blocked: Count of consecutive blocked iterations
        args: CLI arguments object

    Returns:
        Exit code if should stop, None otherwise
    """
    if stop_requested(workspace_full):
        return 3
    if args.max_failures and consecutive_failures >= args.max_failures:
        return 2
    if args.max_blocked and consecutive_blocked >= args.max_blocked:
        return 2
    return None


def update_consecutive_counters(
    director_result: dict[str, Any] | None,
    last_signature: str,
    pm_state: dict[str, Any],
) -> tuple[int, int]:
    """Update consecutive failure/blocked counters.

    Args:
        director_result: Director execution result (optional)
        last_signature: Last task signature for comparison
        pm_state: PM state dictionary

    Returns:
        Tuple of (consecutive_failures, consecutive_blocked)
    """
    consecutive_failures = int(pm_state.get("consecutive_failures") or 0)
    consecutive_blocked = int(pm_state.get("consecutive_blocked") or 0)

    if isinstance(director_result, dict):
        last_director_status = str(director_result.get("status") or "").strip().lower()
        last_director_task_fingerprint = str(director_result.get("task_fingerprint") or "").strip()
        last_director_task_id = str(director_result.get("task_id") or "").strip()
        last_director_task_title = str(director_result.get("task_title") or "").strip()
        signature = last_director_task_fingerprint or last_director_task_id or last_director_task_title

        if last_director_status == "fail":
            if signature and signature == last_signature:
                consecutive_failures += 1
            else:
                consecutive_failures = 1
            consecutive_blocked = 0
        elif last_director_status == "blocked":
            if signature and signature == last_signature:
                consecutive_blocked += 1
            else:
                consecutive_blocked = 1
            consecutive_failures = 0
        elif last_director_status == "success":
            consecutive_failures = 0
            consecutive_blocked = 0

    return consecutive_failures, consecutive_blocked


__all__ = [
    "archive_task_history",
    "check_spin_guard",
    "check_stop_conditions",
    "ensure_docs_ready",
    "load_state_and_context",
    "update_consecutive_counters",
]
