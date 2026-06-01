"""Director execute 方法实现

包含 execute 方法及其辅助函数。此模块提供 Director 任务执行的核心逻辑。
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.events.file_event_broadcaster import (
    broadcast_file_written,
    calculate_patch,
)
from polaris.kernelone.fs.text_ops import write_text_atomic

from .helpers import (
    _DEFAULT_TASK_LEASE_TTL_SECONDS,
    _TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
    has_successful_write_tool,
    taskboard_snapshot_brief,
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
                        adapter.task_runtime.complete_execution(
                            target_task_id,
                            session_id=task_claim_session_id,
                            result_summary=f"director_{'hybrid' if use_hybrid else 'sequential'}_completed",
                            metadata={"adapter_phase": "completed"},
                        )
                    else:
                        adapter.task_runtime.fail_execution(
                            target_task_id,
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
                adapter.task_runtime.fail_execution(
                    target_task_id,
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
        claim_result = adapter.task_runtime.claim_execution(
            active_task_id,
            worker_id=adapter.role_id,
            role_id=adapter.role_id,
            run_id=run_id,
            lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
            selection_source=active_source,
            external_task_id=requested_task_id,
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
    preflight_existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=baseline_files,
        workspace_name=workspace_name,
    )
    if (
        _director_existing_scope_preflight_enabled(context)
        and not requires_fresh_materialization
        and bool(preflight_existing_contract_evidence.get("ok"))
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
        if board_claim_applied and task_claim_session_id:
            adapter.task_runtime.complete_execution(
                target_task_id,
                session_id=task_claim_session_id,
                result_summary=(
                    "preflight_verified_existing_workspace_scope="
                    f"{len(preflight_existing_contract_evidence.get('existing_paths') or [])}"
                ),
                metadata=completion_metadata,
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
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": "preflight_verified_existing_workspace_scope",
            "existing_contract_evidence": preflight_existing_contract_evidence,
        }

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
            context=None,
            timeout_seconds=llm_call_timeout,
            stage_label="first_call",
        )
    content = result.get("content", "")

    # 执行工具
    tool_results = adapter._execution.extract_kernel_tool_results(result)
    if not tool_results or not has_successful_write_tool(tool_results):
        fallback_tool_results = await adapter._execution.execute_tools(
            content, target_task_id, adapter._update_task_progress
        )
        if fallback_tool_results:
            tool_results.extend(fallback_tool_results)

    # 收集变更文件
    current_files = adapter._state_tracker.collect_workspace_code_files()
    new_files = sorted(set(current_files.keys()) - set(baseline_files.keys()))
    modified_files = [
        rel_path
        for rel_path, fingerprint in current_files.items()
        if rel_path in baseline_files and baseline_files[rel_path] != fingerprint
    ]

    all_affected_files = sorted(set(new_files + modified_files))
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

        current_files = adapter._state_tracker.collect_workspace_code_files()
        new_files = sorted(set(current_files.keys()) - set(baseline_files.keys()))
        modified_files = [
            rel_path
            for rel_path, fingerprint in current_files.items()
            if rel_path in baseline_files and baseline_files[rel_path] != fingerprint
        ]
        all_affected_files = sorted(set(new_files + modified_files))

    if not all_affected_files:
        deterministic_tool_results = _apply_deterministic_typescript_reexport_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
        if deterministic_tool_results:
            tool_results.extend(deterministic_tool_results)
            current_files = adapter._state_tracker.collect_workspace_code_files()
            new_files = sorted(set(current_files.keys()) - set(baseline_files.keys()))
            modified_files = [
                rel_path
                for rel_path, fingerprint in current_files.items()
                if rel_path in baseline_files and baseline_files[rel_path] != fingerprint
            ]
            all_affected_files = sorted(set(new_files + modified_files))

    existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=current_files,
        workspace_name=workspace_name,
    )
    write_tool_evidence = has_successful_write_tool(tool_results)

    if not all_affected_files and (requires_fresh_materialization or not bool(existing_contract_evidence.get("ok"))):
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
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if board_claim_applied and task_claim_session_id:
            adapter.task_runtime.fail_execution(
                target_task_id,
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

    if not all_affected_files and bool(existing_contract_evidence.get("ok")) and not requires_fresh_materialization:
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
        if board_claim_applied and task_claim_session_id:
            adapter.task_runtime.complete_execution(
                target_task_id,
                session_id=task_claim_session_id,
                result_summary=(
                    "verified_existing_workspace_scope="
                    f"{len(existing_contract_evidence.get('existing_paths') or [])}; "
                    f"tools_executed={len(tool_results)}"
                ),
                metadata=completion_metadata,
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
        decision_signals.append(
            {
                "code": "director.workspace_diff_without_write_tool",
                "severity": "info",
                "detail": (
                    "Workspace file changes were accepted as materialization evidence even though "
                    "the provider did not return a normalized write-tool result."
                ),
                "new_file_count": len(new_files),
                "modified_file_count": len(modified_files),
            }
        )

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
    if direct_fallback_summary is not None:
        completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary

    if board_claim_applied and task_claim_session_id:
        adapter.task_runtime.complete_execution(
            target_task_id,
            session_id=task_claim_session_id,
            result_summary=f"changed_files={len(all_affected_files)}; tools_executed={len(tool_results)}",
            metadata=completion_metadata,
        )

    adapter._update_task_progress(target_task_id, "completed")

    return {
        "success": True,
        "task_id": target_task_id,
        "tools_executed": len(tool_results),
        "tool_results": tool_results,
        "decision_signals": decision_signals,
        "qa_required_for_final_verdict": True,
        "artifacts": [],
        "materialization_mode": materialization_mode,
    }


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
                broadcast_file_written(
                    file_path=rel_module,
                    operation="modify",
                    content_size=len(new_text.encode("utf-8")),
                    task_id=task_id,
                    patch=calculate_patch(module_text, new_text),
                    message_bus=message_bus,
                    worker_id="director",
                )
                write_text_atomic(str(module_path), new_text, encoding="utf-8")
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
                        },
                    }
                ]
    return []


def _task_text_blob(task: dict[str, Any]) -> str:
    rows: list[str] = []
    raw_metadata = task.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for record in (task, metadata):
        if not isinstance(record, dict):
            continue
        for key in ("subject", "title", "description", "goal", "scope", "steps", "acceptance"):
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
    if phase in {"verification", "validation", "qa", "test"} and not bool(metadata.get("qa_rework_requested")):
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
        return False
    fresh_hints = (
        "repair",
        "fix",
        "bug",
        "regression",
        "update",
        "modify",
        "change",
        "smallest code change",
        "minimal",
        "测试失败",
        "修复",
        "更新",
        "修改",
        "最小变更",
    )
    return any(hint in token for hint in fresh_hints)


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
    return {
        "ok": ok,
        "reason": "declared_scope_present" if ok else "declared_scope_incomplete",
        "candidate_paths": _dedupe_preserve_order([*existing, *missing])[:40],
        "existing_paths": existing[:40],
        "missing_paths": missing[:40],
        "coverage": round(coverage, 3),
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
