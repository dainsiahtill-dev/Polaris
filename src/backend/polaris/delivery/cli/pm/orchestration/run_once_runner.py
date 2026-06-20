"""``run_once`` PM-iteration runner (extracted from ``orchestration_engine``).

This module owns the heavy body of the PM-iteration entry point. The canonical
public name ``run_once`` stays defined in ``orchestration_engine`` as a thin
delegating shim; this module holds the implementation as ``run_once_impl``.

CRITICAL - monkeypatch-through-namespace invariant:
``run_once`` reads many collaborators that tests ``monkeypatch.setattr`` on the
``orchestration_engine`` module object (e.g. ``run_pm_planning_iteration``,
``resolve_pm_backend_kind``, ``ensure_pm_backend_available``,
``wait_for_agents_confirmation``, ``check_stop_conditions``, ``build_cache_root``,
``state_to_ramdisk_enabled``, ``persist_pm_payload``, ``emit_event``,
``_run_dispatch_pipeline_with_workflow``). To keep those patches effective, every
such name is resolved through the ``orchestration_engine`` module object at call
time (``_oe.NAME``) rather than via a frozen ``from ... import name``. A
function-local import of ``orchestration_engine`` avoids the import cycle (this
module is imported by ``orchestration_engine`` at module load).

Embedded section-8 business heuristics (the deterministic requirements-fallback
path and the hardcoded Chinese PM meta-prompt hint) are preserved verbatim -
flagged, not changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from typing import Any

from polaris.application.traceability_admin import TraceabilityAdminService

__all__ = ["run_once_impl"]


def run_once_impl(args: argparse.Namespace, iteration: int = 1) -> int:
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
    # Resolve monkeypatchable globals through the orchestration_engine module
    # object at call time so test setattr patches still win.
    import polaris.delivery.cli.pm.orchestration_engine as _oe

    requested_backend = str(getattr(args, "pm_backend", "auto") or "auto").strip().lower()
    workspace_full = _oe.resolve_workspace_path(args.workspace, require_docs=False)

    # Ensure docs are ready
    docs_exit = _oe.ensure_docs_ready(workspace_full)
    if docs_exit is not None:
        return docs_exit

    # Initialize PMPM system
    from polaris.delivery.cli.pm.orchestration_core import ensure_shangshuling_pm_initialized

    ensure_shangshuling_pm_initialized(workspace_full)

    # Initialize traceability service (bypass observer)
    trace_service = _oe.create_traceability_service(workspace_full)
    trace_admin = TraceabilityAdminService(trace_service=trace_service)  # noqa: F841

    # Setup paths
    ramdisk_root = _oe.resolve_ramdisk_root(getattr(args, "ramdisk_root", None))
    cache_root_full = _oe.build_cache_root(ramdisk_root, workspace_full) or ""
    if _oe.state_to_ramdisk_enabled() and not cache_root_full:
        raise RuntimeError(
            "KERNELONE_STATE_TO_RAMDISK is enabled but no ramdisk cache root is available. "
            "Set KERNELONE_RAMDISK_ROOT (e.g. X:\\) or disable KERNELONE_STATE_TO_RAMDISK."
        )
    _oe._sync_plan_to_runtime(workspace_full, cache_root_full)

    pm_report_full = _oe.resolve_artifact_path(workspace_full, cache_root_full, args.pm_report)
    pm_state_full = _oe.resolve_artifact_path(workspace_full, cache_root_full, args.state_path)
    pm_history_full = _oe.resolve_artifact_path(
        workspace_full,
        cache_root_full,
        args.task_history_path,
    )

    run_id = f"pm-{iteration:05d}"
    run_dir = _oe.resolve_run_dir(workspace_full, cache_root_full, run_id)
    _oe.update_latest_pointer(workspace_full, cache_root_full, run_id)

    run_pm_tasks = os.path.join(run_dir, "contracts", "pm_tasks.contract.json")
    run_director_result = os.path.join(run_dir, "results", "director.result.json")
    run_events = os.path.join(run_dir, "events", "runtime.events.jsonl")
    runtime_engine_status = _oe.resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/status/engine.status.json",
    )
    runtime_plan_full = _oe.resolve_artifact_path(
        workspace_full,
        cache_root_full,
        getattr(args, "plan_path", "runtime/contracts/plan.md"),
    )
    runtime_pm_tasks_full = _oe.resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/contracts/pm_tasks.contract.json",
    )

    args.director_result_path = run_director_result
    args.director_events_path = run_events
    args.pm_task_path = run_pm_tasks

    # Initialize engine
    engine = _oe.PolarisEngine(_oe.EngineRuntimeConfig.from_sources(args, None))
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

    pm_llm_events_full = _oe.resolve_artifact_path(
        workspace_full,
        cache_root_full,
        "runtime/events/pm.llm.events.jsonl",
    )
    pm_last_full = _oe.resolve_artifact_path(
        workspace_full,
        cache_root_full,
        args.pm_last_message_path,
    )

    role_state = _oe.PmRoleState(
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

    backend, backend_llm_cfg = _oe.resolve_pm_backend_kind(requested_backend, role_state)
    args._resolved_pm_backend_kind = backend
    args._backend_llm_cfg = backend_llm_cfg
    _oe.ensure_pm_backend_available(backend)

    _provider_id = backend_llm_cfg.provider_id if backend_llm_cfg else ""
    _model_name = backend_llm_cfg.model if backend_llm_cfg else args.model
    _oe.emit_llm_event(
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

    events_seq_start = _oe.scan_last_seq(run_events) if run_events and os.path.exists(run_events) else 0
    dialogue_full = (
        _oe.resolve_artifact_path(workspace_full, cache_root_full, args.dialogue_path) if args.dialogue_path else ""
    )
    if dialogue_full:
        _oe.set_dialogue_seq(_oe.scan_last_seq(dialogue_full))

    # Load state and context
    context = _oe.load_state_and_context(workspace_full, cache_root_full, args, iteration)
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
    spin_guard_reason = _oe.check_spin_guard(pm_state)
    if spin_guard_reason:
        _oe.handle_spin_guard(
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
        _oe.emit_event(
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

    _oe.append_pm_report(
        pm_report_full,
        f"\n\n## {start_timestamp} (iteration {iteration}) - start\n"
        + f"Run ID: {run_id}\n"
        + f"Backend: {backend}\n"
        + docs_stage_line
        + "Status: running\n",
    )

    # Wait for agents confirmation
    if not _oe.wait_for_agents_confirmation(
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
        resumed_payload = _oe.build_resume_payload_from_last_tasks(last_tasks, iteration, start_timestamp)
        if isinstance(resumed_payload, dict):
            resumed_from_manual = True
            _oe.clear_manual_intervention(
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
    exit_code, normalized = _oe.run_pm_planning_iteration(
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
    normalized_tasks = _oe._extract_normalized_tasks(normalized)
    has_requirements = bool(str(requirements or "").strip())
    planning_pm_invoke_failed = exit_code != 0 and _oe._pm_invoke_failed(pm_state, normalized)
    if planning_pm_invoke_failed:
        _oe._mark_pm_invoke_terminal_failure(pm_state, normalized)

    if has_requirements and len(normalized_tasks) == 0:
        original_exit_code = exit_code
        fatal_pm_invoke_failure = planning_pm_invoke_failed
        fallback_applied = False
        if fatal_pm_invoke_failure:
            warning = (
                "PM LLM provider invocation failed; deterministic requirements fallback was suppressed "
                "to avoid dispatching tasks that were not produced by the configured PM role."
            )
            raw_warnings = normalized.get("schema_warnings")
            invoke_schema_warnings = (
                [str(item) for item in raw_warnings if str(item).strip()] if isinstance(raw_warnings, list) else []
            )
            invoke_schema_warnings.append(warning)
            normalized["schema_warnings"] = invoke_schema_warnings
            normalized["schema_warning_count"] = len(invoke_schema_warnings)
            _oe._mark_pm_invoke_terminal_failure(pm_state, normalized, warning=warning)
            _oe.emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_zero_tasks_fallback_suppressed",
                refs={"run_id": run_id, "phase": "planning"},
                summary="PM zero-task fallback suppressed after provider invoke failure",
                ok=False,
                output={
                    "requirements_non_empty": True,
                    "original_exit_code": original_exit_code,
                    "task_count": 0,
                    "error_code": "PM_LLM_INVOKE_FAILED",
                },
                error="PM_LLM_INVOKE_FAILED",
            )
        else:
            (
                exit_code,
                normalized,
                normalized_tasks,
                fallback_applied,
            ) = _oe._apply_requirements_fallback_for_empty_tasks(
                exit_code=exit_code,
                normalized=normalized,
                normalized_tasks=normalized_tasks,
                requirements=requirements,
                iteration=iteration,
                timestamp=start_timestamp,
                plan_text=plan_text,
                docs_stage=docs_stage,
                run_id=run_id,
                workspace_files=_oe._collect_workspace_file_candidates(workspace_full),
            )

        if fallback_applied:
            exit_code, normalized_tasks, fallback_quality_payload = _oe._apply_quality_gate_to_requirements_fallback(
                normalized=normalized,
                workspace_full=workspace_full,
                docs_stage=docs_stage,
            )
            _oe.emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_zero_tasks_autofallback_quality_gate",
                refs={"run_id": run_id, "phase": "planning"},
                summary="PM zero-task fallback repaired and evaluated by the PM task quality gate",
                ok=(exit_code == 0),
                output={
                    "requirements_non_empty": True,
                    "original_exit_code": original_exit_code,
                    "fallback_from_failure": original_exit_code != 0,
                    "task_count": len(normalized_tasks),
                    **fallback_quality_payload,
                },
                error="" if exit_code == 0 else "PM_TASK_QUALITY_FAILED",
            )
            if original_exit_code != 0 and exit_code == 0:
                downgraded_pm_invoke_error = _oe._downgrade_recovered_pm_invoke_error(
                    pm_state=pm_state,
                    pm_state_full=pm_state_full,
                    timestamp=start_timestamp,
                )
                if downgraded_pm_invoke_error:
                    _oe.emit_event(
                        run_events,
                        kind="status",
                        actor="PM",
                        name="pm_invoke_error_downgraded_after_fallback",
                        refs={"run_id": run_id, "phase": "planning"},
                        summary="Recovered PM invoke error downgraded after deterministic fallback",
                        ok=True,
                        output={
                            "previous_error_code": "PM_LLM_INVOKE_FAILED",
                            "warning_code": "PM_LLM_FALLBACK_APPLIED",
                            "task_count": len(normalized_tasks),
                        },
                    )
        if len(normalized_tasks) == 0:
            exit_code = 1
            warning = (
                "PM produced zero tasks while requirements are non-empty; "
                "marking iteration as failed to avoid false PASS."
            )
            normalized["terminal_error_code"] = "PM_EMPTY_TASKS_WITH_REQUIREMENTS"
            normalized["terminal_error"] = warning
            _raw_schema = normalized.get("schema_warnings") if isinstance(normalized, dict) else None
            schema_warnings: list[str] = _raw_schema if isinstance(_raw_schema, list) else []
            schema_warnings.append(warning)
            normalized["schema_warnings"] = schema_warnings
            normalized["schema_warning_count"] = len(schema_warnings)
            notes_parts = [str(normalized.get("notes") or "").strip(), warning]
            normalized["notes"] = "; ".join(part for part in notes_parts if part)
            _oe.emit_event(
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
    engine_cfg_payload = _oe._merge_engine_config(normalized.get("engine"), args)
    normalized["engine"] = engine_cfg_payload
    engine.config = _oe.EngineRuntimeConfig.from_sources(args, engine_cfg_payload)
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
    _oe.persist_pm_payload(
        normalized=normalized,
        pm_out_full=pm_out_full,
        run_pm_tasks=run_pm_tasks,
    )

    # Ensure engine dispatch contracts if running director
    if bool(getattr(args, "run_director", False)) and exit_code == 0:
        _oe.ensure_engine_dispatch_contracts(
            normalized=normalized,
            run_pm_tasks=run_pm_tasks,
            runtime_pm_tasks_full=runtime_pm_tasks_full,
            runtime_plan_full=runtime_plan_full,
        )

    _oe.emit_event(
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
        pm_failure_detail = _oe._build_pm_failure_detail(
            pm_state=pm_state,
            normalized=normalized,
            fallback="PM planning output failed validation or invoke",
        )
        engine.update_role_status(
            "PM",
            status="failed",
            running=False,
            detail=pm_failure_detail,
        )
        engine.set_phase("failed", running=False, error="PM_PLANNING_FAILED")

    # Initialize dispatch results
    engine_dispatch: dict[str, Any] | None = None
    chief_engineer_result: dict[str, Any] | None = None
    integration_qa_result: dict[str, Any] | None = None
    run_director_enabled = bool(getattr(args, "run_director", False))
    orchestration_runtime = _oe._resolve_orchestration_runtime(args)
    workflow_pipeline_error = ""

    # Run dispatch pipeline if enabled and planning succeeded
    if run_director_enabled and exit_code == 0:
        workflow_pipeline_result = _oe._run_dispatch_pipeline_with_workflow(
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
        if _oe.should_apply_degrade_settings(pm_state)[0]:
            pm_state = _oe.consume_degrade_settings(pm_state)

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
                    bp_node = _oe.safe_register_node(
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
                        task_node = _oe.safe_find_node(trace_service, task_id, "task")
                        if task_node is not None and bp_node is not None:
                            _oe.safe_link(trace_service, task_node, bp_node, "implements")

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
                        commit_node = _oe.safe_register_node(
                            trace_service,
                            node_kind="commit",
                            role="director",
                            external_id=f"{task_id}:{commit_hash}",
                            content=commit_content,
                        )
                        if commit_node is not None:
                            _oe.safe_link(trace_service, bp_node, commit_node, "implements")

                # QA verdict
                if isinstance(integration_qa_result, dict):
                    verdict_id = f"qa-{run_id}-{iteration}"
                    verdict_node = _oe.safe_register_node(
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
                                _oe.safe_link(trace_service, node, verdict_node, "verifies")
        else:
            workflow_pipeline_error = str(workflow_pipeline_result.get("error") or "").strip()
            exit_code = 1
            _oe.emit_event(
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
        canonical_status = _oe.normalize_director_status(director_result.get("status"))
        task_signature = str(
            director_result.get("task_fingerprint")
            or director_result.get("task_id")
            or director_result.get("task_title")
            or ""
        ).strip()

    # Update consecutive counters AFTER Director completes
    last_signature = str(pm_state.get("last_task_fingerprint") or pm_state.get("last_task_signature") or "").strip()
    consecutive_failures, consecutive_blocked = _oe.update_consecutive_counters(
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
        blocked_policy_result = _oe.evaluate_blocked_policy(
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
                _oe.emit_event(
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
                _oe.emit_event(
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
    if _oe.should_apply_degrade_settings(pm_state)[0]:
        degrade_settings = pm_state.get("degrade_settings", {})
        _oe.emit_event(
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
        stop_code = _oe.check_stop_conditions(
            workspace_full,
            pm_state,
            consecutive_failures,
            consecutive_blocked,
            args,
        )
        if stop_code is not None:
            _oe.record_stop(
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
            _oe.emit_event(
                run_events,
                kind="status",
                actor="PM",
                name="pm_stop_condition_reset",
                refs={"run_id": run_id, "phase": "execution"},
                summary=f"Stop condition {stop_code} triggered, resetting counters and continuing",
                ok=True,
            )

    # Append final report
    _oe.append_pm_report(
        pm_report_full,
        f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (iteration {iteration}) - complete\n"
        + f"Exit code: {exit_code}\n"
        + f"Task count: {len(normalized.get('tasks') or [])}\n"
        + (
            f"ChiefEngineer: {_oe.format_chief_engineer_for_report(chief_engineer_result)}\n"
            if isinstance(chief_engineer_result, dict)
            else ""
        )
        + (
            f"Director summary: {_oe.format_director_summary_for_report(engine_dispatch)}\n"
            if isinstance(engine_dispatch, dict)
            else "Director summary: skipped\n"
        )
        + (
            f"Integration QA: {_oe.format_integration_qa_for_report(integration_qa_result)}\n"
            if isinstance(integration_qa_result, dict)
            else ""
        )
        + (f"Blocked policy: {blocked_policy_result.decision.value if blocked_policy_result else 'N/A'}\n"),
    )

    qa_reason_final = (
        str(integration_qa_result.get("reason") or "").strip() if isinstance(integration_qa_result, dict) else ""
    )
    qa_failed_after_director_success = bool(
        isinstance(integration_qa_result, dict)
        and (integration_qa_result.get("passed") is False or qa_reason_final in {"integration_qa_failed", "qa_failed"})
        and isinstance(director_result, dict)
        and str(director_result.get("status") or "").strip().lower() == "success"
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
    elif qa_failed_after_director_success:
        engine.update_role_status(
            "PM",
            status="completed",
            running=False,
            detail="PM contract persisted; downstream QA failed",
        )
        engine.update_role_status(
            "QA",
            status="failed",
            running=False,
            detail=qa_reason_final or "Integration QA failed",
        )
        engine.set_phase("failed", running=False, error=qa_reason_final or "INTEGRATION_QA_FAILED")
    else:
        pm_failure_detail = _oe._build_pm_failure_detail(
            pm_state=pm_state,
            normalized=normalized,
            fallback="PM iteration failed",
        )
        engine.update_role_status(
            "PM",
            status="failed",
            running=False,
            detail=pm_failure_detail,
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
        _oe.safe_persist_matrix(trace_service, matrix, traceability_path)
        _oe.safe_reset(trace_service)

    _oe.finalize_iteration(
        args=args,
        workspace_full=workspace_full,
        iteration=iteration,
        status="completed" if exit_code == 0 else "failed",
        state=pm_state,
        context=finalize_context,
        result=director_result,
    )

    return exit_code
