"""PM orchestration engine - Facade layer.

This module serves as the facade layer for PM orchestration.
All actual implementations have been migrated to app.orchestration.* modules.

Delegates to:
- app.orchestration.planning_pipeline: PM planning iteration with quality retry
- app.orchestration.iteration_state: Iteration finalization and state management
- app.orchestration.dispatch_pipeline: Task dispatch to Director
- app.orchestration.chief_engineer_preflight: Chief Engineer preflight checks
- app.orchestration.pm_contract_store: Contract persistence
- pm.orchestration_core: Core orchestration utilities (spin guard, stop conditions, etc.)
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, cast

from polaris.application.traceability_admin import TraceabilityAdminService
from polaris.cells.orchestration.pm_dispatch.public import (
    clear_manual_intervention,
    finalize_iteration,
    handle_spin_guard,
    record_stop,
    resolve_director_dispatch_tasks,
    run_post_dispatch_integration_qa,
)
from polaris.cells.orchestration.pm_planning.public.pipeline import (
    run_pm_planning_iteration,
)

# Import from refactored app.orchestration modules (via public boundary)
from polaris.cells.orchestration.workflow_runtime.public import (
    SUPPORTED_ORCHESTRATION_RUNTIMES,
    resolve_orchestration_runtime as resolve_workflow_orchestration_runtime,
    submit_pm_workflow_sync,
    wait_for_workflow_completion_sync,
)
from polaris.cells.runtime.projection.public import (
    canonicalize_workflow_task_state,
    get_workflow_runtime_status,
    summarize_workflow_tasks,
    write_workflow_state,
)
from polaris.cells.runtime.state_owner.public import (
    ensure_engine_dispatch_contracts,
    persist_pm_payload,
)
from polaris.delivery.cli.pm.agents import wait_for_agents_confirmation

# Import from pm modules
from polaris.delivery.cli.pm.backend import (
    ensure_pm_backend_available,
    resolve_pm_backend_kind,
)
from polaris.delivery.cli.pm.blocked_policy import (
    consume_degrade_settings,
    evaluate_blocked_policy,
    normalize_director_status,
    should_apply_degrade_settings,
)
from polaris.delivery.cli.pm.config import PmRoleState
from polaris.delivery.cli.pm.engine.core import EngineRuntimeConfig, PolarisEngine
from polaris.delivery.cli.pm.orchestration_core import (
    archive_task_history,
    check_spin_guard,
    check_stop_conditions,
    ensure_docs_ready,
    load_state_and_context,
    update_consecutive_counters,
)
from polaris.delivery.cli.pm.report_utils import (
    append_pm_report,
    format_chief_engineer_for_report,
    format_director_summary_for_report,
    format_integration_qa_for_report,
)
from polaris.delivery.cli.pm.tasks import (
    build_resume_payload_from_last_tasks,
)
from polaris.delivery.cli.pm.tasks_utils import (
    build_requirements_fallback_payload,
)
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.events import emit_event, emit_llm_event, set_dialogue_seq

# Import io utilities
from polaris.kernelone.fs.jsonl.ops import scan_last_seq
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.runtime.shared_types import normalize_timeout_seconds
from polaris.kernelone.storage import (
    resolve_ramdisk_root,
    state_to_ramdisk_enabled,
)
from polaris.kernelone.storage.io_paths import (
    build_cache_root,
    resolve_artifact_path,
    resolve_run_dir,
    resolve_workspace_path,
    update_latest_pointer,
)
from polaris.kernelone.traceability.internal.safety import (
    safe_find_node,
    safe_link,
    safe_persist_matrix,
    safe_register_node,
    safe_reset,
)
from polaris.kernelone.traceability.public.service import create_traceability_service

logger = logging.getLogger(__name__)

# Constants
_PM_TASK_QUALITY_MODE_ENV = "KERNELONE_PM_TASK_QUALITY_MODE"
_PM_TASK_QUALITY_RETRIES_ENV = "KERNELONE_PM_TASK_QUALITY_RETRIES"
_PM_TASK_QUALITY_MODES = {"off", "warn", "strict"}
_PM_TASK_QUALITY_DEFAULT_MODE = "strict"
_ORCHESTRATION_RUNTIME_OPTIONS = set(SUPPORTED_ORCHESTRATION_RUNTIMES)
_ORCHESTRATION_RUNTIME_DEFAULT = "workflow"
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
        fallback_payload["notes"] = "; ".join(
            part
            for part in (
                fallback_notes,
                f"Original PM failure/context: {original_notes}",
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


def _resolve_orchestration_runtime(args: argparse.Namespace) -> str:
    """Resolve orchestration runtime from args and environment."""
    runtime = resolve_workflow_orchestration_runtime(
        getattr(args, "orchestration_runtime", ""),
        environ=os.environ,
    )
    return runtime if runtime in _ORCHESTRATION_RUNTIME_OPTIONS else _ORCHESTRATION_RUNTIME_DEFAULT


def run_once(args: argparse.Namespace, iteration: int = 1) -> int:
    """Run PM iteration once - Main entry point.

    This is the facade function that orchestrates the entire PM iteration:
    1. Load state and context
    2. Run planning iteration (with quality retry)
    3. Persist contracts
    4. Run dispatch pipeline (if enabled)
    5. Finalize iteration

    Args:
        args: Command line arguments namespace
        iteration: Current iteration number (default: 1)

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    requested_backend = str(getattr(args, "pm_backend", "auto") or "auto").strip().lower()
    workspace_full = resolve_workspace_path(args.workspace, require_docs=False)

    # Ensure docs are ready
    docs_exit = ensure_docs_ready(workspace_full)
    if docs_exit is not None:
        return docs_exit

    # Initialize PMPM system
    from polaris.delivery.cli.pm.orchestration_core import ensure_shangshuling_pm_initialized

    ensure_shangshuling_pm_initialized(workspace_full)

    # Initialize traceability service (bypass observer)
    trace_service = create_traceability_service(workspace_full)
    trace_admin = TraceabilityAdminService(trace_service=trace_service)  # noqa: F841

    # Setup paths
    ramdisk_root = resolve_ramdisk_root(getattr(args, "ramdisk_root", None))
    cache_root_full = build_cache_root(ramdisk_root, workspace_full) or ""
    if state_to_ramdisk_enabled() and not cache_root_full:
        raise RuntimeError(
            "KERNELONE_STATE_TO_RAMDISK is enabled but no ramdisk cache root is available. "
            "Set KERNELONE_RAMDISK_ROOT (e.g. X:\\) or disable KERNELONE_STATE_TO_RAMDISK."
        )

    pm_report_full = resolve_artifact_path(workspace_full, cache_root_full, args.pm_report)
    pm_state_full = resolve_artifact_path(workspace_full, cache_root_full, args.state_path)
    pm_history_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        args.task_history_path,
    )

    run_id = f"pm-{iteration:05d}"
    run_dir = resolve_run_dir(workspace_full, cache_root_full, run_id)
    update_latest_pointer(workspace_full, cache_root_full, run_id)

    run_pm_tasks = os.path.join(run_dir, "contracts", "pm_tasks.contract.json")
    run_director_result = os.path.join(run_dir, "results", "director.result.json")
    run_events = os.path.join(run_dir, "events", "runtime.events.jsonl")
    runtime_engine_status = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/status/engine.status.json",
    )
    runtime_plan_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        getattr(args, "plan_path", "runtime/contracts/plan.md"),
    )
    runtime_pm_tasks_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/contracts/pm_tasks.contract.json",
    )

    args.director_result_path = run_director_result
    args.director_events_path = run_events
    args.pm_task_path = run_pm_tasks

    # Initialize engine
    engine = PolarisEngine(EngineRuntimeConfig.from_sources(args, None))
    engine.bind_run_context(
        run_id=run_id,
        pm_iteration=iteration,
        run_dir=run_dir,
        runtime_status_path=runtime_engine_status,
        events_path=run_events,
    )
    engine.register_role("PM", status="running", detail="Planning started")
    engine.register_role("ChiefEngineer", status="idle", detail="Waiting for PM task blueprint sync")
    engine.register_role("Director", status="idle", detail="Waiting for PM dispatch")
    engine.register_role("QA", status="idle", detail="Waiting for Director output")
    engine.update_role_status(
        "PM",
        status="planning",
        running=True,
        detail="PM is generating PLAN/contract outputs",
    )
    engine.set_phase("planning", running=True)

    pm_llm_events_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/events/pm.llm.events.jsonl",
    )
    pm_last_full = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        args.pm_last_message_path,
    )

    role_state = PmRoleState(
        workspace_full=workspace_full,
        cache_root_full=cache_root_full,
        model=args.model,
        show_output=bool(getattr(args, "pm_show_output", False)),
        timeout=args.timeout,
        prompt_profile=str(getattr(args, "prompt_profile", "") or ""),
        output_path=pm_last_full,
        events_path=run_events,
        log_path=pm_report_full,
        llm_events_path=pm_llm_events_full,
    )

    backend, backend_llm_cfg = resolve_pm_backend_kind(requested_backend, role_state)
    ensure_pm_backend_available(backend)

    _provider_id = backend_llm_cfg.provider_id if backend_llm_cfg else ""
    _model_name = backend_llm_cfg.model if backend_llm_cfg else args.model
    emit_llm_event(
        pm_llm_events_full,
        event="config",
        role="pm",
        run_id=run_id,
        iteration=iteration,
        source="system",
        data={
            "tag": "PM Loop",
            "message": f"provider={_provider_id}, model={_model_name}, backend={backend}",
        },
    )

    events_seq_start = scan_last_seq(run_events) if run_events and os.path.exists(run_events) else 0
    dialogue_full = (
        resolve_artifact_path(workspace_full, cache_root_full, args.dialogue_path) if args.dialogue_path else ""
    )
    if dialogue_full:
        set_dialogue_seq(scan_last_seq(dialogue_full))

    # Load state and context
    context = load_state_and_context(workspace_full, cache_root_full, args, iteration)
    requirements = context["requirements"]
    plan_text = context["plan_text"]
    gap_report = context["gap_report"]
    last_qa = context["last_qa"]
    last_tasks = context["last_tasks"]
    pm_state = context["pm_state"]
    pm_out_full = context["pm_out_full"]
    _raw_docs_stage = context.get("docs_stage")
    docs_stage: dict[str, Any] = _raw_docs_stage if isinstance(_raw_docs_stage, dict) else {}

    director_result: dict[str, Any] | None = None

    # Check spin guard
    spin_guard_reason = check_spin_guard(pm_state)
    if spin_guard_reason:
        handle_spin_guard(
            pm_state=pm_state,
            reason=spin_guard_reason,
            pm_report_full=pm_report_full,
            run_events=run_events,
            dialogue_full=dialogue_full,
            run_id=run_id,
            iteration=iteration,
            args=args,
        )
        # Graceful degradation: reset counters and continue
        pm_state["consecutive_failures"] = 0
        pm_state["consecutive_blocked"] = 0
        pm_state["spin_guard_reset_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    start_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pm_state["last_updated_ts"] = start_timestamp

    # Emit docs stage info
    docs_stage_line = ""
    if bool(docs_stage.get("enabled")):
        docs_stage_line = (
            "Doc Stage: "
            + f"{int(docs_stage.get('active_stage_index', 0)) + 1}/"
            + f"{int(docs_stage.get('total_stages', 0) or 0)} "
            + f"{str(docs_stage.get('active_stage_title') or '').strip()} "
            + f"({str(docs_stage.get('active_doc_path') or '').strip()})\n"
        )
        emit_event(
            run_events,
            kind="status",
            actor="PM",
            name="pm_docs_stage_active",
            refs={"run_id": run_id, "phase": "planning"},
            summary="PM staged-doc planning context activated",
            ok=True,
            output={
                "active_stage_index": int(docs_stage.get("active_stage_index", 0)),
                "total_stages": int(docs_stage.get("total_stages", 0) or 0),
                "active_stage_id": str(docs_stage.get("active_stage_id") or "").strip(),
                "active_doc_path": str(docs_stage.get("active_doc_path") or "").strip(),
                "advanced": bool(docs_stage.get("advanced")),
                "advance_reason": str(docs_stage.get("advance_reason") or "").strip(),
            },
        )

    append_pm_report(
        pm_report_full,
        f"\n\n## {start_timestamp} (iteration {iteration}) - start\n"
        + f"Run ID: {run_id}\n"
        + f"Backend: {backend}\n"
        + docs_stage_line
        + "Status: running\n",
    )

    # Wait for agents confirmation
    if not wait_for_agents_confirmation(
        workspace_full,
        cache_root_full,
        pm_state_full,
        pm_state,
        pm_report_full,
        dialogue_full,
        run_id,
        iteration,
        start_timestamp,
        args,
    ):
        engine.update_role_status(
            "PM",
            status="blocked",
            running=False,
            detail="Awaiting agents approval/confirmation",
        )
        engine.set_phase("blocked", running=False, error="AGENTS_CONFIRMATION_PENDING")
        return 3

    # Check for manual intervention resume
    resumed_from_manual = False
    resumed_payload = None
    if bool(pm_state.get("awaiting_manual_intervention")):
        resumed_payload = build_resume_payload_from_last_tasks(last_tasks, iteration, start_timestamp)
        if isinstance(resumed_payload, dict):
            resumed_from_manual = True
            clear_manual_intervention(
                pm_state=pm_state,
                pm_state_full=pm_state_full,
                workspace_full=workspace_full,
                dialogue_full=dialogue_full,
                run_id=run_id,
                iteration=iteration,
            )

    # Build planning context for the pipeline
    planning_context = {
        "requirements": requirements,
        "plan_text": plan_text,
        "gap_report": gap_report,
        "last_qa": last_qa,
        "last_tasks": last_tasks,
        "director_result": director_result,
        "pm_state": pm_state,
        "docs_stage": docs_stage,
        "run_id": run_id,
        "start_timestamp": start_timestamp,
        "run_events": run_events,
        "dialogue_full": dialogue_full,
        "pm_last_full": pm_last_full,
        "pm_llm_events_full": pm_llm_events_full,
        "pm_state_full": pm_state_full,
        "resumed_from_manual": resumed_from_manual,
        "resumed_payload": resumed_payload,
        "trace_service": trace_service,
    }

    # Run planning iteration with quality retry loop
    exit_code, normalized = run_pm_planning_iteration(
        args=args,
        workspace_full=workspace_full,
        iteration=iteration,
        state=role_state,
        context=planning_context,
    )

    # Handle zero tasks fallback
    normalized = normalized if isinstance(normalized, dict) else {}
    normalized["run_id"] = run_id
    normalized["pm_iteration"] = iteration
    normalized_tasks = _extract_normalized_tasks(normalized)
    has_requirements = bool(str(requirements or "").strip())

    if has_requirements and len(normalized_tasks) == 0:
        original_exit_code = exit_code
        (
            exit_code,
            normalized,
            normalized_tasks,
            fallback_applied,
        ) = _apply_requirements_fallback_for_empty_tasks(
            exit_code=exit_code,
            normalized=normalized,
            normalized_tasks=normalized_tasks,
            requirements=requirements,
            iteration=iteration,
            timestamp=start_timestamp,
            plan_text=plan_text,
            docs_stage=docs_stage,
            run_id=run_id,
            workspace_files=_collect_workspace_file_candidates(workspace_full),
        )

        if fallback_applied:
            emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_zero_tasks_autofallback",
                refs={"run_id": run_id, "phase": "planning"},
                summary="PM zero-task output replaced with requirements-derived fallback tasks",
                ok=True,
                output={
                    "requirements_non_empty": True,
                    "original_exit_code": original_exit_code,
                    "fallback_from_failure": original_exit_code != 0,
                    "task_count": len(normalized_tasks),
                },
            )
        if len(normalized_tasks) == 0:
            exit_code = 1
            warning = (
                "PM produced zero tasks while requirements are non-empty; "
                "marking iteration as failed to avoid false PASS."
            )
            _raw_schema = normalized.get("schema_warnings") if isinstance(normalized, dict) else None
            schema_warnings: list[str] = _raw_schema if isinstance(_raw_schema, list) else []
            schema_warnings.append(warning)
            normalized["schema_warnings"] = schema_warnings
            normalized["schema_warning_count"] = len(schema_warnings)
            notes_parts = [str(normalized.get("notes") or "").strip(), warning]
            normalized["notes"] = "; ".join(part for part in notes_parts if part)
            emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_zero_tasks_fail_fast",
                refs={"run_id": run_id, "phase": "planning"},
                summary="PM output rejected: zero tasks with non-empty requirements",
                ok=False,
                output={"requirements_non_empty": True, "task_count": 0},
                error="PM_EMPTY_TASKS_WITH_REQUIREMENTS",
            )
            try:
                from polaris.kernelone.prompts.meta_prompting import (
                    append_meta_prompt_hint,
                )

                append_meta_prompt_hint(
                    workspace_root=workspace_full,
                    role="pm",
                    hint=(
                        "当 requirements 非空时，必须至少输出 1 个可执行任务。"
                        "若缺少文件路径，先输出 bootstrap/scaffold 任务并给出可验证产物。"
                    ),
                    trigger="pm_zero_tasks_fail_fast",
                    run_id=run_id,
                    pm_iteration=iteration,
                    source="pm.orchestration_engine",
                )
            except ImportError:
                pass

    # Merge engine config and update engine
    engine_cfg_payload = _merge_engine_config(normalized.get("engine"), args)
    normalized["engine"] = engine_cfg_payload
    engine.config = EngineRuntimeConfig.from_sources(args, engine_cfg_payload)
    engine.bind_run_context(
        run_id=run_id,
        pm_iteration=iteration,
        run_dir=run_dir,
        runtime_status_path=runtime_engine_status,
        events_path=run_events,
    )
    engine.update_role_status(
        "PM",
        status="planning",
        running=True,
        detail="PM contract generated; persisting artifacts",
    )
    engine.set_phase("planning", running=True)

    # Persist PM payloads
    persist_pm_payload(
        normalized=normalized,
        pm_out_full=pm_out_full,
        run_pm_tasks=run_pm_tasks,
    )

    # Ensure engine dispatch contracts if running director
    if bool(getattr(args, "run_director", False)) and exit_code == 0:
        ensure_engine_dispatch_contracts(
            normalized=normalized,
            run_pm_tasks=run_pm_tasks,
            runtime_pm_tasks_full=runtime_pm_tasks_full,
            runtime_plan_full=runtime_plan_full,
        )

    emit_event(
        run_events,
        kind="status",
        actor="PM",
        name="pm_tasks_persisted",
        refs={
            "run_id": run_id,
            "phase": "planning",
            "files": [pm_out_full, run_pm_tasks],
        },
        summary="PM tasks contract persisted",
        ok=(exit_code == 0),
        output={
            "task_count": len(normalized.get("tasks") or []),
            "schema_warning_count": int(normalized.get("schema_warning_count") or 0),
            "engine": engine_cfg_payload,
        },
    )

    # Update engine status based on planning result
    if exit_code == 0:
        engine.update_role_status(
            "PM",
            status="dispatching" if bool(getattr(args, "run_director", False)) else "completed",
            running=bool(getattr(args, "run_director", False)),
            detail=(
                "PM contract persisted; dispatching Director tasks"
                if bool(getattr(args, "run_director", False))
                else "PM contract persisted; Director dispatch disabled"
            ),
        )
        engine.set_phase(
            "dispatching" if bool(getattr(args, "run_director", False)) else "completed",
            running=bool(getattr(args, "run_director", False)),
        )
    else:
        engine.update_role_status(
            "PM",
            status="failed",
            running=False,
            detail="PM planning output failed validation or invoke",
        )
        engine.set_phase("failed", running=False, error="PM_PLANNING_FAILED")

    # Initialize dispatch results
    engine_dispatch: dict[str, Any] | None = None
    chief_engineer_result: dict[str, Any] | None = None
    integration_qa_result: dict[str, Any] | None = None
    run_director_enabled = bool(getattr(args, "run_director", False))
    orchestration_runtime = _resolve_orchestration_runtime(args)
    workflow_pipeline_error = ""

    # Run dispatch pipeline if enabled and planning succeeded
    if run_director_enabled and exit_code == 0:
        workflow_pipeline_result = _run_dispatch_pipeline_with_workflow(
            args=args,
            engine=engine,
            workspace_full=workspace_full,
            cache_root_full=cache_root_full,
            run_dir=run_dir,
            run_id=run_id,
            iteration=iteration,
            normalized=normalized,
            run_events=run_events,
            dialogue_full=dialogue_full,
            runtime_pm_tasks_full=runtime_pm_tasks_full,
            pm_out_full=pm_out_full,
            run_pm_tasks=run_pm_tasks,
            run_director_result=run_director_result,
            docs_stage=docs_stage,
            pm_state=pm_state,
        )
        # Consume degrade settings after dispatch (they've been applied)
        if should_apply_degrade_settings(pm_state)[0]:
            pm_state = consume_degrade_settings(pm_state)

        if bool(workflow_pipeline_result.get("used")):
            exit_code = int(workflow_pipeline_result.get("exit_code") or 0)
            chief_engineer_result = (
                workflow_pipeline_result.get("chief_engineer_result")
                if isinstance(workflow_pipeline_result.get("chief_engineer_result"), dict)
                else None
            )
            engine_dispatch = (
                workflow_pipeline_result.get("engine_dispatch")
                if isinstance(workflow_pipeline_result.get("engine_dispatch"), dict)
                else None
            )
            integration_qa_result = (
                workflow_pipeline_result.get("integration_qa_result")
                if isinstance(workflow_pipeline_result.get("integration_qa_result"), dict)
                else None
            )
            director_result = (
                workflow_pipeline_result.get("director_result")
                if isinstance(workflow_pipeline_result.get("director_result"), dict)
                else director_result
            )

            # Register traceability nodes for CE blueprint, Director commits, and QA verdict
            if trace_service is not None:
                ce_bp_id = (
                    str(chief_engineer_result.get("blueprint_id") or "").strip()
                    if isinstance(chief_engineer_result, dict)
                    else ""
                )
                bp_node = None
                if ce_bp_id:
                    bp_node = safe_register_node(
                        trace_service,
                        node_kind="blueprint",
                        role="chief_engineer",
                        external_id=ce_bp_id,
                        content=json.dumps(chief_engineer_result, ensure_ascii=False)[:1024],
                    )
                    _raw_dispatch_tasks = normalized.get("tasks") if isinstance(normalized, dict) else []
                    for task in _raw_dispatch_tasks if isinstance(_raw_dispatch_tasks, list) else []:
                        task_id = str(task.get("id") or "").strip()
                        if not task_id:
                            continue
                        task_node = safe_find_node(trace_service, task_id, "task")
                        if task_node is not None and bp_node is not None:
                            safe_link(trace_service, task_node, bp_node, "implements")

                # Director commits: use task-level proxy based on director_result
                if isinstance(director_result, dict) and bp_node is not None:
                    result_tasks = director_result.get("tasks") or director_result.get("results") or []
                    if isinstance(result_tasks, dict):
                        result_tasks = list(result_tasks.values())
                    for task_result in result_tasks if isinstance(result_tasks, list) else []:
                        if not isinstance(task_result, dict):
                            continue
                        task_id = str(task_result.get("task_id") or "").strip()
                        if not task_id:
                            continue
                        changed_files = task_result.get("changed_files") or []
                        commit_content = json.dumps(
                            {"task_id": task_id, "changed_files": changed_files},
                            ensure_ascii=False,
                        )
                        commit_hash = hashlib.sha256(commit_content.encode("utf-8")).hexdigest()[:16]
                        commit_node = safe_register_node(
                            trace_service,
                            node_kind="commit",
                            role="director",
                            external_id=f"{task_id}:{commit_hash}",
                            content=commit_content,
                        )
                        if commit_node is not None:
                            safe_link(trace_service, bp_node, commit_node, "implements")

                # QA verdict
                if isinstance(integration_qa_result, dict):
                    verdict_id = f"qa-{run_id}-{iteration}"
                    verdict_node = safe_register_node(
                        trace_service,
                        node_kind="qa_verdict",
                        role="qa",
                        external_id=verdict_id,
                        content=json.dumps(integration_qa_result, ensure_ascii=False)[:1024],
                    )
                    # Link verdict to all commit nodes registered in this iteration
                    if verdict_node is not None:
                        for node in trace_service.list_nodes():
                            if node.node_kind == "commit":
                                safe_link(trace_service, node, verdict_node, "verifies")
        else:
            workflow_pipeline_error = str(workflow_pipeline_result.get("error") or "").strip()
            exit_code = 1
            emit_event(
                run_events,
                kind="status",
                actor="Engine",
                name="orchestration_workflow_failed",
                refs={"run_id": run_id, "phase": "dispatching"},
                summary="Workflow orchestration failed",
                ok=False,
                output={
                    "orchestration_runtime": orchestration_runtime,
                    "error": workflow_pipeline_error,
                },
                error="WORKFLOW_PIPELINE_FAILED",
            )
            engine.update_role_status(
                "ChiefEngineer",
                status="blocked",
                running=False,
                detail="ChiefEngineer skipped because workflow dispatch failed",
            )
            engine.update_role_status(
                "Director",
                status="blocked",
                running=False,
                detail="Director workflow dispatch failed",
            )
            engine.update_role_status(
                "QA",
                status="blocked",
                running=False,
                detail="QA blocked because workflow dispatch failed",
            )
    elif exit_code == 0 and not run_director_enabled:
        # Director dispatch disabled
        engine.update_role_status(
            "ChiefEngineer",
            status="idle",
            running=False,
            task_id="",
            task_title="",
            detail="ChiefEngineer skipped (Director dispatch disabled)",
        )
        engine.update_role_status(
            "Director",
            status="idle",
            running=False,
            task_id="",
            task_title="",
            detail="Director dispatch is disabled",
        )
        engine.update_role_status(
            "QA",
            status="idle",
            running=False,
            task_id="",
            task_title="",
            detail="QA waiting (Director dispatch disabled)",
        )

    # --- Post-Dispatch: Status normalization, counter update, blocked policy ---
    # Normalize Director status to canonical form
    canonical_status = "unknown"
    task_signature = ""
    if isinstance(director_result, dict):
        canonical_status = normalize_director_status(director_result.get("status"))
        task_signature = str(
            director_result.get("task_fingerprint")
            or director_result.get("task_id")
            or director_result.get("task_title")
            or ""
        ).strip()

    # Update consecutive counters AFTER Director completes
    last_signature = str(pm_state.get("last_task_fingerprint") or pm_state.get("last_task_signature") or "").strip()
    consecutive_failures, consecutive_blocked = update_consecutive_counters(
        director_result,
        last_signature,
        pm_state,
    )

    # Persist counters back to pm_state
    pm_state["consecutive_failures"] = consecutive_failures
    pm_state["consecutive_blocked"] = consecutive_blocked
    pm_state["last_task_signature"] = task_signature

    # Evaluate blocked policy if Director status is blocked
    blocked_policy_result = None
    if canonical_status == "blocked" and isinstance(director_result, dict):
        # Get retry counts from task or director result
        retry_count = int(director_result.get("qa_retry_count") or 0)
        max_retries = int(getattr(args, "max_director_retries", 5) or 5)
        degrade_max_retries = int(getattr(args, "blocked_degrade_max_retries", 1) or 1)
        strategy = str(getattr(args, "blocked_strategy", "auto") or "auto")

        # Find the blocked task from normalized tasks
        blocked_task = None
        blocked_task_id = director_result.get("task_id")
        if not blocked_task_id:
            task_val = director_result.get("task")
            if isinstance(task_val, dict):
                blocked_task_id = task_val.get("task_id")
        if blocked_task_id and isinstance(normalized, dict):
            for t in normalized.get("tasks", []):
                if t.get("task_id") == blocked_task_id or t.get("id") == blocked_task_id:
                    blocked_task = t
                    break
        if not blocked_task:
            blocked_task = {"task_id": blocked_task_id or "unknown"}

        # Evaluate blocked policy
        blocked_policy_result = evaluate_blocked_policy(
            strategy=strategy,
            task=blocked_task,
            director_result=director_result,
            pm_state=pm_state,
            retry_count=retry_count,
            max_retries=max_retries,
            degrade_retry_budget=degrade_max_retries,
        )

        # Apply policy decision
        if blocked_policy_result:
            # Update pm_state with policy patch
            pm_state.update(blocked_policy_result.pm_state_patch)

            # Override exit_code based on policy decision
            if blocked_policy_result.exit_code != 0:
                exit_code = blocked_policy_result.exit_code
                emit_event(
                    run_events,
                    kind="status",
                    actor="PM",
                    name="blocked_policy_stop",
                    refs={"run_id": run_id, "phase": "execution"},
                    summary=f"Blocked policy decided to stop: {blocked_policy_result.reason}",
                    ok=False,
                    output={
                        "decision": blocked_policy_result.decision.value,
                        "reason": blocked_policy_result.reason,
                        "strategy": blocked_policy_result.strategy,
                    },
                )
            else:
                emit_event(
                    run_events,
                    kind="status",
                    actor="PM",
                    name="blocked_policy_continue",
                    refs={"run_id": run_id, "phase": "execution"},
                    summary=f"Blocked policy decided to continue: {blocked_policy_result.reason}",
                    ok=True,
                    output={
                        "decision": blocked_policy_result.decision.value,
                        "reason": blocked_policy_result.reason,
                        "strategy": blocked_policy_result.strategy,
                    },
                )

            # Persist audit payload to director_result
            director_result["blocked_resolution"] = blocked_policy_result.audit_payload
            director_result["strategy_decision"] = blocked_policy_result.decision.value

            # Apply task status update if provided (e.g., skip strategy marks task as skipped)
            if blocked_policy_result.task_status_update and blocked_task:
                blocked_task.update(blocked_policy_result.task_status_update)
                director_result["task_status_update"] = blocked_policy_result.task_status_update

            # For skip/continue strategies, reset consecutive_blocked to avoid legacy stop
            if blocked_policy_result.decision in ("skip_and_continue", "continue"):
                pm_state["consecutive_blocked"] = 0
                consecutive_blocked = 0

    # Apply degrade settings if present (consumed next iteration)
    if should_apply_degrade_settings(pm_state)[0]:
        degrade_settings = pm_state.get("degrade_settings", {})
        emit_event(
            run_events,
            kind="status",
            actor="PM",
            name="degrade_settings_applied",
            refs={"run_id": run_id, "phase": "execution"},
            summary="Degraded settings applied for retry",
            ok=True,
            output=degrade_settings,
        )

    # Check legacy stop conditions (for backward compatibility)
    # Skip if blocked policy already handled the stop decision
    if blocked_policy_result is None or blocked_policy_result.exit_code == 0:
        stop_code = check_stop_conditions(
            workspace_full,
            pm_state,
            consecutive_failures,
            consecutive_blocked,
            args,
        )
        if stop_code is not None:
            record_stop(
                pm_report_full=pm_report_full,
                timestamp=start_timestamp,
                iteration=iteration,
                pm_state=pm_state,
                pm_state_full=pm_state_full,
                exit_code=stop_code,
            )
            exit_code = stop_code
            # Graceful degradation: reset counters and continue
            pm_state["consecutive_failures"] = 0
            pm_state["consecutive_blocked"] = 0
            pm_state["stop_condition_reset_ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_stop_condition_reset",
                refs={"run_id": run_id, "phase": "execution"},
                summary=f"Stop condition {stop_code} triggered, resetting counters and continuing",
                ok=True,
            )

    # Append final report
    append_pm_report(
        pm_report_full,
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (iteration {iteration}) - complete\n"
        + f"Exit code: {exit_code}\n"
        + f"Task count: {len(normalized.get('tasks') or [])}\n"
        + (
            f"ChiefEngineer: {format_chief_engineer_for_report(chief_engineer_result)}\n"
            if isinstance(chief_engineer_result, dict)
            else ""
        )
        + (
            f"Director summary: {format_director_summary_for_report(engine_dispatch)}\n"
            if isinstance(engine_dispatch, dict)
            else "Director summary: skipped\n"
        )
        + (
            f"Integration QA: {format_integration_qa_for_report(integration_qa_result)}\n"
            if isinstance(integration_qa_result, dict)
            else ""
        )
        + (f"Blocked policy: {blocked_policy_result.decision.value if blocked_policy_result else 'N/A'}\n"),
    )

    # Update final engine status
    if exit_code == 0:
        engine.update_role_status(
            "PM",
            status="completed",
            running=False,
            detail="PM iteration completed",
        )
        engine.set_phase("completed", running=False)
    else:
        engine.update_role_status(
            "PM",
            status="failed",
            running=False,
            detail="PM iteration failed",
        )
        engine.set_phase("failed", running=False, error="PM_ITERATION_FAILED")
        if not isinstance(engine_dispatch, dict):
            engine.update_role_status(
                "ChiefEngineer",
                status="blocked",
                running=False,
                task_id="",
                task_title="",
                detail="ChiefEngineer skipped because PM iteration failed",
            )
            engine.update_role_status(
                "Director",
                status="blocked",
                running=False,
                task_id="",
                task_title="",
                detail="Director dispatch skipped because PM iteration failed",
            )
            engine.update_role_status(
                "QA",
                status="blocked",
                running=False,
                task_id="",
                task_title="",
                detail="QA blocked because PM iteration failed",
            )

    # Finalize iteration
    finalize_context = {
        "pm_state_full": pm_state_full,
        "pm_history_full": pm_history_full,
        "normalized": normalized,
        "start_timestamp": start_timestamp,
        "cache_root_full": cache_root_full,
        "run_id": run_id,
        "exit_code": exit_code,
        "backend": backend,
        "events_seq_start": events_seq_start,
        "run_events": run_events,
        "pm_llm_events_full": pm_llm_events_full,
        "trace_service": trace_service,
    }

    # Persist traceability matrix before finalizing (bypass failure)
    traceability_dir = os.path.join(workspace_full, "runtime", "traceability")
    traceability_path = os.path.join(traceability_dir, f"{run_id}.{iteration}.matrix.json")
    if trace_service is not None:
        matrix = trace_service.build_matrix(run_id, iteration)
        safe_persist_matrix(trace_service, matrix, traceability_path)
        safe_reset(trace_service)

    finalize_iteration(
        args=args,
        workspace_full=workspace_full,
        iteration=iteration,
        status="completed" if exit_code == 0 else "failed",
        state=pm_state,
        context=finalize_context,
        result=director_result,
    )

    return exit_code


def _run_dispatch_pipeline_with_workflow(
    *,
    args: argparse.Namespace,
    engine: PolarisEngine,
    workspace_full: str,
    cache_root_full: str,
    run_dir: str,
    run_id: str,
    iteration: int,
    normalized: dict[str, Any],
    run_events: str,
    dialogue_full: str,
    runtime_pm_tasks_full: str,
    pm_out_full: str,
    run_pm_tasks: str,
    run_director_result: str,
    docs_stage: dict[str, Any] | None,
    pm_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run dispatch pipeline with workflow orchestration.

    This function uses the new dispatch_pipeline module to execute
    the full dispatch pipeline including Chief Engineer preflight,
    engine dispatch, and integration QA.
    """
    # Apply degrade settings from pm_state if present
    should_degrade, degrade_settings = should_apply_degrade_settings(pm_state or {})
    if should_degrade:
        # Create a copy of args with degraded settings
        args = argparse.Namespace(**vars(args))
        if degrade_settings.get("serial_mode"):
            args.director_workflow_execution_mode = "serial"
        if degrade_settings.get("max_parallel") is not None:
            args.director_max_parallel_tasks = degrade_settings["max_parallel"]
        # Note: integration_qa and max_verification_retries are handled in metadata below

    outcome: dict[str, Any] = {
        "used": False,
        "exit_code": 0,
        "chief_engineer_result": None,
        "engine_dispatch": None,
        "integration_qa_result": None,
        "director_result": None,
        "error": "",
    }

    def _summarize_workflow_execution(
        workflow_status: dict[str, Any] | None,
        base_tasks: list[dict[str, Any]],
        default_total: int,
    ) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        if isinstance(workflow_status, dict):
            summary = summarize_workflow_tasks(
                workflow_status,
                base_tasks=base_tasks,
                workspace=workspace_full,
                cache_root=cache_root_full,
            )
        _raw_tasks = summary.get("tasks") if isinstance(summary, dict) else None
        tasks: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
        if not tasks and base_tasks:
            tasks = list(base_tasks)

        counts = {
            "completed": 0,
            "failed": 0,
            "blocked": 0,
            "active": 0,
            "pending": 0,
        }
        for item in tasks:
            if not isinstance(item, dict):
                continue
            state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
            if state == "completed":
                counts["completed"] += 1
            elif state == "failed":
                counts["failed"] += 1
            elif state == "blocked":
                counts["blocked"] += 1
            elif state in {"ready", "claimed", "in_progress"}:
                counts["active"] += 1
            else:
                counts["pending"] += 1

        total = int(summary.get("total") or 0)
        if total <= 0:
            total = max(int(default_total or 0), len(tasks))
        if not tasks and total > 0:
            counts["pending"] = total

        workflow_status_token = str((workflow_status or {}).get("workflow_status") or "").strip().lower()
        if workflow_status_token in {"failed", "terminated", "timed_out", "canceled", "cancelled"}:
            unresolved_count = max(0, total - counts["completed"])
            if unresolved_count > 0 and counts["failed"] == 0 and counts["blocked"] == 0:
                counts["failed"] = unresolved_count
                counts["pending"] = 0
                counts["active"] = 0
                for item in tasks:
                    if not isinstance(item, dict):
                        continue
                    state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
                    if state == "completed":
                        continue
                    item["status"] = "failed"
                    item["state"] = "failed"
                    item["error"] = "Workflow execution failed before Director task completion"

        state = str(summary.get("state") or "").strip().lower()
        if workflow_status_token in {"failed", "terminated", "timed_out", "canceled", "cancelled"}:
            state = "failed"
        if not state:
            if counts["failed"] > 0 or counts["blocked"] > 0:
                state = "failed"
            elif total > 0 and counts["completed"] >= total:
                state = "completed"
            elif counts["active"] > 0:
                state = "running"
            elif counts["pending"] > 0:
                state = "queued"
            else:
                state = "idle"

        return {
            "tasks": tasks,
            "total": total,
            "state": state,
            **counts,
        }

    def _extract_nested_workflow_result(payload: Any) -> dict[str, Any]:
        current = payload
        for _ in range(8):
            if not isinstance(current, dict):
                return {}
            if any(
                key in current
                for key in (
                    "director_status",
                    "qa_status",
                    "completed_tasks",
                    "failed_tasks",
                    "blocked_tasks",
                )
            ):
                return current
            details = current.get("details")
            if isinstance(details, dict) and isinstance(details.get("final"), dict):
                current = details["final"]
                continue
            result = current.get("result")
            if isinstance(result, dict) and result is not current:
                current = result
                continue
            return {}
        return {}

    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (RuntimeError, TypeError, ValueError):
            return int(default)

    def _workflow_result_director_status(payload: dict[str, Any]) -> str:
        token = str(payload.get("director_status") or payload.get("status") or "").strip().lower()
        if token in {"completed", "success", "succeeded", "passed"}:
            return "success"
        if token in {"failed", "fail", "error", "director_failed"}:
            return "failed"
        if token in {"blocked", "dependency_blocked"}:
            return "blocked"
        if token in {"running", "in_progress"}:
            return "running"
        if token in {"queued", "pending", "submitted"}:
            return "queued"
        failed_count = _safe_int(payload.get("failed_tasks"), 0)
        blocked_count = _safe_int(payload.get("blocked_tasks"), 0)
        completed_count = _safe_int(payload.get("completed_tasks"), 0)
        if failed_count > 0:
            return "failed"
        if blocked_count > 0:
            return "blocked"
        if completed_count > 0:
            return "success"
        return ""

    def _apply_workflow_result_summary(
        workflow_summary: dict[str, Any],
        result_payload: dict[str, Any],
        director_status_token: str,
        default_total: int,
    ) -> None:
        total = max(
            _safe_int(workflow_summary.get("total"), 0),
            _safe_int(result_payload.get("completed_tasks"), 0)
            + _safe_int(result_payload.get("failed_tasks"), 0)
            + _safe_int(result_payload.get("blocked_tasks"), 0),
            int(default_total or 0),
        )
        if total > 0:
            workflow_summary["total"] = total

        completed_count = _safe_int(result_payload.get("completed_tasks"), -1)
        failed_count = _safe_int(result_payload.get("failed_tasks"), -1)
        blocked_count = _safe_int(result_payload.get("blocked_tasks"), -1)

        if completed_count >= 0:
            workflow_summary["completed"] = completed_count
        if failed_count >= 0:
            workflow_summary["failed"] = failed_count
        if blocked_count >= 0:
            workflow_summary["blocked"] = blocked_count

        if director_status_token == "success":
            workflow_summary["completed"] = max(_safe_int(workflow_summary.get("completed"), 0), total)
            workflow_summary["failed"] = 0
            workflow_summary["blocked"] = 0
            workflow_summary["active"] = 0
            workflow_summary["pending"] = 0
            workflow_summary["state"] = "completed"
            raw_tasks = workflow_summary.get("tasks")
            if isinstance(raw_tasks, list):
                for item in raw_tasks:
                    if isinstance(item, dict):
                        item["status"] = "completed"
                        item["state"] = "completed"
        elif director_status_token in {"failed", "blocked"}:
            unresolved = max(
                0,
                total
                - _safe_int(workflow_summary.get("completed"), 0)
                - _safe_int(workflow_summary.get("failed"), 0)
                - _safe_int(workflow_summary.get("blocked"), 0),
            )
            if director_status_token == "failed" and _safe_int(workflow_summary.get("failed"), 0) <= 0:
                workflow_summary["failed"] = max(1 if total > 0 else 0, unresolved)
            if director_status_token == "blocked" and _safe_int(workflow_summary.get("blocked"), 0) <= 0:
                workflow_summary["blocked"] = max(1 if total > 0 else 0, unresolved)
            workflow_summary["active"] = 0
            workflow_summary["pending"] = 0
            workflow_summary["state"] = director_status_token
            raw_tasks = workflow_summary.get("tasks")
            if isinstance(raw_tasks, list):
                for item in raw_tasks:
                    if not isinstance(item, dict):
                        continue
                    state = canonicalize_workflow_task_state(item.get("status") or item.get("state"))
                    if state == "completed":
                        continue
                    item["status"] = director_status_token
                    item["state"] = director_status_token
        elif director_status_token in {"running", "queued"}:
            workflow_summary["state"] = director_status_token

    # Resolve dispatch tasks using shangshuling
    _raw_dispatch = normalized.get("tasks") if isinstance(normalized, dict) else None
    tasks: list[dict[str, Any]] = _raw_dispatch if isinstance(_raw_dispatch, list) else []
    dispatch_tasks, shangshuling_dispatch_meta = resolve_director_dispatch_tasks(
        workspace_full=workspace_full,
        tasks=tasks,
    )

    if not dispatch_tasks:
        outcome["error"] = "No tasks ready for dispatch"
        return outcome

    dispatch_payload = normalized
    if dispatch_tasks:
        dispatch_payload = dict(normalized)
        dispatch_payload["tasks"] = dispatch_tasks

    # Setup runtime DB path
    from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

    runtime_root_for_db = cache_root_full or os.path.join(workspace_full, get_workspace_metadata_dir_name(), "runtime")
    runtime_db_path = os.path.join(runtime_root_for_db, "state", "workflow.runtime.db")
    try:
        os.makedirs(os.path.dirname(runtime_db_path), exist_ok=True)
        os.environ["KERNELONE_RUNTIME_DB"] = runtime_db_path
    except (RuntimeError, ValueError):
        logger.debug("DEBUG: orchestration_engine.py:{1117} {exc} (swallowed)")

    # Submit workflow
    from polaris.cells.orchestration.workflow_runtime.public.service import PMWorkflowInput, WorkflowConfig

    config = WorkflowConfig.from_env(force_enable=True)
    workflow_run_id = f"{run_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    workflow_input = PMWorkflowInput(
        workspace=workspace_full,
        run_id=workflow_run_id,
        precomputed_payload=dispatch_payload,
        metadata={
            "pm_run_id": run_id,
            "run_dir": run_dir,
            "cache_root_full": cache_root_full,
            "pm_iteration": int(iteration or 0),
            "docs_stage": docs_stage if isinstance(docs_stage, dict) else {},
            "director_config": {
                "type": str(getattr(args, "director_type", os.environ.get("KERNELONE_DIRECTOR_TYPE", "auto")) or "auto")
                .strip()
                .lower(),
                "script": str(
                    getattr(
                        args,
                        "director_path",
                        "src/backend/polaris/delivery/cli/loop-director.py",
                    )
                    or "src/backend/polaris/delivery/cli/loop-director.py"
                ).strip(),
                "timeout": int(
                    normalize_timeout_seconds(
                        getattr(args, "director_timeout", None),
                        default=3600,
                    )
                    or 3600
                ),
                "model": str(getattr(args, "director_model", "") or "").strip(),
                "prompt_profile": str(getattr(args, "prompt_profile", "") or "").strip(),
                "execution_mode": (
                    "serial"
                    if str(getattr(args, "director_workflow_execution_mode", "parallel") or "parallel").strip().lower()
                    == "serial"
                    else "parallel"
                ),
                "max_parallel_tasks": max(
                    1,
                    int(getattr(args, "director_max_parallel_tasks", 3) or 3),
                ),
                "ready_timeout_seconds": max(
                    1,
                    int(getattr(args, "director_ready_timeout_seconds", 30) or 30),
                ),
                "claim_timeout_seconds": max(
                    1,
                    int(getattr(args, "director_claim_timeout_seconds", 30) or 30),
                ),
                "phase_timeout_seconds": max(
                    1,
                    int(getattr(args, "director_phase_timeout_seconds", 900) or 900),
                ),
                "complete_timeout_seconds": max(
                    1,
                    int(getattr(args, "director_complete_timeout_seconds", 30) or 30),
                ),
                "task_timeout_seconds": max(
                    1,
                    int(
                        getattr(args, "director_task_timeout_seconds", MAX_WORKFLOW_TIMEOUT_SECONDS)
                        or MAX_WORKFLOW_TIMEOUT_SECONDS
                    ),
                ),
            },
            "max_verification_retries": (
                0 if should_degrade and degrade_settings.get("max_verification_retries") == 0 else 2
            ),
            "integration_qa": (not (should_degrade and degrade_settings.get("integration_qa") is False)),
            "degraded_mode": should_degrade,
        },
    )

    wait_timeout = normalize_timeout_seconds(
        getattr(args, "director_result_timeout", None),
        default=60,
    )
    wait_seconds = None
    if wait_timeout is not None:
        wait_seconds = float(wait_timeout)
        if wait_seconds <= 0:
            wait_seconds = None

    submission = submit_pm_workflow_sync(
        workflow_input,
        config,
        wait_until_complete=True,
        timeout_seconds=wait_seconds,
        poll_interval_seconds=0.5,
    )
    if not submission.submitted:
        outcome["error"] = str(submission.error or submission.status or "").strip()
        return outcome

    submission_payload = {
        "submitted": bool(submission.submitted),
        "status": str(submission.status or "").strip(),
        "workflow_id": str(submission.workflow_id or "").strip(),
        "workflow_run_id": str(submission.workflow_run_id or "").strip(),
        "error": str(submission.error or "").strip(),
        "details": submission.details if isinstance(submission.details, dict) else {},
    }

    # Build Chief Engineer result (deferred in workflow mode)
    chief_engineer_result = {
        "mode": "workflow",
        "ran": False,
        "reason": "workflow_runtime",
        "summary": "ChiefEngineer stage is deferred to the Workflow workflow chain",
    }
    engine.update_role_status(
        "ChiefEngineer",
        status="idle",
        running=False,
        detail=str(chief_engineer_result.get("summary") or "").strip(),
    )
    engine.update_role_status(
        "Director",
        status="running",
        running=True,
        detail="Director workflow scheduled in Workflow",
        meta={
            "workflow_id": submission.workflow_id,
            "workflow_run_id": submission.workflow_run_id,
            "task_queue": config.task_queue,
        },
    )
    engine.update_role_status(
        "QA",
        status="idle",
        running=False,
        detail="QA workflow is deferred to Workflow after Director completes",
    )

    # Build engine dispatch result
    _raw_dispatch_tasks = dispatch_payload.get("tasks") if isinstance(dispatch_payload, dict) else None
    task_count: int = len(_raw_dispatch_tasks) if isinstance(_raw_dispatch_tasks, list) else 0
    engine_dispatch = {
        "summary": {
            "mode": "workflow",
            "runtime": "workflow",
            "submitted": 1,
            "total": task_count,
            "successes": 0,
            "failures": 0,
            "workflow_id": submission.workflow_id,
            "workflow_run_id": submission.workflow_run_id,
            "task_queue": config.task_queue,
            "namespace": config.namespace,
            "deferred_execution": True,
        },
        "records": [],
        "hard_failure": False,
        "status_updates": {},
        "workflow": submission_payload,
    }

    director_result = {
        "run_id": run_id,
        "status": "queued",
        "mode": "workflow",
        "workflow_id": submission.workflow_id,
        "workflow_run_id": submission.workflow_run_id,
        "successes": task_count,
        "total": task_count,
    }

    # Write workflow state early so runtime readers can pick it up.
    workflow_state_payload = {
        "schema_version": 1,
        "workflow_id": submission.workflow_id,
        "workflow_run_id": submission.workflow_run_id,
        "run_id": run_id,
        "workflow_chain_run_id": workflow_run_id,
        "pm_iteration": int(iteration or 0),
        "workspace": workspace_full,
        "workflow_status": "running",
        "stage": "pm_started",
        "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_queue": config.task_queue,
        "namespace": config.namespace,
        "details": submission_payload,
    }
    workflow_state_path = write_workflow_state(
        workspace_full,
        cache_root_full,
        workflow_state_payload,
    )

    workflow_exit_code = 0
    final_wait_payload = submission.details.get("final") if isinstance(submission.details, dict) else None
    wait_payload = (
        final_wait_payload
        if isinstance(final_wait_payload, dict)
        else wait_for_workflow_completion_sync(
            submission.workflow_id,
            timeout_seconds=wait_seconds,
            config=config,
        )
    )
    wait_error = str(wait_payload.get("error") or "").strip()

    workflow_status = get_workflow_runtime_status(workspace_full, cache_root_full)
    _raw_tasks = dispatch_payload.get("tasks") if isinstance(dispatch_payload, dict) else None
    _dispatch_tasks_list: list[dict[str, Any]] = _raw_tasks if isinstance(_raw_tasks, list) else []
    workflow_summary = _summarize_workflow_execution(
        workflow_status,
        _dispatch_tasks_list,
        task_count,
    )
    workflow_domain_result = _extract_nested_workflow_result(wait_payload)
    workflow_domain_director_status = _workflow_result_director_status(workflow_domain_result)
    if workflow_domain_director_status:
        _apply_workflow_result_summary(
            workflow_summary,
            workflow_domain_result,
            workflow_domain_director_status,
            task_count,
        )

    director_status = "queued"
    wait_status = str(wait_payload.get("status") or "").strip().lower()
    workflow_status_token = str((workflow_status or {}).get("workflow_status") or "").strip().lower()
    if workflow_domain_director_status in {"failed", "blocked", "success", "running", "queued"}:
        director_status = workflow_domain_director_status
    elif (
        wait_status in {"failed", "terminated", "timed_out", "canceled", "cancelled"}
        or workflow_status_token
        in {
            "failed",
            "terminated",
            "timed_out",
            "canceled",
            "cancelled",
        }
        or workflow_summary.get("failed", 0) > 0
    ):
        director_status = "failed"
    elif workflow_summary.get("blocked", 0) > 0:
        director_status = "blocked"
    elif workflow_summary.get("total", 0) > 0 and workflow_summary.get("completed", 0) >= workflow_summary.get(
        "total", 0
    ):
        director_status = "success"
    elif workflow_summary.get("active", 0) > 0:
        director_status = "running"
    elif workflow_summary.get("pending", 0) > 0:
        director_status = "queued"
    elif wait_error:
        director_status = "failed"

    if wait_error and wait_error != "workflow_wait_timeout" and director_status in {"queued", "running"}:
        director_status = "failed"

    if director_status in {"failed", "blocked"}:
        workflow_exit_code = 1

    summary_text = "Director workflow scheduled in Workflow"
    if director_status == "success":
        summary_text = "Director workflow completed"
    elif director_status == "failed":
        summary_text = "Director workflow failed"
    elif director_status == "blocked":
        summary_text = "Director workflow blocked"
    elif wait_error == "workflow_wait_timeout":
        summary_text = f"Director workflow still running after {int(wait_timeout or 0)}s"

    director_result.update(
        {
            "status": director_status,
            "successes": int(workflow_summary.get("completed", 0)),
            "failures": int(workflow_summary.get("failed", 0)),
            "blocked": int(workflow_summary.get("blocked", 0)),
            "total": int(workflow_summary.get("total", task_count)),
            "summary": summary_text,
            "error": wait_error
            or (
                str(workflow_domain_result.get("qa_status") or workflow_domain_result.get("reason") or "").strip()
                if director_status in {"failed", "blocked"}
                else ""
            ),
        }
    )

    if isinstance(engine_dispatch, dict) and "summary" in engine_dispatch:
        cast("dict[str, Any]", engine_dispatch["summary"]).update(
            {
                "total": int(workflow_summary.get("total", task_count)),
                "successes": int(workflow_summary.get("completed", 0)),
                "failures": int(workflow_summary.get("failed", 0)),
                "blocked": int(workflow_summary.get("blocked", 0)),
                "deferred_execution": director_status in {"queued", "running"},
                "workflow_status": str((workflow_status or {}).get("workflow_status") or "").strip(),
                "workflow_domain_director_status": workflow_domain_director_status,
            }
        )

    if director_status == "success":
        engine.update_role_status(
            "Director",
            status="completed",
            running=False,
            detail=summary_text,
        )
    elif director_status == "failed":
        engine.update_role_status(
            "Director",
            status="failed",
            running=False,
            detail=summary_text,
        )
    elif director_status == "blocked":
        engine.update_role_status(
            "Director",
            status="blocked",
            running=False,
            detail=summary_text,
        )
    else:
        engine.update_role_status(
            "Director",
            status="running",
            running=True,
            detail=summary_text,
        )

    qa_status_token = str((workflow_status or {}).get("qa_workflow_status") or "").strip().lower()
    if qa_status_token in {"completed", "failed", "canceled", "cancelled", "terminated"}:
        qa_status = "completed" if qa_status_token == "completed" else "failed"
        engine.update_role_status(
            "QA",
            status=qa_status,
            running=False,
            detail=f"QA workflow {qa_status_token}",
        )

    # Run integration QA only after workflow reaches terminal status.
    if director_status in {"queued", "running"}:
        docs_stage_payload = docs_stage if isinstance(docs_stage, dict) else {}
        integration_qa_result = {
            "schema_version": 1,
            "enabled": True,
            "ran": False,
            "passed": None,
            "reason": "workflow_execution_incomplete",
            "summary": "Director workflow is not in a terminal state; integration QA deferred.",
            "errors": [wait_error] if wait_error and wait_error != "workflow_wait_timeout" else [],
            "run_id": run_id,
            "pm_iteration": int(iteration or 0),
            "director_task_status": {
                "total": int(workflow_summary.get("total", task_count)),
                "completed": int(workflow_summary.get("completed", 0)),
                "failed": int(workflow_summary.get("failed", 0)),
                "blocked": int(workflow_summary.get("blocked", 0)),
                "todo": int(workflow_summary.get("pending", 0)),
                "in_progress": int(workflow_summary.get("active", 0)),
                "review": 0,
                "needs_continue": 0,
            },
            "result_path": "",
            "runtime_result_path": "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "docs_stage": {
                "enabled": bool(docs_stage_payload.get("enabled")),
                "active_doc_path": str(docs_stage_payload.get("active_doc_path") or "").strip(),
            },
        }
        deferred_qa_result_path = os.path.join(run_dir, "qa", "integration_qa.result.json")
        runtime_qa_result_path = resolve_artifact_path(
            workspace_full,
            cache_root_full,
            "runtime/results/integration_qa.result.json",
        )
        integration_qa_result["result_path"] = deferred_qa_result_path
        integration_qa_result["runtime_result_path"] = runtime_qa_result_path
        try:
            write_json_atomic(deferred_qa_result_path, integration_qa_result)
            write_json_atomic(runtime_qa_result_path, integration_qa_result)
        except (OSError, TypeError, ValueError) as qa_write_exc:
            _errors = integration_qa_result.get("errors") or []
            integration_qa_result["errors"] = (list(_errors) if isinstance(_errors, list) else []) + [
                f"qa_result_persist_failed: {qa_write_exc}"
            ]
            with contextlib.suppress(OSError, TypeError, ValueError):
                write_json_atomic(deferred_qa_result_path, integration_qa_result)
    else:
        workflow_tasks = workflow_summary.get("tasks")
        qa_tasks = (
            workflow_tasks if isinstance(workflow_tasks, list) and workflow_tasks else dispatch_payload.get("tasks")
        )
        integration_qa_result = run_post_dispatch_integration_qa(
            args=args,
            workspace_full=workspace_full,
            cache_root_full=cache_root_full,
            run_dir=run_dir,
            run_id=run_id,
            iteration=iteration,
            tasks=qa_tasks if isinstance(qa_tasks, list) else [],
            run_events=run_events,
            dialogue_full=dialogue_full,
            docs_stage=docs_stage,
        )

    # Persist results
    write_json_atomic(run_director_result, director_result)
    runtime_director_result = resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/results/director.result.json",
    )
    write_json_atomic(runtime_director_result, director_result)

    # Update normalized with execution info
    normalized["engine_execution"] = {
        "summary": engine_dispatch.get("summary", {}),
        "records": engine_dispatch.get("records", []),
        "shangshuling_dispatch": shangshuling_dispatch_meta,
        "integration_qa": integration_qa_result,
        "workflow": submission_payload,
    }
    persist_pm_payload(
        normalized=normalized,
        pm_out_full=pm_out_full,
        run_pm_tasks=run_pm_tasks,
    )

    emit_event(
        run_events,
        kind="status",
        actor="Engine",
        name="orchestration_workflow_submitted",
        refs={
            "run_id": run_id,
            "phase": "dispatching",
            "files": [run_director_result, runtime_pm_tasks_full],
        },
        summary="Workflow orchestration submitted",
        ok=True,
        output={
            "workflow_id": submission.workflow_id,
            "workflow_run_id": submission.workflow_run_id,
            "task_queue": config.task_queue,
            "task_count": task_count,
            "state_path": workflow_state_path,
        },
    )

    outcome.update(
        {
            "used": True,
            "exit_code": workflow_exit_code,
            "chief_engineer_result": chief_engineer_result,
            "engine_dispatch": engine_dispatch,
            "integration_qa_result": integration_qa_result,
            "director_result": director_result,
        }
    )
    return outcome


def _merge_engine_config(payload_engine: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Merge PM payload engine hints with CLI defaults."""
    from polaris.delivery.cli.pm.tasks import normalize_engine_config

    normalized_payload = normalize_engine_config(payload_engine)
    cfg = EngineRuntimeConfig.from_sources(args, normalized_payload)
    return cfg.to_payload()


# Re-export from orchestration_core for backward compatibility

# For backward compatibility - these are now in iteration_state

__all__ = [
    "archive_task_history",
    "check_spin_guard",
    "check_stop_conditions",
    # Core orchestration functions (re-exported from orchestration_core)
    "ensure_docs_ready",
    "load_state_and_context",
    # Main entry point
    "run_once",
    "update_consecutive_counters",
]
