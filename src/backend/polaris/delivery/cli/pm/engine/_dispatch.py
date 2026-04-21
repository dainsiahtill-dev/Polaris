"""Internal dispatch implementation for Polaris engine.

This module contains the _run_single_task and dispatch_director_tasks
implementation, extracted for maintainability.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import TYPE_CHECKING, Any, cast

from polaris.delivery.cli.pm.engine.completion_lock import (
    _apply_task_stability_filters,
    _completion_lock_state_path,
    _load_completion_lock_state,
    _save_completion_lock_state,
    _update_completion_lock_state,
)
from polaris.delivery.cli.pm.engine.core import (
    PolarisEngine,
    _collect_active_director_tasks,
    _resolve_preflight_paths,
)
from polaris.delivery.cli.pm.engine.delivery_floor import (
    _evaluate_delivery_floor,
)
from polaris.delivery.cli.pm.engine.helpers import (
    _DIRECTOR_RESULT_STATUSES,
    _env_float,
    _join_non_empty,
    _normalize_failure_detail,
    _safe_int,
)
from polaris.delivery.cli.pm.engine.taskboard import (
    _build_taskboard_runtime,
    _select_taskboard_ready_batch,
)
from polaris.delivery.cli.pm.engine.tri_council import (
    _DEFAULT_MAX_DIRECTOR_RETRIES,
    _resolve_tri_council_policy,
    _run_tri_council_round,
)
from polaris.delivery.cli.pm.qa_auditor import (
    evaluate_qa_contract,
    normalize_qa_contract,
    normalize_qa_mode,
)
from polaris.delivery.cli.pm.utils import (
    _slug_token,
    normalize_path_list,
    read_json_file,
)
from polaris.kernelone.events import emit_event
from polaris.kernelone.fs.jsonl.ops import append_jsonl
from polaris.kernelone.fs.text_ops import ensure_parent_dir, write_json_atomic

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def _dispatch_director_tasks_impl(
    engine: PolarisEngine,
    *,
    args: argparse.Namespace,
    workspace_full: str,
    run_dir: str,
    pm_payload: dict[str, Any],
    events_path: str = "",
    dialogue_path: str = "",
    plan_path: str = "",
    pm_tasks_paths: Sequence[str] | None = None,
    runtime_status_path: str = "",
    progress_payload_paths: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Implementation of dispatch_director_tasks."""
    run_id = str(pm_payload.get("run_id") or "").strip()
    pm_iteration = int(pm_payload.get("pm_iteration") or 0)
    engine.bind_run_context(
        run_id=run_id,
        pm_iteration=pm_iteration,
        run_dir=run_dir,
        runtime_status_path=runtime_status_path,
        events_path=events_path,
    )
    engine.register_role("PM", status="running", detail="PM contract ready, awaiting dispatch")
    engine.register_role("Director", status="idle", detail="Waiting for dispatch")
    engine.register_role(
        "ChiefEngineer",
        status="idle",
        detail="Waiting for Director escalation",
    )
    engine.register_role("Architect", status="idle", detail="Waiting for PM escalation")
    engine.register_role("Human", status="idle", detail="Waiting for manual escalation")
    engine.register_role("QA", status="idle", detail="Waiting for Director output")
    engine._update_engine_status(phase="dispatching", running=True, error="")
    engine.update_role_status(
        "PM",
        status="dispatching",
        running=True,
        detail="Engine is preparing Director task dispatch",
    )

    director_tasks = _collect_active_director_tasks(pm_payload)
    completion_lock_path = _completion_lock_state_path(workspace_full)
    completion_state = _load_completion_lock_state(completion_lock_path)
    director_tasks, filter_meta = _apply_task_stability_filters(
        director_tasks,
        pm_payload=pm_payload,
        completion_state=completion_state,
    )

    # Handle empty task case
    if not director_tasks:
        dependency_blocked = int(filter_meta.get("dependency_blocked") or 0)
        budget_limited = int(filter_meta.get("budget_limited") or 0)
        filter_blocked = dependency_blocked > 0 or budget_limited > 0
        blocked_error_code = ""
        blocked_detail = ""
        blocked_count = 0
        if dependency_blocked > 0:
            blocked_error_code = "DIRECTOR_DEPENDENCY_UNRESOLVED"
            blocked_detail = f"No dependency-closed Director tasks available ({dependency_blocked} blocked)"
            blocked_count = dependency_blocked
        elif budget_limited > 0:
            blocked_error_code = "DIRECTOR_DISPATCH_BUDGET_EXHAUSTED"
            blocked_detail = f"No Director tasks available within stability budget ({budget_limited} skipped)"
            blocked_count = budget_limited
        summary = {
            "run_id": run_id,
            "pm_iteration": pm_iteration,
            "total": 0,
            "successes": 0 if not filter_blocked else 1,
            "failures": 0,
            "blocked": blocked_count,
            "degraded_to_single": False,
            "config": engine.config.to_payload(),
            "batches": [],
            "stability_filters": filter_meta,
            "dispatch_blocked": bool(filter_blocked),
        }
        if filter_blocked:
            emit_event(
                events_path,
                kind="status",
                actor="Engine",
                name="director_dispatch_blocked",
                refs={"run_id": run_id, "phase": "engine_dispatch"},
                summary=blocked_detail,
                ok=False,
                output=summary,
                error=blocked_error_code,
            )
        engine.update_role_status(
            "PM",
            status="blocked" if filter_blocked else "completed",
            running=False,
            detail=blocked_detail if filter_blocked else "No Director tasks to dispatch",
        )
        engine.update_role_status(
            "Director",
            status="blocked" if filter_blocked else "idle",
            running=False,
            task_id="",
            task_title="",
            detail=blocked_detail if filter_blocked else "No tasks in queue",
        )
        engine.update_role_status(
            "QA",
            status="blocked" if filter_blocked else "idle",
            running=False,
            task_id="",
            task_title="",
            detail=blocked_detail if filter_blocked else "No tasks in queue",
        )
        engine._update_engine_status(
            phase="failed" if filter_blocked else "completed",
            running=False,
            summary=summary,
            error=blocked_error_code,
        )
        return {
            "summary": summary,
            "records": [],
            "status_updates": {},
            "failure_info": {},
            "director_result": None,
            "hard_failure": bool(filter_blocked),
        }

    # Preflight check
    preflight = _resolve_preflight_paths(
        args=args,
        workspace_full=workspace_full,
        plan_path=plan_path,
        pm_tasks_paths=pm_tasks_paths,
    )
    autofixed = preflight.get("autofixed")
    if isinstance(autofixed, list) and autofixed:
        emit_event(
            events_path,
            kind="status",
            actor="Engine",
            name="engine_preflight_autofixed",
            refs={"run_id": run_id, "phase": "engine_dispatch"},
            summary="Engine preflight auto-fixed missing prerequisites",
            ok=True,
            output={
                "autofixed": [str(item) for item in autofixed if str(item).strip()],
                "resolved_plan_path": str(preflight.get("resolved_plan_path") or "").strip(),
            },
        )
    if not bool(preflight.get("ok")):
        missing = preflight.get("missing")
        missing_items = missing if isinstance(missing, list) else []
        missing_text = ", ".join(str(item) for item in missing_items if str(item).strip()) or "required files"
        summary = {
            "run_id": run_id,
            "pm_iteration": pm_iteration,
            "total": 0,
            "successes": 0,
            "failures": 1,
            "blocked": 0,
            "degraded_to_single": False,
            "config": engine.config.to_payload(),
            "batches": [],
            "preflight": preflight,
        }
        emit_event(
            events_path,
            kind="status",
            actor="Engine",
            name="engine_preflight_failed",
            refs={"run_id": run_id, "phase": "engine_dispatch"},
            summary=f"Engine preflight failed: {missing_text}",
            ok=False,
            output=preflight,
            error="ENGINE_PREFLIGHT_FAILED",
        )
        engine.update_role_status(
            "PM",
            status="blocked",
            running=False,
            detail=f"Engine preflight failed: {missing_text}",
        )
        engine.update_role_status(
            "Director",
            status="blocked",
            running=False,
            task_id="",
            task_title="",
            detail=f"Missing prerequisites: {missing_text}",
        )
        engine.update_role_status(
            "QA",
            status="blocked",
            running=False,
            task_id="",
            task_title="",
            detail="Blocked because dispatch preflight failed",
        )
        engine._update_engine_status(
            phase="failed",
            running=False,
            summary=summary,
            error="ENGINE_PREFLIGHT_FAILED",
        )
        return {
            "summary": summary,
            "records": [],
            "status_updates": {},
            "failure_info": {},
            "director_result": None,
            "hard_failure": True,
        }

    # Build batches and taskboard
    degraded_to_single = engine.config.director_execution_mode == "multi" and int(engine.config.max_directors or 1) > 1
    max_workers = max(1, int(engine.config.max_directors or 1))
    taskboard_runtime = _build_taskboard_runtime(
        workspace_full=workspace_full,
        run_id=run_id,
        director_tasks=director_tasks,
        max_workers=max_workers,
    )
    taskboard_enabled = bool(taskboard_runtime)
    batches = [] if taskboard_enabled else engine.plan_batches(director_tasks)
    dispatch_batches: list[list[str]] = []

    emit_event(
        events_path,
        kind="action",
        actor="Engine",
        name="director_dispatch_start",
        refs={"run_id": run_id, "phase": "engine_dispatch"},
        summary=f"Dispatching {len(director_tasks)} director task(s)",
        input={
            "config": engine.config.to_payload(),
            "batches": ([[str(task.get("id") or "") for task in batch] for batch in batches] if batches else []),
            "degraded_to_single": degraded_to_single,
            "taskboard_enabled": taskboard_enabled,
        },
    )

    status_updates: dict[str, str] = {}
    failure_info: dict[str, dict[str, str]] = {}
    records: list[dict[str, Any]] = []
    latest_result: dict[str, Any] | None = None

    def _apply_progress_update(
        *,
        task_id: str,
        pm_status: str,
        record: dict[str, Any],
        payload: dict[str, Any] | None,
    ) -> None:
        """Apply progress update to PM payload."""
        if not task_id or not isinstance(progress_payload_paths, (list, tuple)):
            return
        try:
            tasks_list = pm_payload.get("tasks")
            if isinstance(tasks_list, list):
                for t in tasks_list:
                    if isinstance(t, dict) and str(t.get("id") or "").strip() == task_id:
                        t["status"] = pm_status
                        if pm_status in ("failed", "blocked"):
                            t["error_code"] = record.get("error_code", "")
                            t["failure_detail"] = _normalize_failure_detail(record.get("failure_detail", ""))
                        elif pm_status == "needs_continue":
                            payload_obj = payload if isinstance(payload, dict) else {}
                            t.pop("error_code", None)
                            t.pop("failure_detail", None)
                            t.pop("failed_at", None)
                            t["continue_reason"] = str(payload_obj.get("continue_reason") or "").strip()
                            t["build_round_index"] = _safe_int(
                                payload_obj.get("build_round_index"),
                                default=_safe_int(t.get("build_round_index"), default=0),
                            )
                            t["build_round_budget"] = _safe_int(
                                payload_obj.get("build_round_budget"),
                                default=_safe_int(t.get("build_round_budget"), default=0),
                            )
                            t["stall_rounds"] = _safe_int(
                                payload_obj.get("stall_rounds"),
                                default=_safe_int(t.get("stall_rounds"), default=0),
                            )
                            progress_delta = payload_obj.get("progress_delta")
                            t["progress_delta"] = progress_delta if isinstance(progress_delta, dict) else {}
                            soft_check = payload_obj.get("soft_check")
                            if not isinstance(soft_check, dict):
                                soft_check = {}
                            t["soft_check"] = soft_check
                            t["last_missing_targets"] = normalize_path_list(
                                payload_obj.get("last_missing_targets") or soft_check.get("missing_targets") or []
                            )
                            unresolved = (
                                payload_obj.get("last_unresolved_imports") or soft_check.get("unresolved_imports") or []
                            )
                            t["last_unresolved_imports"] = [
                                str(item).strip() for item in unresolved if str(item).strip()
                            ]
                        else:
                            t.pop("error_code", None)
                            t.pop("failure_detail", None)
                            t.pop("failed_at", None)
                            t.pop("continue_reason", None)
                            t.pop("build_round_index", None)
                            t.pop("build_round_budget", None)
                            t.pop("stall_rounds", None)
                            t.pop("progress_delta", None)
                            t.pop("soft_check", None)
                            t.pop("last_missing_targets", None)
                            t.pop("last_unresolved_imports", None)
                        break
            for path in progress_payload_paths:
                if path and isinstance(path, str):
                    try:
                        write_json_atomic(path, pm_payload)
                    except (RuntimeError, ValueError) as e:
                        logger.debug(f"Failed to write progress payload: {e}")
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to emit progress: {e}")

    def _register_record(
        *,
        record: dict[str, Any],
        board_id: int = 0,
    ) -> None:
        """Register task record."""
        nonlocal latest_result
        records.append(record)
        task_id = str(record.get("task_id") or "").strip()
        pm_status = str(record.get("pm_status") or "review").strip()
        if task_id:
            status_updates[task_id] = pm_status
            if pm_status in ("failed", "blocked"):
                failure_info[task_id] = {
                    "error_code": str(record.get("error_code") or "").strip(),
                    "failure_detail": _normalize_failure_detail(record.get("failure_detail") or ""),
                }
        payload = record.get("result_payload")
        if isinstance(payload, dict):
            latest_result = payload
        _apply_progress_update(
            task_id=task_id,
            pm_status=pm_status,
            record=record,
            payload=payload if isinstance(payload, dict) else None,
        )
        if taskboard_enabled and board_id > 0:
            board = taskboard_runtime.get("board")
            module = taskboard_runtime.get("module")
            if board is not None and module is not None:
                if pm_status == "done":
                    board.complete(board_id)
                elif pm_status == "needs_continue":
                    board.update(
                        board_id,
                        status=module.TaskStatus.PENDING,
                        assignee="",
                        metadata={"last_pm_status": pm_status},
                    )
                else:
                    board.fail(
                        board_id,
                        _normalize_failure_detail(record.get("failure_detail") or pm_status),
                    )

    # Dispatch loop
    if taskboard_enabled:
        batch_index = 0
        dispatched_board_ids: set[int] = set()
        while True:
            batch_entries = _select_taskboard_ready_batch(
                taskboard_runtime,
                max_workers,
                dispatched_board_ids=dispatched_board_ids,
            )
            if not batch_entries:
                break
            batch_index += 1
            dispatch_batches.append(
                [
                    str(entry.get("task", {}).get("id") or "")
                    for entry in batch_entries
                    if isinstance(entry.get("task"), dict)
                ]
            )
            for task_offset, entry in enumerate(batch_entries, start=1):
                task = entry.get("task")
                if not isinstance(task, dict):
                    continue
                board_id = int(entry.get("board_id") or 0)
                worker_id = str(entry.get("worker_id") or "").strip()
                record = _run_single_task(
                    engine=engine,
                    args=args,
                    workspace_full=workspace_full,
                    run_dir=run_dir,
                    pm_payload=pm_payload,
                    task=task,
                    batch_index=batch_index,
                    batch_size=len(batch_entries),
                    task_offset=task_offset,
                    events_path=events_path,
                    dialogue_path=dialogue_path,
                    worker_id=worker_id,
                )
                _register_record(record=record, board_id=board_id)
                if board_id > 0:
                    dispatched_board_ids.add(board_id)
    else:
        dispatch_batches = [[str(task.get("id") or "") for task in batch] for batch in batches]
        for batch_index, batch in enumerate(batches, start=1):
            for task_offset, task in enumerate(batch, start=1):
                record = _run_single_task(
                    engine=engine,
                    args=args,
                    workspace_full=workspace_full,
                    run_dir=run_dir,
                    pm_payload=pm_payload,
                    task=task,
                    batch_index=batch_index,
                    batch_size=len(batch),
                    task_offset=task_offset,
                    events_path=events_path,
                    dialogue_path=dialogue_path,
                )
                _register_record(record=record, board_id=0)

    # Summary calculation
    success_count = sum(1 for item in records if item.get("pm_status") == "done")
    failed_count = sum(1 for item in records if item.get("pm_status") == "failed")
    blocked_count = sum(1 for item in records if item.get("pm_status") == "blocked")
    needs_continue_count = sum(1 for item in records if item.get("pm_status") == "needs_continue")
    terminal_count = success_count + failed_count + blocked_count
    target_failure_rate = _env_float("POLARIS_TARGET_FAILURE_RATE", 0.05)
    if target_failure_rate < 0:
        target_failure_rate = 0.05
    failure_rate = float(failed_count + blocked_count) / float(terminal_count) if terminal_count > 0 else 0.0
    hard_failure = failed_count > 0 or blocked_count > 0
    delivery_floor = _evaluate_delivery_floor(records, workspace_full=workspace_full)
    if (
        not hard_failure
        and needs_continue_count == 0
        and delivery_floor.get("enabled") is True
        and delivery_floor.get("passed") is not True
    ):
        hard_failure = True
    dispatch_error = ""
    if failed_count > 0:
        dispatch_error = "DIRECTOR_DISPATCH_FAILURE"
    elif blocked_count > 0:
        dispatch_error = "DIRECTOR_DISPATCH_BLOCKED"
    elif (
        needs_continue_count == 0 and delivery_floor.get("enabled") is True and delivery_floor.get("passed") is not True
    ):
        dispatch_error = "DELIVERY_FLOOR_NOT_MET"

    taskboard_stats: dict[str, Any] = {}
    if taskboard_enabled:
        try:
            board = taskboard_runtime.get("board")
            if board is not None:
                stats = board.get_stats()
                taskboard_stats = stats if isinstance(stats, dict) else {}
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to get taskboard stats, skipping stats in summary: %s", exc)
            taskboard_stats = {}

    summary = {
        "run_id": run_id,
        "pm_iteration": pm_iteration,
        "total": len(records),
        "successes": success_count,
        "failures": failed_count,
        "blocked": blocked_count,
        "needs_continue": needs_continue_count,
        "terminal_count": terminal_count,
        "failure_rate": failure_rate,
        "target_failure_rate": target_failure_rate,
        "failure_rate_ok": bool(failure_rate <= target_failure_rate),
        "degraded_to_single": degraded_to_single,
        "config": engine.config.to_payload(),
        "batches": dispatch_batches,
        "taskboard_mainline": taskboard_enabled,
        "taskboard_stats": taskboard_stats,
        "taskboard_root": str(taskboard_runtime.get("taskboard_root") or "") if taskboard_enabled else "",
        "preflight": preflight,
        "stability_filters": filter_meta,
        "delivery_floor": delivery_floor,
    }
    _update_completion_lock_state(completion_state, records)
    _save_completion_lock_state(completion_lock_path, completion_state)

    emit_event(
        events_path,
        kind="status",
        actor="Engine",
        name="director_dispatch_complete",
        refs={"run_id": run_id, "phase": "engine_dispatch"},
        summary="Director dispatch finished",
        ok=not hard_failure,
        output=summary,
        error=dispatch_error,
    )
    engine.update_role_status(
        "PM",
        status="completed" if not hard_failure else "failed",
        running=False,
        detail="Director dispatch finished",
    )
    engine.update_role_status(
        "Director",
        status="completed" if not hard_failure else "blocked",
        running=False,
        task_id="",
        task_title="",
        detail="Dispatch cycle finished",
    )
    engine.update_role_status(
        "QA",
        status="completed" if not hard_failure else "blocked",
        running=False,
        task_id="",
        task_title="",
        detail="Dispatch cycle finished",
    )
    engine._update_engine_status(
        phase="completed" if not hard_failure else "failed",
        running=False,
        summary=summary,
        error=dispatch_error,
    )

    return {
        "summary": summary,
        "records": records,
        "status_updates": status_updates,
        "failure_info": failure_info,
        "director_result": latest_result,
        "hard_failure": hard_failure,
    }


def _run_single_task(
    engine: PolarisEngine,
    *,
    args: argparse.Namespace,
    workspace_full: str,
    run_dir: str,
    pm_payload: dict[str, Any],
    task: dict[str, Any],
    batch_index: int,
    batch_size: int,
    task_offset: int,
    events_path: str,
    dialogue_path: str,
    worker_id: str = "",
) -> dict[str, Any]:
    """Run a single director task."""
    from polaris.delivery.cli.pm.engine.core import _build_single_task_payload

    task_id = str(task.get("id") or "").strip() or f"TASK-{batch_index:03d}-{task_offset:03d}"
    task_title = str(task.get("title") or task.get("goal") or "").strip()
    task_slug = _slug_token(task_id, fallback=f"task-{batch_index:03d}-{task_offset:03d}")

    worker_token = _slug_token(worker_id, fallback="")
    if worker_token:
        task_root = os.path.join(
            run_dir,
            "engine",
            "workers",
            worker_token,
            "tasks",
            task_slug,
        )
    else:
        task_root = os.path.join(run_dir, "engine", "tasks", task_slug)
    contract_path = os.path.join(task_root, "contracts", "pm_tasks.contract.json")
    result_path = os.path.join(task_root, "results", "director.result.json")
    subprocess_log_path = os.path.join(task_root, "logs", "director.process.log")
    director_log_path = os.path.join(task_root, "logs", "director.runlog.md")
    status_path = os.path.join(task_root, "status", "director.status.json")
    planner_response_path = os.path.join(task_root, "results", "planner.output.md")
    ollama_response_path = os.path.join(task_root, "results", "director_llm.output.md")
    qa_response_path = os.path.join(task_root, "results", "qa.review.md")
    reviewer_response_path = os.path.join(task_root, "results", "auditor.review.md")

    ensure_parent_dir(contract_path)
    task["engine_role_context"] = engine._build_engine_role_context(task)
    task_payload = _build_single_task_payload(pm_payload, task)
    write_json_atomic(contract_path, task_payload)
    qa_contract = normalize_qa_contract(task.get("qa_contract"), task=task)
    coordination_policy = _resolve_tri_council_policy(
        qa_contract=qa_contract,
        enabled_override=os.environ.get("POLARIS_TRI_COUNCIL_ENABLED", ""),
        max_rounds_override=os.environ.get("POLARIS_TRI_COUNCIL_MAX_ROUNDS", ""),
    )
    pre_dispatch_council = _run_tri_council_round(
        stage="pre_dispatch",
        workspace_full=workspace_full,
        task=task,
        qa_contract=qa_contract,
        qa_result={},
        qa_verdict="",
        task_root=task_root,
        run_dir=run_dir,
        run_id=str(pm_payload.get("run_id") or ""),
        pm_iteration=int(pm_payload.get("pm_iteration") or 0),
        task_id=task_id,
        task_title=task_title,
        events_path=events_path,
        dialogue_path=dialogue_path,
        director_status="pending",
        changed_files=[],
        coordination_policy=coordination_policy,
        error_code="",
        failure_detail="",
        qa_retry_count=int(task.get("qa_retry_count") or 0),
        max_director_retries=0,
    )
    if isinstance(pre_dispatch_council, dict):
        _pre_scope_raw = pre_dispatch_council.get("coordination_scope")
        pre_scope = cast("dict[str, Any]", _pre_scope_raw if isinstance(_pre_scope_raw, dict) else {})
        pre_role = str(pre_scope.get("current_role") or "").strip()
        if pre_role:
            engine._append_role_context(
                pre_role,
                event="pre_dispatch_alignment",
                task_id=task_id,
                task_title=task_title,
                pm_status="pending",
                details={
                    "trigger": pre_dispatch_council.get("trigger"),
                    "action": pre_dispatch_council.get("action"),
                    "reason": pre_dispatch_council.get("reason"),
                },
            )
    engine.update_role_status(
        "Director",
        status="running",
        running=True,
        task_id=task_id,
        task_title=task_title,
        detail=f"Executing task {task_id}",
        meta={
            "batch_index": batch_index,
            "batch_size": batch_size,
            "worker_id": worker_id,
        },
    )
    engine.update_role_status(
        "QA",
        status="pending",
        running=True,
        task_id=task_id,
        task_title=task_title,
        detail=f"Waiting for Director result of task {task_id}",
    )
    engine._append_role_context(
        "Director",
        event="task_started",
        task_id=task_id,
        task_title=task_title,
        pm_status="running",
        details={
            "batch_index": batch_index,
            "batch_size": batch_size,
            "contract_path": contract_path,
        },
    )
    engine._append_role_context(
        "QA",
        event="task_waiting_director",
        task_id=task_id,
        task_title=task_title,
        pm_status="pending",
        details={"contract_path": contract_path},
    )

    task_args = argparse.Namespace(**vars(args))
    task_args.pm_task_path = contract_path
    task_args.director_result_path = result_path
    task_args.planner_response_path = planner_response_path
    task_args.ollama_response_path = ollama_response_path
    task_args.qa_response_path = qa_response_path
    task_args.reviewer_response_path = reviewer_response_path

    emit_event(
        events_path,
        kind="action",
        actor="Engine",
        name="director_task_claimed",
        refs={"task_id": task_id, "phase": "engine_dispatch"},
        summary=f"Dispatch task to Director: {task_title or task_id}",
        input={
            "task_id": task_id,
            "batch_index": batch_index,
            "batch_size": batch_size,
            "worker_id": worker_id,
            "contract_path": contract_path,
            "result_path": result_path,
        },
    )

    started = time.time()
    exit_code = engine._director_runner(
        task_args,
        workspace_full,
        int(pm_payload.get("pm_iteration") or 1),
        subprocess_log_path=subprocess_log_path,
        director_log_path=director_log_path,
        status_path=status_path,
        status_payload={
            "task_id": task_id,
            "run_id": str(pm_payload.get("run_id") or ""),
        },
        task=task,
    )
    duration_ms = max(0, int((time.time() - started) * 1000))

    result_payload = read_json_file(result_path)
    if not isinstance(result_payload, dict):
        fallback_status = "success" if int(exit_code) == 0 else "blocked"
        result_payload = {
            "schema_version": 1,
            "status": fallback_status,
            "task_id": task_id,
            "task_title": task_title,
            "run_id": str(pm_payload.get("run_id") or ""),
            "error_code": "" if int(exit_code) == 0 else f"DIRECTOR_EXIT_{int(exit_code)}",
            "changed_files": [],
            "timestamp_epoch": time.time(),
        }
        write_json_atomic(result_path, result_payload)
    canonical_changed_files = normalize_path_list(result_payload.get("changed_files") or [])
    if canonical_changed_files != (result_payload.get("changed_files") or []):
        result_payload["changed_files"] = canonical_changed_files
        write_json_atomic(result_path, result_payload)
    else:
        result_payload["changed_files"] = canonical_changed_files

    director_status = str(result_payload.get("status") or "").strip().lower()
    if director_status not in _DIRECTOR_RESULT_STATUSES:
        director_status = "unknown"
        result_payload["status"] = director_status
        write_json_atomic(result_path, result_payload)

    pm_status = normalize_director_result_status(director_status)
    if pm_status == "review":
        if director_status == "success":
            pm_status = "done"
        elif director_status == "needs_continue":
            pm_status = "needs_continue"
        elif director_status == "fail":
            pm_status = "failed"
        elif director_status == "blocked":
            pm_status = "blocked"

    error_code = str(result_payload.get("error_code") or "").strip()
    if not error_code and int(exit_code) != 0:
        error_code = f"DIRECTOR_EXIT_{int(exit_code)}"
    failure_detail = _normalize_failure_detail(
        result_payload.get("summary") or result_payload.get("completion_summary") or result_payload.get("reason") or ""
    )

    qa_mode = normalize_qa_mode(getattr(args, "qa_mode", None) or os.environ.get("POLARIS_QA_MODE", "blocking"))
    ui_plugin_enabled = str(os.environ.get("POLARIS_QA_UI_PLUGIN_ENABLED", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    qa_result: dict[str, Any] = {}
    tri_council_result: dict[str, Any] = dict(pre_dispatch_council) if isinstance(pre_dispatch_council, dict) else {}

    if qa_mode in ("shadow", "blocking") and director_status == "success":
        verification_log_path = str(result_payload.get("verification_log_path") or "").strip()
        evidence_index = {
            "director_result": result_path,
            "planner_output": planner_response_path,
            "director_output": ollama_response_path,
            "qa_review": qa_response_path,
            "auditor_review": reviewer_response_path,
            "verification_log": verification_log_path,
            "director_log": director_log_path,
            "subprocess_log": subprocess_log_path,
        }
        current_retry_count = _safe_int(task.get("qa_retry_count"), default=0)
        _contract_retry_raw = qa_contract.get("retry_policy")
        contract_retry_policy = cast(
            "dict[str, Any]", _contract_retry_raw if isinstance(_contract_retry_raw, dict) else {}
        )
        max_retries_hint = _safe_int(
            contract_retry_policy.get("max_director_retries"),
            default=_DEFAULT_MAX_DIRECTOR_RETRIES,
        )
        if max_retries_hint < 1:
            max_retries_hint = _DEFAULT_MAX_DIRECTOR_RETRIES
        allow_verify_commands = current_retry_count >= max(max_retries_hint - 1, 0)
        qa_context = {
            "task": task,
            "director_result": result_payload,
            "director_status": director_status,
            "changed_files": canonical_changed_files,
            "result_path": result_path,
            "contract_path": contract_path,
            "evidence_index": evidence_index,
            "allow_verify_commands": allow_verify_commands,
            "verify_deferred_reason": "defer_verify_until_final_retry",
        }
        qa_result = evaluate_qa_contract(
            contract=qa_contract,
            context=qa_context,
            workspace_full=workspace_full,
            run_dir=run_dir,
            ui_plugin_enabled=ui_plugin_enabled,
        )
        qa_verdict = str(qa_result.get("verdict") or "INCONCLUSIVE").strip().upper()
        _retry_raw = (
            qa_result.get("retry_policy")
            if isinstance(qa_result.get("retry_policy"), dict)
            else qa_contract.get("retry_policy")
            if isinstance(qa_contract.get("retry_policy"), dict)
            else {"max_director_retries": _DEFAULT_MAX_DIRECTOR_RETRIES}
        )
        retry_policy = cast("dict[str, Any]", _retry_raw)
        try:
            max_director_retries = int(
                retry_policy.get(
                    "max_director_retries",
                    _DEFAULT_MAX_DIRECTOR_RETRIES,
                )
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to parse max_director_retries, using default %d: %s", _DEFAULT_MAX_DIRECTOR_RETRIES, exc
            )
            max_director_retries = _DEFAULT_MAX_DIRECTOR_RETRIES
        if max_director_retries < 1:
            max_director_retries = _DEFAULT_MAX_DIRECTOR_RETRIES

        if qa_mode == "blocking":
            if qa_verdict == "PASS":
                pm_status = "done"
                task["qa_retry_count"] = 0
                task.pop("qa_failed_final", None)
                task.pop("qa_coordination_pending", None)
            elif qa_verdict == "CONTINUE":
                pm_status = "needs_continue"
                task["qa_retry_count"] = _safe_int(task.get("qa_retry_count"), default=0)
                task.pop("qa_failed_final", None)
                task.pop("qa_coordination_pending", None)
                error_code = ""
                failure_detail = _normalize_failure_detail(str(qa_result.get("diagnostics") or "qa_continue").strip())
            else:
                failed_gates = qa_result.get("failed_gates")
                failed_gates = failed_gates if isinstance(failed_gates, list) else []
                missing_evidence = qa_result.get("missing_evidence")
                missing_evidence = missing_evidence if isinstance(missing_evidence, list) else []
                diagnostics = str(qa_result.get("diagnostics") or "").strip()
                previous_retry = _safe_int(task.get("qa_retry_count"), default=0)
                tri_council_result = _run_tri_council_round(
                    stage="post_qa_failure",
                    workspace_full=workspace_full,
                    task=task,
                    qa_contract=qa_contract,
                    qa_result=qa_result,
                    qa_verdict=qa_verdict,
                    task_root=task_root,
                    run_dir=run_dir,
                    run_id=str(pm_payload.get("run_id") or ""),
                    pm_iteration=int(pm_payload.get("pm_iteration") or 0),
                    task_id=task_id,
                    task_title=task_title,
                    events_path=events_path,
                    dialogue_path=dialogue_path,
                    director_status=director_status,
                    changed_files=canonical_changed_files,
                    coordination_policy=coordination_policy,
                    error_code=error_code,
                    failure_detail=failure_detail,
                    qa_retry_count=previous_retry,
                    max_director_retries=max_director_retries,
                )

                tri_action = str(tri_council_result.get("action") or "retry_with_fix").strip()
                tri_reason = str(tri_council_result.get("reason") or "").strip()
                tri_round = _safe_int(
                    tri_council_result.get("round_count"),
                    default=_safe_int(task.get("tri_council_round_count"), default=0),
                )
                _tri_scope_raw = tri_council_result.get("coordination_scope")
                tri_scope = cast("dict[str, Any]", _tri_scope_raw if isinstance(_tri_scope_raw, dict) else {})
                tri_current_role = str(tri_scope.get("current_role") or "").strip()
                tri_next_role = str(tri_scope.get("next_role") or "").strip()
                qa_failed_final = bool(task.get("qa_failed_final", False))

                if tri_current_role:
                    engine._append_role_context(
                        tri_current_role,
                        event="coordination_round",
                        task_id=task_id,
                        task_title=task_title,
                        pm_status="blocked",
                        error_code=error_code,
                        failure_detail=failure_detail,
                        details={
                            "stage": tri_scope.get("current_stage"),
                            "next_stage": tri_scope.get("next_stage"),
                            "action": tri_action,
                            "reason": tri_reason,
                            "round_count": tri_round,
                        },
                    )
                if (
                    tri_next_role
                    and tri_next_role != tri_current_role
                    and tri_action in {"replan_required", "request_human"}
                ):
                    engine._append_role_context(
                        tri_next_role,
                        event="escalation_received",
                        task_id=task_id,
                        task_title=task_title,
                        pm_status="pending",
                        error_code=error_code,
                        failure_detail=failure_detail,
                        details={
                            "from_role": tri_current_role,
                            "action": tri_action,
                            "reason": tri_reason,
                            "round_count": tri_round,
                        },
                    )

                pm_status = "blocked"
                if tri_action in ("replan_required", "escalate_to_architect"):
                    task["qa_coordination_pending"] = True
                    task["qa_retry_count"] = previous_retry
                    task.pop("qa_failed_final", None)
                    qa_failed_final = False
                    error_code = "QA_CONTRACT_FAIL" if qa_verdict == "FAIL" else "QA_INCONCLUSIVE"
                    if tri_action == "escalate_to_architect":
                        task["escalate_to_role"] = "Architect"
                elif tri_action == "request_human":
                    task["qa_coordination_pending"] = False
                    task["qa_retry_count"] = previous_retry
                    task["qa_failed_final"] = True
                    qa_failed_final = True
                    error_code = "QA_FAILED_FINAL"
                else:
                    task["qa_coordination_pending"] = False
                    qa_retry_count = max(previous_retry + 1, 1)
                    task["qa_retry_count"] = qa_retry_count
                    qa_failed_final = qa_retry_count >= max_director_retries
                    if qa_failed_final:
                        task["qa_failed_final"] = True
                    else:
                        task.pop("qa_failed_final", None)
                    error_code = (
                        "QA_FAILED_FINAL"
                        if qa_failed_final
                        else "QA_CONTRACT_FAIL"
                        if qa_verdict == "FAIL"
                        else "QA_INCONCLUSIVE"
                    )

                detail_parts: list[str] = []
                if diagnostics:
                    detail_parts.append(diagnostics)
                if failed_gates:
                    detail_parts.append("failed_gates=" + ",".join(str(item) for item in failed_gates))
                if missing_evidence:
                    detail_parts.append("missing_evidence=" + ",".join(str(item) for item in missing_evidence))
                detail_parts.append(
                    f"qa_retry={_safe_int(task.get('qa_retry_count'), default=0)}/{max_director_retries}"
                )
                detail_parts.append(f"tri_council_action={tri_action}")
                if tri_reason:
                    detail_parts.append(f"tri_council_reason={tri_reason}")
                if tri_round > 0:
                    detail_parts.append(f"tri_council_round={tri_round}")
                failure_detail = _join_non_empty(detail_parts)

                if qa_failed_final:
                    human_queue_path = os.path.join(run_dir, "engine", "queues", "human_queue.jsonl")
                    append_jsonl(
                        human_queue_path,
                        {
                            "timestamp": time.time(),
                            "run_id": str(pm_payload.get("run_id") or ""),
                            "pm_iteration": int(pm_payload.get("pm_iteration") or 0),
                            "task_id": task_id,
                            "task_title": task_title,
                            "verdict": qa_verdict,
                            "error_code": error_code,
                            "failed_gates": failed_gates,
                            "missing_evidence": missing_evidence,
                            "diagnostics": diagnostics,
                            "result_path": result_path,
                            "tri_council": tri_council_result,
                        },
                        buffered=False,
                    )

        result_payload["qa_verdict"] = qa_result.get("verdict")
        result_payload["qa_failed_gates"] = qa_result.get("failed_gates", [])
        result_payload["qa_missing_evidence"] = qa_result.get("missing_evidence", [])
        result_payload["qa_diagnostics"] = qa_result.get("diagnostics", "")
        result_payload["qa_plugin"] = qa_result.get("plugin", "")
        result_payload["qa_plugin_hint"] = qa_result.get("plugin_hint", "")
        result_payload["qa_task_type"] = qa_result.get("task_type", "")
        result_payload["qa_mode"] = qa_mode
        result_payload["qa_retry_count"] = int(task.get("qa_retry_count") or 0)
        result_payload["qa_failed_final"] = bool(task.get("qa_failed_final", False))
        result_payload["qa_coordination_pending"] = bool(task.get("qa_coordination_pending", False))
        result_payload["tri_council"] = tri_council_result if isinstance(tri_council_result, dict) else {}
        write_json_atomic(result_path, result_payload)
    else:
        if director_status == "needs_continue":
            qa_result = {
                "verdict": "CONTINUE",
                "failed_gates": [],
                "missing_evidence": [],
                "diagnostics": "director_needs_continue",
            }
            result_payload["qa_verdict"] = "CONTINUE"
            result_payload["qa_failed_gates"] = []
            result_payload["qa_missing_evidence"] = []
            result_payload["qa_diagnostics"] = "director_needs_continue"
        result_payload["qa_mode"] = qa_mode
        write_json_atomic(result_path, result_payload)

    emit_event(
        events_path,
        kind="status",
        actor="Engine",
        name="director_task_reported",
        refs={
            "task_id": task_id,
            "phase": "engine_dispatch",
            "files": [result_path],
        },
        summary=f"Director task finished: {task_title or task_id}",
        ok=(pm_status in {"done", "needs_continue"}),
        output={
            "task_id": task_id,
            "pm_status": pm_status,
            "director_status": director_status,
            "qa_mode": qa_mode,
            "qa_verdict": qa_result.get("verdict") if isinstance(qa_result, dict) else "",
            "exit_code": int(exit_code),
            "duration_ms": duration_ms,
            "result_path": result_path,
        },
        duration_ms=duration_ms,
        error="" if pm_status in {"done", "needs_continue"} else (error_code or "DIRECTOR_TASK_FAILED"),
    )
    director_role_status = "completed"
    qa_role_status = "completed"
    if pm_status in ("failed", "blocked"):
        director_role_status = "blocked"
        qa_role_status = "blocked"
    elif pm_status == "needs_continue":
        director_role_status = "running"
        qa_role_status = "running"
    engine.update_role_status(
        "Director",
        status=director_role_status,
        running=False,
        task_id="",
        task_title="",
        detail=f"Task {task_id} finished with {pm_status}",
        meta={
            "last_task_id": task_id,
            "last_result_path": result_path,
            "last_pm_status": pm_status,
        },
    )
    engine.update_role_status(
        "QA",
        status=qa_role_status,
        running=False,
        task_id="",
        task_title="",
        detail=f"Task {task_id} verification status: {pm_status}",
        meta={"last_task_id": task_id, "last_pm_status": pm_status},
    )
    qa_verdict_token = str(qa_result.get("verdict") or "").strip().upper() if isinstance(qa_result, dict) else ""
    engine._append_role_context(
        "PM",
        event="task_status_updated",
        task_id=task_id,
        task_title=task_title,
        pm_status=pm_status,
        error_code=error_code,
        failure_detail=failure_detail,
        details={
            "director_status": director_status,
            "qa_verdict": qa_verdict_token,
        },
    )
    engine._append_role_context(
        "Director",
        event="task_finished",
        task_id=task_id,
        task_title=task_title,
        pm_status=pm_status,
        error_code=error_code,
        failure_detail=failure_detail,
        details={
            "director_status": director_status,
            "qa_verdict": qa_verdict_token,
            "result_path": result_path,
        },
    )
    engine._append_role_context(
        "QA",
        event="qa_review_finished",
        task_id=task_id,
        task_title=task_title,
        pm_status=pm_status,
        error_code=error_code,
        failure_detail=failure_detail,
        details={
            "qa_verdict": qa_verdict_token,
            "failed_gates": qa_result.get("failed_gates", []) if isinstance(qa_result, dict) else [],
            "missing_evidence": qa_result.get("missing_evidence", []) if isinstance(qa_result, dict) else [],
        },
    )

    return {
        "task_id": task_id,
        "task_title": task_title,
        "pm_status": pm_status,
        "director_status": director_status,
        "exit_code": int(exit_code),
        "duration_ms": duration_ms,
        "error_code": error_code,
        "failure_detail": _normalize_failure_detail(failure_detail),
        "result_path": result_path,
        "contract_path": contract_path,
        "target_files": normalize_path_list(task.get("target_files") or []),
        "result_payload": result_payload,
        "qa_result": qa_result if isinstance(qa_result, dict) else {},
        "qa_retry_count": int(task.get("qa_retry_count") or 0),
        "qa_failed_final": bool(task.get("qa_failed_final", False)),
        "qa_coordination_pending": bool(task.get("qa_coordination_pending", False)),
        "tri_council": (tri_council_result if isinstance(tri_council_result, dict) else {}),
        "escalate_to_role": task.get("escalate_to_role"),
    }


def normalize_director_result_status(status: str) -> str:
    """Normalize director result status."""
    normalized = str(status or "").strip().lower()
    if normalized in ("success", "pass", "passed"):
        return "done"
    if normalized in ("needs_continue", "continue"):
        return "needs_continue"
    if normalized in ("fail", "failed", "error"):
        return "failed"
    if normalized in ("blocked", "block"):
        return "blocked"
    return "review"


__all__ = [
    "_dispatch_director_tasks_impl",
    "_run_single_task",
    "normalize_director_result_status",
]
