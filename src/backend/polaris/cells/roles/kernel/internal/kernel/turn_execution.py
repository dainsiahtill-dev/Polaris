"""Single-turn and streaming TransactionKernel execution.

Owns the canonical free-function execution paths consumed by
``RoleExecutionKernel.run`` and ``RoleExecutionKernel.run_stream``. The public
kernel entrypoint delegates to these functions directly so transaction
execution has one implementation surface.

Behavior notes:
- The turn body and the stream body share the tool-definitions preamble shape
  but are NOT byte-identical: the turn body additionally emits the
  ``PRONG_A_TRACE`` info log and the ``KERNELONE_DELIVERY_MODE_TRACE`` warning,
  and restores the delivery-mode marker via ``_ensure_context_delivery_mode_marker``.
  Tool-surface planning is shared through the transaction-layer planner, while
  execution, projection feedback, and RoleTurnResult/stream event mapping stay
  in this public-entrypoint adapter.
- §8 governance note: the embedded weak-Director Prong-A / R7 / Fix-11
  write-vs-edit heuristics live in the planner. They are not deleted here (a
  separate governance pass owns that decision).
- Function-local lazy imports for RoleContextGateway / turn_events are preserved
  to keep the original circular-import-avoidance and monkeypatch-target ordering.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.commit_protocol import (
    _build_turn_history_and_events,
    _commit_turn_to_snapshot,
)
from polaris.cells.roles.kernel.internal.kernel.context_assembly import build_context_handoff_pack
from polaris.cells.roles.kernel.internal.kernel.delivery_mode import (
    _context_requests_materialize_delivery,
    _ensure_context_delivery_mode_marker,
    _ensure_platform_tool_contract_metadata,
    _latest_user_content_preview,
    _text_requests_materialize_delivery,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import _resolve_transaction_turn_id
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest, RoleTurnResult
from polaris.kernelone.audit.context_os_prompt import summarize_context_os_audit_from_ledger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.kernelone.events.uep_publisher import UEPEventPublisher

logger = logging.getLogger(__name__)


def _tool_calls_from_batch_receipt(batch_receipt: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(batch_receipt, dict):
        return []
    raw_results = batch_receipt.get("results")
    if not isinstance(raw_results, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        tool_calls.append(
            {
                "tool": result.get("tool_name", ""),
                "args": result.get("arguments") or {},
                "call_id": result.get("call_id", ""),
            }
        )
    return tool_calls


def _tool_results_from_batch_receipt(batch_receipt: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(batch_receipt, dict):
        return []
    raw_results = batch_receipt.get("results")
    if not isinstance(raw_results, list):
        return []
    tool_results: list[dict[str, Any]] = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        tool_results.append(
            {
                "tool": result.get("tool_name", ""),
                "tool_name": result.get("tool_name", ""),
                "result": result.get("result"),
                "success": result.get("status") == "success",
                "status": result.get("status"),
                "call_id": result.get("call_id", ""),
                "arguments": result.get("arguments"),
                "effect_receipt": result.get("effect_receipt"),
                "raw_result": dict(result),
            }
        )
    return tool_results


def _clean_relative_paths(values: Any) -> list[str]:
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    paths: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        path = str(value or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or path.startswith("../") or "/../" in path:
            continue
        if "*" in path or "," in path:
            continue
        normalized = path[2:] if path.startswith("./") else path
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return paths


def _extend_context_paths(paths: list[str], value: Any) -> None:
    for path in _clean_relative_paths(value):
        if path not in paths:
            paths.append(path)


def _director_task_boundary_target_files(context_override: Any) -> list[str]:
    if not isinstance(context_override, dict):
        return []
    paths: list[str] = []
    for key in ("target_files", "repair_target_files"):
        _extend_context_paths(paths, context_override.get(key))
    for key in ("director_execution_profile", "task_execution_profile", "execution_profile"):
        profile = context_override.get(key)
        if isinstance(profile, dict):
            _extend_context_paths(paths, profile.get("target_files"))
    construction_step = context_override.get("construction_step")
    if isinstance(construction_step, dict):
        _extend_context_paths(paths, construction_step.get("target_file"))
        _extend_context_paths(paths, construction_step.get("target_files"))
    for key in ("task", "current_task", "pm_task_contract"):
        task = context_override.get(key)
        if isinstance(task, dict):
            _extend_context_paths(paths, task.get("target_files"))
    return paths


def _completed_artifacts_from_tool_results(tool_results: list[dict[str, Any]]) -> list[str]:
    artifacts: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict) or item.get("success") is False:
            continue
        candidates: list[Any] = [item]
        for key in ("result", "effect_receipt", "raw_result"):
            value = item.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        for candidate in candidates:
            for key in ("file", "path", "target_file"):
                _extend_context_paths(artifacts, candidate.get(key))
            for key in ("files_changed", "changed_files"):
                _extend_context_paths(artifacts, candidate.get(key))
    return artifacts


def _director_task_boundary_verdict(
    *,
    role: str,
    workspace: str,
    task_id: str,
    run_id: str,
    context_override: Any,
    tool_results: list[dict[str, Any]],
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    if str(role or "").strip().lower() != "director":
        return None
    target_files = _director_task_boundary_target_files(context_override)
    completed_artifacts = _completed_artifacts_from_tool_results(tool_results)
    dispatch = dict(tool_dispatch or {})
    if not target_files and not completed_artifacts and not bool(dispatch.get("dropped")):
        return None
    from polaris.cells.control_plane.run_ledger.public import evaluate_task_boundary_verdict

    return evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        target_files=target_files,
        completed_artifacts=completed_artifacts,
        tool_dispatch=dispatch,
        evidence_refs=evidence_refs,
    ).to_dict()


def _append_director_task_boundary_verdict(
    *,
    role: str,
    workspace: str,
    task_id: str,
    run_id: str,
    context_override: Any,
    tool_results: list[dict[str, Any]],
    tool_dispatch: dict[str, Any] | None = None,
    evidence_refs: list[str] | tuple[str, ...] | None = None,
) -> None:
    verdict = _director_task_boundary_verdict(
        role=role,
        workspace=workspace,
        task_id=task_id,
        run_id=run_id,
        context_override=context_override,
        tool_results=tool_results,
        tool_dispatch=tool_dispatch,
        evidence_refs=evidence_refs,
    )
    if verdict is None:
        return
    from polaris.cells.control_plane.run_ledger.public import AppendRunLedgerEventCommandV1, append_run_ledger_event

    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=run_id,
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": task_id,
                "run_id": run_id,
                "task_boundary_verdict": verdict,
                "job_token": {
                    "run_id": run_id,
                    "task_id": task_id,
                    "project_id": task_id or "unknown",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )


def _forced_tool_choice_override(context_override: Any) -> Any | None:
    if not isinstance(context_override, dict):
        return None

    forced_choice = context_override.get("_transaction_kernel_forced_tool_choice")
    if isinstance(forced_choice, dict):
        forced_function = forced_choice.get("function")
        if isinstance(forced_function, dict) and str(forced_function.get("name") or "").strip():
            return forced_choice
        return None

    forced_token = str(forced_choice or "").strip().lower()
    if forced_token == "required":
        return "required"
    return None


def _tool_schema_names_for_error_audit(tool_definitions: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for definition in tool_definitions:
        function_payload = definition.get("function") if isinstance(definition, dict) else None
        if not isinstance(function_payload, dict):
            continue
        name = str(function_payload.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _llm_metadata_from_ledger_on_error(
    ledger: Any,
    *,
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project final-request audit evidence even when TransactionKernel raises."""

    metadata: dict[str, Any] = {}
    calls = getattr(ledger, "llm_calls", None)
    if isinstance(calls, list):
        for call in reversed(calls):
            if not isinstance(call, dict):
                continue
            raw_metadata = call.get("metadata")
            if not isinstance(raw_metadata, dict):
                continue
            for key in (
                "final_request_context_audit",
                "context_snapshot_ref",
                "context_snapshot_degraded",
                "context_snapshot_degraded_reason",
                "context_tokens_after",
                "contextTokens",
                "usage",
                "usage_source",
            ):
                if key in raw_metadata and key not in metadata:
                    value = raw_metadata.get(key)
                    metadata[key] = dict(value) if isinstance(value, dict) else value
            if metadata:
                break
    context_os_audit_summary = summarize_context_os_audit_from_ledger(ledger)
    if context_os_audit_summary:
        metadata.setdefault("context_os_audit", context_os_audit_summary)
    anomaly_flags = getattr(ledger, "anomaly_flags", None)
    if isinstance(anomaly_flags, list) and anomaly_flags:
        metadata["anomaly_flags"] = [dict(item) for item in anomaly_flags if isinstance(item, dict)]
        if any(str(item.get("type") or "") == "TOOL_DISPATCH_DROPPED" for item in metadata["anomaly_flags"]):
            metadata["tool_dispatch_dropped"] = True
    metadata["transaction_kernel_error_audit_available"] = bool(
        isinstance(metadata.get("final_request_context_audit"), dict)
        or str(metadata.get("context_snapshot_ref") or "").strip()
    )
    if not metadata["transaction_kernel_error_audit_available"]:
        metadata["provider_request_snapshot_degraded"] = True
        metadata["provider_request_snapshot_degraded_reason"] = "transaction_kernel_failed_without_llm_metadata"
        metadata["provider_request_assembly_projection"] = {
            "schema_version": "llm.provider_request_assembly_projection.v1",
            "source": "roles.kernel.transaction_error_path",
            "message_count": len(messages),
            "tool_schema_count": len(tool_definitions),
            "tool_names": _tool_schema_names_for_error_audit(tool_definitions),
        }
    return metadata


def _append_tool_dispatch_dropped_control_plane_events(
    *,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    workspace: str,
    turn_id: str,
    error_metadata: dict[str, Any],
    reason: str,
) -> None:
    """Commit dropped native tool-call facts to the control-plane ledger."""

    from polaris.cells.control_plane.run_ledger.public import (
        AppendRunLedgerEventCommandV1,
        append_run_ledger_event,
        build_tool_call_lifecycle_receipt,
    )

    native_count = 1
    provider_response_hash = ""
    for flag in error_metadata.get("anomaly_flags", []):
        if isinstance(flag, dict) and str(flag.get("type") or "") == "TOOL_DISPATCH_DROPPED":
            native_count = max(1, int(flag.get("native_tool_calls_count") or 1))
            provider_response_hash = str(flag.get("provider_response_hash") or "").strip()
            break
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=str(request.run_id or turn_id),
        task_id=str(request.task_id or ""),
        turn_id=turn_id,
        role=str(getattr(profile, "role_id", "") or role or ""),
        provider_response_hash=provider_response_hash,
        native_tool_calls_count=native_count,
        decoded_tool_calls_count=0,
        dispatched_tool_calls_count=0,
        receipts=[],
        dispatch_status="dropped",
        failure_class="TOOL_DISPATCH_DROPPED",
        reason=reason,
    )
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=str(request.run_id or turn_id),
            event={
                "event_type": "tool_call_lifecycle",
                "stage": "director_tool_dispatch",
                "task_id": str(request.task_id or ""),
                "run_id": str(request.run_id or turn_id),
                "tool_call_lifecycle_receipt": lifecycle.to_dict(),
                "job_token": {
                    "run_id": str(request.run_id or turn_id),
                    "task_id": str(request.task_id or ""),
                    "project_id": str(request.task_id or "unknown"),
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )
    _append_director_task_boundary_verdict(
        role=role,
        workspace=workspace,
        task_id=str(request.task_id or ""),
        run_id=str(request.run_id or turn_id),
        context_override=getattr(request, "context_override", None),
        tool_results=[],
        tool_dispatch={
            "status": "dropped",
            "dropped": True,
            "native_tool_calls_count": native_count,
            "provider_response_hash": provider_response_hash,
        },
        evidence_refs=[str(error_metadata.get("context_snapshot_ref") or "").strip()],
    )


async def execute_transaction_kernel_turn(
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    system_prompt: str,
    fingerprint: Any,
    observer_run_id: str,
    response_schema: type | None,
) -> RoleTurnResult:
    """Execute a single turn via TransactionKernel and map to RoleTurnResult."""
    from polaris.cells.roles.kernel.internal.transaction.tool_surface import plan_transaction_tool_surface
    from polaris.cells.roles.kernel.public.service import RoleContextGateway

    tk = create_transaction_kernel(kernel, role, profile, request)
    turn_id = _resolve_transaction_turn_id(request, observer_run_id)

    controller = ToolLoopController.from_request(request=request, profile=profile)
    context_request = controller.build_context_request()
    context_gateway = RoleContextGateway(
        profile,
        kernel.workspace,
        config=kernel._build_context_gateway_config(role, profile, request),
    )
    # ADR-0090 I4.3: gateway budgets AND prepends the role system prompt — no
    # second projection pass.
    context_result = await context_gateway.build_context(context_request, system_prompt=system_prompt)
    messages: list[dict[str, Any]] = list(context_result.messages)
    messages = _ensure_context_delivery_mode_marker(
        messages,
        getattr(request, "context_override", None),
        getattr(request, "message", None),
    )
    messages = _ensure_platform_tool_contract_metadata(
        messages,
        getattr(request, "context_override", None),
    )
    if os.getenv("KERNELONE_DELIVERY_MODE_TRACE") == "1":
        context_override = getattr(request, "context_override", None)
        logger.warning(
            "delivery-mode-kernel-trace: role=%s request_marker=%s context_materialize=%s latest_marker=%s "
            "latest_user_preview=%r",
            role,
            _text_requests_materialize_delivery(getattr(request, "message", None)),
            _context_requests_materialize_delivery(context_override),
            _text_requests_materialize_delivery(_latest_user_content_preview(messages)),
            _latest_user_content_preview(messages),
        )

    _co_dbg = getattr(request, "context_override", None)
    logger.info(
        "PRONG_A_TRACE: ctx_override_dict=%s has_construction_step=%s keys=%s",
        isinstance(_co_dbg, dict),
        isinstance(_co_dbg, dict) and isinstance(_co_dbg.get("construction_step"), dict),
        list(_co_dbg.keys())[:14] if isinstance(_co_dbg, dict) else None,
    )
    tool_surface = plan_transaction_tool_surface(
        role=role,
        profile=profile,
        request=request,
        context_result=context_result,
        messages=messages,
        workspace=str(request.workspace or kernel.workspace or "."),
        mode="turn",
    )
    tool_definitions = tool_surface.tool_definitions
    runtime_tool_policy_audit = tool_surface.runtime_tool_policy_audit
    tool_filter_audit = tool_surface.tool_filter_audit
    if tool_surface.conflict_error:
        try:
            context_gateway.record_projection_outcome(
                success=False,
                tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug("Projection outcome feedback failed after tool-filter conflict", exc_info=True)
        filter_error_metadata: dict[str, Any] = {"tool_filter_audit": tool_filter_audit or {}}
        if profile.provider_id:
            filter_error_metadata["provider_id"] = str(profile.provider_id).strip()
        if profile.model:
            filter_error_metadata["model"] = str(profile.model).strip()
        return RoleTurnResult(
            content="",
            error=tool_surface.conflict_error,
            is_complete=False,
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
            execution_stats={
                "duration_ms": 0,
                "llm_calls": 0,
                "tool_calls": 0,
                "transaction_kernel": True,
                "tool_filter_blocked": True,
                "tool_filter_status": "conflict",
                **runtime_tool_policy_audit,
            },
            metadata=filter_error_metadata,
        )

    try:
        tk_result = await tk.execute(
            turn_id,
            messages,
            tool_definitions,
            tool_choice_override=_forced_tool_choice_override(getattr(request, "context_override", None)),
        )
    except Exception as exc:
        logger.exception("TransactionKernel execute failed: turn_id=%s", turn_id)
        try:
            context_gateway.record_projection_outcome(
                success=False,
                tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug("Projection outcome feedback failed after TransactionKernel error", exc_info=True)
        error_metadata = _llm_metadata_from_ledger_on_error(
            getattr(exc, "turn_ledger", None),
            messages=messages,
            tool_definitions=tool_definitions,
        )
        if bool(error_metadata.get("tool_dispatch_dropped")):
            try:
                _append_tool_dispatch_dropped_control_plane_events(
                    role=role,
                    workspace=str(request.workspace or kernel.workspace or "."),
                    profile=profile,
                    request=request,
                    turn_id=turn_id,
                    error_metadata=error_metadata,
                    reason=str(exc),
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.debug("failed to append tool_dispatch_dropped ledger event", exc_info=True)
        if profile.provider_id:
            error_metadata["provider_id"] = str(profile.provider_id).strip()
        if profile.model:
            error_metadata["model"] = str(profile.model).strip()
        if tool_filter_audit is not None:
            error_metadata["tool_filter_audit"] = tool_filter_audit
        return RoleTurnResult(
            content="",
            error=f"TransactionKernel execution failed: {exc}",
            is_complete=False,
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
            metadata=error_metadata,
        )

    kind = tk_result.get("kind", "final_answer")
    visible_content = tk_result.get("visible_content", "")
    thinking_text: str | None = None
    if visible_content:
        parsed = kernel._get_output_parser().parse_thinking(visible_content)
        visible_content = str(parsed.clean_content or "")
        thinking_text = parsed.thinking
    batch_receipt = tk_result.get("batch_receipt")
    normalized_batch_receipt = dict(batch_receipt) if isinstance(batch_receipt, dict) else None
    finalization = tk_result.get("finalization")
    workflow_context = tk_result.get("workflow_context")
    metrics = tk_result.get("metrics", {})

    # Pull the ledger from the TransactionKernel result so it can be committed
    # into the ContextOS snapshot, eliminating the parallel TurnLedger state.
    ledger = tk_result.get("ledger")

    # Map tool calls/results from batch receipt
    tool_calls = _tool_calls_from_batch_receipt(normalized_batch_receipt)
    tool_results = _tool_results_from_batch_receipt(normalized_batch_receipt)

    # Handle structured output if response_schema was requested
    structured_output: dict[str, Any] | None = None
    if response_schema is not None and visible_content:
        try:
            candidate = kernel._get_output_parser().extract_json(visible_content)
            if candidate is not None:
                validated = response_schema(**candidate)
                structured_output = validated.model_dump()
        except (RuntimeError, ValueError):
            structured_output = None

    execution_stats = {
        "duration_ms": metrics.get("duration_ms", 0),
        "llm_calls": metrics.get("llm_calls", 0),
        "tool_calls": metrics.get("tool_calls", 0),
        "transaction_kernel": True,
        **runtime_tool_policy_audit,
    }

    metadata: dict[str, Any] = {}
    # Propagate provider/model identity from the role profile so downstream
    # evidence extractors (e.g. factory CE _ce_extract_llm_evidence) can
    # resolve the real provider and model instead of defaulting to "unknown".
    if profile.provider_id:
        metadata["provider_id"] = str(profile.provider_id).strip()
    if profile.model:
        metadata["model"] = str(profile.model).strip()
    if tool_filter_audit is not None:
        metadata["tool_filter_audit"] = tool_filter_audit
    context_os_audit_summary = summarize_context_os_audit_from_ledger(ledger)
    if context_os_audit_summary:
        metadata["context_os_audit"] = context_os_audit_summary
    llm_response_metadata = tk_result.get("llm_response_metadata")
    if isinstance(llm_response_metadata, dict):
        for key in (
            "final_request_context_audit",
            "context_snapshot_ref",
            "context_snapshot_degraded",
            "context_snapshot_degraded_reason",
            "context_tokens_after",
            "contextTokens",
            "usage",
            "usage_source",
        ):
            if key in llm_response_metadata and key not in metadata:
                value = llm_response_metadata.get(key)
                metadata[key] = dict(value) if isinstance(value, dict) else value
        if "context_os_audit" in llm_response_metadata and "context_os_audit" not in metadata:
            raw_context_os_audit = llm_response_metadata.get("context_os_audit")
            metadata["context_os_audit"] = (
                dict(raw_context_os_audit) if isinstance(raw_context_os_audit, dict) else raw_context_os_audit
            )
    if kind == "handoff_workflow" and workflow_context is not None:
        handoff_pack = build_context_handoff_pack(kernel, tk_result, role, request)
        metadata["handoff_pack"] = handoff_pack.to_dict()
        metadata["transaction_kind"] = "handoff_workflow"

    error_msg: str | None = None
    is_complete = True
    if kind == "ask_user" and isinstance(finalization, dict):
        # SUSPENDED state: model needs user clarification. Stream callers
        # consume this as an error event so orchestration can retry or pause.
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

    final_thinking = thinking_text
    if final_thinking is None and isinstance(finalization, dict):
        final_thinking = finalization.get("final_visible_message")

    try:
        metadata["projection_adaptive_weights_after_turn"] = context_gateway.record_projection_outcome(
            success=bool(is_complete and not error_msg),
            tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        logger.debug("Projection outcome feedback failed", exc_info=True)

    # Build turn history and events metadata for ContextOS persistence
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
    if not bool(metadata.get("needs_followup_workflow")):
        try:
            _append_director_task_boundary_verdict(
                role=role,
                workspace=str(request.workspace or kernel.workspace or "."),
                task_id=str(request.task_id or ""),
                run_id=str(request.run_id or turn_id),
                context_override=getattr(request, "context_override", None),
                tool_results=tool_results,
                evidence_refs=[str(metadata.get("context_snapshot_ref") or "").strip()],
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug("failed to append director task boundary verdict", exc_info=True)
    if bool(metadata.get("needs_followup_workflow")):
        try:
            from polaris.cells.control_plane.run_ledger.public import (
                AppendRunLedgerEventCommandV1,
                append_run_ledger_event,
                build_deferred_followup_task_boundary_verdict,
            )

            verdict = build_deferred_followup_task_boundary_verdict(
                task_id=str(request.task_id or ""),
                run_id=str(request.run_id or turn_id),
                reason=str(metadata.get("workflow_reason") or error_msg or "needs_followup_workflow"),
            ).to_dict()
            append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=str(request.workspace or kernel.workspace or "."),
                    run_id=str(request.run_id or turn_id),
                    event={
                        "event_type": "task_boundary_verdict",
                        "stage": "task_boundary",
                        "task_id": str(request.task_id or ""),
                        "run_id": str(request.run_id or turn_id),
                        "task_boundary_verdict": verdict,
                        "job_token": {
                            "run_id": str(request.run_id or turn_id),
                            "task_id": str(request.task_id or ""),
                            "project_id": str(request.task_id or "unknown"),
                            "capability_audit": {"ok": True, "issues": []},
                            "gate_policy": {},
                        },
                    },
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            logger.debug("failed to append deferred follow-up task boundary verdict", exc_info=True)

    return RoleTurnResult(
        content=visible_content,
        thinking=final_thinking,
        structured_output=structured_output,
        tool_calls=tool_calls,
        tool_results=tool_results,
        batch_receipt=normalized_batch_receipt,
        profile_version=profile.version,
        prompt_fingerprint=fingerprint,
        tool_policy_id=profile.tool_policy.policy_id,
        error=error_msg,
        is_complete=is_complete,
        execution_stats=execution_stats,
        turn_history=turn_history,
        turn_events_metadata=turn_events_metadata,
        metadata=metadata,
    )


async def execute_transaction_kernel_stream(
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    system_prompt: str,
    fingerprint: Any,
    stream_run_id: str,
    uep_publisher: UEPEventPublisher,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream execution via TransactionKernel."""
    from polaris.cells.roles.kernel.internal.transaction.tool_surface import plan_transaction_tool_surface
    from polaris.cells.roles.kernel.public.service import RoleContextGateway
    from polaris.cells.roles.kernel.public.turn_events import (
        CompletionEvent,
        ContentChunkEvent,
        ErrorEvent,
        FinalizationEvent,
        ToolBatchEvent,
        TurnPhaseEvent,
    )

    tk = create_transaction_kernel(kernel, role, profile, request)
    turn_id = str(request.run_id or stream_run_id or uuid.uuid4().hex[:12])

    controller = ToolLoopController.from_request(request=request, profile=profile)
    context_request = controller.build_context_request()
    context_gateway = RoleContextGateway(
        profile,
        kernel.workspace,
        config=kernel._build_context_gateway_config(role, profile, request),
    )
    # ADR-0090 I4.3: gateway budgets AND prepends the role system prompt — no
    # second projection pass.
    context_result = await context_gateway.build_context(context_request, system_prompt=system_prompt)
    messages: list[dict[str, Any]] = list(context_result.messages)
    messages = _ensure_platform_tool_contract_metadata(
        messages,
        getattr(request, "context_override", None),
    )

    tool_surface = plan_transaction_tool_surface(
        role=role,
        profile=profile,
        request=request,
        context_result=context_result,
        messages=messages,
        workspace=str(request.workspace or kernel.workspace or "."),
        mode="stream",
    )
    tool_definitions = tool_surface.tool_definitions
    runtime_tool_policy_audit = tool_surface.runtime_tool_policy_audit
    tool_filter_audit = tool_surface.tool_filter_audit
    if tool_surface.conflict_error:
        try:
            context_gateway.record_projection_outcome(
                success=False,
                tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug("Projection outcome feedback failed after stream tool-filter conflict", exc_info=True)
        error_event: dict[str, Any] = {
            "type": "error",
            "error": tool_surface.conflict_error,
            "error_type": "tool_schema_filter_conflict",
            "turn_id": turn_id,
            "metadata": {"tool_filter_audit": tool_filter_audit or {}},
        }
        await uep_publisher.publish_stream_event(
            workspace=kernel.workspace or os.getcwd(),
            run_id=stream_run_id,
            role=role,
            event_type="error",
            payload=error_event,
        )
        yield error_event
        return

    accumulated_content: list[str] = []
    accumulated_thinking: list[str] = []
    stream_tool_calls: list[dict[str, Any]] = []
    stream_tool_results: list[dict[str, Any]] = []

    async def _iter_transaction_stream_events():
        try:
            async for stream_event in tk.execute_stream(
                turn_id,
                messages,
                tool_definitions,
                tool_choice_override=_forced_tool_choice_override(getattr(request, "context_override", None)),
            ):
                yield stream_event
        except Exception as exc:
            try:
                context_gateway.record_projection_outcome(
                    success=False,
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after stream TransactionKernel error", exc_info=True)
            error_metadata = _llm_metadata_from_ledger_on_error(
                getattr(exc, "turn_ledger", None),
                messages=messages,
                tool_definitions=tool_definitions,
            )
            if bool(error_metadata.get("tool_dispatch_dropped")):
                try:
                    _append_tool_dispatch_dropped_control_plane_events(
                        role=role,
                        workspace=str(request.workspace or kernel.workspace or "."),
                        profile=profile,
                        request=request,
                        turn_id=turn_id,
                        error_metadata=error_metadata,
                        reason=str(exc),
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.debug("failed to append stream tool_dispatch_dropped ledger event", exc_info=True)
            raise

    async for event in _iter_transaction_stream_events():
        event_dict: dict[str, Any]
        if isinstance(event, TurnPhaseEvent):
            event_dict = {
                "type": event.phase,
                "turn_id": event.turn_id,
                "metadata": dict(event.metadata),
            }
        elif isinstance(event, ContentChunkEvent):
            if event.is_thinking:
                accumulated_thinking.append(event.chunk)
                event_dict = {
                    "type": "thinking_chunk",
                    "content": event.chunk,
                    "turn_id": event.turn_id,
                }
            else:
                if getattr(event, "is_finalization", False):
                    accumulated_content = [event.chunk]
                else:
                    accumulated_content.append(event.chunk)
                event_dict = {
                    "type": "content_chunk",
                    "content": event.chunk,
                    "turn_id": event.turn_id,
                }
        elif isinstance(event, ToolBatchEvent):
            arguments = dict(event.arguments) if isinstance(event.arguments, dict) else {}
            if event.status == "started":
                stream_tool_calls.append(
                    {
                        "tool": event.tool_name,
                        "args": arguments,
                        "call_id": event.call_id,
                    }
                )
            else:
                stream_tool_results.append(
                    {
                        "tool": event.tool_name,
                        "result": event.result,
                        "success": event.status == "success",
                        "call_id": event.call_id,
                    }
                )
            event_dict = {
                "type": "tool_result" if event.status in ("success", "error") else "tool_call",
                "tool": event.tool_name,
                "call_id": event.call_id,
                "status": event.status,
                "progress": event.progress,
                "turn_id": event.turn_id,
                "args": arguments,
                "result": event.result,
                "error": event.error,
            }
        elif isinstance(event, FinalizationEvent):
            continue
        elif isinstance(event, CompletionEvent):
            final_content = "".join(accumulated_content)
            final_thinking = "".join(accumulated_thinking) or None
            completion_batch_receipt = (
                dict(event.batch_receipt) if isinstance(getattr(event, "batch_receipt", None), dict) else None
            )
            completion_tool_calls = _tool_calls_from_batch_receipt(completion_batch_receipt) or stream_tool_calls
            completion_tool_results = _tool_results_from_batch_receipt(completion_batch_receipt) or stream_tool_results
            # Failed / suspended completions are surfaced as error events for
            # stream consumers.
            if event.status in ("failed", "suspended"):
                try:
                    context_gateway.record_projection_outcome(
                        success=False,
                        tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                    )
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    logger.debug("Projection outcome feedback failed after stream failure", exc_info=True)
                event_dict = {
                    "type": "error",
                    "error": event.error or "execution_failed",
                    "error_type": "stream_execution_failed",
                    "turn_id": event.turn_id,
                }
                await uep_publisher.publish_stream_event(
                    workspace=kernel.workspace or os.getcwd(),
                    run_id=stream_run_id,
                    role=role,
                    event_type="error",
                    payload=event_dict,
                )
                yield event_dict
                return
            event_dict = {
                "type": "complete",
                "status": event.status,
                "content": final_content,
                "thinking": final_thinking,
                "duration_ms": event.duration_ms,
                "llm_calls": event.llm_calls,
                "tool_calls": event.tool_calls,
                "turn_id": event.turn_id,
            }
            if event.monitoring:
                event_dict["monitoring"] = dict(event.monitoring)
            result_metadata: dict[str, Any] = {}
            # Propagate provider/model identity from the role profile for
            # downstream evidence extractors.
            if profile.provider_id:
                result_metadata["provider_id"] = str(profile.provider_id).strip()
            if profile.model:
                result_metadata["model"] = str(profile.model).strip()
            if tool_filter_audit is not None:
                result_metadata["tool_filter_audit"] = tool_filter_audit
            monitoring_payload = event.monitoring if isinstance(event.monitoring, dict) else {}
            context_os_audit = monitoring_payload.get("context_os_audit")
            if isinstance(context_os_audit, dict):
                result_metadata["context_os_audit"] = dict(context_os_audit)
                event_dict["metadata"] = dict(result_metadata)
            try:
                result_metadata["projection_adaptive_weights_after_turn"] = context_gateway.record_projection_outcome(
                    success=event.status == "success",
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
                event_dict["metadata"] = dict(result_metadata)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after stream completion", exc_info=True)
            # Include RoleTurnResult so that stream consumers can persist turn state
            turn_history, turn_events_metadata = _build_turn_history_and_events(
                turn_id=turn_id,
                request=request,
                visible_content=final_content,
                thinking=final_thinking,
                tool_results=completion_tool_results,
            )
            from polaris.cells.roles.profile.public.service import RoleTurnResult

            event_dict["result"] = RoleTurnResult(
                content=final_content,
                thinking=final_thinking,
                tool_calls=completion_tool_calls,
                tool_results=completion_tool_results,
                batch_receipt=completion_batch_receipt,
                profile_version=profile.version,
                prompt_fingerprint=fingerprint,
                tool_policy_id=profile.tool_policy.policy_id,
                is_complete=True,
                execution_stats={
                    "duration_ms": event.duration_ms,
                    "llm_calls": event.llm_calls,
                    "tool_calls": event.tool_calls,
                    "transaction_kernel": True,
                    **runtime_tool_policy_audit,
                },
                turn_history=turn_history,
                turn_events_metadata=turn_events_metadata,
                metadata=result_metadata,
            )
            try:
                _append_director_task_boundary_verdict(
                    role=role,
                    workspace=str(request.workspace or kernel.workspace or "."),
                    task_id=str(request.task_id or ""),
                    run_id=str(request.run_id or turn_id),
                    context_override=getattr(request, "context_override", None),
                    tool_results=completion_tool_results,
                    evidence_refs=[str(result_metadata.get("context_snapshot_ref") or "").strip()],
                )
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.debug("failed to append stream director task boundary verdict", exc_info=True)
        elif isinstance(event, ErrorEvent):
            try:
                context_gateway.record_projection_outcome(
                    success=False,
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after stream error", exc_info=True)
            event_dict = {
                "type": "error",
                "error": event.message,
                "error_type": event.error_type,
                "turn_id": event.turn_id,
            }
        else:
            continue

        await uep_publisher.publish_stream_event(
            workspace=kernel.workspace or os.getcwd(),
            run_id=stream_run_id,
            role=role,
            event_type=event_dict.get("type", "unknown"),
            payload=event_dict,
        )
        yield event_dict
