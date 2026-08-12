"""Standard LLM execution flow orchestration for Director execute."""

from __future__ import annotations

import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
)
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)

from ..helpers import (
    has_successful_write_tool,
)
from ._helpers import (
    MaterializationState,
    _attach_current_task_project_receipt_evidence,
    _pin_materialize_context_delivery_mode,
    _pin_materialize_delivery_mode,
)
from ._phases_failure import (
    _attach_director_file_event_bus,
    _cross_artifact_llm_escalation_enabled,
    _phase_cross_artifact_unplannable_llm_escalation,
    _phase_existing_scope_verified,
    _phase_missing_write_receipt,
    _phase_no_materialized_changes,
    _phase_quality_failed,
    _phase_semantic_quality_failed,
)
from ._phases_materialization import (
    _phase_deterministic_cleanup,
    _phase_direct_fallback,
    _phase_empty_write_retry,
    _phase_existing_scope_preflight,
    _phase_finalize_materialization,
    _phase_first_llm_call,
    _phase_no_write_materialization_retry,
    _phase_pre_materialization_quality,
    _phase_pre_materialization_target_repair,
    _phase_python_unittest_repair,
    _phase_typescript_reexport_repair,
)
from ._phases_quality import (
    _phase_quality_repair_loop,
    _phase_semantic_quality_repair_loop,
)

logger = logging.getLogger(__name__)


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
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
) -> dict[str, Any]:
    """执行标准 LLM 流程"""
    await _attach_director_file_event_bus(adapter)
    message = adapter._build_director_message(task, context=context)
    requires_fresh_materialization = _task_requires_fresh_materialization(task)
    context = _pin_materialize_context_delivery_mode(context, requires_fresh_materialization)
    message = _pin_materialize_delivery_mode(message, requires_fresh_materialization)
    workspace_name = Path(str(getattr(adapter, "workspace", "") or "")).resolve().name
    direct_fallback_summary: dict[str, Any] | None = None
    empty_write_content_retry_summary: dict[str, Any] | None = None
    no_write_materialization_retry_summary: dict[str, Any] | None = None
    all_affected_files: list[str] = []
    primary_llm_summary: dict[str, Any] | None = None
    quality_repair_summary: dict[str, Any] | None = None
    quality_repair_attempts: list[dict[str, Any]] = []
    state = MaterializationState(
        current_files=baseline_files,
        new_files=[],
        modified_files=[],
        all_affected_files=[],
        tool_results=[],
    )
    state = _phase_deterministic_cleanup(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        workspace_name=workspace_name,
        state=state,
    )

    preflight_result = _phase_existing_scope_preflight(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        task_claim_session_id=task_claim_session_id,
        decision_signals=decision_signals,
        requires_fresh_materialization=requires_fresh_materialization,
        workspace_name=workspace_name,
        state=state,
    )
    if preflight_result is not None:
        return preflight_result

    state, primary_llm_summary = await _phase_first_llm_call(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        decision_signals=decision_signals,
        workspace_name=workspace_name,
        state=state,
    )

    state, no_write_materialization_retry_summary = await _phase_no_write_materialization_retry(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        primary_llm_summary=primary_llm_summary,
        workspace_name=workspace_name,
        state=state,
    )

    state, direct_fallback_summary = _phase_direct_fallback(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        workspace_name=workspace_name,
        state=state,
    )

    state, empty_write_content_retry_summary = await _phase_empty_write_retry(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        workspace_name=workspace_name,
        state=state,
    )

    state = _phase_typescript_reexport_repair(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        workspace_name=workspace_name,
        state=state,
    )

    state = _phase_python_unittest_repair(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        workspace_name=workspace_name,
        state=state,
    )

    state, quality_repair_summary = _phase_pre_materialization_target_repair(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        workspace_name=workspace_name,
        state=state,
    )

    existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=state.current_files,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        workspace_name=workspace_name,
    )
    existing_contract_evidence, project_artifact_receipt_evidence = _attach_current_task_project_receipt_evidence(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        existing_contract_evidence=existing_contract_evidence,
    )
    write_tool_evidence = has_successful_write_tool(state.tool_results)
    can_accept_existing_scope = bool(existing_contract_evidence.get("ok")) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=write_tool_evidence,
        project_artifact_receipt_evidence=project_artifact_receipt_evidence,
    )

    (
        state,
        existing_contract_evidence,
        can_accept_existing_scope,
        write_tool_evidence,
        quality_repair_summary,
    ) = await _phase_pre_materialization_quality(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        baseline_files=baseline_files,
        existing_contract_evidence=existing_contract_evidence,
        can_accept_existing_scope=can_accept_existing_scope,
        write_tool_evidence=write_tool_evidence,
        requires_fresh_materialization=requires_fresh_materialization,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        workspace_name=workspace_name,
        state=state,
    )
    all_affected_files = state.all_affected_files

    if not all_affected_files and not can_accept_existing_scope:
        acceptance_verify_satisfied, acceptance_verify_evidence = _evaluate_acceptance_verify_exists(
            task=task,
            workspace_full=str(getattr(adapter, "workspace", "") or ""),
            write_tool_evidence=write_tool_evidence,
        )
        if acceptance_verify_satisfied:
            # Acceptance exemption: the contract's own machine checks pass and
            # the Director has successful write receipts — route through the
            # verified-existing-scope success path instead of a pseudo-failure.
            can_accept_existing_scope = True
            existing_contract_evidence = dict(existing_contract_evidence)
            existing_contract_evidence["acceptance_verify_exists"] = acceptance_verify_evidence

    no_materialized_result = _phase_no_materialized_changes(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        baseline_files=baseline_files,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        can_accept_existing_scope=can_accept_existing_scope,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        existing_contract_evidence=existing_contract_evidence,
        primary_llm_summary=primary_llm_summary,
        requires_fresh_materialization=requires_fresh_materialization,
        task_claim_session_id=task_claim_session_id,
        workspace_name=workspace_name,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if no_materialized_result is not None:
        return no_materialized_result

    existing_verified_result = _phase_existing_scope_verified(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        can_accept_existing_scope=can_accept_existing_scope,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        existing_contract_evidence=existing_contract_evidence,
        primary_llm_summary=primary_llm_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if existing_verified_result is not None:
        return existing_verified_result

    materialization_mode = (
        "write_tool_and_workspace_diff" if write_tool_evidence else "workspace_diff_without_write_tool"
    )

    missing_receipt_result = _phase_missing_write_receipt(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if missing_receipt_result is not None:
        return missing_receipt_result

    _adapter_workspace = str(getattr(adapter, "workspace", "") or "")

    (
        state,
        artifact_quality_errors,
        quality_repair_summary,
        write_tool_evidence,
    ) = await _phase_quality_repair_loop(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        adapter_workspace=_adapter_workspace,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        workspace_name=workspace_name,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )

    if _cross_artifact_llm_escalation_enabled():
        state, artifact_quality_errors = await _phase_cross_artifact_unplannable_llm_escalation(
            adapter,
            adapter_workspace=_adapter_workspace,
            baseline_files=baseline_files,
            context=context,
            llm_call_timeout=llm_call_timeout,
            message=message,
            run_id=run_id,
            target_task_id=target_task_id,
            task=task,
            workspace_name=workspace_name,
            artifact_quality_errors=artifact_quality_errors,
            quality_repair_attempts=quality_repair_attempts,
            state=state,
        )

    quality_failed_result = _phase_quality_failed(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        artifact_quality_errors=artifact_quality_errors,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if quality_failed_result is not None:
        return quality_failed_result

    (
        state,
        semantic_quality_error,
        semantic_quality_repair_summary,
        semantic_quality_repair_attempts,
    ) = await _phase_semantic_quality_repair_loop(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        adapter_workspace=_adapter_workspace,
        workspace_name=workspace_name,
        state=state,
    )

    semantic_failed_result = _phase_semantic_quality_failed(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        semantic_quality_error=semantic_quality_error,
        semantic_quality_repair_attempts=semantic_quality_repair_attempts,
        semantic_quality_repair_summary=semantic_quality_repair_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if semantic_failed_result is not None:
        return semantic_failed_result

    return _phase_finalize_materialization(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        semantic_quality_repair_attempts=semantic_quality_repair_attempts,
        semantic_quality_repair_summary=semantic_quality_repair_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )


from ..quality_gate import (  # noqa: E402
    _build_existing_workspace_task_evidence as _build_existing_workspace_task_evidence,
    _can_accept_existing_workspace_scope as _can_accept_existing_workspace_scope,
    _evaluate_acceptance_verify_exists as _evaluate_acceptance_verify_exists,
    _task_requires_fresh_materialization as _task_requires_fresh_materialization,
)
