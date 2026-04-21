"""Iteration state management for PM orchestration.

This module handles iteration finalization, error handling, spin guard,
and manual intervention state management.

Design invariant: this file MUST NOT contain any import of
``polaris.delivery.*``.  Validated by
``tests/test_pm_dispatch_no_delivery_import.py``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

from polaris.cells.runtime.state_owner.public.service import (
    merge_director_result_into_pm_state,
    write_json_atomic,
)

# kernelone / infrastructure imports – these are not delivery layer
from polaris.kernelone.events import emit_dialogue, emit_event, emit_llm_event
from polaris.kernelone.fs.control_flags import pause_flag_path
from polaris.kernelone.fs.jsonl.ops import append_jsonl


# Cell-internal port – no delivery dependency
from polaris.cells.orchestration.pm_dispatch.internal.pm_task_utils import (
    PM_SPIN_GUARD_STATUS,
    append_pm_report,
    get_task_signature,
)

if TYPE_CHECKING:
    import argparse

__all__ = [
    "clear_manual_intervention",
    "finalize_iteration",
    "handle_invoke_error",
    "handle_spin_guard",
    "record_stop",
]

logger = logging.getLogger(__name__)


def _get_shangshuling_port() -> Any:
    """Return the cell-local Shangshuling port."""
    from polaris.cells.orchestration.pm_dispatch.internal.shangshuling_registry import (
        get_shangshuling_port,
    )

    return get_shangshuling_port()


def finalize_iteration(
    args: argparse.Namespace,
    workspace_full: str,
    iteration: int,
    status: str,
    state: Any,
    context: dict[str, Any],
    result: dict[str, Any] | None = None,
    shangshuling_port: Any | None = None,
) -> dict[str, Any]:
    """Finalize PM iteration and write state.

    This is the public API for finalizing an iteration. It coordinates
    all the internal state management functions and provides a clean
    interface for the orchestration engine.

    Args:
        args: Command line arguments namespace
        workspace_full: Absolute path to workspace
        iteration: Current iteration number
        status: Iteration status (completed, failed, etc.)
        state: PM state object (will be mutated)
        context: Context dictionary with paths and metadata
        result: Optional director result to merge into state
        shangshuling_port: Optional pre-injected ShangshulingPort; when None,
            the delivery adapter is loaded lazily.

    Returns:
        Updated state dictionary
    """
    pm_state: dict[str, Any] = state if isinstance(state, dict) else {}

    # Extract paths from context
    pm_state_full = context.get("pm_state_full", "")
    pm_history_full = context.get("pm_history_full", "")
    normalized = context.get("normalized", {})
    start_timestamp = context.get("start_timestamp", datetime.now().isoformat())
    cache_root_full = context.get("cache_root_full", "")
    run_id = context.get("run_id", "")
    exit_code = context.get("exit_code", 0 if status == "completed" else 1)
    backend = context.get("backend", "")
    events_seq_start = context.get("events_seq_start", 0)
    run_events = context.get("run_events", "")
    pm_llm_events_full = context.get("pm_llm_events_full", "")
    trace_service = context.get("trace_service")

    port = shangshuling_port if shangshuling_port is not None else _get_shangshuling_port()

    # Call internal implementation
    _finalize_iteration(
        pm_state=pm_state,
        pm_state_full=pm_state_full,
        pm_history_full=pm_history_full,
        normalized=normalized,
        start_timestamp=start_timestamp,
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        run_id=run_id,
        iteration=iteration,
        director_result=result,
        exit_code=exit_code,
        backend=backend,
        events_seq_start=events_seq_start,
        run_events=run_events,
        pm_llm_events_full=pm_llm_events_full,
        shangshuling_port=port,
        trace_service=trace_service,
    )

    return pm_state


def _finalize_iteration(
    *,
    pm_state: dict[str, Any],
    pm_state_full: str,
    pm_history_full: str,
    normalized: dict[str, Any],
    start_timestamp: str,
    workspace_full: str,
    cache_root_full: str,
    run_id: str,
    iteration: int,
    director_result: dict[str, Any] | None,
    exit_code: int,
    backend: str,
    events_seq_start: int,
    run_events: str,
    pm_llm_events_full: str,
    shangshuling_port: Any,
    trace_service: Any,
) -> None:
    """Finalize iteration state, history, and telemetry."""
    tasks = normalized.get("tasks") if isinstance(normalized, dict) else []
    tasks = tasks if isinstance(tasks, list) else []

    pm_state["pm_iteration"] = iteration
    pm_state["last_task_signature"] = get_task_signature(tasks)
    pm_state["last_task_fingerprint"] = pm_state["last_task_signature"]
    merge_director_result_into_pm_state(pm_state, director_result)
    pm_state["last_updated_ts"] = start_timestamp
    write_json_atomic(pm_state_full, pm_state)

    shangshuling_port.archive_task_history(
        workspace_full,
        cache_root_full,
        run_id,
        iteration,
        normalized,
        director_result,
        start_timestamp,
        trace_service=trace_service,
    )

    if pm_history_full:
        append_jsonl(
            pm_history_full,
            {
                "timestamp": start_timestamp,
                "pm_iteration": iteration,
                "focus": normalized.get("focus"),
                "tasks": tasks,
            },
        )

    try:
        from polaris.kernelone.audit.invariant_sentinel import run_invariant_sentinel

        try:
            from polaris.kernelone.storage import (
                resolve_workspace_persistent_path,
            )
        except (RuntimeError, ValueError):  # pragma: no cover - script-mode fallback
            from polaris.kernelone.storage import resolve_workspace_persistent_path

        memory_path = resolve_workspace_persistent_path(
            workspace_full,
            "workspace/brain/MEMORY.jsonl",
        )
        run_invariant_sentinel(
            events_path=run_events,
            run_id=run_id,
            step=iteration,
            pm_task_path="",
            contract_fingerprint="",
            events_seq_start=events_seq_start,
            events_size_start=0,
            memory_path=memory_path,
            director_result_path="",
        )
    except (RuntimeError, ValueError):
        logger.error(
            "iteration_state: run_invariant_sentinel failed: run_id=%s iteration=%d",
            run_id,
            iteration,
            exc_info=True,
        )

    stage = "completed" if exit_code == 0 else "failed"
    emit_llm_event(
        pm_llm_events_full,
        event="iteration",
        role="pm",
        run_id=run_id,
        iteration=iteration,
        source="system",
        data={
            "iteration": iteration,
            "timestamp": start_timestamp,
            "backend": backend,
            "stage": stage,
            "exit_code": exit_code,
            "task_count": len(tasks),
        },
    )

    # Phase 3.2: Trigger task snapshot archive (async, non-blocking)
    # Archive format: pm-{iteration:05d}-{timestamp}
    # Source: runtime/tasks/plan.json + runtime/tasks/task_*.json
    try:
        snapshot_timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        snapshot_id = f"pm-{iteration:05d}-{snapshot_timestamp}"
        tasks_dir = os.path.join(workspace_full, "runtime", "tasks")
        plan_path = os.path.join(tasks_dir, "plan.json")

        # Only trigger archive if tasks directory exists
        if os.path.isdir(tasks_dir):
            from polaris.cells.archive.task_snapshot_archive.public.service import (
                trigger_task_snapshot_archive,
            )

            trigger_task_snapshot_archive(
                workspace=workspace_full,
                snapshot_id=snapshot_id,
                source_tasks_dir=tasks_dir,
                source_plan_path=plan_path if os.path.isfile(plan_path) else None,
                reason=stage,
            )
    except (RuntimeError, ValueError):
        # Archive failure must not affect main flow, but log for observability.
        logger.warning(
            "iteration_state: task snapshot archive failed: run_id=%s iteration=%d",
            run_id,
            iteration,
            exc_info=True,
        )


def handle_invoke_error(
    *,
    error: str,
    run_events: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
    workspace_full: str,
    pm_state: dict[str, Any],
    pm_state_full: str,
    backend_label: str,
    start_timestamp: str,
    pm_llm_events_full: str,
) -> tuple[bool, dict[str, Any]]:
    """Handle invoke error and determine if retry is needed.

    Args:
        error: Error message string
        run_events: Path to events JSONL file
        dialogue_full: Path to dialogue JSONL file
        run_id: Current run identifier
        iteration: Current iteration number
        workspace_full: Absolute workspace path
        pm_state: Current PM state dict
        pm_state_full: Path to PM state file
        backend_label: Backend identifier label
        start_timestamp: ISO timestamp string
        pm_llm_events_full: Path to LLM events file

    Returns:
        Tuple of (should_retry, updated_state)
    """
    _handle_invoke_error(
        error=error,
        run_events=run_events,
        dialogue_full=dialogue_full,
        run_id=run_id,
        iteration=iteration,
        workspace_full=workspace_full,
        pm_state=pm_state,
        pm_state_full=pm_state_full,
        backend_label=backend_label,
        start_timestamp=start_timestamp,
        pm_llm_events_full=pm_llm_events_full,
    )

    # Currently no retry logic - could be extended
    return False, pm_state


def _handle_invoke_error(
    *,
    error: str,
    run_events: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
    workspace_full: str,
    pm_state: dict[str, Any],
    pm_state_full: str,
    backend_label: str,
    start_timestamp: str,
    pm_llm_events_full: str,
) -> None:
    """Handle backend invocation error."""
    emit_event(
        run_events,
        kind="status",
        actor="PM",
        name="planning_invoke_failed",
        refs={"run_id": run_id, "phase": "planning"},
        summary="PM planning invoke failed",
        ok=False,
        output={"backend": backend_label, "stage": "invoke"},
        error=error,
    )
    emit_dialogue(
        dialogue_full,
        speaker="PM",
        type="warning",
        text=f"PM planning invoke failed: {error}",
        summary="PM invoke failed",
        run_id=run_id,
        pm_iteration=iteration,
        refs={"phase": "planning"},
        meta={"error_code": "PM_LLM_INVOKE_FAILED"},
    )

    pm_state["last_pm_error_code"] = "PM_LLM_INVOKE_FAILED"
    pm_state["last_pm_error_detail"] = error
    pm_state["last_updated_ts"] = start_timestamp
    write_json_atomic(pm_state_full, pm_state)

    emit_llm_event(
        pm_llm_events_full,
        event="invoke_error",
        role="pm",
        run_id=run_id,
        iteration=iteration,
        source="system",
        data={
            "iteration": iteration,
            "timestamp": start_timestamp,
            "backend": backend_label,
            "error": error,
        },
    )

    try:
        from polaris.kernelone.prompts.meta_prompting import append_meta_prompt_hint

        append_meta_prompt_hint(
            workspace_root=workspace_full,
            role="pm",
            hint=(
                "当 LLM 调用失败时，必须返回严格 JSON 并优先输出基于当前阶段文档的最小可执行任务，"
                "禁止空任务或非结构化输出。"
            ),
            trigger="pm_llm_invoke_failed",
            run_id=run_id,
            pm_iteration=iteration,
            source="pm.orchestration_engine",
        )
    except (RuntimeError, ValueError):
        # Meta-prompting hint write failure is non-critical.
        logger.debug(
            "iteration_state: append_meta_prompt_hint failed: run_id=%s iteration=%d",
            run_id,
            iteration,
            exc_info=True,
        )


def handle_spin_guard(
    pm_state: dict[str, Any],
    reason: str,
    pm_report_full: str,
    run_events: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
    args: argparse.Namespace,
) -> bool:
    """Handle spin guard condition.

    Args:
        pm_state: Current PM state dict
        reason: Reason for spin guard activation
        pm_report_full: Path to PM report file
        run_events: Path to events JSONL file
        dialogue_full: Path to dialogue JSONL file
        run_id: Current run identifier
        iteration: Current iteration number
        args: Command line arguments

    Returns:
        True if spin guard was handled successfully
    """
    return _handle_spin_guard(
        pm_state=pm_state,
        reason=reason,
        pm_report_full=pm_report_full,
        run_events=run_events,
        dialogue_full=dialogue_full,
        run_id=run_id,
        iteration=iteration,
        args=args,
    )


def _handle_spin_guard(
    pm_state: dict[str, Any],
    reason: str,
    pm_report_full: str,
    run_events: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
    args: argparse.Namespace,
) -> bool:
    """Handle spin guard activation."""
    append_pm_report(
        pm_report_full,
        f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (iteration {iteration}) - halted\n"
        + f"Status: {PM_SPIN_GUARD_STATUS}. Manual intervention required.\n"
        + f"Reason: {reason}\n",
    )
    emit_event(
        run_events,
        kind="status",
        actor="PM",
        name="spin_guard",
        refs={"run_id": run_id, "phase": "planning"},
        summary="PM spin guard active; iteration halted",
        ok=False,
        output={
            "reason": reason,
            "pm_no_progress_count": pm_state.get("pm_no_progress_count", 0),
            "max_spin_rounds": int(getattr(args, "max_spin_rounds", 0) or 0),
        },
        error=PM_SPIN_GUARD_STATUS,
    )
    emit_dialogue(
        dialogue_full,
        speaker="PM",
        type="warning",
        text="PM spin guard is active. Clear guard after manual intervention, then retry.",
        summary="PM spin guard active",
        run_id=run_id,
        pm_iteration=iteration,
        refs={"phase": "planning", "files": ["runtime/state/pm.state.json"]},
        meta={"error_code": PM_SPIN_GUARD_STATUS},
    )
    return True


def record_stop(
    pm_report_full: str,
    timestamp: str,
    iteration: int,
    pm_state: dict[str, Any],
    pm_state_full: str,
    exit_code: int,
) -> None:
    """Record stop condition in report/state.

    Args:
        pm_report_full: Path to PM report file
        timestamp: ISO timestamp string
        iteration: Current iteration number
        pm_state: Current PM state dict
        pm_state_full: Path to PM state file
        exit_code: Exit code to record
    """
    _record_stop(
        pm_report_full=pm_report_full,
        timestamp=timestamp,
        iteration=iteration,
        pm_state=pm_state,
        pm_state_full=pm_state_full,
        exit_code=exit_code,
    )


def _record_stop(
    pm_report_full: str,
    timestamp: str,
    iteration: int,
    pm_state: dict[str, Any],
    pm_state_full: str,
    exit_code: int,
) -> None:
    """Record stop condition in report/state."""
    append_pm_report(
        pm_report_full,
        f"\n## {timestamp} (iteration {iteration}) - halted\n" + f"Status: stopped with exit code {exit_code}\n",
    )
    pm_state["pm_iteration"] = iteration
    pm_state["last_updated_ts"] = timestamp
    write_json_atomic(pm_state_full, pm_state)


def clear_manual_intervention(
    pm_state: dict[str, Any],
    pm_state_full: str,
    workspace_full: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
) -> None:
    """Clear manual intervention state and pause flag.

    Args:
        pm_state: Current PM state dict (will be mutated)
        pm_state_full: Path to PM state file
        workspace_full: Absolute workspace path
        dialogue_full: Path to dialogue JSONL file
        run_id: Current run identifier
        iteration: Current iteration number
    """
    _clear_manual_intervention(
        pm_state=pm_state,
        pm_state_full=pm_state_full,
        workspace_full=workspace_full,
        dialogue_full=dialogue_full,
        run_id=run_id,
        iteration=iteration,
    )


def _clear_manual_intervention(
    pm_state: dict[str, Any],
    pm_state_full: str,
    workspace_full: str,
    dialogue_full: str,
    run_id: str,
    iteration: int,
) -> None:
    """Clear manual intervention state and pause flag."""
    pm_state["awaiting_manual_intervention"] = False
    pm_state.pop("awaiting_manual_intervention_since", None)
    pm_state.pop("manual_intervention_reason_code", None)
    pm_state.pop("manual_intervention_detail", None)
    write_json_atomic(pm_state_full, pm_state)

    pause_full = pause_flag_path(workspace_full)
    try:
        if os.path.exists(pause_full):
            os.remove(pause_full)
    except (OSError, RuntimeError, ValueError):
        # Log at warning level so operators can observe and investigate, but do not
        # block the resume flow (removing the pause flag is best-effort).
        logger.warning(
            "iteration_state: failed to remove pause flag: workspace=%s path=%s",
            workspace_full,
            pause_full,
            exc_info=True,
        )

    emit_dialogue(
        dialogue_full,
        speaker="PM",
        type="note",
        text="Manual intervention acknowledged. Resuming the previously paused task queue.",
        summary="Manual resume",
        run_id=run_id,
        pm_iteration=iteration,
        refs={"phase": "resume", "files": ["contracts/pm_tasks.contract.json"]},
        meta={"manual_resume": True},
    )
