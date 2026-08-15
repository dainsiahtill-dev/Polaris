"""Failure / no-materialization / quality-failed phases for Director execute."""

from __future__ import annotations

import contextlib
import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from collections.abc import Mapping
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    AppendToolCallLifecycleEventCommandV1,
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    append_tool_call_lifecycle_event,
    build_claimed_materialization_without_tool_lifecycle_receipt,
    is_failure_class,
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    summarize_tool_lifecycle_events,
    tool_call_lifecycle_receipts_from_metadata,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
)
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)

from ._claim import (
    _emit_director_adapter_cognitive_receipt,
    _finalize_claimed_execution,
    _task_completion_projection_from_context,
    _task_runtime_finalization_failed_result,
    _with_task_runtime_finalize_evidence,
)
from ._helpers import (
    MaterializationState,
    _diag_write_results_summary,
)

logger = logging.getLogger(__name__)


def _summary_field_matches_failure_class(value: Any, expected: FailureClassV1) -> bool:
    if is_failure_class(value, expected):
        return True
    raw = str(value or "").strip()
    if not raw:
        return False
    for separator in (":", ";", "\n"):
        candidate = raw.split(separator, 1)[0].strip()
        if candidate != raw and is_failure_class(candidate, expected):
            return True
    return False


def _tool_dispatch_dropped_failure_payload() -> dict[str, str]:
    return {
        "error": "tool_dispatch_dropped",
        "error_code": "tool_dispatch_dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "responsible_layer": "execution_control_plane",
        "materialization_mode": "tool_dispatch_dropped",
        "failure_stage": "director_tool_lifecycle",
        "root_cause_hint": "required_tool_without_dispatch_receipt",
        "detail": "Director role runtime reported required/native tool calls without dispatch/effect receipt.",
    }


def _primary_llm_summary_text(primary_llm_summary: dict[str, Any]) -> str:
    """Return compact, structured LLM failure text for deterministic matching."""

    metadata_raw = primary_llm_summary.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    parts: list[str] = []
    for source in (primary_llm_summary, metadata):
        for key in (
            "error",
            "error_code",
            "error_category",
            "last_transport_error",
            "retry_decision",
            "provider",
            "model",
        ):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)


def _primary_llm_provider_failure_payload(primary_llm_summary: dict[str, Any] | None) -> dict[str, str] | None:
    """Classify a failed primary role LLM call before materialization fallback.

    This only consumes structured LLM-stage summary fields. It deliberately does
    not infer provider failure from generic no-output/no-file symptoms; those
    still belong to the materialization boundary.
    """

    if not isinstance(primary_llm_summary, dict) or bool(primary_llm_summary.get("success")):
        return None

    error_text = _primary_llm_summary_text(primary_llm_summary)
    lowered = error_text.lower()
    error_category = str(primary_llm_summary.get("error_category") or "").strip().lower()
    if not error_text:
        return None

    timeout_markers = (
        "connecttimeouterror",
        "readtimeouterror",
        "timed out",
        "timeout",
        "time out",
    )
    if error_category == "timeout" or any(marker in lowered for marker in timeout_markers):
        return {
            "error": "model_provider_timeout",
            "error_code": "model_provider_timeout",
            "failure_class": FailureClassV1.MODEL_PROVIDER_TIMEOUT.value,
            "responsible_layer": "model_provider",
            "materialization_mode": "llm_call_failed",
            "failure_stage": "director_llm_call",
            "root_cause_hint": "provider_timeout",
            "detail": "Director primary LLM provider call timed out before tool dispatch or materialization.",
        }

    provider_markers = (
        "rate_limit",
        "429",
        "invalid_request",
        "provider_unavailable",
        "connection refused",
        "connection reset",
        "max retries exceeded",
        "httpconnectionpool",
    )
    if error_category in {"rate_limit", "provider", "provider_error", "unavailable"} or any(
        marker in lowered for marker in provider_markers
    ):
        return {
            "error": "model_provider_failure",
            "error_code": "model_provider_failure",
            "failure_class": FailureClassV1.MODEL_PROVIDER_FAILURE.value,
            "responsible_layer": "model_provider",
            "materialization_mode": "llm_call_failed",
            "failure_stage": "director_llm_call",
            "root_cause_hint": "provider_failure",
            "detail": "Director primary LLM provider call failed before tool dispatch or materialization.",
        }

    return None


def _no_write_retry_platform_failure_payload(
    no_write_retry_summary: dict[str, Any] | None,
) -> dict[str, str] | None:
    """Classify a forced write retry that never reached the provider.

    Live L2-12 TASK-3-source-core: first turn sealed ``no_write_tool_available``,
    then the forced ``edit_file`` retry died on
    ``final_physical_context_snapshot_persist_failed`` with ``llm_calls=0``.
    Settling ``director_no_materialized_changes`` hid the ContextOS persist
    miss and skipped same-task repair.
    """

    if not isinstance(no_write_retry_summary, dict) or bool(no_write_retry_summary.get("success")):
        return None
    metadata = no_write_retry_summary.get("metadata")
    metadata_map = metadata if isinstance(metadata, dict) else {}
    tokens = " ".join(
        (
            str(no_write_retry_summary.get("error") or ""),
            str(no_write_retry_summary.get("error_message") or ""),
            str(metadata_map.get("error_message") or ""),
            str(metadata_map.get("error") or ""),
        )
    ).lower()
    if "final_physical_context_snapshot_persist_failed" not in tokens:
        return None
    return {
        "error": "director_final_request_context_persist_failed",
        "error_code": "final_physical_context_snapshot_persist_failed",
        "failure_class": FailureClassV1.EXECUTION_EVIDENCE_MISSING.value,
        "responsible_layer": "execution_control_plane",
        "materialization_mode": "final_request_context_persist_failed",
        "failure_stage": "director_no_write_materialization_retry",
        "root_cause_hint": "final_physical_context_snapshot_persist_failed",
        "detail": (
            "Forced Director write retry never reached the provider because "
            "the final physical ContextOS snapshot could not be persisted."
        ),
    }


def _lifecycle_tool_dispatch_failure_from_summary(
    primary_llm_summary: dict[str, Any],
) -> dict[str, str] | None:
    """Return the tool_dispatch_dropped failure payload if Run Ledger lifecycle
    evidence in *primary_llm_summary* indicates a dispatch-dropped failure.

    Boundary:
        Consumes only Run Ledger public helpers:
        - ``project_tool_lifecycle_failure_status`` for already-summarized data.
        - ``tool_call_lifecycle_receipts_from_metadata`` +
          ``project_tool_lifecycle_event`` + ``summarize_tool_lifecycle_events``
          for raw receipt evidence.
        No local count/precedence interpretation is maintained here.

    Complexity:
        O(r * s) where ``r`` is receipt count and ``s`` is receipt size for
        deduplication; O(r) additional memory.
    """
    # 1. Already-summarized lifecycle mapping → public failure status.
    for key in ("tool_lifecycle_summary", "tool_call_lifecycle_summary"):
        candidate = primary_llm_summary.get(key)
        if isinstance(candidate, dict) and candidate:
            failure_status = project_tool_lifecycle_failure_status(candidate)
            if failure_status.get("failed") and is_failure_class(
                failure_status.get("failure_class"), FailureClassV1.TOOL_DISPATCH_DROPPED
            ):
                return _tool_dispatch_dropped_failure_payload()
    # 2. Raw receipt evidence in metadata → public receipt helpers → status.
    metadata = primary_llm_summary.get("metadata")
    if isinstance(metadata, dict):
        receipts = tool_call_lifecycle_receipts_from_metadata(metadata)
        if receipts:
            events = tuple(project_tool_lifecycle_event(r) for r in receipts)
            summary = summarize_tool_lifecycle_events(events)
            failure_status = project_tool_lifecycle_failure_status(summary)
            if failure_status.get("failed") and is_failure_class(
                failure_status.get("failure_class"), FailureClassV1.TOOL_DISPATCH_DROPPED
            ):
                return _tool_dispatch_dropped_failure_payload()
    return None


def _primary_llm_tool_dispatch_failure(primary_llm_summary: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(primary_llm_summary, dict):
        return None
    # Lifecycle evidence from Run Ledger public helpers takes priority over
    # error/error_code/failure_class field matching.
    lifecycle_result = _lifecycle_tool_dispatch_failure_from_summary(primary_llm_summary)
    if lifecycle_result is not None:
        return lifecycle_result
    # Fallback: legacy error/error_code/failure_class field matching.
    if not any(
        _summary_field_matches_failure_class(
            primary_llm_summary.get(key),
            FailureClassV1.TOOL_DISPATCH_DROPPED,
        )
        for key in ("error", "error_code", "failure_class")
    ):
        return None
    return _tool_dispatch_dropped_failure_payload()


def _seal_claimed_materialization_without_tool_lifecycle(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    turn_id: str = "",
    reason: str,
    failure_class: str,
    primary_llm_summary: Mapping[str, Any] | None = None,
    completion_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """R137: seal blocked lifecycle when claimed materialization ends with no tools.

    Claimed Director materialization activates a tool-lifecycle requirement. If the
    attempt never appends a tool_call_lifecycle receipt (closed without tools,
    no_materialized_changes, fail-closed before dispatch), projection reports
    TOOL_LIFECYCLE_MISSING even though claim/fail facts exist. Seal one blocked
    receipt so missing_required_task_keys clears and failure stays attributable.
    """

    resolved_run_id = str(run_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    if not resolved_run_id or not resolved_task_id:
        return None

    # Skip when turn/adapter metadata already carries lifecycle receipts.
    candidates: list[Mapping[str, Any]] = []
    if isinstance(primary_llm_summary, Mapping):
        candidates.append(primary_llm_summary)
        nested = primary_llm_summary.get("metadata")
        if isinstance(nested, Mapping):
            candidates.append(nested)
    if isinstance(completion_metadata, Mapping):
        candidates.append(completion_metadata)
        adapter_result = completion_metadata.get("adapter_result")
        if isinstance(adapter_result, Mapping):
            candidates.append(adapter_result)
            nested_primary = adapter_result.get("primary_llm")
            if isinstance(nested_primary, Mapping):
                candidates.append(nested_primary)
                nested_meta = nested_primary.get("metadata")
                if isinstance(nested_meta, Mapping):
                    candidates.append(nested_meta)
    for candidate in candidates:
        if tool_call_lifecycle_receipts_from_metadata(dict(candidate)):
            return None

    lifecycle = build_claimed_materialization_without_tool_lifecycle_receipt(
        run_id=resolved_run_id,
        task_id=resolved_task_id,
        turn_id=str(turn_id or "").strip(),
        role="director",
        reason=str(reason or "claimed_materialization_without_tool_lifecycle"),
        failure_class=str(failure_class or FailureClassV1.INCOMPLETE_MATERIALIZATION.value),
    )
    if isinstance(completion_metadata, dict):
        completion_metadata["tool_call_lifecycle_receipt"] = dict(lifecycle)
        completion_metadata["tool_call_lifecycle"] = dict(lifecycle)
        adapter_result = completion_metadata.get("adapter_result")
        if isinstance(adapter_result, dict):
            adapter_result["tool_call_lifecycle_receipt"] = dict(lifecycle)
    try:
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=str(workspace or ""),
                run_id=resolved_run_id,
                task_id=resolved_task_id,
                turn_id=str(turn_id or "").strip(),
                role="director",
                lifecycle_receipt=lifecycle,
                stage="tool_batch",
                project_id=resolved_task_id,
                ok=False,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug(
            "R137: failed to append claimed-without-tools lifecycle task_id=%s run_id=%s",
            resolved_task_id,
            resolved_run_id,
            exc_info=True,
        )
    return lifecycle


def _materialization_failure_evidence_row(
    *,
    error: str,
    error_code: str,
    failure_class: str,
    responsible_layer: str,
    failure_stage: str,
    root_cause_hint: str,
    detail: str,
    materialization_mode: str,
    run_id: str,
    target_task_id: str,
    cognitive_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build structured failure evidence for Director materialization failures.

    The Director adapter still owns the materialization decision, but the
    evidence row shape belongs to Run Ledger public contracts.
    """

    evidence_refs: list[str] = []
    if isinstance(cognitive_receipt, dict):
        for key in ("receipt_ref", "receipt_id", "id"):
            value = str(cognitive_receipt.get(key) or "").strip()
            if value:
                evidence_refs.append(value)
                break
    if not evidence_refs:
        evidence_refs.append(
            ":".join(
                token
                for token in (
                    "director_adapter_failure",
                    str(run_id or "").strip(),
                    str(target_task_id or "").strip(),
                    str(error_code or error or "").strip(),
                )
                if token
            )
        )
    return FailureEvidenceV1(
        failure_class=failure_class,
        responsible_layer=responsible_layer,
        reason=detail,
        evidence_refs=tuple(evidence_refs),
        metadata={
            "error": error,
            "error_code": error_code,
            "failure_stage": failure_stage,
            "root_cause_hint": root_cause_hint,
            "materialization_mode": materialization_mode,
            "run_id": run_id,
            "task_id": target_task_id,
        },
    ).to_dict()


def _phase_no_materialized_changes(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    can_accept_existing_scope: bool,
    context: dict[str, Any],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    existing_contract_evidence: dict[str, Any],
    primary_llm_summary: dict[str, Any] | None,
    requires_fresh_materialization: bool,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    workspace_name: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    current_files, new_files, modified_files, all_affected_files, tool_results = (
        state.current_files,
        state.new_files,
        state.modified_files,
        state.all_affected_files,
        state.tool_results,
    )
    if (
        not all_affected_files
        and not can_accept_existing_scope
        and (requires_fresh_materialization or not bool(existing_contract_evidence.get("ok")))
    ):
        out_of_scope_diff = _collect_workspace_out_of_scope_diff(
            task=task,
            baseline_files=baseline_files,
            current_files=current_files,
            workspace=str(getattr(adapter, "workspace", "") or ""),
            cache_root=_quality_repair_cache_root(task, context),
            workspace_name=workspace_name,
        )
        out_of_scope_files = list(out_of_scope_diff.get("affected_files") or [])
        task_boundary_scope_filter = out_of_scope_diff.get("task_boundary_scope_filter")
        if not isinstance(task_boundary_scope_filter, dict):
            task_boundary_scope_filter = None
        primary_llm_claimed_success = bool(primary_llm_summary.get("success")) if primary_llm_summary else False
        direct_side_effect_success = primary_llm_claimed_success and not tool_results
        lifecycle_failure = _primary_llm_tool_dispatch_failure(primary_llm_summary)
        retry_platform_failure = _no_write_retry_platform_failure_payload(
            no_write_materialization_retry_summary
        )
        if lifecycle_failure is not None:
            error = lifecycle_failure["error"]
            materialization_mode = lifecycle_failure["materialization_mode"]
            public_error_code = lifecycle_failure["error_code"]
            failure_class = lifecycle_failure["failure_class"]
            responsible_layer = lifecycle_failure["responsible_layer"]
            failure_stage = lifecycle_failure["failure_stage"]
            root_cause_hint = lifecycle_failure["root_cause_hint"]
            failure_detail = lifecycle_failure["detail"]
        elif retry_platform_failure is not None:
            error = retry_platform_failure["error"]
            materialization_mode = retry_platform_failure["materialization_mode"]
            public_error_code = retry_platform_failure["error_code"]
            failure_class = retry_platform_failure["failure_class"]
            responsible_layer = retry_platform_failure["responsible_layer"]
            failure_stage = retry_platform_failure["failure_stage"]
            root_cause_hint = retry_platform_failure["root_cause_hint"]
            failure_detail = retry_platform_failure["detail"]
        elif (provider_failure := _primary_llm_provider_failure_payload(primary_llm_summary)) is not None:
            error = provider_failure["error"]
            materialization_mode = provider_failure["materialization_mode"]
            public_error_code = provider_failure["error_code"]
            failure_class = provider_failure["failure_class"]
            responsible_layer = provider_failure["responsible_layer"]
            failure_stage = provider_failure["failure_stage"]
            root_cause_hint = provider_failure["root_cause_hint"]
            failure_detail = provider_failure["detail"]
        elif out_of_scope_files and (write_tool_evidence or direct_side_effect_success):
            error = "director_materialized_out_of_scope"
            materialization_mode = "materialized_out_of_scope"
            public_error_code = error
            failure_class = FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value
            responsible_layer = "director_scope_guard"
            failure_stage = "director_materialization"
            root_cause_hint = "no_changed_files"
            failure_detail = "Director returned no workspace file changes."
        else:
            error = "director_no_materialized_changes"
            materialization_mode = "no_materialized_changes"
            public_error_code = "incomplete_materialization"
            failure_class = FailureClassV1.INCOMPLETE_MATERIALIZATION.value
            responsible_layer = "director"
            failure_stage = "director_materialization"
            root_cause_hint = "no_changed_files"
            failure_detail = (
                "Director returned no workspace file changes; "
                "fresh materialization is required for repair/update tasks."
                if requires_fresh_materialization
                else "Director returned no workspace file changes."
            )
        # Wall 2 diagnostic (F16 follow-up): the forced write emitted but the
        # workspace diff is empty. Surface the discriminating signals so a single
        # solo rerun reveals whether the write content ARG was empty (prose lands
        # in reasoning, structured `content` stays blank) or the write was
        # non-authoritative — directs the Wall 2 fix without guessing.
        logger.warning(
            "%s DIAGNOSTIC: write_tool_evidence=%s tools_executed=%s "
            "new_files=%s modified_files=%s out_of_scope_files=%s requires_fresh=%s "
            "write_args(name,content_len)=%s",
            error,
            write_tool_evidence,
            len(tool_results),
            len(new_files),
            len(modified_files),
            out_of_scope_files[:20],
            requires_fresh_materialization,
            _diag_write_results_summary(tool_results),
        )
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
                "materialization_error": error,
                "materialization_error_code": public_error_code,
                "failure_class": failure_class,
                "responsible_layer": responsible_layer,
                "materialization_mode": materialization_mode,
                "out_of_scope_files": out_of_scope_files[:20],
                "out_of_scope_file_count": len(out_of_scope_files),
                "out_of_scope_diff": out_of_scope_diff,
                "existing_contract_evidence": existing_contract_evidence,
            }
        }
        if task_boundary_scope_filter is not None:
            completion_metadata["adapter_result"]["task_boundary_scope_filter"] = task_boundary_scope_filter
            scope_authority = task_boundary_scope_filter.get("scope_authority")
            if isinstance(scope_authority, dict):
                completion_metadata["adapter_result"]["scope_authority"] = scope_authority
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        # R137: claimed materialization with no tool lifecycle must seal blocked
        # evidence before finalize; otherwise Run Ledger projects TOOL_LIFECYCLE_MISSING.
        if not tool_results and not write_tool_evidence:
            sealed_lifecycle = _seal_claimed_materialization_without_tool_lifecycle(
                workspace=str(getattr(adapter, "workspace", "") or ""),
                run_id=str(run_id or ""),
                task_id=str(target_task_id or ""),
                turn_id=str(
                    (primary_llm_summary or {}).get("turn_id") or (primary_llm_summary or {}).get("last_turn_id") or ""
                ),
                reason=str(error or "director_no_materialized_changes"),
                failure_class=str(failure_class or FailureClassV1.INCOMPLETE_MATERIALIZATION.value),
                primary_llm_summary=primary_llm_summary,
                completion_metadata=completion_metadata,
            )
            if sealed_lifecycle is not None:
                completion_metadata["adapter_result"]["tool_call_lifecycle_sealed"] = True
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
                "error_code": public_error_code,
                "failure_class": failure_class,
                "responsible_layer": responsible_layer,
                "materialization_mode": materialization_mode,
                "changed_files": out_of_scope_files if out_of_scope_files else [],
                "out_of_scope_files": out_of_scope_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                **(
                    {"task_boundary_scope_filter": task_boundary_scope_filter}
                    if task_boundary_scope_filter is not None
                    else {}
                ),
            },
        )
        failure_evidence_row = _materialization_failure_evidence_row(
            error=error,
            error_code=public_error_code,
            failure_class=failure_class,
            responsible_layer=responsible_layer,
            failure_stage=failure_stage,
            root_cause_hint=root_cause_hint,
            detail=failure_detail,
            materialization_mode=materialization_mode,
            run_id=run_id,
            target_task_id=target_task_id,
            cognitive_receipt=cognitive_receipt if isinstance(cognitive_receipt, dict) else None,
        )
        failure_evidence_rows = append_failure_evidence_to_metadata(completion_metadata, failure_evidence_row)
        completion_metadata["adapter_result"]["failure_evidence"] = failure_evidence_rows
        completion_metadata["adapter_result"]["failure_evidence_summary"] = completion_metadata.get(
            "failure_evidence_summary",
            {},
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        result = {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": public_error_code,
            "failure_class": failure_class,
            "responsible_layer": responsible_layer,
            "failure_stage": failure_stage,
            "root_cause_hint": root_cause_hint,
            "failure_evidence": failure_evidence_rows,
            "failure_evidence_summary": completion_metadata.get("failure_evidence_summary", {}),
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [
                {
                    "code": public_error_code,
                    "severity": "error",
                    "failure_class": failure_class,
                    "responsible_layer": responsible_layer,
                    "detail": failure_detail,
                }
            ],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


def _phase_existing_scope_verified(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    can_accept_existing_scope: bool,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    existing_contract_evidence: dict[str, Any],
    primary_llm_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, tool_results = (
        state.all_affected_files,
        state.tool_results,
    )
    if not all_affected_files and can_accept_existing_scope:
        completion_metadata: dict[str, Any] = {
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
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
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
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                authority=task_execution_attempt_authority,
                result_summary=(
                    "verified_existing_workspace_scope="
                    f"{len(existing_contract_evidence.get('existing_paths') or [])}; "
                    f"tools_executed={len(tool_results)}"
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
    return None


def _phase_missing_write_receipt(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, new_files, modified_files, tool_results = (
        state.all_affected_files,
        state.new_files,
        state.modified_files,
        state.tool_results,
    )
    if all_affected_files and not write_tool_evidence:
        error = "director_missing_write_receipt"
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
                "materialization_mode": materialization_mode,
                "materialization_error": error,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
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
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
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
        result = {
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
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


def _cross_artifact_llm_escalation_enabled() -> bool:
    """Default OFF -> preserve current deterministic fail-closed behavior.

    Opt in via env to escalate
    residual cross-artifact quality errors to a bounded Director LLM re-generation
    before the hard materialization-quality fail."""
    raw = str(os.environ.get("KERNELONE_DIRECTOR_CROSS_ARTIFACT_LLM_ESCALATION", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def _phase_cross_artifact_unplannable_llm_escalation(
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
    artifact_quality_errors: list[str],
    quality_repair_attempts: list[dict[str, Any]],
    state: MaterializationState,
) -> tuple[MaterializationState, list[str]]:
    """Escalate deterministically-unplannable cross-artifact quality errors to a
    bounded Director LLM re-generation before ``_phase_quality_failed`` hard-fails.
    Cross-file symbol mismatches (consumer imports a symbol the sibling owner never
    defines) are ``coverage_matched_but_unplannable`` for the deterministic kernel,
    so without this the LLM is never asked to re-generate the files to cohere.
    Reuses the semantic-repair LLM-retry + recompute pipeline. Inert when empty."""
    if not artifact_quality_errors:
        return state, artifact_quality_errors
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        if not artifact_quality_errors:
            break
        repair_tool_results, repair_summary = await _run_materialization_quality_repair_retry(
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
        if isinstance(repair_summary, dict):
            quality_repair_attempts.append({**repair_summary, "escalation": "cross_artifact_unplannable"})
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
        scan_paths = _materialization_quality_scan_paths(all_affected_files, tool_results)
        artifact_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=scan_paths,
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
        artifact_quality_errors += run_python_static_smoke(adapter, all_affected_files=scan_paths)
        runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
            adapter,
            task_id=target_task_id,
            all_affected_files=scan_paths,
            context=context,
        )
        artifact_quality_errors += runtime_smoke_errors
        tool_results.extend(runtime_smoke_tool_results)
        artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
            artifact_quality_errors,
            str(getattr(adapter, "workspace", "") or ""),
        )
    state = MaterializationState.from_locals(current_files, new_files, modified_files, all_affected_files, tool_results)
    return state, artifact_quality_errors


def _phase_quality_failed(
    adapter: Any,
    *,
    artifact_quality_errors: list[str],
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, new_files, modified_files, tool_results = (
        state.all_affected_files,
        state.new_files,
        state.modified_files,
        state.tool_results,
    )
    if artifact_quality_errors:
        error = "director_materialization_quality_failed"
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
                "materialization_mode": materialization_mode,
                "materialization_error": error,
                "artifact_quality_errors": artifact_quality_errors[:20],
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
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
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
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
        result = {
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
            # Forensic trail: without this, a repair attempt that died before
            # its LLM call is indistinguishable from one that never ran.
            "quality_repair_attempts": quality_repair_attempts,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


def _phase_semantic_quality_failed(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    run_id: str,
    semantic_quality_error: str | None,
    semantic_quality_repair_attempts: list[dict[str, Any]],
    semantic_quality_repair_summary: dict[str, Any] | None,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, new_files, modified_files, tool_results = (
        state.all_affected_files,
        state.new_files,
        state.modified_files,
        state.tool_results,
    )
    if semantic_quality_error:
        error = "director_materialization_semantic_quality_failed"
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
                "materialization_mode": materialization_mode,
                "materialization_error": error,
                "semantic_quality_error": semantic_quality_error,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        if semantic_quality_repair_summary is not None:
            completion_metadata["adapter_result"]["semantic_quality_repair"] = semantic_quality_repair_summary
        if semantic_quality_repair_attempts:
            completion_metadata["adapter_result"]["semantic_quality_repair_attempts"] = semantic_quality_repair_attempts
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
                "semantic_quality_repair": semantic_quality_repair_summary or {},
                "semantic_quality_repair_attempts": semantic_quality_repair_attempts,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        semantic_signal = {
            "code": error,
            "severity": "error",
            "detail": semantic_quality_error,
        }
        result = {
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
            "semantic_quality_repair_attempts": semantic_quality_repair_attempts,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


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


from ..artifact_quality_diagnostics import (  # noqa: E402
    _filter_satisfied_declared_target_missing_errors as _filter_satisfied_declared_target_missing_errors,
)
from ..execute_method_repair_bridge import (  # noqa: E402
    run_python_runtime_smoke as run_python_runtime_smoke,
    run_python_static_smoke as run_python_static_smoke,
)
from ..quality_gate import (  # noqa: E402
    _QUALITY_REPAIR_ATTEMPT_HARD_CAP as _QUALITY_REPAIR_ATTEMPT_HARD_CAP,
    _collect_materialization_quality_errors as _collect_materialization_quality_errors,
    _collect_step_verify_errors as _collect_step_verify_errors,
    _collect_workspace_code_diff as _collect_workspace_code_diff,
    _collect_workspace_out_of_scope_diff as _collect_workspace_out_of_scope_diff,
    _extract_successful_write_paths as _extract_successful_write_paths,
    _materialization_quality_scan_paths as _materialization_quality_scan_paths,
    _merge_successful_write_paths as _merge_successful_write_paths,
    _quality_repair_cache_root as _quality_repair_cache_root,
    _run_materialization_quality_repair_retry as _run_materialization_quality_repair_retry,
)
