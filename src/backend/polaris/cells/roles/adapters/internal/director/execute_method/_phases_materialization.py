"""Materialization and pre-quality phases for Director execute."""

from __future__ import annotations

import asyncio
import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from typing import Any

from polaris.cells.director.runtime.public.service import (
    AttachDirectorRepairRevalidationEvidenceV1,
    project_director_repair_revalidation_evidence,
)
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
from ..post_execution_repair_bridge import run_post_execution_language_repairs
from ._claim import (
    _append_declared_scope_preflight_task_boundary,
    _append_receipt_bound_preflight_task_boundary,
    _attach_dependency_artifact_receipt_evidence,
    _commit_deferred_materialization_quality_results,
    _emit_director_adapter_cognitive_receipt,
    _execution_attempt_identity_from_context,
    _extract_resident_agi_repair_advisory_overlay,
    _finalize_claimed_execution,
    _project_preflight_execution_capability,
    _record_project_artifacts_before_settlement,
    _task_completion_projection_from_context,
    _task_runtime_finalization_failed_result,
    _with_task_runtime_finalize_evidence,
)
from ._helpers import (
    MaterializationState,
    _adapter_materialized_file_paths,
    _attach_current_task_project_receipt_evidence,
    _build_post_execution_repair_convergence_verifier,
    _deterministic_repair_profile_summary_from_tool_results,
    _empty_write_content_retry_needed,
    _invoke_role_dialogue_with_transient_provider_retry,
    _no_write_materialization_retry_needed,
    _run_empty_write_content_materialization_retry,
    _run_materialization_quality_public_boundary,
    _run_no_write_materialization_retry,
)

logger = logging.getLogger(__name__)


def _phase_finalize_materialization(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    run_id: str,
    semantic_quality_repair_attempts: list[dict[str, Any]],
    semantic_quality_repair_summary: dict[str, Any] | None,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
    no_write_materialization_retry_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialized-paths reconcile + completion-metadata + finalize (Block D).

    Reconciles reported changed files against what actually materialized on
    disk, returning the ``no_physical_files`` failure dict when nothing
    materialized, otherwise assembling the completion metadata, emitting the
    cognitive receipt, finalizing the board claim, and returning the success
    result dict. This is the success/failure epilogue of the standard flow.
    """
    _current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    deterministic_repair_profile_summary = _deterministic_repair_profile_summary_from_tool_results(tool_results)
    reported_affected_files = list(all_affected_files)
    all_affected_files, unmaterialized_affected_files = _adapter_materialized_file_paths(
        adapter,
        reported_affected_files,
    )
    new_files = [path for path in new_files if path in all_affected_files]
    modified_files = [path for path in modified_files if path in all_affected_files]
    if unmaterialized_affected_files:
        decision_signals.append(
            {
                "code": "director.materialization.unmaterialized_reported_files",
                "severity": "error",
                "detail": "Director reported changed_files that did not materialize on disk",
                "reported_changed_files": reported_affected_files,
                "unmaterialized_reported_changed_files": unmaterialized_affected_files,
            }
        )
    if not all_affected_files:
        error = "Director reported no physically materialized changed files"
        failure_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "deterministic_repair_profiles": deterministic_repair_profile_summary,
                "reported_changed_files": reported_affected_files,
                "unmaterialized_reported_changed_files": unmaterialized_affected_files,
                "materialization_mode": materialization_mode,
            }
        }
        if no_write_materialization_retry_summary is not None:
            failure_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=failure_metadata,
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
            "error_code": "director.materialization.no_physical_files",
            "failure_stage": "director_materialization",
            "root_cause_hint": error,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "deterministic_repair_profiles": deterministic_repair_profile_summary,
            "changed_files": [],
            "new_files": [],
            "modified_files": [],
            "reported_changed_files": reported_affected_files,
            "unmaterialized_reported_changed_files": unmaterialized_affected_files,
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )

    # 返回结果
    completion_metadata: dict[str, Any] = {
        "adapter_result": {
            "tools_executed": len(tool_results),
            "write_tool_evidence": write_tool_evidence,
            "qa_passed": None,
            "qa_required_for_final_verdict": True,
            "new_files": new_files[:20],
            "new_file_count": len(new_files),
            "modified_files": modified_files[:20],
            "modified_file_count": len(modified_files),
            "reported_changed_files": reported_affected_files[:40],
            "unmaterialized_reported_changed_files": unmaterialized_affected_files[:40],
            "materialization_mode": materialization_mode,
            "deterministic_repair_profiles": deterministic_repair_profile_summary,
        }
    }
    if primary_llm_summary is not None:
        completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
    if direct_fallback_summary is not None:
        completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
    if no_write_materialization_retry_summary is not None:
        completion_metadata["adapter_result"]["no_write_materialization_retry"] = no_write_materialization_retry_summary
    if empty_write_content_retry_summary is not None:
        completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
    if quality_repair_summary is not None:
        completion_metadata["adapter_result"]["quality_repair"] = quality_repair_summary
    if quality_repair_attempts:
        completion_metadata["adapter_result"]["quality_repair_attempts"] = quality_repair_attempts
    if semantic_quality_repair_summary is not None:
        completion_metadata["adapter_result"]["semantic_quality_repair"] = semantic_quality_repair_summary
    if semantic_quality_repair_attempts:
        completion_metadata["adapter_result"]["semantic_quality_repair_attempts"] = semantic_quality_repair_attempts
    _attach_dependency_artifact_receipt_evidence(
        completion_metadata["adapter_result"],
        tool_results=tool_results,
        primary_llm_summary=primary_llm_summary if isinstance(primary_llm_summary, dict) else None,
    )
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
            "no_write_materialization_retry": no_write_materialization_retry_summary or {},
            "quality_repair": quality_repair_summary or {},
            "quality_repair_attempts": quality_repair_attempts,
            "deterministic_repair_profiles": deterministic_repair_profile_summary,
        },
        export_handoff=True,
    )
    completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt

    if board_claim_applied:
        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id=target_task_id,
            outcome="completed",
            authority=task_execution_attempt_authority,
            result_summary=f"changed_files={len(all_affected_files)}; tools_executed={len(tool_results)}",
            metadata=completion_metadata,
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
        "deterministic_repair_profiles": deterministic_repair_profile_summary,
        "changed_files": all_affected_files,
        "new_files": new_files,
        "modified_files": modified_files,
        "cognitive_runtime_receipt": cognitive_receipt,
        "decision_signals": decision_signals,
        "qa_required_for_final_verdict": True,
        "artifacts": [],
        "materialization_mode": materialization_mode,
    }


def _phase_deterministic_cleanup(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> MaterializationState:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    deterministic_tool_results: list[dict[str, Any]] = []
    deterministic_tool_results.extend(
        run_scaffold_marker_cleanup(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    deterministic_tool_results.extend(
        run_node_test_script_contract_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    deterministic_tool_results.extend(
        run_patch_residue_cleanup(
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
    return MaterializationState.from_locals(
        current_files,
        new_files,
        modified_files,
        all_affected_files,
        tool_results,
    )


def _mark_quality_repair_summary_revalidated(
    summary: dict[str, Any] | None, artifact_quality_errors: list[str]
) -> None:
    if not isinstance(summary, dict):
        return
    revalidated_summary = _project_repair_revalidation_summary(
        summary,
        artifact_quality_errors=artifact_quality_errors,
        stage="director_materialization_quality",
    )
    summary.clear()
    summary.update(revalidated_summary)
    _mark_nested_repair_kernel_summaries_revalidated(summary, artifact_quality_errors)
    residual_error_count = len(artifact_quality_errors)
    summary["revalidated"] = True
    summary["residual_error_count"] = residual_error_count
    summary["success"] = residual_error_count == 0


def _project_repair_revalidation_summary(
    summary: dict[str, Any],
    *,
    artifact_quality_errors: list[str],
    stage: str,
) -> dict[str, Any]:
    return dict(
        project_director_repair_revalidation_evidence(
            AttachDirectorRepairRevalidationEvidenceV1(
                summary=summary,
                residual_artifact_quality_errors=tuple(artifact_quality_errors),
                command=("materialization_quality_revalidation",),
                metadata={"stage": stage},
            )
        ).summary
    )


def _mark_nested_repair_kernel_summaries_revalidated(
    summary: dict[str, Any],
    artifact_quality_errors: list[str],
) -> None:
    """Attach the same post-check evidence to nested repair-kernel projections."""

    nested_kernel = summary.get("post_execution_repair_kernel")
    if isinstance(nested_kernel, dict):
        summary["post_execution_repair_kernel"] = _project_repair_revalidation_summary(
            nested_kernel,
            artifact_quality_errors=artifact_quality_errors,
            stage="director_post_execution_language_repairs",
        )

    repair_attempts = summary.get("repair_attempts")
    if not isinstance(repair_attempts, list):
        return
    for attempt in repair_attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_kernel = attempt.get("repair_kernel")
        if not isinstance(attempt_kernel, dict):
            continue
        attempt["repair_kernel"] = _project_repair_revalidation_summary(
            attempt_kernel,
            artifact_quality_errors=artifact_quality_errors,
            stage=str(attempt.get("stage") or "director_materialization_quality_attempt"),
        )


def _phase_existing_scope_preflight(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    requires_fresh_materialization: bool,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    workspace_name: str,
    state: MaterializationState,
) -> dict[str, Any] | None:
    current_files, all_affected_files = (
        state.current_files,
        state.all_affected_files,
    )
    preflight_existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=current_files,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        workspace_name=workspace_name,
    )
    preflight_existing_contract_evidence, project_artifact_receipt_evidence = (
        _attach_current_task_project_receipt_evidence(
            adapter,
            task=task,
            target_task_id=target_task_id,
            context=context,
            existing_contract_evidence=preflight_existing_contract_evidence,
        )
    )
    preflight_quality_errors: list[str] = []
    if preflight_existing_contract_evidence.get("ok"):
        preflight_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=list(preflight_existing_contract_evidence.get("existing_paths") or []),
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        if preflight_quality_errors:
            decision_signals.append(
                {
                    "code": "director.existing_workspace_scope_preflight_quality_failed",
                    "severity": "warning",
                    "detail": (
                        "Declared scope exists but failed materialization quality checks; "
                        "execution must continue through the authorized repair path."
                    ),
                    "error_count": len(preflight_quality_errors),
                    "errors": preflight_quality_errors[:20],
                }
            )
        elif requires_fresh_materialization and not project_artifact_receipt_evidence:
            # Live L1-10: settle repaired main_test.go (0.34→0.32) under the
            # factory-mat-settle task. TASK-3 rematerialize then saw a stale
            # owner receipt and failed director_no_materialized_changes even
            # though go test was already green. Record current owned bytes
            # only after the owned quality scan is clean.
            projection = _task_completion_projection_from_context(
                context,
                target_task_id=target_task_id,
            )
            if isinstance(projection, dict):
                contract_task_id = str(projection.get("task_id") or "").strip() or target_task_id
                try:
                    _project_preflight_execution_capability(
                        adapter,
                        context=context,
                        target_task_id=target_task_id,
                        contract_task_id=contract_task_id,
                        run_id=run_id,
                    )
                    _record_project_artifacts_before_settlement(
                        adapter,
                        contract_task_id=contract_task_id,
                        task_completion_projection=projection,
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.warning(
                        "Existing-scope receipt refresh failed for task=%s: %s",
                        target_task_id,
                        exc,
                    )
                else:
                    preflight_existing_contract_evidence, project_artifact_receipt_evidence = (
                        _attach_current_task_project_receipt_evidence(
                            adapter,
                            task=task,
                            target_task_id=target_task_id,
                            context=context,
                            existing_contract_evidence=preflight_existing_contract_evidence,
                        )
                    )
    preflight_can_accept_existing_scope = bool(
        preflight_existing_contract_evidence.get("ok")
    ) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=False,
        project_artifact_receipt_evidence=project_artifact_receipt_evidence,
    )
    if (
        not all_affected_files
        and _director_existing_scope_preflight_enabled(context)
        and preflight_can_accept_existing_scope
        and not preflight_quality_errors
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
        if board_claim_applied:
            preflight_projection = _task_completion_projection_from_context(
                context,
                target_task_id=target_task_id,
            )
            contract_task_id = target_task_id
            if isinstance(preflight_projection, dict):
                contract_task_id = str(preflight_projection.get("task_id") or "").strip() or target_task_id
            _project_preflight_execution_capability(
                adapter,
                context=context,
                target_task_id=target_task_id,
                contract_task_id=contract_task_id,
                run_id=run_id,
            )
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                authority=task_execution_attempt_authority,
                result_summary=(
                    "preflight_verified_existing_workspace_scope="
                    f"{len(preflight_existing_contract_evidence.get('existing_paths') or [])}"
                ),
                metadata=completion_metadata,
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
                    materialization_mode="preflight_verified_existing_workspace_scope",
                )
            # Live L2-12 TASK-3-foundation: rematerialize preflight completed
            # TaskRuntime with existing receipt-bound artifacts, but the
            # append was gated on requires_fresh_materialization. Chinese
            # implementation splits often compute requires_fresh=False, so
            # Factory then fail-closed on task_boundary_verdict_missing
            # after every owner row already completed. Provider-less
            # preflight must still seal the receipt-bound TaskBoundary.
            try:
                if project_artifact_receipt_evidence:
                    task_boundary_verdict = _append_receipt_bound_preflight_task_boundary(
                        adapter,
                        context=context,
                        target_task_id=target_task_id,
                        run_id=run_id,
                        finalize_result=finalize_result,
                        receipt_evidence=preflight_existing_contract_evidence.get(
                            "project_artifact_receipt_evidence",
                            {},
                        ),
                    )
                else:
                    task_boundary_verdict = _append_declared_scope_preflight_task_boundary(
                        adapter,
                        context=context,
                        target_task_id=target_task_id,
                        run_id=run_id,
                        finalize_result=finalize_result,
                        existing_paths=list(preflight_existing_contract_evidence.get("existing_paths") or []),
                    )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                return {
                    "success": False,
                    "task_id": target_task_id,
                    "error": "director_task_boundary_receipt_projection_failed",
                    "error_code": "director.task_boundary_receipt_projection_failed",
                    "failure_class": "TASK_BOUNDARY_FAILED",
                    "root_cause_hint": str(exc),
                    "retry_scope": "same_director_task_only",
                    "pm_ce_restart_allowed": False,
                    "decision_signals": decision_signals,
                }
            completion_metadata["adapter_result"]["task_boundary_verdict"] = task_boundary_verdict
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
    return None


async def _phase_first_llm_call(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    llm_call_timeout: float,
    message: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    primary_llm_summary: dict[str, Any] | None = None
    if not all_affected_files:
        if _director_direct_text_patch_only_enabled(context):
            result = {
                "content": "",
                "success": False,
                "error": "director_direct_text_patch_only",
                "raw_response": {"direct_text_patch_only": True},
            }
        else:
            try:
                result = await _invoke_role_dialogue_with_transient_provider_retry(
                    adapter,
                    message=message,
                    context=context,
                    timeout_seconds=llm_call_timeout,
                    stage_label="first_call",
                    target_task_id=target_task_id,
                )
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                if not _is_recoverable_no_write_mutation_contract_exception(exc):
                    raise
                error_text = str(exc)
                if not error_text.lower().startswith("transactionkernel execution failed"):
                    error_text = f"TransactionKernel execution failed: {error_text}"
                result = {
                    "content": "",
                    "success": False,
                    "error": error_text,
                    "raw_response": {
                        "recoverable_mutation_contract_exception": True,
                        "exception_type": type(exc).__name__,
                    },
                }
                decision_signals.append(
                    {
                        "code": "director.recoverable_no_write_mutation_contract_exception",
                        "severity": "warning",
                        "detail": str(exc),
                    }
                )
        primary_llm_summary = _summarize_llm_stage_result(result, stage="first_call")
        content = result.get("content", "")

        # 执行工具
        extracted_tool_results = adapter._execution.extract_kernel_tool_results(result)
        tool_results.extend(extracted_tool_results)
        if not extracted_tool_results or not has_successful_write_tool(extracted_tool_results):
            fallback_tool_results = await adapter._execution.execute_tools(
                content,
                target_task_id,
                adapter._update_task_progress,
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
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        primary_llm_summary,
    )


async def _phase_no_write_materialization_retry(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    primary_llm_summary: dict[str, Any] | None,
    requires_fresh_materialization: bool,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    no_write_retry_summary: dict[str, Any] | None = None
    if not all_affected_files and _no_write_materialization_retry_needed(
        primary_llm_summary=primary_llm_summary,
        task=task,
        tool_results=tool_results,
        workspace=str(getattr(adapter, "workspace", "") or ""),
        requires_fresh_materialization=requires_fresh_materialization,
    ):
        retry_tool_results, no_write_retry_summary = await _run_no_write_materialization_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            context=context,
            original_message=message,
            tool_results=tool_results,
            llm_call_timeout=llm_call_timeout,
        )
        if retry_tool_results:
            tool_results.extend(retry_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
        all_affected_files = _merge_successful_write_paths(
            all_affected_files,
            _extract_successful_write_paths(retry_tool_results),
        )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        no_write_retry_summary,
    )


def _phase_direct_fallback(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    direct_fallback_summary: dict[str, Any] | None = None
    if not all_affected_files:
        direct_timeout = adapter._execution.resolve_direct_fallback_timeout_seconds(context, llm_call_timeout)
        direct_content = ""
        direct_tool_results: list[dict[str, Any]] = []
        direct_fallback_summary = {
            "timeout_seconds": direct_timeout,
            "content_length": len(direct_content),
            "error": "",
            "skipped_reason": "runtime_provider_bypass_removed",
            "tool_results": len(direct_tool_results),
            "provider": "",
            "model": "",
            "success": False,
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
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        direct_fallback_summary,
    )


async def _phase_empty_write_retry(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    empty_write_content_retry_summary: dict[str, Any] | None = None
    if not all_affected_files and _empty_write_content_retry_needed(tool_results):
        (
            empty_retry_tool_results,
            empty_write_content_retry_summary,
        ) = await _run_empty_write_content_materialization_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            context=context,
            original_message=message,
            tool_results=tool_results,
            llm_call_timeout=llm_call_timeout,
        )
        if empty_retry_tool_results:
            tool_results.extend(empty_retry_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        empty_write_content_retry_summary,
    )


def _phase_typescript_reexport_repair(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> MaterializationState:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    if not all_affected_files:
        deterministic_tool_results = run_typescript_reexport_repair(
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
    return MaterializationState.from_locals(
        current_files,
        new_files,
        modified_files,
        all_affected_files,
        tool_results,
    )


def _phase_python_unittest_repair(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> MaterializationState:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    if not all_affected_files:
        deterministic_tool_results = run_python_unittest_missing_target_repair(
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
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_tool_results),
            )
    return MaterializationState.from_locals(
        current_files,
        new_files,
        modified_files,
        all_affected_files,
        tool_results,
    )


def _phase_pre_materialization_target_repair(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    quality_repair_summary: dict[str, Any] | None = None
    missing_declared_targets = _missing_declared_target_files(
        task,
        str(getattr(adapter, "workspace", "") or ""),
    )
    if (
        missing_declared_targets
        or not all_affected_files
        or (
            not has_successful_write_tool(tool_results)
            and _stage_summary_has_recoverable_no_write_mutation_contract_exception(primary_llm_summary)
        )
    ):
        deterministic_prematerialization_tool_results, deterministic_prematerialization_summary = (
            run_pre_materialization_declared_target_repairs(
                adapter,
                task=task,
                task_id=target_task_id,
                workspace_name=workspace_name,
            )
        )
        if deterministic_prematerialization_tool_results:
            tool_results.extend(deterministic_prematerialization_tool_results)
            quality_repair_summary = deterministic_prematerialization_summary
            quality_repair_attempts.append(deterministic_prematerialization_summary)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_prematerialization_tool_results),
            )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        quality_repair_summary,
    )


async def _phase_pre_materialization_quality(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    can_accept_existing_scope: bool,
    context: dict[str, Any],
    existing_contract_evidence: dict[str, Any],
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    requires_fresh_materialization: bool,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any], bool, bool, dict[str, Any] | None]:
    """Pre-materialization deterministic quality recompute (Block A).

    When the Director produced a write receipt but no in-scope diff yet, run the
    deterministic materialization-quality repairs once and recompute the
    existing-contract evidence / acceptance gate. Returns the updated state, the
    (possibly updated) existing-contract evidence, the can-accept-existing-scope
    and write-tool-evidence flags, and the latest quality-repair summary.
    ``quality_repair_attempts`` is appended in place.
    """
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    rematerialized_dirty_scope = bool(existing_contract_evidence.get("existing_paths")) and (
        existing_contract_evidence.get("reason") in {"declared_scope_quality_failed", "declared_scope_incomplete"}
        or bool(existing_contract_evidence.get("artifact_quality_errors"))
    )
    if (
        not all_affected_files
        and not can_accept_existing_scope
        and (write_tool_evidence or rematerialized_dirty_scope)
        and (requires_fresh_materialization or not bool(existing_contract_evidence.get("ok")))
    ):
        pre_materialization_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _run_materialization_quality_public_boundary(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=pre_materialization_quality_errors,
                execution_attempt=_execution_attempt_identity_from_context(context),
                convergence_verifier=_build_post_execution_repair_convergence_verifier(
                    adapter,
                    task_id=target_task_id,
                    all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    context=context,
                    artifact_quality_errors=pre_materialization_quality_errors,
                ),
            )
        )
        if deterministic_quality_tool_results:
            tool_results.extend(deterministic_quality_tool_results)
            committed_writes = await _commit_deferred_materialization_quality_results(
                adapter,
                context=context,
                tool_results=deterministic_quality_tool_results,
                task_id=target_task_id,
            )
            if committed_writes:
                tool_results.extend(committed_writes)
                deterministic_quality_tool_results = [
                    *deterministic_quality_tool_results,
                    *committed_writes,
                ]
            quality_repair_summary = deterministic_quality_summary
            quality_repair_attempts.append(deterministic_quality_summary)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            if all_affected_files:
                all_affected_files = _merge_successful_write_paths(
                    all_affected_files,
                    _extract_successful_write_paths(deterministic_quality_tool_results),
                )
            existing_contract_evidence = _build_existing_workspace_task_evidence(
                task=task,
                current_files=current_files,
                workspace_full=str(getattr(adapter, "workspace", "") or ""),
                workspace_name=workspace_name,
            )
            existing_contract_evidence, project_artifact_receipt_evidence = (
                _attach_current_task_project_receipt_evidence(
                    adapter,
                    task=task,
                    target_task_id=target_task_id,
                    context=context,
                    existing_contract_evidence=existing_contract_evidence,
                )
            )
            write_tool_evidence = has_successful_write_tool(tool_results)
            can_accept_existing_scope = bool(
                existing_contract_evidence.get("ok")
            ) and _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=requires_fresh_materialization,
                write_tool_evidence=write_tool_evidence,
                project_artifact_receipt_evidence=project_artifact_receipt_evidence,
            )
    # Post-execution language-specific repair pass: always run deterministic
    # repairs after Director finishes writing files, regardless of quality gate
    # outcome. This catches import/syntax/dedup/field issues that QA might not
    # detect.
    if write_tool_evidence:
        resident_agi_repair_advisory_overlay = _extract_resident_agi_repair_advisory_overlay(
            task=task,
            context=context,
        )
        convergence_verifier = _build_post_execution_repair_convergence_verifier(
            adapter,
            task_id=target_task_id,
            all_affected_files=all_affected_files,
            context=context,
            artifact_quality_errors=[],
        )
        post_execution_tool_results, post_execution_repair_summary = run_post_execution_language_repairs(
            adapter,
            task_id=target_task_id,
            resident_agi_repair_advisory_overlay=resident_agi_repair_advisory_overlay,
            convergence_verifier=convergence_verifier,
            execution_attempt=_execution_attempt_identity_from_context(context),
        )
        if post_execution_tool_results and post_execution_repair_summary is not None:
            tool_results.extend(post_execution_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            quality_repair_attempts.append(post_execution_repair_summary)
            quality_repair_summary = dict(quality_repair_summary or {})
            quality_repair_summary["post_execution_repair_kernel"] = post_execution_repair_summary["repair_kernel"]
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        existing_contract_evidence,
        can_accept_existing_scope,
        write_tool_evidence,
        quality_repair_summary,
    )


from ..execute_method_repair_bridge import (  # noqa: E402
    run_node_test_script_contract_repair as run_node_test_script_contract_repair,
    run_patch_residue_cleanup as run_patch_residue_cleanup,
    run_pre_materialization_declared_target_repairs as run_pre_materialization_declared_target_repairs,
    run_python_unittest_missing_target_repair as run_python_unittest_missing_target_repair,
    run_scaffold_marker_cleanup as run_scaffold_marker_cleanup,
    run_typescript_reexport_repair as run_typescript_reexport_repair,
)
from ..quality_gate import (  # noqa: E402
    _build_existing_workspace_task_evidence as _build_existing_workspace_task_evidence,
    _can_accept_existing_workspace_scope as _can_accept_existing_workspace_scope,
    _collect_materialization_quality_errors as _collect_materialization_quality_errors,
    _collect_workspace_code_diff as _collect_workspace_code_diff,
    _director_direct_text_patch_only_enabled as _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled as _director_existing_scope_preflight_enabled,
    _extract_successful_write_paths as _extract_successful_write_paths,
    _is_recoverable_no_write_mutation_contract_exception as _is_recoverable_no_write_mutation_contract_exception,
    _materialization_quality_scan_paths as _materialization_quality_scan_paths,
    _merge_successful_write_paths as _merge_successful_write_paths,
    _missing_declared_target_files as _missing_declared_target_files,
    _stage_summary_has_recoverable_no_write_mutation_contract_exception as _stage_summary_has_recoverable_no_write_mutation_contract_exception,
    _summarize_llm_stage_result as _summarize_llm_stage_result,
)
