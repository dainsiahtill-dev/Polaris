"""Non-streaming TransactionKernel completion projection.

The TransactionKernel adapter calls this owner after a successful kernel
execution. It converts the raw TransactionKernel result into a RoleTurnResult,
records projection feedback, commits the turn to ContextOS, and appends the
task-boundary verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from polaris.cells.control_plane.run_ledger.public import (
    build_missing_dispatch_lifecycle_receipt,
    project_tool_lifecycle_receipt_to_metadata,
    task_boundary_tool_dispatch_from_lifecycle_metadata,
)
from polaris.cells.roles.kernel.internal.kernel.commit_protocol import (
    _build_turn_history_and_events,
    _commit_turn_to_snapshot,
)
from polaris.cells.roles.kernel.internal.kernel.context_assembly import build_context_handoff_pack
from polaris.cells.roles.kernel.internal.kernel.output_parser_provider import get_output_parser
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    role_result_metadata_from_profile,
    role_turn_completion_result,
    tool_calls_from_batch_receipt,
    tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.kernel.task_boundary import append_role_turn_task_boundary_verdict
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest, RoleTurnResult
from polaris.kernelone.tools import is_write_tool_name, normalize_tool_name

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)

MISSING_DISPATCH_COMPLETION_ERROR = "tool_dispatch_dropped: required write tool was not dispatched before completion"


def build_transaction_turn_completion_result(
    *,
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    fingerprint: Any,
    turn_id: str,
    tk_result: dict[str, Any],
    response_schema: type | None,
    runtime_tool_policy_audit: dict[str, Any],
    tool_filter_audit: dict[str, Any] | None,
    context_gateway: Any,
    context_result: Any,
) -> RoleTurnResult:
    """Convert a successful TransactionKernel result into a RoleTurnResult.

    Boundary:
        This function may write ContextOS turn state and Run Ledger
        task-boundary verdicts. It must not call LLM providers, dispatch tools,
        or mutate tool authorization policy.

    Complexity:
        O(c + r) time and memory where ``c`` is visible content length and
        ``r`` is batch receipt size.
    """

    kind = tk_result.get("kind", "final_answer")
    visible_content = str(tk_result.get("visible_content", "") or "")
    thinking_text: str | None = None
    if visible_content:
        parsed = get_output_parser(kernel).parse_thinking(visible_content)
        visible_content = str(parsed.clean_content or "")
        thinking_text = parsed.thinking
    batch_receipt = tk_result.get("batch_receipt")
    normalized_batch_receipt = dict(batch_receipt) if isinstance(batch_receipt, dict) else None
    finalization = tk_result.get("finalization")
    workflow_context = tk_result.get("workflow_context")
    metrics = tk_result.get("metrics", {})
    metrics_dict = metrics if isinstance(metrics, dict) else {}

    # Pull the ledger from the TransactionKernel result so it can be committed
    # into the ContextOS snapshot, eliminating the parallel TurnLedger state.
    ledger = tk_result.get("ledger")
    tool_calls = tool_calls_from_batch_receipt(normalized_batch_receipt)
    tool_results = tool_results_from_batch_receipt(normalized_batch_receipt)
    structured_output = _extract_structured_output(
        kernel=kernel,
        response_schema=response_schema,
        visible_content=visible_content,
    )
    execution_stats = {
        "duration_ms": metrics_dict.get("duration_ms", 0),
        "llm_calls": metrics_dict.get("llm_calls", 0),
        "tool_calls": metrics_dict.get("tool_calls", 0),
        "transaction_kernel": True,
        **runtime_tool_policy_audit,
    }
    metadata = _build_completion_metadata(
        kernel=kernel,
        role=role,
        profile=profile,
        request=request,
        tk_result=tk_result,
        kind=str(kind),
        workflow_context=workflow_context,
        tool_filter_audit=tool_filter_audit,
        ledger=ledger,
    )
    error_msg, is_complete = _resolve_completion_status(kind=str(kind), finalization=finalization, metadata=metadata)
    lifecycle_receipt = record_missing_dispatch_lifecycle_receipt(
        role=role,
        request=request,
        kernel=kernel,
        turn_id=turn_id,
        metadata=metadata,
        ledger=ledger,
        tool_results=tool_results,
        batch_receipt=normalized_batch_receipt,
    )
    if lifecycle_receipt is not None and is_complete and not error_msg:
        # Dropped-dispatch evidence is always recorded above; it only becomes
        # the turn error when the turn would otherwise claim a successful
        # completion. Legitimate ask_user / needs_followup suspensions keep
        # their own error and status.
        error_msg = MISSING_DISPATCH_COMPLETION_ERROR
        is_complete = False
    final_thinking = thinking_text
    if final_thinking is None and isinstance(finalization, dict):
        final_thinking = finalization.get("final_visible_message")

    task_boundary_verdict = _append_task_boundary_verdict(
        role=role,
        request=request,
        kernel=kernel,
        turn_id=turn_id,
        tool_results=tool_results,
        metadata=metadata,
        error_msg=error_msg,
    )
    error_msg, is_complete = _apply_task_boundary_completion_gate(
        verdict=task_boundary_verdict,
        metadata=metadata,
        error_msg=error_msg,
        is_complete=is_complete,
    )
    _record_projection_outcome(
        context_gateway=context_gateway,
        context_result=context_result,
        metadata=metadata,
        success=bool(is_complete and not error_msg),
    )
    turn_history, turn_events_metadata = _build_turn_history_and_events(
        turn_id=turn_id,
        request=request,
        visible_content=visible_content,
        thinking=final_thinking,
        tool_results=tool_results,
    )
    _commit_turn_to_snapshot(
        request=request,
        turn_id=turn_id,
        turn_history=turn_history,
        turn_events_metadata=turn_events_metadata,
        tool_results=tool_results,
        ledger=ledger,
    )
    return role_turn_completion_result(
        content=visible_content,
        thinking=final_thinking,
        structured_output=structured_output,
        tool_calls=tool_calls,
        tool_results=tool_results,
        batch_receipt=normalized_batch_receipt,
        profile=profile,
        fingerprint=fingerprint,
        error=error_msg,
        is_complete=is_complete,
        execution_stats=execution_stats,
        turn_history=turn_history,
        turn_events_metadata=turn_events_metadata,
        metadata=metadata,
    )


def _extract_structured_output(
    *,
    kernel: RoleExecutionKernel,
    response_schema: type | None,
    visible_content: str,
) -> dict[str, Any] | None:
    if response_schema is None or not visible_content:
        return None
    try:
        candidate = get_output_parser(kernel).extract_json(visible_content)
        if candidate is not None:
            validated = response_schema(**candidate)
            return validated.model_dump()
    except (RuntimeError, ValueError):
        return None
    return None


def _build_completion_metadata(
    *,
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    tk_result: dict[str, Any],
    kind: str,
    workflow_context: Any,
    tool_filter_audit: dict[str, Any] | None,
    ledger: Any,
) -> dict[str, Any]:
    llm_response_metadata = tk_result.get("llm_response_metadata")
    metadata = role_result_metadata_from_profile(
        profile=profile,
        tool_filter_audit=tool_filter_audit,
        ledger=ledger,
        llm_response_metadata=llm_response_metadata if isinstance(llm_response_metadata, dict) else None,
    )
    if kind == "handoff_workflow" and workflow_context is not None:
        handoff_pack = build_context_handoff_pack(kernel, tk_result, role, request)
        metadata["handoff_pack"] = handoff_pack.to_dict()
        metadata["transaction_kind"] = "handoff_workflow"
    return metadata


def _copy_string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple | set | frozenset):
        return []
    copied: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            copied.append(token)
    return copied


def _extract_required_tools(metadata: Mapping[str, Any]) -> list[str]:
    required = _copy_string_list(metadata.get("required_tools"))
    audit = metadata.get("final_request_context_audit")
    if isinstance(audit, Mapping):
        required.extend(_copy_string_list(audit.get("required_tools")))
        evidence = audit.get("final_request_evidence_coverage")
        if isinstance(evidence, Mapping):
            required.extend(_copy_string_list(evidence.get("required_tools")))
    deduped: list[str] = []
    seen: set[str] = set()
    for tool_name in required:
        normalized = normalize_tool_name(tool_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _last_decision_metadata(ledger: Any) -> dict[str, Any]:
    decisions = getattr(ledger, "decisions", None)
    if not isinstance(decisions, list) or not decisions:
        return {}
    latest = decisions[-1]
    if not isinstance(latest, Mapping):
        return {}
    metadata = latest.get("metadata")
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _build_missing_dispatch_lifecycle_receipt(
    *,
    metadata: Mapping[str, Any],
    ledger: Any,
    tool_results: list[dict[str, Any]],
    batch_receipt: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    required_write_tools = [tool for tool in _extract_required_tools(metadata) if is_write_tool_name(tool)]
    return build_missing_dispatch_lifecycle_receipt(
        required_write_tools=required_write_tools,
        metadata_candidates=(metadata, _last_decision_metadata(ledger)),
        tool_results=tool_results,
        batch_receipt=batch_receipt,
    )


def _append_tool_call_lifecycle_event(
    *,
    role: str,
    request: RoleTurnRequest,
    kernel: RoleExecutionKernel,
    turn_id: str,
    lifecycle_receipt: Mapping[str, Any],
) -> None:
    """Commit completion-path tool lifecycle evidence to the Run Ledger."""

    try:
        from polaris.cells.control_plane.run_ledger.public import (
            AppendRunLedgerEventCommandV1,
            append_run_ledger_event,
            build_tool_call_lifecycle_run_ledger_event,
        )

        run_id = str(request.run_id or turn_id)
        task_id = str(request.task_id or "")
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(request.workspace or kernel.workspace or "."),
                run_id=run_id,
                event=build_tool_call_lifecycle_run_ledger_event(
                    run_id=run_id,
                    task_id=task_id,
                    turn_id=turn_id,
                    role=str(role or ""),
                    lifecycle_receipt=lifecycle_receipt,
                    stage="director_tool_dispatch",
                    ok=False,
                ),
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to append completion tool-call lifecycle event", exc_info=True)


def record_missing_dispatch_lifecycle_receipt(
    *,
    role: str,
    request: RoleTurnRequest,
    kernel: RoleExecutionKernel,
    turn_id: str,
    metadata: dict[str, Any],
    ledger: Any,
    tool_results: list[dict[str, Any]],
    batch_receipt: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Record dropped required-write dispatch evidence for a completed turn.

    Shared by the non-stream and stream completion owners: when the final
    request required a write tool but the turn completed with zero
    dispatch/effect evidence, this builds the ``tool_call_lifecycle_receipt.v1``,
    stores it in the result metadata, and appends the Run Ledger lifecycle
    event. Callers own error/status precedence over the returned receipt.

    Complexity:
        O(r + t) time where ``r`` is batch receipt size and ``t`` is the number
        of required tools; O(t) additional memory for the receipt.
    """

    lifecycle_receipt = _build_missing_dispatch_lifecycle_receipt(
        metadata=metadata,
        ledger=ledger,
        tool_results=tool_results,
        batch_receipt=batch_receipt,
    )
    if lifecycle_receipt is None:
        return None
    project_tool_lifecycle_receipt_to_metadata(metadata, lifecycle_receipt)
    _append_tool_call_lifecycle_event(
        role=role,
        request=request,
        kernel=kernel,
        turn_id=turn_id,
        lifecycle_receipt=lifecycle_receipt,
    )
    return lifecycle_receipt


def _resolve_completion_status(
    *,
    kind: str,
    finalization: Any,
    metadata: dict[str, Any],
) -> tuple[str | None, bool]:
    error_msg: str | None = None
    is_complete = True
    if kind == "ask_user" and isinstance(finalization, dict):
        error_msg = finalization.get("error") or finalization.get("suspended_reason")
        is_complete = False
    if isinstance(finalization, dict) and bool(finalization.get("needs_followup_workflow")):
        workflow_reason = str(finalization.get("workflow_reason") or kind or "").strip()
        metadata["transaction_kind"] = str(kind or workflow_reason)
        metadata["needs_followup_workflow"] = True
        metadata["workflow_reason"] = workflow_reason
        metadata["blocked_reason"] = finalization.get("blocked_reason")
        metadata["blocked_detail"] = finalization.get("blocked_detail")
        error_msg = (
            str(
                finalization.get("error")
                or finalization.get("blocked_reason")
                or workflow_reason
                or "needs_followup_workflow"
            ).strip()
            or None
        )
        is_complete = False
    return error_msg, is_complete


def _record_projection_outcome(
    *,
    context_gateway: Any,
    context_result: Any,
    metadata: dict[str, Any],
    success: bool,
) -> None:
    try:
        metadata["projection_adaptive_weights_after_turn"] = context_gateway.record_projection_outcome(
            success=success,
            tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.debug("Projection outcome feedback failed", exc_info=True)


def _append_task_boundary_verdict(
    *,
    role: str,
    request: RoleTurnRequest,
    kernel: RoleExecutionKernel,
    turn_id: str,
    tool_results: list[dict[str, Any]],
    metadata: dict[str, Any],
    error_msg: str | None,
) -> dict[str, Any] | None:
    try:
        return append_role_turn_task_boundary_verdict(
            role=role,
            workspace=str(request.workspace or kernel.workspace or "."),
            task_id=str(request.task_id or ""),
            run_id=str(request.run_id or turn_id),
            context_override=getattr(request, "context_override", None),
            tool_results=tool_results,
            tool_dispatch=task_boundary_tool_dispatch_from_lifecycle_metadata(metadata),
            needs_followup_workflow=bool(metadata.get("needs_followup_workflow")),
            workflow_reason=str(metadata.get("workflow_reason") or ""),
            error_message=error_msg,
            evidence_refs=[str(metadata.get("context_snapshot_ref") or "").strip()],
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to append role-turn task boundary verdict", exc_info=True)
        return None


def _apply_task_boundary_completion_gate(
    *,
    verdict: dict[str, Any] | None,
    metadata: dict[str, Any],
    error_msg: str | None,
    is_complete: bool,
) -> tuple[str | None, bool]:
    if not isinstance(verdict, dict):
        return error_msg, is_complete
    metadata["task_boundary_verdict"] = dict(verdict)
    if bool(verdict.get("ok")):
        return error_msg, is_complete
    status = str(verdict.get("status") or "failed").strip() or "failed"
    failure_class = str(verdict.get("failure_class") or "TASK_BOUNDARY_FAILED").strip()
    reason = str(verdict.get("reason") or "Task boundary failed").strip()
    metadata["task_boundary_failed"] = True
    metadata["task_boundary_failure_class"] = failure_class
    metadata["task_boundary_failure_status"] = status
    if error_msg:
        return error_msg, False
    return f"task_boundary_failed:{status}: {reason}", False
