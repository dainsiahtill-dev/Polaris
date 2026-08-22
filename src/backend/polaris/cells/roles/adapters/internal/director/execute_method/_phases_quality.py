"""Quality-repair loop phases for Director execute."""

from __future__ import annotations

import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from typing import Any

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)

from ..helpers import (
    has_successful_write_tool,
)
from ._claim import (
    _commit_deferred_materialization_quality_results,
    _execution_attempt_identity_from_context,
)
from ._helpers import (
    _QUALITY_REPAIR_STAGNATION_LIMIT,
    MaterializationState,
    _annotate_quality_repair_progress,
    _artifact_quality_error_signature,
    _build_post_execution_repair_convergence_verifier,
    _quality_repair_progress_evidence,
    _run_materialization_quality_public_boundary,
)
from ._phases_materialization import (
    _mark_quality_repair_summary_revalidated,
)

logger = logging.getLogger(__name__)


async def _phase_quality_repair_loop(
    adapter: Any,
    *,
    adapter_workspace: str,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> tuple[MaterializationState, list[str], dict[str, Any] | None, bool]:
    """Progress-aware deterministic + LLM quality-repair ladder (Block B).

    Runs the declared-target contract repair, then a progress-budgeted repair
    loop that interleaves deterministic materialization-quality repairs with an
    LLM repair retry, recomputing the artifact-quality error set after each
    write attempt. Returns the updated state, the residual artifact-quality
    errors, the latest quality-repair summary, and the (possibly updated)
    write-tool evidence flag. ``quality_repair_attempts`` is appended in place.
    """
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    _adapter_workspace = adapter_workspace

    deterministic_contract_tool_results, deterministic_contract_summary = run_declared_target_contract_repairs(
        adapter,
        task=task,
        task_id=target_task_id,
    )
    if deterministic_contract_tool_results:
        tool_results.extend(deterministic_contract_tool_results)
        quality_repair_summary = deterministic_contract_summary
        quality_repair_attempts.append(deterministic_contract_summary)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
        all_affected_files = _merge_successful_write_paths(
            all_affected_files,
            _extract_successful_write_paths(deterministic_contract_tool_results),
        )
        write_tool_evidence = has_successful_write_tool(tool_results)

    artifact_quality_errors = _collect_materialization_quality_errors(
        adapter,
        task=task,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        workspace_name=workspace_name,
        context=context,
        task_boundary=True,
    )
    step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
        adapter,
        context,
        task_id=target_task_id,
        task=task,
        workspace_name=workspace_name,
    )
    artifact_quality_errors += step_verify_errors
    tool_results.extend(step_verify_tool_results)
    # Live factory-bench L1-01 (2026-06-17, after the symbol-coherence fix):
    # py_compile + scan_workspace_artifact_quality pass for a calculator.py
    # whose __main__ block raises at call time. The deterministic ladder
    # must actually run the code to surface this kind of failure.
    artifact_quality_errors += run_python_static_smoke(
        adapter,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
    )
    runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
        adapter,
        task_id=target_task_id,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        context=context,
    )
    artifact_quality_errors += runtime_smoke_errors
    tool_results.extend(runtime_smoke_tool_results)
    artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
        artifact_quality_errors,
        _adapter_workspace,
    )
    # Each LLM attempt must prove a responsible workspace mutation and net
    # verifier improvement.  A changed diagnostic signature alone is not
    # progress: r14 changed one TypeScript error into multiple syntax/name
    # errors, yet the old predicate renewed the loop until the hard cap.
    stagnant_attempts = 0
    seen_error_signatures: set[tuple[str, ...]] = {
        _artifact_quality_error_signature(artifact_quality_errors)
    }
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        if not artifact_quality_errors:
            break
        deterministic_before_files = dict(current_files)
        deterministic_before_errors = list(artifact_quality_errors)
        deterministic_before_missing_count = len(_missing_declared_target_files(task, _adapter_workspace))
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _run_materialization_quality_public_boundary(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=artifact_quality_errors,
                execution_attempt=_execution_attempt_identity_from_context(context),
                convergence_verifier=_build_post_execution_repair_convergence_verifier(
                    adapter,
                    task_id=target_task_id,
                    all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    context=context,
                    artifact_quality_errors=artifact_quality_errors,
                ),
            )
        )
        if _materialization_plan_probe_requires_task_boundary_triage(deterministic_quality_summary):
            quality_repair_summary = _materialization_task_boundary_triage_summary(
                deterministic_quality_summary,
                repair_attempt=repair_attempt,
                artifact_quality_errors=artifact_quality_errors,
            )
            quality_repair_attempts.append(quality_repair_summary)
            break
        if deterministic_quality_tool_results:
            tool_results.extend(deterministic_quality_tool_results)
            # DEO: plan returns deferred_request only; commit physical writes via kernel followup.
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
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_quality_tool_results),
            )
            artifact_quality_errors = _collect_materialization_quality_errors(
                adapter,
                task=task,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                workspace_name=workspace_name,
                context=context,
                task_boundary=True,
            )
            step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
                adapter,
                context,
                task_id=target_task_id,
                task=task,
                workspace_name=workspace_name,
            )
            artifact_quality_errors += step_verify_errors
            tool_results.extend(step_verify_tool_results)
            artifact_quality_errors += run_python_static_smoke(
                adapter,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
                adapter,
                task_id=target_task_id,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                context=context,
            )
            artifact_quality_errors += runtime_smoke_errors
            tool_results.extend(runtime_smoke_tool_results)
            artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                artifact_quality_errors,
                _adapter_workspace,
            )
            _mark_quality_repair_summary_revalidated(deterministic_quality_summary, artifact_quality_errors)
            deterministic_progress = _quality_repair_progress_evidence(
                before_files=deterministic_before_files,
                after_files=current_files,
                before_errors=deterministic_before_errors,
                after_errors=artifact_quality_errors,
                before_missing_count=deterministic_before_missing_count,
                after_missing_count=len(_missing_declared_target_files(task, _adapter_workspace)),
                successful_write_paths=_extract_successful_write_paths(deterministic_quality_tool_results),
                previously_seen_error_signatures=seen_error_signatures,
            )
            seen_error_signatures.add(_artifact_quality_error_signature(artifact_quality_errors))
            _annotate_quality_repair_progress(
                deterministic_quality_summary,
                evidence=deterministic_progress,
                stagnant_attempts=0,
                stopped=False,
            )
            if not artifact_quality_errors:
                break
            # A deterministic repair can expose the next compiler/verifier
            # layer (for example adding tsconfig reveals a missing TypeScript
            # dependency).  When both the physical mutation and changed
            # diagnostic signature are proven, run the bounded deterministic
            # ladder again before spending a Provider call.  A no-op or equal
            # diagnostic stays on the LLM fallback/stagnation path below.
            if bool(deterministic_progress.get("workspace_mutation_evidenced")) and (
                _artifact_quality_error_signature(deterministic_before_errors)
                != _artifact_quality_error_signature(artifact_quality_errors)
            ):
                continue
        llm_before_files = dict(current_files)
        llm_before_errors = list(artifact_quality_errors)
        llm_before_missing_count = len(_missing_declared_target_files(task, _adapter_workspace))
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
            progress_evidence = _quality_repair_progress_evidence(
                before_files=llm_before_files,
                after_files=dict(current_files),
                before_errors=llm_before_errors,
                after_errors=artifact_quality_errors,
                before_missing_count=llm_before_missing_count,
                after_missing_count=len(_missing_declared_target_files(task, _adapter_workspace)),
                successful_write_paths=[],
                previously_seen_error_signatures=seen_error_signatures,
            )
            stagnant_attempts += 1
            stopped = stagnant_attempts >= _QUALITY_REPAIR_STAGNATION_LIMIT
            _annotate_quality_repair_progress(
                quality_repair_summary,
                evidence=progress_evidence,
                stagnant_attempts=stagnant_attempts,
                stopped=stopped,
            )
            if not stopped and artifact_quality_errors:
                continue
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
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                workspace_name=workspace_name,
                context=context,
                task_boundary=True,
            )
            step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
                adapter,
                context,
                task_id=target_task_id,
                task=task,
                workspace_name=workspace_name,
            )
            artifact_quality_errors += step_verify_errors
            tool_results.extend(step_verify_tool_results)
            artifact_quality_errors += run_python_static_smoke(
                adapter,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
                adapter,
                task_id=target_task_id,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                context=context,
            )
            artifact_quality_errors += runtime_smoke_errors
            tool_results.extend(runtime_smoke_tool_results)
            artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                artifact_quality_errors,
                _adapter_workspace,
            )
            _mark_quality_repair_summary_revalidated(quality_repair_summary, artifact_quality_errors)
            progress_evidence = _quality_repair_progress_evidence(
                before_files=llm_before_files,
                after_files=current_files,
                before_errors=llm_before_errors,
                after_errors=artifact_quality_errors,
                before_missing_count=llm_before_missing_count,
                after_missing_count=len(_missing_declared_target_files(task, _adapter_workspace)),
                successful_write_paths=_extract_successful_write_paths(repair_tool_results),
                previously_seen_error_signatures=seen_error_signatures,
            )
            seen_error_signatures.add(_artifact_quality_error_signature(artifact_quality_errors))
            if bool(progress_evidence.get("effective_progress")):
                stagnant_attempts = 0
            else:
                stagnant_attempts += 1
            stopped = bool(artifact_quality_errors) and stagnant_attempts >= _QUALITY_REPAIR_STAGNATION_LIMIT
            _annotate_quality_repair_progress(
                quality_repair_summary,
                evidence=progress_evidence,
                stagnant_attempts=stagnant_attempts,
                stopped=stopped,
            )
            if stopped:
                break
            if artifact_quality_errors:
                deterministic_quality_tool_results, deterministic_quality_summary = (
                    _run_materialization_quality_public_boundary(
                        adapter,
                        task=task,
                        task_id=target_task_id,
                        artifact_quality_errors=artifact_quality_errors,
                        execution_attempt=_execution_attempt_identity_from_context(context),
                        convergence_verifier=_build_post_execution_repair_convergence_verifier(
                            adapter,
                            task_id=target_task_id,
                            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                            context=context,
                            artifact_quality_errors=artifact_quality_errors,
                        ),
                    )
                )
                if _materialization_plan_probe_requires_task_boundary_triage(deterministic_quality_summary):
                    quality_repair_summary = _materialization_task_boundary_triage_summary(
                        deterministic_quality_summary,
                        repair_attempt=repair_attempt,
                        artifact_quality_errors=artifact_quality_errors,
                    )
                    quality_repair_attempts.append(quality_repair_summary)
                    break
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
                    all_affected_files = _merge_successful_write_paths(
                        all_affected_files,
                        _extract_successful_write_paths(deterministic_quality_tool_results),
                    )
                    artifact_quality_errors = _collect_materialization_quality_errors(
                        adapter,
                        task=task,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                        workspace_name=workspace_name,
                        context=context,
                        task_boundary=True,
                    )
                    step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
                        adapter,
                        context,
                        task_id=target_task_id,
                        task=task,
                        workspace_name=workspace_name,
                    )
                    artifact_quality_errors += step_verify_errors
                    tool_results.extend(step_verify_tool_results)
                    artifact_quality_errors += run_python_static_smoke(
                        adapter,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    )
                    runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
                        adapter,
                        task_id=target_task_id,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                        context=context,
                    )
                    artifact_quality_errors += runtime_smoke_errors
                    tool_results.extend(runtime_smoke_tool_results)
                    artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                        artifact_quality_errors,
                        _adapter_workspace,
                    )
                    _mark_quality_repair_summary_revalidated(deterministic_quality_summary, artifact_quality_errors)
                    if not artifact_quality_errors:
                        break

    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        artifact_quality_errors,
        quality_repair_summary,
        write_tool_evidence,
    )


def _materialization_task_boundary_triage_summary(
    summary: dict[str, Any],
    *,
    repair_attempt: int,
    artifact_quality_errors: list[str],
) -> dict[str, Any]:
    plan_probe = summary.get("plan_probe_preaudit")
    plan_probe_payload = dict(plan_probe) if isinstance(plan_probe, dict) else {}
    raw_evidence = summary.get("interface_discrepancy_evidence")
    existing_evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
    source_tools = [
        str(item)
        for item in plan_probe_payload.get(
            "covered_unplannable_source_tools",
            existing_evidence.get("covered_unplannable_source_tools", []),
        )
        if str(item or "").strip()
    ]
    covered_count = int(plan_probe_payload.get("covered_unplannable_diagnostic_count") or len(artifact_quality_errors))
    coverage_gap_count = int(plan_probe_payload.get("coverage_gap_count") or 0)
    existing_director_retry_allowed = bool(
        existing_evidence.get("director_retry_allowed")
        or summary.get("task_boundary_interface_discrepancy_retry_authorized")
        or summary.get("task_boundary_director_continuation_allowed")
    )
    existing_metadata = existing_evidence.get("metadata")
    receipt_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    receipt_metadata.update(
        {
            "route": "task_boundary_quality_loop",
            "coverage_gap_count": coverage_gap_count,
            "repair_attempt": repair_attempt,
        }
    )
    receipt = DirectorInterfaceDiscrepancyReceiptV1.from_mapping(
        {
            **existing_evidence,
            "task_id": str(summary.get("task_id") or summary.get("target_task_id") or "materialization-task"),
            "source": existing_evidence.get("source") or "roles.adapters.execute_method.materialization_quality_loop",
            "plan_probe_status": plan_probe_payload.get("status") or existing_evidence.get("plan_probe_status"),
            "covered_unplannable_source_tools": source_tools,
            "diagnostics": existing_evidence.get("diagnostics")
            or [{"message": str(item)} for item in artifact_quality_errors[:20]],
            "recommended_owner": existing_evidence.get("recommended_owner") or "chief_engineer",
            "recommended_route": existing_evidence.get("recommended_route") or "pending_design_interface_contract",
            "llm_fallback_blocked": not existing_director_retry_allowed,
            "director_retry_allowed": existing_director_retry_allowed,
            "reason": "coverage_matched_but_unplannable",
            "metadata": receipt_metadata,
        }
    ).to_dict()
    receipt.update(
        {
            "route": "task_boundary_quality_loop",
            "coverage_gap_count": coverage_gap_count,
            "covered_unplannable_diagnostic_count": covered_count,
        }
    )
    return {
        **dict(summary or {}),
        "stage": "runtime_plan_probe_unplannable",
        "attempted": True,
        "attempt": repair_attempt,
        "success": False,
        "success_reason": "task_boundary_interface_discrepancy_required",
        "tool_results": 0,
        "write_tool_evidence": False,
        "llm_fallback_blocked": not existing_director_retry_allowed,
        "director_retry_allowed": existing_director_retry_allowed,
        "task_boundary_interface_discrepancy_retry_authorized": existing_director_retry_allowed,
        "residual_error_count": len(artifact_quality_errors),
        "interface_discrepancy_evidence": receipt,
    }


async def _phase_semantic_quality_repair_loop(
    adapter: Any,
    *,
    adapter_workspace: str,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Semantic-quality + missing-declared-target LLM repair loop (Block C).

    Runs ``validate_generated_output`` plus the missing-declared-target check,
    and while either fails drives an LLM repair retry (hard-capped), recomputing
    the artifact-quality error set after each write. Returns the updated state,
    the residual semantic-quality error (or ``None``), the latest repair summary,
    and the list of per-attempt repair summaries.
    """
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    _adapter_workspace = adapter_workspace

    semantic_quality_repair_summary: dict[str, Any] | None = None
    semantic_quality_repair_attempts: list[dict[str, Any]] = []
    semantic_quality_error = adapter._execution.validate_generated_output(task, all_affected_files)
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        missing_declared_targets = _missing_declared_target_files(task, _adapter_workspace)
        if not semantic_quality_error and not missing_declared_targets:
            break
        semantic_repair_errors: list[str] = []
        if semantic_quality_error:
            semantic_repair_errors.append(semantic_quality_error)
        semantic_repair_errors.extend(
            f"Artifact quality scan failed: declared target file missing '{path}'" for path in missing_declared_targets
        )
        if not semantic_repair_errors:
            break
        repair_tool_results, semantic_quality_repair_summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            original_message=message,
            llm_call_timeout=llm_call_timeout,
            artifact_quality_errors=semantic_repair_errors,
            changed_files=all_affected_files,
            repair_attempt=repair_attempt,
        )
        semantic_quality_repair_attempts.append(semantic_quality_repair_summary)
        if not repair_tool_results:
            break
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
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
            adapter,
            context,
            task_id=target_task_id,
            task=task,
            workspace_name=workspace_name,
        )
        artifact_quality_errors += step_verify_errors
        tool_results.extend(step_verify_tool_results)
        artifact_quality_errors += run_python_static_smoke(
            adapter,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        )
        runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
            adapter,
            task_id=target_task_id,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            context=context,
        )
        artifact_quality_errors += runtime_smoke_errors
        tool_results.extend(runtime_smoke_tool_results)
        artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
            artifact_quality_errors,
            str(getattr(adapter, "workspace", "") or ""),
        )
        if artifact_quality_errors:
            semantic_quality_error = "Director output quality gate failed after semantic repair: " + "; ".join(
                artifact_quality_errors[:6]
            )
            break
        semantic_quality_error = adapter._execution.validate_generated_output(task, all_affected_files)

    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        semantic_quality_error,
        semantic_quality_repair_summary,
        semantic_quality_repair_attempts,
    )


from ..artifact_quality_diagnostics import (  # noqa: E402
    _filter_satisfied_declared_target_missing_errors as _filter_satisfied_declared_target_missing_errors,
)
from ..execute_method_repair_bridge import (  # noqa: E402
    run_declared_target_contract_repairs as run_declared_target_contract_repairs,
    run_python_runtime_smoke as run_python_runtime_smoke,
    run_python_static_smoke as run_python_static_smoke,
)
from ..quality_gate import (  # noqa: E402
    _QUALITY_REPAIR_ATTEMPT_HARD_CAP as _QUALITY_REPAIR_ATTEMPT_HARD_CAP,
    _collect_materialization_quality_errors as _collect_materialization_quality_errors,
    _collect_step_verify_errors as _collect_step_verify_errors,
    _collect_workspace_code_diff as _collect_workspace_code_diff,
    _extract_successful_write_paths as _extract_successful_write_paths,
    _materialization_plan_probe_requires_task_boundary_triage as _materialization_plan_probe_requires_task_boundary_triage,
    _materialization_quality_scan_paths as _materialization_quality_scan_paths,
    _merge_successful_write_paths as _merge_successful_write_paths,
    _missing_declared_target_files as _missing_declared_target_files,
    _run_materialization_quality_repair_retry as _run_materialization_quality_repair_retry,
)
