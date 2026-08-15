"""Public execute_director_task entrypoint for Director execute."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from typing import Any

from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    create_task_runtime_execution_attempt_authority,
)
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)

from ..helpers import (
    _DEFAULT_TASK_LEASE_TTL_SECONDS,
    _TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
)
from ._claim import (
    _claim_task_with_retry,
    _finalize_claimed_execution,
    _handle_claim_required,
    _suspend_claimed_execution_for_cancellation,
    _task_completion_projection_from_context,
    _task_runtime_finalization_failed_result,
    _task_runtime_heartbeat_exception_signal,
    _task_runtime_heartbeat_failed_signal,
    _with_decision_signals,
    _with_task_runtime_finalize_evidence,
)
from ._llm_flow import (
    _execute_standard_llm_flow,
)

logger = logging.getLogger(__name__)


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
    task_market_exact_claim = bool(str(input_metadata.get("task_market_task_id") or "").strip()) or str(
        input_metadata.get("source") or ""
    ).strip().startswith("runtime.task_market")
    exact_handoff_claim = any(
        str(input_metadata.get(key) or "").strip()
        for key in (
            "chief_engineer_blueprint_id",
            "chief_engineer_handoff_id",
            "pm_task_id",
            "source_task_id",
            "external_task_id",
        )
    )

    task = adapter._get_task(target_task_id)
    if task:
        selected_from_board = True
    # Live L1-10: factory drain left TASK-1 as overlay row 42 with no TaskBoard
    # file. get_task() is overlay-only so lookup succeeded, then claim_execution
    # failed task_not_found. ensure_task_row rematerializes a claimable row.
    if requested_task_id:
        ensured = adapter._materialize_runtime_task(requested_task_id, input_data)
        if isinstance(ensured, dict) and str(ensured.get("id") or "").strip():
            found_id = str((task or {}).get("id") or "").strip()
            ensured_id = str(ensured.get("id") or "").strip()
            if found_id != ensured_id:
                selection_source = "materialized_orchestration_task"
            task = ensured
            selected_from_board = True
    if not task:
        if task_market_exact_claim or exact_handoff_claim:
            selection_source = "materialized_orchestration_task"
            task = adapter._materialize_runtime_task(requested_task_id, input_data)
            selected_from_board = True
        else:
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
    context = dict(context or {})
    metadata = dict(context.get("metadata") or {})
    context["task_id"] = target_task_id
    context["target_task_id"] = target_task_id
    context.setdefault("pm_task_id", requested_task_id or target_task_id)
    metadata["task_id"] = target_task_id
    metadata["target_task_id"] = target_task_id
    metadata.setdefault("pm_task_id", requested_task_id or target_task_id)
    context["metadata"] = metadata
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
    ) = await _claim_task_with_retry(
        adapter,
        task,
        target_task_id,
        selection_source,
        requested_task_id,
        run_id,
        input_metadata,
    )

    selected_subject = str(task.get("subject") or task.get("title") or "").strip()
    session_raw = task_claim_result.get("session")
    task_claim_session: dict[str, Any] = session_raw if isinstance(session_raw, dict) else {}
    task_claim_session_id = str(task_claim_session.get("session_id") or "").strip()
    attempt_record = task_claim_result.get("execution_attempt")
    try:
        task_execution_attempt = (
            TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
            if isinstance(attempt_record, dict)
            else None
        )
    except (TypeError, ValueError):
        task_execution_attempt = None
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None
    if board_claim_applied and task_execution_attempt is None:
        return {
            "success": False,
            "task_id": target_task_id,
            "error": "director_task_runtime_execution_attempt_missing",
            "control_plane_failure_code": "director_task_runtime_execution_attempt_missing",
        }
    if board_claim_applied and task_execution_attempt is not None:
        if task_claim_session_id and task_claim_session_id != task_execution_attempt.session_id:
            return {
                "success": False,
                "task_id": target_task_id,
                "error": "director_task_runtime_execution_attempt_session_mismatch",
                "control_plane_failure_code": "director_task_runtime_execution_attempt_session_mismatch",
            }
        task_claim_session_id = task_execution_attempt.session_id
        # Propagate the physical task-runtime lease into RoleRuntime/TransactionKernel.
        # The kernel checks this immediately before executing tools, so a late LLM
        # response from a cancelled/suspended Director claim cannot still write files.
        task_execution_attempt_authority = create_task_runtime_execution_attempt_authority(task_execution_attempt)
        context = dict(context or {})
        context["session_id"] = task_claim_session_id
        context["task_runtime_session_id"] = task_claim_session_id
        context["task_runtime_guard"] = True
        # Preserve the immutable claim identity for deferred planning. Commit still
        # consumes the live attempt authority and therefore remains fail-closed.
        context["task_runtime_execution_attempt"] = task_execution_attempt
        context["task_runtime_execution_attempt_authority"] = task_execution_attempt_authority
        metadata = dict(context.get("metadata") or {})
        metadata.setdefault("session_id", task_claim_session_id)
        metadata["task_runtime_session_id"] = task_claim_session_id
        metadata["task_runtime_guard"] = True
        context["metadata"] = metadata

    promote_task_contract = getattr(adapter, "_promote_task_contract_to_runtime_context", None)
    if callable(promote_task_contract):
        promote_task_contract(
            task=task,
            context=context,
            workspace=str(getattr(adapter, "workspace", "") or ""),
        )

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
    decision_signals: list[dict[str, Any]] = []
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
                    if task_execution_attempt_authority is None:
                        raise RuntimeError("director_task_runtime_execution_attempt_authority_missing")
                    heartbeat_verdict = task_execution_attempt_authority.heartbeat(
                        lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
                        lock_timeout_seconds=5.0,
                        context_summary=selected_subject,
                    )
                    if heartbeat_verdict.success is not True:
                        decision_signals.append(
                            _task_runtime_heartbeat_failed_signal(
                                {
                                    "success": False,
                                    "reason": heartbeat_verdict.code,
                                    "identity": (
                                        heartbeat_verdict.identity.to_record()
                                        if heartbeat_verdict.identity is not None
                                        else None
                                    ),
                                }
                            )
                        )
                        return
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    decision_signals.append(_task_runtime_heartbeat_exception_signal(exc))
                    return

    async def _stop_task_claim_heartbeat() -> None:
        if heartbeat_task is None:
            return
        heartbeat_stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    if board_claim_applied and task_execution_attempt_authority is not None:
        heartbeat_task = asyncio.create_task(_run_task_claim_heartbeat())

    try:
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

                if board_claim_applied and task_execution_attempt_authority is not None:
                    if bool(result.get("success")):
                        finalize_result = _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="completed",
                            authority=task_execution_attempt_authority,
                            result_summary=f"director_{'hybrid' if use_hybrid else 'sequential'}_completed",
                            metadata={"adapter_phase": "completed"},
                            task_completion_projection=_task_completion_projection_from_context(
                                context,
                                target_task_id=target_task_id,
                            ),
                        )
                        if finalize_result.get("success") is not True:
                            return _task_runtime_finalization_failed_result(
                                target_task_id=target_task_id,
                                requested_outcome="completed",
                                finalize_result=finalize_result,
                                decision_signals=decision_signals,
                            )
                    else:
                        finalize_result = _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="failed",
                            authority=task_execution_attempt_authority,
                            error=str(result.get("error") or "director_sequential_execution_failed"),
                            metadata={"adapter_phase": "failed"},
                            task_completion_projection=_task_completion_projection_from_context(
                                context,
                                target_task_id=target_task_id,
                            ),
                        )
                        if isinstance(result, dict):
                            result = _with_task_runtime_finalize_evidence(
                                result,
                                requested_outcome="failed",
                                finalize_result=finalize_result,
                            )
                return _with_decision_signals(result, decision_signals) if isinstance(result, dict) else result
            except asyncio.CancelledError:
                if board_claim_applied and task_execution_attempt_authority is not None:
                    await _suspend_claimed_execution_for_cancellation(
                        adapter,
                        target_task_id=target_task_id,
                        run_id=run_id,
                        authority=task_execution_attempt_authority,
                    )
                raise

        # 标准 LLM 执行路径
        llm_call_timeout = adapter._execution.resolve_llm_call_timeout_seconds(context)

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
                task_execution_attempt_authority=task_execution_attempt_authority,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = f"director_runtime_exception:{exc}"
            runtime_exception_finalize_result: dict[str, Any] | None = None
            if board_claim_applied and task_execution_attempt_authority is not None:
                runtime_exception_finalize_result = _finalize_claimed_execution(
                    adapter,
                    target_task_id=target_task_id,
                    outcome="failed",
                    authority=task_execution_attempt_authority,
                    error=error,
                    metadata={"adapter_phase": "failed", "exception_type": type(exc).__name__},
                    task_completion_projection=_task_completion_projection_from_context(
                        context,
                        target_task_id=target_task_id,
                    ),
                )
            adapter._update_task_progress(target_task_id, "failed")
            result = {
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
            return _with_task_runtime_finalize_evidence(
                result,
                requested_outcome="failed",
                finalize_result=runtime_exception_finalize_result,
            )

    except asyncio.CancelledError:
        if board_claim_applied and task_execution_attempt_authority is not None:
            await _suspend_claimed_execution_for_cancellation(
                adapter,
                target_task_id=target_task_id,
                run_id=run_id,
                authority=task_execution_attempt_authority,
            )
        raise
    finally:
        await _stop_task_claim_heartbeat()
