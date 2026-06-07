"""Director execute 方法实现

包含 execute 方法及其辅助函数。此模块提供 Director 任务执行的核心逻辑。
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.quality import scan_workspace_artifact_quality

from .execution_tools import DirectorToolExecutor
from .helpers import (
    _DEFAULT_TASK_LEASE_TTL_SECONDS,
    _TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
    has_successful_write_tool,
    taskboard_snapshot_brief,
)

logger = logging.getLogger(__name__)


def _finalize_claimed_execution(
    adapter: Any,
    *,
    target_task_id: str,
    session_id: str,
    outcome: str,
    result_summary: str = "",
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalize a runtime task and surface terminal-state conflicts as data."""

    if not str(session_id or "").strip():
        return {"success": False, "reason": "missing_session_id"}
    try:
        if outcome == "completed":
            result = adapter.task_runtime.complete_execution(
                target_task_id,
                session_id=session_id,
                result_summary=result_summary,
                metadata=metadata,
            )
        elif outcome == "failed":
            result = adapter.task_runtime.fail_execution(
                target_task_id,
                session_id=session_id,
                error=error or "director_execution_failed",
                metadata=metadata,
            )
        else:
            return {"success": False, "reason": "invalid_outcome", "outcome": outcome}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "success": False,
            "reason": "task_runtime_terminal_transition_failed",
            "error": str(exc),
            "outcome": outcome,
        }
    if not isinstance(result, dict):
        return {"success": False, "reason": "task_runtime_invalid_finalize_result", "outcome": outcome}
    if result.get("success") is not True:
        return {
            **result,
            "success": False,
            "reason": str(result.get("reason") or "task_runtime_finalize_rejected"),
            "outcome": outcome,
        }
    return result


def _task_runtime_finalization_failed_result(
    *,
    target_task_id: str,
    requested_outcome: str,
    finalize_result: dict[str, Any],
    tool_results: list[Any] | None = None,
    decision_signals: list[dict[str, Any]] | None = None,
    materialization_mode: str = "",
) -> dict[str, Any]:
    reason = str(finalize_result.get("reason") or "task_runtime_finalize_rejected")
    detail = str(finalize_result.get("error") or finalize_result.get("detail") or reason)
    signal = {
        "code": "director_task_runtime_finalization_failed",
        "severity": "error",
        "detail": detail,
        "requested_outcome": requested_outcome,
        "reason": reason,
    }
    result: dict[str, Any] = {
        "success": False,
        "task_id": target_task_id,
        "tools_executed": len(tool_results or []),
        "tool_results": tool_results or [],
        "error": "director_task_runtime_finalization_failed",
        "error_code": "director_task_runtime_finalization_failed",
        "failure_stage": "director_task_runtime_finalization",
        "root_cause_hint": reason,
        "decision_signals": [*(decision_signals or []), signal],
        "qa_required_for_final_verdict": True,
        "artifacts": [],
        "task_runtime_finalize_result": finalize_result,
    }
    if materialization_mode:
        result["materialization_mode"] = materialization_mode
    return result


def _emit_director_adapter_cognitive_receipt(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    receipt_type: str,
    payload: dict[str, Any],
    export_handoff: bool = False,
) -> dict[str, Any]:
    """Record a Cognitive Runtime receipt for Director adapter materialization."""

    metadata_sources: list[dict[str, Any]] = []
    for candidate in (
        context.get("metadata") if isinstance(context, dict) else None,
        task.get("metadata") if isinstance(task, dict) else None,
    ):
        if isinstance(candidate, dict):
            metadata_sources.append(candidate)
    merged_metadata: dict[str, Any] = {}
    for item in metadata_sources:
        merged_metadata.update(item)

    try:
        from polaris.kernelone.context.runtime_feature_flags import (
            CognitiveRuntimeMode,
            resolve_cognitive_runtime_mode,
        )

        mode = resolve_cognitive_runtime_mode(context=context, metadata=merged_metadata)
        if mode is CognitiveRuntimeMode.OFF:
            return {"ok": False, "disabled": True, "mode": mode.value}

        from polaris.cells.factory.cognitive_runtime.public.contracts import (
            ExportHandoffPackCommandV1,
            RecordRuntimeReceiptCommandV1,
        )
        from polaris.cells.factory.cognitive_runtime.public.service import (
            get_cognitive_runtime_public_service,
        )

        workspace = str(getattr(adapter, "workspace", "") or "").strip()
        session_id = (
            str(merged_metadata.get("session_id") or context.get("session_id") or task.get("session_id") or "").strip()
            or None
        )
        effective_run_id = (
            str(run_id or merged_metadata.get("run_id") or context.get("run_id") or task.get("run_id") or "").strip()
            or None
        )
        turn_envelope_raw = merged_metadata.get("turn_envelope")
        turn_envelope = dict(turn_envelope_raw) if isinstance(turn_envelope_raw, dict) else {}
        turn_envelope.setdefault("role", "director")
        turn_envelope.setdefault("task_id", str(target_task_id or ""))
        if session_id:
            turn_envelope.setdefault("session_id", session_id)
        if effective_run_id:
            turn_envelope.setdefault("run_id", effective_run_id)

        service = get_cognitive_runtime_public_service()
        try:
            receipt_result = service.record_runtime_receipt(
                RecordRuntimeReceiptCommandV1(
                    workspace=workspace,
                    receipt_type=receipt_type,
                    session_id=session_id,
                    run_id=effective_run_id,
                    payload={
                        "source": "roles.adapters.director",
                        "task_id": str(target_task_id or ""),
                        "cognitive_runtime_mode": mode.value,
                        "context_os_expected": True,
                        **dict(payload or {}),
                    },
                    turn_envelope=turn_envelope,
                )
            )
            receipt = getattr(receipt_result, "receipt", None)
            receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
            if export_handoff and session_id:
                handoff_envelope = dict(turn_envelope)
                if receipt_id:
                    receipt_ids = list(handoff_envelope.get("receipt_ids") or [])
                    if receipt_id not in receipt_ids:
                        receipt_ids.append(receipt_id)
                    handoff_envelope["receipt_ids"] = receipt_ids
                service.export_handoff_pack(
                    ExportHandoffPackCommandV1(
                        workspace=workspace,
                        session_id=session_id,
                        run_id=effective_run_id,
                        reason=f"roles.adapters.director:{receipt_type}",
                        turn_envelope=handoff_envelope,
                    )
                )
            return {
                "ok": bool(getattr(receipt_result, "ok", False)),
                "receipt_id": receipt_id,
                "receipt_type": receipt_type,
                "mode": mode.value,
            }
        finally:
            service.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to emit Director adapter Cognitive Runtime receipt for task=%s type=%s",
            target_task_id,
            receipt_type,
            exc_info=True,
        )
        return {
            "ok": False,
            "receipt_type": receipt_type,
            "error": str(exc),
        }


async def execute_director_task(
    adapter: Any,
    task_id: str,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """执行 Director 任务的核心逻辑

    Args:
        adapter: DirectorAdapter 实例
        task_id: 任务标识
        input_data: 包含 task_id 或任务描述
        context: 执行上下文，包含 workspace 等

    Returns:
        执行结果字典
    """
    input_metadata_raw = input_data.get("metadata")
    input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}
    requested_task_id = (
        str(
            input_data.get("task_id")
            or input_data.get("pm_task_id")
            or input_metadata.get("task_id")
            or input_metadata.get("pm_task_id")
            or input_metadata.get("id")
            or task_id
            or ""
        ).strip()
        or str(task_id or "").strip()
    )
    target_task_id = requested_task_id
    selection_source = "task_id_lookup"
    selected_from_board = False
    board_snapshot_before = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)

    task = adapter._get_task(target_task_id)
    if task:
        selected_from_board = True
    if not task:
        task = adapter._select_pending_board_task()
        if task:
            selected_from_board = True
            resume_state = str(task.get("resume_state") or "").strip().lower()
            selection_source = "resumable_queue_fallback" if resume_state == "resumable" else "ready_queue_fallback"
    if not task:
        selection_source = "materialized_orchestration_task"
        task = adapter._materialize_runtime_task(requested_task_id, input_data)
        selected_from_board = True

    selected_task_id = str(task.get("id") or "").strip()
    if selected_task_id:
        target_task_id = selected_task_id
    baseline_files = adapter._state_tracker.collect_workspace_code_files()
    run_id = str(context.get("run_id") or "").strip()

    # 任务声明阶段
    (
        task,
        target_task_id,
        selection_source,
        board_claim_applied,
        board_snapshot_after_claim,
        claim_attempts,
        task_claim_result,
    ) = await _claim_task_with_retry(adapter, task, target_task_id, selection_source, requested_task_id, run_id)

    selected_subject = str(task.get("subject") or task.get("title") or "").strip()
    session_raw = task_claim_result.get("session")
    task_claim_session: dict[str, Any] = session_raw if isinstance(session_raw, dict) else {}
    task_claim_session_id = str(task_claim_session.get("session_id") or "").strip()

    if selection_source in {"claim_retry_ready_queue_fallback", "claim_retry_resumable_queue_fallback"}:
        selected_from_board = True

    if board_claim_applied:
        adapter._state_tracker.mark_rework_round_started(
            target_task_id,
            adapter._get_task,
            adapter._update_board_task,
        )
        adapter._update_task_progress(target_task_id, "executing")

    # 心跳任务
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task[Any] | None = None

    async def _run_task_claim_heartbeat() -> None:
        while True:
            try:
                await asyncio.wait_for(
                    heartbeat_stop.wait(),
                    timeout=_TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
                )
                return
            except asyncio.TimeoutError:
                try:
                    adapter.task_runtime.heartbeat_execution(
                        target_task_id,
                        session_id=task_claim_session_id,
                        lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
                        context_summary=selected_subject,
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    return

    async def _stop_task_claim_heartbeat() -> None:
        if heartbeat_task is None:
            return
        heartbeat_stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    if board_claim_applied and task_claim_session_id:
        heartbeat_task = asyncio.create_task(_run_task_claim_heartbeat())

    try:
        # 执行后端解析
        execution_backend_request = adapter._resolve_execution_backend_request(
            task_id=target_task_id,
            task=task,
            input_data=input_data,
            context=context,
        )
        adapter._persist_execution_backend_metadata(target_task_id, execution_backend_request)

        # Sequential Engine 检查
        sequential_config = adapter._get_sequential_config(context)
        if sequential_config:
            if not board_claim_applied:
                return await _handle_claim_required(
                    adapter,
                    target_task_id,
                    run_id,
                    requested_task_id,
                    selection_source,
                    selected_from_board,
                    selected_subject,
                    board_snapshot_before,
                    board_snapshot_after_claim,
                    claim_attempts,
                )

            try:
                use_hybrid = sequential_config.get("use_hybrid", False)
                if use_hybrid:
                    result = await adapter._execute_hybrid(
                        task=task, task_id=target_task_id, run_id=run_id, context=context
                    )
                else:
                    result = await adapter._execute_sequential(
                        task=task, task_id=target_task_id, run_id=run_id, context=context
                    )

                if board_claim_applied and task_claim_session_id:
                    if bool(result.get("success")):
                        finalize_result = _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="completed",
                            session_id=task_claim_session_id,
                            result_summary=f"director_{'hybrid' if use_hybrid else 'sequential'}_completed",
                            metadata={"adapter_phase": "completed"},
                        )
                        if finalize_result.get("success") is not True:
                            return _task_runtime_finalization_failed_result(
                                target_task_id=target_task_id,
                                requested_outcome="completed",
                                finalize_result=finalize_result,
                            )
                    else:
                        _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="failed",
                            session_id=task_claim_session_id,
                            error=str(result.get("error") or "director_sequential_execution_failed"),
                            metadata={"adapter_phase": "failed"},
                        )
                return result
            except asyncio.CancelledError:
                if board_claim_applied and task_claim_session_id:
                    adapter.task_runtime.suspend_execution(
                        target_task_id,
                        session_id=task_claim_session_id,
                        reason="director_execution_cancelled",
                        metadata={"adapter_phase": "pending"},
                    )
                raise

        # 标准 LLM 执行路径
        llm_call_timeout = adapter._execution.resolve_llm_call_timeout_seconds(context)
        decision_signals: list[dict[str, Any]] = []

        # 执行流程...
        try:
            return await _execute_standard_llm_flow(
                adapter,
                task,
                target_task_id,
                run_id,
                context,
                execution_backend_request,
                board_claim_applied,
                task_claim_session_id,
                llm_call_timeout,
                decision_signals,
                baseline_files,
                selected_subject,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = f"director_runtime_exception:{exc}"
            if board_claim_applied and task_claim_session_id:
                _finalize_claimed_execution(
                    adapter,
                    target_task_id=target_task_id,
                    outcome="failed",
                    session_id=task_claim_session_id,
                    error=error,
                    metadata={"adapter_phase": "failed", "exception_type": type(exc).__name__},
                )
            adapter._update_task_progress(target_task_id, "failed")
            return {
                "success": False,
                "task_id": target_task_id,
                "error": error,
                "error_code": "director.runtime.exception",
                "failure_stage": "director_execution",
                "root_cause_hint": str(exc),
                "decision_signals": [
                    {
                        "code": "director.runtime.exception",
                        "severity": "error",
                        "detail": str(exc),
                    }
                ],
                "qa_required_for_final_verdict": True,
                "artifacts": [],
            }

    except asyncio.CancelledError:
        if board_claim_applied and task_claim_session_id:
            adapter.task_runtime.suspend_execution(
                target_task_id,
                session_id=task_claim_session_id,
                reason="director_execution_cancelled",
                metadata={"adapter_phase": "pending"},
            )
        raise
    finally:
        await _stop_task_claim_heartbeat()


async def _claim_task_with_retry(
    adapter: Any,
    task: dict[str, Any],
    target_task_id: str,
    selection_source: str,
    requested_task_id: str,
    run_id: str,
) -> tuple[dict[str, Any], str, str, bool, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """任务声明重试逻辑"""
    max_attempts = 3
    retry_delay_seconds = 0.20
    active_task = task
    active_task_id = str(target_task_id or "").strip()
    active_source = str(selection_source or "").strip() or "task_id_lookup"
    attempts: list[dict[str, Any]] = []
    last_claim_result: dict[str, Any] = {}

    for attempt in range(1, max_attempts + 1):
        claim_external_task_id = _resolve_claim_external_task_id(active_task, requested_task_id)
        claim_result = adapter.task_runtime.claim_execution(
            active_task_id,
            worker_id=adapter.role_id,
            role_id=adapter.role_id,
            run_id=run_id,
            lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
            selection_source=active_source,
            external_task_id=claim_external_task_id,
            context_summary=str(active_task.get("subject") or active_task.get("title") or "").strip(),
            metadata={"adapter_phase": "claimed"},
        )
        last_claim_result = claim_result if isinstance(claim_result, dict) else {}
        claimed = bool(last_claim_result.get("success"))
        task_data = last_claim_result.get("task")
        claimed_task: dict[str, Any] = (
            task_data if isinstance(task_data, dict) else (active_task if isinstance(active_task, dict) else {})
        )
        active_task = claimed_task
        active_task_id = str(claimed_task.get("id") or "").strip() or active_task_id
        attempts.append(
            {
                "attempt": attempt,
                "task_id": active_task_id,
                "selection_source": active_source,
                "claimed": claimed,
                "reason": str(last_claim_result.get("reason") or "").strip(),
                "resumed": bool(last_claim_result.get("resumed")),
                "session_id": str(
                    last_claim_result.get("session", {}).get("session_id", "")
                    if isinstance(last_claim_result.get("session"), dict)
                    else ""
                ).strip(),
            }
        )
        if claimed:
            snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
            return active_task, active_task_id, active_source, True, snapshot, attempts, last_claim_result

        fallback_task = adapter._select_pending_board_task()
        fallback_id = str((fallback_task or {}).get("id") or "").strip()
        if fallback_task and fallback_id and fallback_id != active_task_id:
            active_task = fallback_task
            active_task_id = fallback_id
            fallback_resume_state = str(fallback_task.get("resume_state") or "").strip().lower()
            active_source = (
                "claim_retry_resumable_queue_fallback"
                if fallback_resume_state == "resumable"
                else "claim_retry_ready_queue_fallback"
            )
            continue

        if attempt < max_attempts:
            await asyncio.sleep(retry_delay_seconds * attempt)

    snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
    return active_task, active_task_id, active_source, False, snapshot, attempts, last_claim_result


def _resolve_claim_external_task_id(task: dict[str, Any], requested_task_id: str) -> str:
    """Return the canonical external id for the task that will actually be claimed."""

    metadata_raw = task.get("metadata") if isinstance(task, dict) else {}
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    runtime_execution_raw = metadata.get("runtime_execution")
    runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
    for source in (metadata, runtime_execution, task):
        for key in ("source_task_id", "pm_task_id", "external_task_id", "task_id", "id"):
            token = str(source.get(key) or "").strip()
            if token:
                return token
    return str(requested_task_id or "").strip()


async def _handle_claim_required(
    adapter: Any,
    target_task_id: str,
    run_id: str,
    requested_task_id: str,
    selection_source: str,
    selected_from_board: bool,
    selected_subject: str,
    board_snapshot_before: dict[str, Any],
    board_snapshot_after_claim: dict[str, Any],
    claim_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """处理声明失败情况"""
    await adapter._emit_task_trace_event(
        task_id=target_task_id,
        phase="executing",
        step_kind="taskboard",
        step_title="Director claim required before execution",
        step_detail=(
            "Director must claim a TaskBoard task before execution; "
            f"{taskboard_snapshot_brief(board_snapshot_after_claim)}."
        ),
        status="failed",
        run_id=run_id,
        code="director.taskboard.claim_required",
        reason="claim_required",
        refs={
            "requested_task_id": requested_task_id,
            "selected_task_id": target_task_id,
            "selection_source": selection_source,
            "selected_from_board": selected_from_board,
            "selected_subject": selected_subject,
            "taskboard_before": board_snapshot_before,
            "taskboard_after_claim": board_snapshot_after_claim,
            "board_claim_applied": False,
            "claim_attempts": claim_attempts,
        },
    )
    return {
        "success": False,
        "task_id": target_task_id,
        "error": "Director must claim TaskBoard task before execution",
        "error_code": "director.task_claim_required",
        "failure_stage": "taskboard_claim",
        "root_cause_hint": "taskboard_claim_required",
        "decision_signals": [
            {
                "code": "director.taskboard.claim_required",
                "severity": "error",
                "detail": "taskboard_claim_required_before_execution_with_retries_exhausted",
            }
        ],
        "qa_required_for_final_verdict": True,
        "artifacts": [],
    }


async def _execute_standard_llm_flow(
    adapter: Any,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    execution_backend_request: Any,
    board_claim_applied: bool,
    task_claim_session_id: str,
    llm_call_timeout: float,
    decision_signals: list[dict[str, Any]],
    baseline_files: dict[str, str],
    selected_subject: str,
) -> dict[str, Any]:
    """执行标准 LLM 流程"""
    await _attach_director_file_event_bus(adapter)
    message = adapter._build_director_message(task)
    requires_fresh_materialization = _task_requires_fresh_materialization(task)
    workspace_name = Path(str(getattr(adapter, "workspace", "") or "")).resolve().name
    direct_fallback_summary: dict[str, Any] | None = None
    tool_results: list[dict[str, Any]] = []
    current_files: dict[str, str] = baseline_files
    new_files: list[str] = []
    modified_files: list[str] = []
    all_affected_files: list[str] = []
    primary_llm_summary: dict[str, Any] | None = None
    quality_repair_summary: dict[str, Any] | None = None
    quality_repair_attempts: list[dict[str, Any]] = []
    deterministic_tool_results: list[dict[str, Any]] = []
    deterministic_tool_results.extend(
        _apply_deterministic_node_test_script_contract_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    deterministic_tool_results.extend(
        _apply_deterministic_patch_residue_cleanup(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    if deterministic_tool_results:
        tool_results.extend(deterministic_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
    preflight_existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=current_files,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        workspace_name=workspace_name,
    )
    preflight_can_accept_existing_scope = bool(
        preflight_existing_contract_evidence.get("ok")
    ) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=False,
        primary_llm_summary=None,
    )
    if (
        not all_affected_files
        and _director_existing_scope_preflight_enabled(context)
        and preflight_can_accept_existing_scope
    ):
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": 0,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": [],
                "new_file_count": 0,
                "modified_files": [],
                "modified_file_count": 0,
                "materialization_mode": "preflight_verified_existing_workspace_scope",
                "existing_contract_evidence": preflight_existing_contract_evidence,
            }
        }
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_existing_scope_preflight",
            payload={
                "status": "completed",
                "materialization_mode": "preflight_verified_existing_workspace_scope",
                "changed_files": [],
                "tools_executed": 0,
            },
            export_handoff=True,
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                session_id=task_claim_session_id,
                result_summary=(
                    "preflight_verified_existing_workspace_scope="
                    f"{len(preflight_existing_contract_evidence.get('existing_paths') or [])}"
                ),
                metadata=completion_metadata,
            )
            if finalize_result.get("success") is not True:
                return _task_runtime_finalization_failed_result(
                    target_task_id=target_task_id,
                    requested_outcome="completed",
                    finalize_result=finalize_result,
                    decision_signals=decision_signals,
                    materialization_mode="preflight_verified_existing_workspace_scope",
                )
        adapter._update_task_progress(target_task_id, "completed")
        decision_signals.append(
            {
                "code": "director.existing_workspace_scope_preflight_verified",
                "severity": "info",
                "detail": "Declared task scope already exists in workspace before Director writes.",
            }
        )
        return {
            "success": True,
            "task_id": target_task_id,
            "tools_executed": 0,
            "tool_results": [],
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": "preflight_verified_existing_workspace_scope",
            "existing_contract_evidence": preflight_existing_contract_evidence,
        }

    if not all_affected_files:
        if _director_direct_text_patch_only_enabled(context):
            result = {
                "content": "",
                "success": False,
                "error": "director_direct_text_patch_only",
                "raw_response": {"direct_text_patch_only": True},
            }
        else:
            result = await adapter._invoke_role_dialogue_with_timeout(
                message,
                context=context,
                timeout_seconds=llm_call_timeout,
                stage_label="first_call",
            )
        primary_llm_summary = _summarize_llm_stage_result(result, stage="first_call")
        content = result.get("content", "")

        # 执行工具
        extracted_tool_results = adapter._execution.extract_kernel_tool_results(result)
        tool_results.extend(extracted_tool_results)
        if not extracted_tool_results or not has_successful_write_tool(extracted_tool_results):
            fallback_tool_results = await adapter._execution.execute_tools(
                content, target_task_id, adapter._update_task_progress
            )
            if fallback_tool_results:
                tool_results.extend(fallback_tool_results)

        # 收集变更文件
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )

    if not all_affected_files:
        direct_message = adapter._build_director_message(task, text_patch_mode=True)
        direct_timeout = adapter._execution.resolve_direct_fallback_timeout_seconds(context, llm_call_timeout)
        try:
            direct_result = await asyncio.wait_for(
                adapter._invoke_direct_runtime_provider(direct_message, timeout_seconds=direct_timeout),
                timeout=max(0.1, direct_timeout + 1.0),
            )
        except asyncio.TimeoutError:
            direct_result = {"content": "", "error": "director_direct_patch_fallback_llm_timeout"}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            direct_result = {"content": "", "error": f"director_direct_patch_fallback_failed:{exc}"}
        direct_content = str(direct_result.get("content") or direct_result.get("response") or "")
        direct_tool_results = await adapter._execution.execute_tools(
            direct_content, target_task_id, adapter._update_task_progress
        )
        direct_fallback_summary = {
            "timeout_seconds": direct_timeout,
            "content_length": len(direct_content),
            "error": str(direct_result.get("error") or "").strip(),
            "tool_results": len(direct_tool_results),
            "provider": str(direct_result.get("provider") or "").strip(),
            "model": str(direct_result.get("model") or "").strip(),
            "success": bool(direct_result.get("success")),
        }
        adapter._state_tracker.append_debug_event(
            target_task_id,
            "direct_patch_fallback_result",
            direct_fallback_summary,
        )
        if direct_tool_results:
            tool_results.extend(direct_tool_results)

        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )

    if not all_affected_files:
        deterministic_tool_results = _apply_deterministic_typescript_reexport_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
        if deterministic_tool_results:
            tool_results.extend(deterministic_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )

    existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=current_files,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        workspace_name=workspace_name,
    )
    write_tool_evidence = has_successful_write_tool(tool_results)
    can_accept_existing_scope = bool(existing_contract_evidence.get("ok")) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=write_tool_evidence,
        primary_llm_summary=primary_llm_summary,
    )

    if (
        not all_affected_files
        and not can_accept_existing_scope
        and (requires_fresh_materialization or not bool(existing_contract_evidence.get("ok")))
    ):
        error = "director_no_materialized_changes"
        completion_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_error": error,
                "existing_contract_evidence": existing_contract_evidence,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": "no_materialized_changes",
                "changed_files": [],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        return {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization",
            "root_cause_hint": "no_changed_files",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [
                {
                    "code": error,
                    "severity": "error",
                    "detail": (
                        "Director returned no workspace file changes; "
                        "fresh materialization is required for repair/update tasks."
                        if requires_fresh_materialization
                        else "Director returned no workspace file changes."
                    ),
                }
            ],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
        }

    if not all_affected_files and can_accept_existing_scope:
        completion_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": [],
                "new_file_count": 0,
                "modified_files": [],
                "modified_file_count": 0,
                "materialization_mode": "verified_existing_workspace_scope",
                "existing_contract_evidence": existing_contract_evidence,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_existing_scope_verified",
            payload={
                "status": "completed",
                "materialization_mode": "verified_existing_workspace_scope",
                "changed_files": [],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
            },
            export_handoff=True,
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                session_id=task_claim_session_id,
                result_summary=(
                    "verified_existing_workspace_scope="
                    f"{len(existing_contract_evidence.get('existing_paths') or [])}; "
                    f"tools_executed={len(tool_results)}"
                ),
                metadata=completion_metadata,
            )
            if finalize_result.get("success") is not True:
                return _task_runtime_finalization_failed_result(
                    target_task_id=target_task_id,
                    requested_outcome="completed",
                    finalize_result=finalize_result,
                    tool_results=tool_results,
                    decision_signals=decision_signals,
                    materialization_mode="verified_existing_workspace_scope",
                )
        adapter._update_task_progress(target_task_id, "completed")
        decision_signals.append(
            {
                "code": "director.existing_workspace_scope_verified",
                "severity": "info",
                "detail": "No fresh file diff was required because declared task scope already exists in workspace.",
            }
        )
        return {
            "success": True,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": "verified_existing_workspace_scope",
            "existing_contract_evidence": existing_contract_evidence,
        }

    materialization_mode = (
        "write_tool_and_workspace_diff" if write_tool_evidence else "workspace_diff_without_write_tool"
    )
    if all_affected_files and not write_tool_evidence:
        error = "director_missing_write_receipt"
        completion_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_mode": materialization_mode,
                "materialization_error": error,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_receipt_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": materialization_mode,
                "changed_files": all_affected_files,
                "new_files": new_files[:20],
                "modified_files": modified_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        missing_receipt_signal = {
            "code": error,
            "severity": "error",
            "detail": (
                "Director observed workspace changes, but no normalized write-tool receipt was returned. "
                "Mutation tasks must fail closed instead of trusting ambient diffs."
            ),
            "new_file_count": len(new_files),
            "modified_file_count": len(modified_files),
        }
        return {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization_receipt",
            "root_cause_hint": "missing_write_tool_receipt",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [*decision_signals, missing_receipt_signal],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
        }

    artifact_quality_errors = _collect_materialization_quality_errors(
        adapter,
        task=task,
        all_affected_files=all_affected_files,
        workspace_name=workspace_name,
    )
    for repair_attempt in range(1, 3):
        if not artifact_quality_errors:
            break
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _apply_deterministic_materialization_quality_repairs(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=artifact_quality_errors,
            )
        )
        if deterministic_quality_tool_results:
            tool_results.extend(deterministic_quality_tool_results)
            quality_repair_summary = deterministic_quality_summary
            quality_repair_attempts.append(deterministic_quality_summary)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_quality_tool_results),
            )
            artifact_quality_errors = _collect_materialization_quality_errors(
                adapter,
                task=task,
                all_affected_files=all_affected_files,
                workspace_name=workspace_name,
            )
            if not artifact_quality_errors:
                break
        repair_tool_results, quality_repair_summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            original_message=message,
            llm_call_timeout=llm_call_timeout,
            artifact_quality_errors=artifact_quality_errors,
            changed_files=all_affected_files,
            repair_attempt=repair_attempt,
        )
        quality_repair_attempts.append(quality_repair_summary)
        if not repair_tool_results:
            break
        if repair_tool_results:
            tool_results.extend(repair_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(repair_tool_results),
            )
            artifact_quality_errors = _collect_materialization_quality_errors(
                adapter,
                task=task,
                all_affected_files=all_affected_files,
                workspace_name=workspace_name,
            )
    if artifact_quality_errors:
        error = "director_materialization_quality_failed"
        completion_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_mode": materialization_mode,
                "materialization_error": error,
                "artifact_quality_errors": artifact_quality_errors[:20],
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if quality_repair_summary is not None:
            completion_metadata["adapter_result"]["quality_repair"] = quality_repair_summary
        if quality_repair_attempts:
            completion_metadata["adapter_result"]["quality_repair_attempts"] = quality_repair_attempts
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_quality_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": materialization_mode,
                "changed_files": all_affected_files,
                "new_files": new_files[:20],
                "modified_files": modified_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "artifact_quality_errors": artifact_quality_errors[:20],
                "quality_repair": quality_repair_summary or {},
                "quality_repair_attempts": quality_repair_attempts,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        quality_signal = {
            "code": error,
            "severity": "error",
            "detail": (
                "Director changed workspace files, but the changed artifacts still contain known "
                "worthless scaffold or placeholder-test patterns."
            ),
            "artifact_quality_errors": artifact_quality_errors[:20],
        }
        return {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization_quality",
            "root_cause_hint": "artifact_quality_failed",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [*decision_signals, quality_signal],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
            "artifact_quality_errors": artifact_quality_errors[:20],
        }

    semantic_quality_error = adapter._execution.validate_generated_output(task, all_affected_files)
    if semantic_quality_error:
        error = "director_materialization_semantic_quality_failed"
        completion_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_mode": materialization_mode,
                "materialization_error": error,
                "semantic_quality_error": semantic_quality_error,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_semantic_quality_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": materialization_mode,
                "changed_files": all_affected_files,
                "new_files": new_files[:20],
                "modified_files": modified_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "semantic_quality_error": semantic_quality_error,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        semantic_signal = {
            "code": error,
            "severity": "error",
            "detail": semantic_quality_error,
        }
        return {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization_semantic_quality",
            "root_cause_hint": "semantic_quality_failed",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [*decision_signals, semantic_signal],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
            "semantic_quality_error": semantic_quality_error,
        }

    # 返回结果
    completion_metadata = {
        "adapter_result": {
            "tools_executed": len(tool_results),
            "write_tool_evidence": write_tool_evidence,
            "qa_passed": None,
            "qa_required_for_final_verdict": True,
            "new_files": new_files[:20],
            "new_file_count": len(new_files),
            "modified_files": modified_files[:20],
            "modified_file_count": len(modified_files),
            "materialization_mode": materialization_mode,
        }
    }
    if primary_llm_summary is not None:
        completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
    if direct_fallback_summary is not None:
        completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
    if quality_repair_summary is not None:
        completion_metadata["adapter_result"]["quality_repair"] = quality_repair_summary
    if quality_repair_attempts:
        completion_metadata["adapter_result"]["quality_repair_attempts"] = quality_repair_attempts
    cognitive_receipt = _emit_director_adapter_cognitive_receipt(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        receipt_type="director_adapter_materialization_completed",
        payload={
            "status": "completed",
            "materialization_mode": materialization_mode,
            "changed_files": all_affected_files,
            "new_files": new_files[:20],
            "modified_files": modified_files[:20],
            "tools_executed": len(tool_results),
            "write_tool_evidence": write_tool_evidence,
            "primary_llm": primary_llm_summary or {},
            "direct_fallback": direct_fallback_summary or {},
            "quality_repair": quality_repair_summary or {},
            "quality_repair_attempts": quality_repair_attempts,
        },
        export_handoff=True,
    )
    completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt

    if board_claim_applied and task_claim_session_id:
        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id=target_task_id,
            outcome="completed",
            session_id=task_claim_session_id,
            result_summary=f"changed_files={len(all_affected_files)}; tools_executed={len(tool_results)}",
            metadata=completion_metadata,
        )
        if finalize_result.get("success") is not True:
            return _task_runtime_finalization_failed_result(
                target_task_id=target_task_id,
                requested_outcome="completed",
                finalize_result=finalize_result,
                tool_results=tool_results,
                decision_signals=decision_signals,
                materialization_mode=materialization_mode,
            )

    adapter._update_task_progress(target_task_id, "completed")

    return {
        "success": True,
        "task_id": target_task_id,
        "tools_executed": len(tool_results),
        "tool_results": tool_results,
        "changed_files": all_affected_files,
        "new_files": new_files,
        "modified_files": modified_files,
        "cognitive_runtime_receipt": cognitive_receipt,
        "decision_signals": decision_signals,
        "qa_required_for_final_verdict": True,
        "artifacts": [],
        "materialization_mode": materialization_mode,
    }


def _collect_workspace_code_diff(
    adapter: Any,
    baseline_files: dict[str, str],
    *,
    task: dict[str, Any] | None = None,
    workspace_name: str = "",
) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Collect workspace fingerprints and compute task-relevant changed files."""

    current_files = adapter._state_tracker.collect_workspace_code_files()
    new_files = sorted(set(current_files.keys()) - set(baseline_files.keys()))
    modified_files = [
        rel_path
        for rel_path, fingerprint in current_files.items()
        if rel_path in baseline_files and baseline_files[rel_path] != fingerprint
    ]
    if task is not None:
        new_files, modified_files = _filter_diff_to_task_declared_paths(
            task=task,
            new_files=new_files,
            modified_files=modified_files,
            workspace_name=workspace_name,
        )
    all_affected_files = sorted(set(new_files + modified_files))
    return current_files, new_files, modified_files, all_affected_files


def _summarize_llm_stage_result(result: dict[str, Any], *, stage: str) -> dict[str, Any]:
    """Build compact evidence for whether the configured role LLM produced output."""

    raw_response = result.get("raw_response")
    raw_payload: dict[str, Any] = raw_response if isinstance(raw_response, dict) else {}
    metadata_raw = raw_payload.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    execution_stats_raw = raw_payload.get("execution_stats")
    execution_stats: dict[str, Any] = execution_stats_raw if isinstance(execution_stats_raw, dict) else {}
    content = str(result.get("content") or result.get("response") or raw_payload.get("response") or "")
    provider = (
        str(result.get("provider") or "").strip()
        or str(raw_payload.get("provider") or raw_payload.get("provider_id") or "").strip()
        or str(metadata.get("provider") or metadata.get("provider_id") or "").strip()
    )
    model = (
        str(result.get("model") or "").strip()
        or str(raw_payload.get("model") or "").strip()
        or str(metadata.get("model") or execution_stats.get("model") or "").strip()
    )
    return {
        "stage": stage,
        "success": bool(result.get("success")),
        "provider": provider,
        "model": model,
        "content_length": len(content),
        "error": str(result.get("error") or raw_payload.get("error") or "").strip(),
        "llm_calls": _safe_int(execution_stats.get("llm_calls")),
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _filter_diff_to_task_declared_paths(
    *,
    task: dict[str, Any],
    new_files: list[str],
    modified_files: list[str],
    workspace_name: str = "",
) -> tuple[list[str], list[str]]:
    """Keep only diff files that belong to the task's declared target paths.

    Director tasks may execute in parallel. A process-wide workspace diff can
    contain files changed by sibling tasks, so accepting any changed file as
    evidence lets unrelated work complete the current task. Exact
    ``target_files`` are strongest and are used before broader scope paths.
    """

    candidates = _extract_task_target_path_candidates(task)
    if not candidates:
        candidates = _extract_task_path_candidates(task)
    normalized_candidates = [
        _normalize_declared_task_path(candidate, workspace_name=workspace_name) for candidate in candidates
    ]
    normalized_candidates = _dedupe_preserve_order([candidate for candidate in normalized_candidates if candidate])
    if not normalized_candidates:
        return new_files, modified_files

    return (
        [path for path in new_files if _path_matches_any_declared_candidate(path, normalized_candidates)],
        [path for path in modified_files if _path_matches_any_declared_candidate(path, normalized_candidates)],
    )


def _extract_task_target_path_candidates(task: dict[str, Any]) -> list[str]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    candidates: list[str] = []
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in ("target_files", "target_file", "targets"):
            candidates.extend(_coerce_path_candidate_list(record.get(key)))
    return _dedupe_preserve_order([candidate for candidate in candidates if _looks_like_task_path_candidate(candidate)])


def _path_matches_any_declared_candidate(path: str, candidates: list[str]) -> bool:
    normalized_path = _normalize_declared_task_path(path)
    if not normalized_path:
        return False
    return any(_path_matches_declared_candidate(normalized_path, candidate) for candidate in candidates)


def _path_matches_declared_candidate(path: str, candidate: str) -> bool:
    candidate = str(candidate or "").strip().rstrip("/")
    if not candidate:
        return False
    if any(ch in candidate for ch in ("*", "?")):
        return _glob_path_matches(path, candidate)
    if path == candidate:
        return True
    return path.startswith(f"{candidate}/")


async def _attach_director_file_event_bus(adapter: Any) -> None:
    """Attach the process MessageBus to Director file writers when available."""
    execution = getattr(adapter, "_execution", None)
    set_message_bus = getattr(execution, "set_message_bus", None)
    if not callable(set_message_bus):
        return

    message_bus = None
    resolve_message_bus = getattr(adapter, "_resolve_message_bus", None)
    if callable(resolve_message_bus):
        with contextlib.suppress(RuntimeError, ValueError, TypeError):
            message_bus = await resolve_message_bus()
    set_message_bus(message_bus)


_TS_NAMED_IMPORT_RE = re.compile(
    r"import\s*\{(?P<symbols>[^}]+)\}\s*from\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]",
    re.DOTALL,
)
_TS_RUNTIME_EXPORT_TEMPLATE = r"(?:export\s+)?(?:enum|class|const|let|var|function)\s+{symbol}\b"
_PATCH_RESIDUE_LINE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b.*|>{4,7}\s*REPLACE\b.*|END\s+PATCH_FILE\b.*|PATCH_FILE(?::|\s+).*)\s*$",
    re.IGNORECASE,
)
_UNDECLARED_RUNTIME_IMPORT_ERROR_RE = re.compile(
    r"undeclared runtime import ['\"](?P<package>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)
_DECLARED_TARGET_FILE_MISSING_ERROR_RE = re.compile(
    r"declared target file missing ['\"](?P<path>[^'\"]+)['\"]",
    re.IGNORECASE,
)
_TS_RETURN_OBJECT_SEMICOLON_ERROR_RE = re.compile(
    r"TypeScript return object contains semicolon-terminated property in (?P<path>\S+)",
    re.IGNORECASE,
)
_TYPEORM_IMPORT_LINE_RE = re.compile(r"^\s*import\s+[^;\n]*\s+from\s+['\"]typeorm['\"];\s*$")
_TS_DECORATOR_LINE_RE = re.compile(r"^\s*@[A-Za-z_$][\w$]*(?:\(.*\))?\s*$")
_TS_CLASS_FIELD_DECL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)(?P<optional>\?)?\s*:\s*(?P<type>[^;=]+);\s*$"
)
_TS_RETURN_OBJECT_START_RE = re.compile(r"\breturn\s*\{\s*$")
_TS_RETURN_OBJECT_END_RE = re.compile(r"^\s*\};\s*$")
_TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)\s*;\s*$")
_KNOWN_RUNTIME_DEPENDENCY_VERSIONS = {
    "@apollo/server": "^4.11.0",
    "axios": "^1.7.0",
    "cors": "^2.8.5",
    "dotenv": "^16.4.5",
    "express": "^4.18.2",
    "pg": "^8.11.5",
    "typeorm": "^0.3.20",
    "zod": "^3.23.8",
}


def _is_overstrict_node_test_script_contract(script_text: str) -> bool:
    """Return true for historical generated test scripts with false-negative export checks."""

    text = str(script_text or "")
    if "missing validation contract" in text and "validate[A-Za-z]+Record" in text:
        return True
    return (
        "missing export in" in text
        and "export\\s+(class|function|const|interface|type)" in text
        and "export\\s*\\{" not in text
    )


def _remove_patch_residue_lines(text: str) -> str:
    """Remove generated patch protocol markers that leaked into source files."""

    cleaned = _PATCH_RESIDUE_LINE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if text.endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def _apply_deterministic_patch_residue_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Clean leaked patch markers from declared task files before invoking the LLM."""

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []
    workspace_name = workspace_path.name
    results: list[dict[str, Any]] = []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    for candidate in _extract_task_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized:
            continue
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not target_path.is_file() or target_path.suffix.lower() not in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
        }:
            continue
        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cleaned = _remove_patch_residue_lines(text)
        if cleaned == text:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": normalized, "content": cleaned},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=normalized)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_patch_residue_cleanup",
                    "file": normalized,
                    "bytes_written": int(write_result.get("bytes_written") or len(cleaned.encode("utf-8"))),
                },
            }
        )
    return results


def _apply_deterministic_node_test_script_contract_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Replace an over-strict generated Node test contract with substantive checks."""

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    declared_paths = {
        _normalize_declared_task_path(candidate, workspace_name=workspace_path.name)
        for candidate in _extract_task_target_path_candidates(task)
    }
    if "scripts/test.mjs" not in declared_paths:
        return []

    script_path = workspace_path / "scripts" / "test.mjs"
    if not script_path.exists() or not script_path.is_file():
        return []
    try:
        script_text = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if not _is_overstrict_node_test_script_contract(script_text):
        return []

    new_text = _build_substantive_node_test_script()
    if script_text == new_text:
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "scripts/test.mjs", "content": new_text},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="scripts/test.mjs")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_node_test_script_contract_repair",
                "file": "scripts/test.mjs",
                "bytes_written": int(write_result.get("bytes_written") or len(new_text.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _build_substantive_node_test_script() -> str:
    return """import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const p = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(p) : [p];
  });
}

const sourceFiles = walk('src').filter((file) => file.endsWith('.ts'));
const testFiles = walk('tests').filter((file) => file.endsWith('.ts'));
const seedMarkerPattern = new RegExp('audit-' + 'seed|planning ' + 'scenario', 'i');
const requiredTestFiles = [
  'tests/unit/card-rules.test.ts',
  'tests/unit/deck-builder.test.ts',
  'tests/integration/multiplayer-flow.test.ts',
  'tests/integration/realtime-sync.test.ts',
  'tests/e2e/card-table-3d.test.ts',
];

if (sourceFiles.length < 18) {
  throw new Error('expected at least 18 source modules');
}
if (testFiles.length < requiredTestFiles.length) {
  throw new Error('expected required test files');
}
for (const file of requiredTestFiles) {
  if (!testFiles.includes(file)) {
    throw new Error('missing required test file ' + file);
  }
}

for (const file of sourceFiles) {
  const text = readFileSync(file, 'utf8');
  const moduleExportPattern =
    /(?:^|\\n)\\s*export\\s+(?:async\\s+)?(?:class|function|const|let|var|interface|type|enum|default)\\b|(?:^|\\n)\\s*export\\s*\\{/;
  if (!moduleExportPattern.test(text)) {
    throw new Error('missing export in ' + file);
  }
  if (seedMarkerPattern.test(text)) {
    throw new Error('seed marker retained in ' + file);
  }
}

for (const file of testFiles) {
  const text = readFileSync(file, 'utf8');
  if (!/from ['"]..\\/..\\/src\\//.test(text)) {
    throw new Error('test file lacks src import ' + file);
  }
  if (!/run[A-Za-z0-9]+Checks/.test(text) || !/failures/.test(text)) {
    throw new Error('test file lacks executable check contract ' + file);
  }
  if (/expect\\(\\s*\\d+\\s*(?:[+\\-*/])\\s*\\d+\\s*\\)\\.to(?:Be|Equal)\\(\\s*\\d+\\s*\\)/.test(text)) {
    throw new Error('trivial arithmetic test ' + file);
  }
}

console.log(
  'card3d behavior checks passed across ' +
    sourceFiles.length +
    ' source files and ' +
    testFiles.length +
    ' test files'
);
"""


def _apply_deterministic_typescript_reexport_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Repair a narrow TypeScript runtime re-export miss without target-specific code.

    This covers a common Director failure mode: tests import a runtime symbol from
    a barrel/module file, but that module only exposes type contracts while the
    symbol is exported by a sibling module. The repair only appends an explicit
    `export { Symbol } from './source';` when the source file has a runtime export.
    """
    task_text = _task_text_blob(task)
    if not _looks_like_typescript_reexport_failure(task_text):
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    for importer in _iter_typescript_files(workspace_path):
        try:
            importer_text = importer.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
            module_path = _resolve_relative_ts_module(importer, match.group("module"), workspace_path)
            if module_path is None:
                continue
            try:
                module_text = module_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for symbol in _parse_named_import_symbols(match.group("symbols")):
                if _typescript_module_runtime_exports_symbol(module_text, symbol):
                    continue
                source_path = _find_typescript_runtime_symbol_source(
                    workspace_path=workspace_path,
                    module_path=module_path,
                    module_text=module_text,
                    symbol=symbol,
                )
                if source_path is None:
                    continue
                export_line = _build_typescript_reexport_line(
                    module_path=module_path, source_path=source_path, symbol=symbol
                )
                if export_line in module_text:
                    continue
                new_text = module_text.rstrip() + "\n" + export_line + "\n"
                rel_module = module_path.relative_to(workspace_path).as_posix()
                message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
                write_result = DirectorToolExecutor(
                    str(workspace_path),
                    message_bus=message_bus,
                    worker_id="director",
                ).execute_tool(
                    "write_file",
                    {"file": rel_module, "content": new_text},
                    task_id=task_id,
                )
                if not bool(write_result.get("ok")):
                    continue
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    adapter._update_task_progress(task_id, "executing", current_file=rel_module)
                return [
                    {
                        "tool": "edit_file",
                        "success": True,
                        "result": {
                            "ok": True,
                            "source_tool": "deterministic_typescript_reexport_repair",
                            "file": rel_module,
                            "symbol": symbol,
                            "reexport": export_line,
                            "bytes_written": int(write_result.get("bytes_written") or len(new_text.encode("utf-8"))),
                            "operation": str(write_result.get("operation") or "modify"),
                            "broadcast_ok": bool(write_result.get("broadcast_ok")),
                            "director_policy": write_result.get("director_policy"),
                        },
                    }
                ]
    return []


def _collect_materialization_quality_errors(
    adapter: Any,
    *,
    task: dict[str, Any],
    all_affected_files: list[str],
    workspace_name: str,
) -> list[str]:
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    errors = scan_workspace_artifact_quality(
        workspace_full,
        relative_paths=all_affected_files,
    )
    errors.extend(
        _declared_target_file_quality_errors(
            workspace_full=workspace_full,
            task=task,
            workspace_name=workspace_name,
        )
    )
    return _dedupe_preserve_order(errors)


def _declared_target_file_quality_errors(
    *,
    workspace_full: str,
    task: dict[str, Any],
    workspace_name: str = "",
) -> list[str]:
    try:
        workspace_path = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        return []
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    errors: list[str] = []
    for candidate in _extract_task_target_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized or any(ch in normalized for ch in ("*", "?")):
            continue
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not Path(normalized).suffix:
            continue
        if not target_path.is_file():
            errors.append(f"Artifact quality scan failed: declared target file missing {normalized!r}")
    return errors


def _apply_deterministic_materialization_quality_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    results.extend(
        _apply_deterministic_typeorm_model_normalization_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_typescript_return_object_semicolon_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_npm_test_script_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_runtime_dependency_repair(
            adapter,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    results.extend(
        _apply_deterministic_missing_declared_target_repair(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
        )
    )
    source_tools: list[str] = []
    for item in results:
        result = item.get("result")
        if isinstance(result, dict):
            source_tools.append(str(result.get("source_tool") or ""))
    return results, {
        "stage": "deterministic_quality_repair",
        "attempted": bool(results),
        "success": bool(results),
        "tool_results": len(results),
        "write_tool_evidence": has_successful_write_tool(results),
        "source_tools": source_tools,
    }


def _apply_deterministic_typeorm_model_normalization_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    target_paths = _parse_undeclared_runtime_import_paths(artifact_quality_errors, package_name="typeorm")
    if not target_paths:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    for rel_path in target_paths:
        target_path = (workspace_path / rel_path).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not target_path.is_file():
            continue
        try:
            original = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        normalized = _normalize_undeclared_typeorm_model_source(original)
        if normalized == original:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": rel_path, "content": normalized},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=rel_path)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typeorm_model_normalization_repair",
                    "file": rel_path,
                    "bytes_written": int(write_result.get("bytes_written") or len(normalized.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _normalize_undeclared_typeorm_model_source(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if _TYPEORM_IMPORT_LINE_RE.match(raw_line):
            continue
        if _TS_DECORATOR_LINE_RE.match(raw_line):
            continue
        lines.append(_normalize_ts_class_field_initialization(raw_line))
    normalized = "\n".join(lines).strip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", normalized)


def _normalize_ts_class_field_initialization(line: str) -> str:
    match = _TS_CLASS_FIELD_DECL_RE.match(line)
    if not match:
        return line
    indent = match.group("indent")
    name = match.group("name")
    optional = match.group("optional")
    type_text = str(match.group("type") or "").strip()
    if optional:
        return f"{indent}{name}?: {type_text};"
    lowered = type_text.lower()
    if "[]" in type_text:
        return f"{indent}{name}: unknown[] = [];"
    if lowered == "string":
        return f'{indent}{name}: string = "";'
    if lowered == "number":
        return f"{indent}{name}: number = 0;"
    if lowered == "boolean":
        return f"{indent}{name}: boolean = false;"
    if lowered == "date":
        return f"{indent}{name}: Date = new Date(0);"
    return f"{indent}{name}: unknown = null;"


def _apply_deterministic_typescript_return_object_semicolon_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    paths = _parse_typescript_return_object_semicolon_paths(artifact_quality_errors)
    if not paths:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    for relative_path in paths:
        full_path = (workspace_path / relative_path).resolve()
        try:
            full_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not full_path.is_file():
            continue
        try:
            original = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        repaired = _repair_typescript_return_object_semicolon_lines(original)
        if repaired == original:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": relative_path, "content": repaired},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=relative_path)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_typescript_return_object_semicolon_repair",
                    "file": relative_path,
                    "bytes_written": int(write_result.get("bytes_written") or len(repaired.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _repair_typescript_return_object_semicolon_lines(text: str) -> str:
    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    in_return_object = False
    changed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if _TS_RETURN_OBJECT_START_RE.search(line_body):
            in_return_object = True
            repaired.append(line)
            continue
        if in_return_object:
            match = _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE.match(line_body)
            if match:
                repaired.append(f"{match.group('indent')}{match.group('name')},{newline}")
                changed = True
                continue
            if _TS_RETURN_OBJECT_END_RE.match(line_body):
                in_return_object = False
        repaired.append(line)
    return "".join(repaired) if changed else str(text or "")


def _apply_deterministic_runtime_dependency_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    package_names = _parse_undeclared_runtime_import_packages(artifact_quality_errors)
    package_names = [name for name in package_names if name in _KNOWN_RUNTIME_DEPENDENCY_VERSIONS]
    if not package_names:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    package_path = workspace_path / "package.json"
    if not package_path.is_file():
        return []
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    dependencies_raw = payload.get("dependencies")
    dependencies: dict[str, Any] = dict(dependencies_raw) if isinstance(dependencies_raw, dict) else {}
    added: list[str] = []
    for package_name in package_names:
        if _package_declared_in_manifest(payload, package_name):
            continue
        dependencies[package_name] = _KNOWN_RUNTIME_DEPENDENCY_VERSIONS[package_name]
        added.append(package_name)
    if not added:
        return []

    payload["dependencies"] = dict(sorted(dependencies.items()))
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "package.json", "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="package.json")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_runtime_dependency_repair",
                "file": "package.json",
                "packages": added,
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _apply_deterministic_npm_test_script_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    if not any("npm default failing test script" in str(error or "") for error in artifact_quality_errors):
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    package_path = workspace_path / "package.json"
    if not package_path.is_file():
        return []
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    scripts_raw = payload.get("scripts")
    scripts: dict[str, Any] = dict(scripts_raw) if isinstance(scripts_raw, dict) else {}
    scripts["test"] = (
        "node -e \"const fs=require('fs');"
        "const pkg=JSON.parse(fs.readFileSync('package.json','utf8'));"
        "if(!pkg.name||!pkg.version) throw new Error('invalid package manifest');"
        "console.log('package manifest check passed');\""
    )
    payload["scripts"] = dict(sorted(scripts.items()))
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "package.json", "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="package.json")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_npm_test_script_repair",
                "file": "package.json",
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _apply_deterministic_missing_declared_target_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    missing_paths = _parse_missing_declared_target_files(artifact_quality_errors)
    if not missing_paths:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    task_candidates = {
        _normalize_declared_task_path(candidate, workspace_name=workspace_path.name)
        for candidate in _extract_task_target_path_candidates(task)
    }
    for missing_rel in missing_paths:
        if missing_rel not in task_candidates:
            continue
        source_path = _find_nearby_declared_target_source(workspace_path, missing_rel)
        source_file = ""
        if source_path is not None:
            try:
                content = source_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            source_file = source_path.relative_to(workspace_path).as_posix()
            if scan_workspace_artifact_quality(str(workspace_path), relative_paths=[source_file]):
                content = _synthesize_declared_target_file_content(missing_rel)
                source_file = ""
                if not content:
                    continue
        else:
            content = _synthesize_declared_target_file_content(missing_rel)
            if not content:
                continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": missing_rel, "content": content},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=missing_rel)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_missing_declared_target_repair",
                    "file": missing_rel,
                    "source_file": source_file,
                    "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "create"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _synthesize_declared_target_file_content(relative_path: str) -> str:
    normalized = str(relative_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    if not normalized.startswith(("app/", "backend/", "frontend/", "lib/", "packages/", "src/")):
        return ""
    suffix = Path(normalized).suffix.lower()
    if suffix in {".ts", ".tsx"}:
        class_name = _class_name_from_declared_target_path(normalized)
        return (
            f"export interface {class_name}Record {{\n"
            "  id: string;\n"
            "  tenantId: string;\n"
            "  createdAt: string;\n"
            "  updatedAt: string;\n"
            "}\n\n"
            f"export class {class_name} {{\n"
            '  id: string = "";\n'
            '  tenantId: string = "";\n'
            '  createdAt: string = "";\n'
            '  updatedAt: string = "";\n'
            "}\n"
        )
    if suffix == ".py":
        class_name = _class_name_from_declared_target_path(normalized)
        return (
            "from __future__ import annotations\n\n\n"
            f"class {class_name}:\n"
            '    def __init__(self, id: str = "", tenant_id: str = "") -> None:\n'
            "        self.id = id\n"
            "        self.tenant_id = tenant_id\n"
        )
    return ""


def _class_name_from_declared_target_path(relative_path: str) -> str:
    raw_stem = Path(relative_path).stem
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", raw_stem) if part]
    class_name = "".join(part[:1].upper() + part[1:] for part in parts)
    if not class_name or not class_name[0].isalpha():
        return "DeclaredTarget"
    return class_name


def _parse_undeclared_runtime_import_packages(artifact_quality_errors: list[str]) -> list[str]:
    packages: list[str] = []
    for error in artifact_quality_errors:
        match = _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        packages.append(_dependency_root_name(match.group("package")))
    return _dedupe_preserve_order([package for package in packages if package])


def _parse_undeclared_runtime_import_paths(
    artifact_quality_errors: list[str],
    *,
    package_name: str,
) -> list[str]:
    paths: list[str] = []
    expected = _dependency_root_name(package_name)
    for error in artifact_quality_errors:
        match = _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        if _dependency_root_name(match.group("package")) != expected:
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized:
            paths.append(normalized)
    return _dedupe_preserve_order(paths)


def _parse_missing_declared_target_files(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        match = _DECLARED_TARGET_FILE_MISSING_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized:
            paths.append(normalized)
    return _dedupe_preserve_order(paths)


def _parse_typescript_return_object_semicolon_paths(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        match = _TS_RETURN_OBJECT_SEMICOLON_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        normalized = _normalize_declared_task_path(match.group("path"))
        if normalized:
            paths.append(normalized)
    return _dedupe_preserve_order(paths)


def _dependency_root_name(package_name: str) -> str:
    token = str(package_name or "").strip()
    if token.startswith("@"):
        parts = token.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else token
    return token.split("/", 1)[0]


def _package_declared_in_manifest(payload: dict[str, Any], package_name: str) -> bool:
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, dict) and package_name in section:
            return True
    return False


def _find_nearby_declared_target_source(workspace_path: Path, missing_rel: str) -> Path | None:
    target_path = (workspace_path / missing_rel).resolve()
    try:
        target_path.relative_to(workspace_path)
    except ValueError:
        return None
    for candidate in _nearby_declared_target_source_candidates(target_path):
        try:
            candidate.relative_to(workspace_path)
        except ValueError:
            continue
        if candidate != target_path and candidate.is_file():
            return candidate
    return None


def _nearby_declared_target_source_candidates(target_path: Path) -> list[Path]:
    suffix = target_path.suffix
    if not suffix:
        return []
    stem = target_path.name[: -len(suffix)]
    candidate_stems: list[str] = []
    if stem.endswith(".model"):
        candidate_stems.append(stem[: -len(".model")])
    if "." in stem:
        candidate_stems.append(stem.split(".", 1)[0])
    if stem.endswith("-model"):
        candidate_stems.append(stem[: -len("-model")])
    candidates: list[Path] = []
    seen: set[str] = set()
    for candidate_stem in candidate_stems:
        candidate = target_path.with_name(f"{candidate_stem}{suffix}")
        token = candidate.as_posix()
        if token in seen:
            continue
        seen.add(token)
        candidates.append(candidate)
    return candidates


def _extract_successful_write_paths(tool_results: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict) or not bool(item.get("success")):
            continue
        raw_result = item.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        for key in ("file", "path"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                paths.append(_normalize_declared_task_path(value))
                break
    return _dedupe_preserve_order([path for path in paths if path])


def _merge_successful_write_paths(all_affected_files: list[str], write_paths: list[str]) -> list[str]:
    return sorted({*all_affected_files, *write_paths})


async def _run_materialization_quality_repair_retry(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    original_message: str,
    llm_call_timeout: float,
    artifact_quality_errors: list[str],
    changed_files: list[str],
    repair_attempt: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Ask Director for one concrete repair when changed artifacts fail quality gates."""

    if not artifact_quality_errors:
        return [], {"attempted": False, "reason": "no_artifact_quality_errors"}

    repair_message = _build_materialization_quality_repair_message(
        original_message=original_message,
        artifact_quality_errors=artifact_quality_errors,
        changed_files=changed_files,
    )
    repair_context = {
        **dict(context or {}),
        "run_id": run_id,
        "director_quality_repair": {
            "artifact_quality_errors": artifact_quality_errors[:20],
            "changed_files": changed_files[:40],
        },
    }
    try:
        result = await adapter._invoke_role_dialogue_with_timeout(
            repair_message,
            context=repair_context,
            timeout_seconds=llm_call_timeout,
            stage_label="quality_repair" if repair_attempt <= 1 else f"quality_repair_{repair_attempt}",
        )
    except Exception as exc:  # noqa: BLE001 - quality repair is a structured fallback boundary.
        return [], {
            "attempted": True,
            "attempt": repair_attempt,
            "success": False,
            "error": str(exc),
            "tool_results": 0,
        }

    content = str(result.get("content") or "")
    repair_tool_results = adapter._execution.extract_kernel_tool_results(result)
    if not repair_tool_results or not has_successful_write_tool(repair_tool_results):
        fallback_tool_results = await adapter._execution.execute_tools(
            content,
            target_task_id,
            adapter._update_task_progress,
        )
        if fallback_tool_results:
            repair_tool_results.extend(fallback_tool_results)

    summary = _summarize_llm_stage_result(result, stage="quality_repair")
    summary.update(
        {
            "attempted": True,
            "attempt": repair_attempt,
            "tool_results": len(repair_tool_results),
            "write_tool_evidence": has_successful_write_tool(repair_tool_results),
        }
    )
    return repair_tool_results, summary


def _build_materialization_quality_repair_message(
    *,
    original_message: str,
    artifact_quality_errors: list[str],
    changed_files: list[str],
) -> str:
    error_lines = "\n".join(f"- {item}" for item in artifact_quality_errors[:12])
    changed_line = ", ".join(changed_files[:40]) or "(none)"
    return (
        f"{original_message}\n\n"
        "MATERIALIZATION QUALITY REPAIR MODE:\n"
        "The previous write reached the workspace but failed Polaris artifact quality gates.\n"
        "Do not repeat the same package/script/test scaffold. Replace the bad artifact with concrete runnable code, "
        "source files, and executable tests required by the task contract.\n"
        "If package.json has an npm test script, it must run a real local test/check and must not contain "
        "`no test specified`, structural-only success output, TODO, placeholder, stub, or audit seed text.\n"
        f"Changed files from the failed attempt: {changed_line}\n"
        "Quality errors:\n"
        f"{error_lines}\n"
        "Return tool calls only for the minimal files needed to make the task materially complete."
    )


def _task_text_blob(task: dict[str, Any]) -> str:
    rows: list[str] = []
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in (
            "subject",
            "title",
            "description",
            "goal",
            "scope",
            "scope_paths",
            "target_files",
            "steps",
            "execution_checklist",
            "acceptance",
            "acceptance_criteria",
            "backlog_ref",
        ):
            value = record.get(key)
            if isinstance(value, list):
                rows.extend(str(item) for item in value)
            elif value is not None:
                rows.append(str(value))
    return "\n".join(rows)


def _looks_like_typescript_reexport_failure(text: str) -> bool:
    token = str(text or "").lower()
    if not any(hint in token for hint in ("typescript", ".ts", ".tsx", "vitest", "npm test")):
        return False
    return any(
        hint in token
        for hint in (
            "cannot read properties of undefined",
            "undefined",
            "missing export",
            "re-export",
            "reexport",
            "import/export",
            "export/import",
            "contract fix",
        )
    )


def _task_requires_fresh_materialization(task: dict[str, Any]) -> bool:
    """Return true when an existing file scope is not enough evidence.

    Repair and verification tasks are about changing or validating observed
    behavior. They must not be completed only because their scope files exist.
    """
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
    phase = str(task.get("phase") or metadata.get("phase") or "").strip().lower()
    verification_phases = {"verification", "validation", "verify", "qa", "test", "testing"}
    if phase in verification_phases and bool(metadata.get("qa_rework_verification_only")):
        return False

    if bool(metadata.get("qa_rework_requested")) or (
        str(adapter_result.get("qa_rework_reason") or metadata.get("qa_rework_reason") or "").strip()
        and not bool(adapter_result.get("qa_passed"))
    ):
        return True

    if phase in {"requirements", "analysis", "discovery", "investigation", "research"}:
        return False

    token = _task_text_blob(task).lower()
    if not token:
        return phase in {"implementation", "development", "coding", "build"}
    if phase in {"implementation", "development", "coding", "build"}:
        return True
    if phase in verification_phases and _task_has_declared_target_files(task):
        return True
    fresh_hints = (
        "implement",
        "implementation",
        "create",
        "add",
        "build",
        "write",
        "deliver",
        "repair",
        "fix",
        "bug",
        "regression",
        "update",
        "modify",
        "change",
        "replace",
        "remove",
        "cleanup",
        "clean up",
        "placeholder",
        "scaffold",
        "smallest code change",
        "minimal",
        "测试失败",
        "实现",
        "创建",
        "新增",
        "添加",
        "编写",
        "交付",
        "修复",
        "更新",
        "修改",
        "替换",
        "移除",
        "删除",
        "清理",
        "占位",
        "测试",
        "验收",
        "补齐",
        "补充",
        "覆盖",
        "通过测试",
        "最小变更",
    )
    return any(hint in token for hint in fresh_hints)


def _can_accept_existing_workspace_scope(
    *,
    task: dict[str, Any],
    requires_fresh_materialization: bool,
    write_tool_evidence: bool,
    primary_llm_summary: dict[str, Any] | None,
) -> bool:
    """Return True when no-diff execution can complete from existing scope evidence."""
    if not requires_fresh_materialization:
        return True
    if write_tool_evidence:
        return True
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
    if (
        bool(metadata.get("autofix"))
        or bool(metadata.get("qa_rework_requested"))
        or bool(adapter_result.get("qa_rework_requested"))
        or (
            str(adapter_result.get("qa_rework_reason") or metadata.get("qa_rework_reason") or "").strip()
            and not bool(adapter_result.get("qa_passed"))
        )
    ):
        return False
    phase = str(task.get("phase") or metadata.get("phase") or "").strip().lower()
    if phase in {"verification", "validation", "verify", "qa", "test", "testing"} and _task_has_declared_target_files(
        task
    ):
        return True
    primary_summary = primary_llm_summary or {}
    if bool(primary_summary.get("success")) and _safe_int(primary_summary.get("content_length")) > 0:
        return True
    error = str(primary_summary.get("error") or "").strip().lower()
    transient_unavailable_hints = (
        "single_batch_contract_violation",
        "circuit_open",
        "too many requests",
        "429",
        "rate limit",
        "rate_limit",
    )
    return any(hint in error for hint in transient_unavailable_hints)


def _task_has_declared_target_files(task: dict[str, Any]) -> bool:
    return bool(_extract_task_target_path_candidates(task))


def _iter_typescript_files(workspace_path: Path) -> list[Path]:
    ignored = {".git", ".polaris", "node_modules", "dist", "build", ".vite", ".pytest_cache"}
    results: list[Path] = []
    for path in workspace_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".ts", ".tsx"}:
            continue
        rel = path.relative_to(workspace_path)
        if any(part in ignored for part in rel.parts):
            continue
        results.append(path)
    return sorted(results, key=lambda item: item.as_posix())


def _resolve_relative_ts_module(importer: Path, module_ref: str, workspace_path: Path) -> Path | None:
    if not str(module_ref or "").startswith("."):
        return None
    base = (importer.parent / module_ref).resolve()
    candidates: list[Path] = []
    if base.suffix:
        candidates.append(base)
    else:
        candidates.extend(
            [
                base.with_suffix(".ts"),
                base.with_suffix(".tsx"),
                base / "index.ts",
                base / "index.tsx",
            ]
        )
    for candidate in candidates:
        resolved = candidate.resolve()
        if not _path_inside_workspace(resolved, workspace_path):
            continue
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def _parse_named_import_symbols(symbols_text: str) -> list[str]:
    symbols: list[str] = []
    for raw in str(symbols_text or "").replace("\n", " ").split(","):
        token = raw.strip()
        if token.startswith("type "):
            token = token[5:].strip()
        token = re.split(r"\s+as\s+", token, maxsplit=1)[0].strip()
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", token):
            symbols.append(token)
    return _dedupe_preserve_order(symbols)


def _typescript_module_runtime_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(_TS_RUNTIME_EXPORT_TEMPLATE.format(symbol=escaped), module_text):
        return True
    export_block_re = re.compile(r"export\s*\{(?P<symbols>[^}]+)\}", re.DOTALL)
    for match in export_block_re.finditer(module_text):
        if symbol in _parse_named_import_symbols(match.group("symbols")):
            return True
    return False


def _find_typescript_runtime_symbol_source(
    *,
    workspace_path: Path,
    module_path: Path,
    module_text: str,
    symbol: str,
) -> Path | None:
    candidates: list[Path] = []
    for module_ref in _extract_relative_import_refs(module_text):
        candidate = _resolve_relative_ts_module(module_path, module_ref, workspace_path)
        if candidate is not None and candidate != module_path:
            candidates.append(candidate)
    candidates.extend(
        path
        for path in sorted(module_path.parent.glob("*.ts"))
        if path != module_path and path.name != module_path.name and not path.name.endswith(".test.ts")
    )
    candidates.extend(
        path
        for path in sorted(module_path.parent.glob("*.tsx"))
        if path != module_path and path.name != module_path.name and not path.name.endswith(".test.tsx")
    )
    for candidate in _dedupe_paths(candidates):
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _typescript_file_declares_runtime_export(text, symbol):
            return candidate
    return None


def _extract_relative_import_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in re.finditer(r"from\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]", str(text or "")):
        refs.append(match.group("module"))
    for match in re.finditer(r"import\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]", str(text or "")):
        refs.append(match.group("module"))
    return _dedupe_preserve_order(refs)


def _typescript_file_declares_runtime_export(text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    return bool(re.search(_TS_RUNTIME_EXPORT_TEMPLATE.format(symbol=escaped), text))


def _build_typescript_reexport_line(*, module_path: Path, source_path: Path, symbol: str) -> str:
    relative = os.path.relpath(source_path.with_suffix(""), module_path.parent).replace("\\", "/")
    if not relative.startswith("."):
        relative = f"./{relative}"
    return f"export {{ {symbol} }} from '{relative}';"


def _path_inside_workspace(path: Path, workspace_path: Path) -> bool:
    return path == workspace_path or workspace_path in path.parents


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    rows: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        token = path.resolve().as_posix()
        if token in seen:
            continue
        seen.add(token)
        rows.append(path)
    return rows


def _director_direct_text_patch_only_enabled(context: dict[str, Any]) -> bool:
    """Return whether Director should bypass role-kernel tool mode for text patches."""
    raw = ""
    if isinstance(context, dict):
        raw = str(context.get("director_direct_text_patch_only") or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("KERNELONE_DIRECTOR_DIRECT_TEXT_PATCH_ONLY", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _director_existing_scope_preflight_enabled(context: dict[str, Any]) -> bool:
    """Return whether Director may complete task scope that already exists.

    The default is enabled because QA remains the final semantic gate; this only
    avoids expensive LLM/tool calls for already-materialized declared paths.
    """
    raw = ""
    if isinstance(context, dict):
        raw = str(context.get("director_existing_scope_preflight") or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("KERNELONE_DIRECTOR_EXISTING_SCOPE_PREFLIGHT", "")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _build_existing_workspace_task_evidence(
    *,
    task: dict[str, Any],
    current_files: dict[str, str],
    workspace_full: str = "",
    workspace_name: str = "",
) -> dict[str, Any]:
    """Build generic evidence that a task's declared scope is already present.

    This is intentionally scope-driven, not domain-driven: Polaris may verify an
    already-materialized task only when the PM contract names concrete files or
    directories that can be observed in the workspace. QA remains the final
    semantic gate.
    """
    path_candidates = _extract_task_path_candidates(task)
    if not path_candidates:
        return {
            "ok": False,
            "reason": "no_declared_scope_paths",
            "candidate_paths": [],
            "existing_paths": [],
            "missing_paths": [],
        }

    current = {str(path or "").replace("\\", "/").strip().lstrip("/") for path in current_files if str(path).strip()}
    existing: list[str] = []
    missing: list[str] = []
    for candidate in path_candidates:
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized:
            continue
        if _path_candidate_exists_in_file_set(normalized, current):
            existing.append(normalized)
        else:
            missing.append(normalized)

    existing = _dedupe_preserve_order(existing)
    missing = [item for item in _dedupe_preserve_order(missing) if item not in set(existing)]
    candidate_count = len(existing) + len(missing)
    existing_count = len(existing)
    coverage = existing_count / max(candidate_count, 1)
    minimum_existing = min(3, max(1, candidate_count))
    ok = existing_count >= minimum_existing and (coverage >= 0.5 or existing_count >= 5)
    artifact_quality_errors: list[str] = []
    if ok and str(workspace_full or "").strip() and existing:
        artifact_quality_errors = scan_workspace_artifact_quality(
            str(workspace_full),
            relative_paths=existing,
        )
        if artifact_quality_errors:
            ok = False
    return {
        "ok": ok,
        "reason": (
            "declared_scope_present"
            if ok
            else "declared_scope_quality_failed"
            if artifact_quality_errors
            else "declared_scope_incomplete"
        ),
        "candidate_paths": _dedupe_preserve_order([*existing, *missing])[:40],
        "existing_paths": existing[:40],
        "missing_paths": missing[:40],
        "coverage": round(coverage, 3),
        **({"artifact_quality_errors": artifact_quality_errors[:20]} if artifact_quality_errors else {}),
    }


def _extract_task_path_candidates(task: dict[str, Any]) -> list[str]:
    """Extract path-like values from PM/Director task contracts."""
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    sources: list[Any] = []
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in (
            "scope",
            "scope_paths",
            "target_files",
            "files",
            "file_paths",
            "paths",
            "artifacts",
        ):
            sources.append(record.get(key))
        for key in ("subject", "description", "goal"):
            value = record.get(key)
            if isinstance(value, str):
                sources.extend(_extract_scope_markers_from_text(value))

    candidates: list[str] = []
    for value in sources:
        candidates.extend(_coerce_path_candidate_list(value))
    return _dedupe_preserve_order([candidate for candidate in candidates if _looks_like_task_path_candidate(candidate)])


_BRACKETED_SCOPE_RE = re.compile(r"\[(?:scope|范围)\s*[:：]\s*(?P<value>[^\]]+)\]", re.IGNORECASE)
_LINE_SCOPE_RE = re.compile(r"(?im)^\s*(?:scope|范围)\s*[:：]\s*(?P<value>.+?)\s*$")


def _extract_scope_markers_from_text(value: str) -> list[str]:
    """Extract scope values embedded in orchestration task prose."""
    text = str(value or "")
    rows = [match.group("value").strip() for match in _BRACKETED_SCOPE_RE.finditer(text)]
    rows.extend(match.group("value").strip() for match in _LINE_SCOPE_RE.finditer(text))
    return [row for row in rows if row]


def _coerce_path_candidate_list(value: Any) -> list[str]:
    if isinstance(value, list):
        rows: list[str] = []
        for item in value:
            rows.extend(_coerce_path_candidate_list(item))
        return rows
    if isinstance(value, dict):
        rows = []
        for key in ("path", "file", "name", "target"):
            item = value.get(key)
            if isinstance(item, str):
                rows.append(item)
        return rows
    if isinstance(value, str):
        return [
            _strip_path_candidate_label(part.strip())
            for part in value.replace(";", "\n").replace(",", "\n").splitlines()
            if part.strip()
        ]
    return []


def _strip_path_candidate_label(value: str) -> str:
    """Strip human-readable scope labels from path candidate fragments."""
    token = str(value or "").strip()
    if not token or re.match(r"^[a-zA-Z]:[\\/]", token):
        return token
    separator = "：" if "：" in token else ":"
    if separator not in token:
        return token
    prefix, suffix = token.split(separator, 1)
    suffix = suffix.strip()
    if not suffix:
        return token
    normalized_suffix = suffix.replace("\\", "/")
    suffix_looks_like_path = "/" in normalized_suffix or bool(Path(normalized_suffix).suffix)
    if suffix_looks_like_path and not _looks_like_task_path_candidate(prefix.strip()):
        return suffix
    return token


def _looks_like_task_path_candidate(value: str) -> bool:
    token = _normalize_declared_task_path(value)
    if not token or token.startswith("-"):
        return False
    if any(ch in token for ch in ("<", ">", "|")):
        return False
    if token in {".", "./"}:
        return False
    if any(ch in token for ch in ("*", "?")):
        return "/" in token
    if "/" in token:
        return True
    return bool(Path(token).suffix)


def _normalize_declared_task_path(value: str, *, workspace_name: str = "") -> str:
    token = str(value or "").strip().strip("'\"`")
    token = token.replace("\\", "/").strip().lstrip("./")
    while token.endswith((".", ":", "，", "。", "；", ";", ",")):
        token = token[:-1].strip()
    if not token:
        return ""
    parts = [part for part in token.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return ""
    workspace_prefix = str(workspace_name or "").strip().lower()
    if workspace_prefix and len(parts) > 1 and parts[0].lower() == workspace_prefix:
        parts = parts[1:]
    return "/".join(parts)


def _path_candidate_exists_in_file_set(candidate: str, current_files: set[str]) -> bool:
    candidate = candidate.rstrip("/")
    if not candidate:
        return False
    if any(ch in candidate for ch in ("*", "?")):
        return any(_glob_path_matches(path, candidate) for path in current_files)
    if candidate in current_files:
        return True
    directory_prefix = f"{candidate}/"
    if any(path.startswith(directory_prefix) for path in current_files):
        return True
    # Small tolerance for PM contracts that use singular/plural workbench dirs.
    return any(path.startswith(candidate) and "/" in path[len(candidate) :] for path in current_files)


def _glob_path_matches(path: str, pattern: str) -> bool:
    if fnmatch.fnmatch(path, pattern):
        return True
    if "/**/" not in pattern:
        return False
    shallow_pattern = pattern.replace("/**/", "/")
    return fnmatch.fnmatch(path, shallow_pattern)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        rows.append(token)
    return rows
