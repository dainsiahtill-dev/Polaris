"""Single-turn and streaming TransactionKernel execution for RoleExecutionKernel.

Holds the bodies of ``RoleExecutionKernel._execute_transaction_kernel_turn`` and
``RoleExecutionKernel._execute_transaction_kernel_stream`` extracted verbatim
(behavior-preserving) into free functions. The class methods become thin
delegating shims.

FROZEN behavior notes (do NOT change):
- The turn body and the stream body share the tool-definitions preamble shape
  but are NOT byte-identical: the turn body additionally emits the
  ``PRONG_A_TRACE`` info log and the ``KERNELONE_DELIVERY_MODE_TRACE`` warning,
  and restores the delivery-mode marker via ``_ensure_context_delivery_mode_marker``.
  This turn-vs-stream asymmetry is intentional and is preserved verbatim here —
  the two functions are kept separate rather than merged behind a shared
  preamble helper, so the PRONG_A_TRACE asymmetry cannot drift.
- §8 governance note: the embedded weak-Director Prong-A / R7 / Fix-11
  write-vs-edit heuristics and their trace logging are moved verbatim. They are
  not deleted here (a separate governance pass owns that decision).
- The function-local lazy imports (tool_helpers / RoleContextGateway /
  turn_events / RoleTurnResult) are preserved verbatim to keep the original
  circular-import-avoidance and monkeypatch-target ordering.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.delivery_mode import (
    _context_requests_materialize_delivery,
    _ensure_context_delivery_mode_marker,
    _ensure_platform_tool_contract_metadata,
    _latest_user_content_preview,
    _text_requests_materialize_delivery,
)
from polaris.cells.roles.kernel.internal.kernel.tool_policy import (
    _apply_forced_transaction_tool_definitions,
)
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
    from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
        build_native_tool_schemas,
        build_tool_filter_audit,
        extract_write_tool_pin_target_files,
        pin_write_tool_file_param_to_targets,
        resolve_from_scratch_write_target,
        resolve_repair_edit_target,
        restrict_tool_definitions_to_edit,
        restrict_tool_definitions_to_write,
        should_use_weak_director_slim_tool_schema,
    )
    from polaris.cells.roles.kernel.public.service import RoleContextGateway

    tk = kernel._create_transaction_kernel(role, profile, request)
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

    tool_definitions = (
        []
        if kernel._tool_contract_requires_no_tools(request) or kernel._request_forces_no_transaction_tools(request)
        else build_native_tool_schemas(profile)
    )
    tool_definitions, runtime_tool_policy_audit = kernel._apply_runtime_tool_policy(
        request=request,
        context_result=context_result,
        tool_definitions=tool_definitions,
    )
    # Fix-11 (live I3-r9/r12): a fission step is single-file by contract —
    # pin write tools' file-param enum to the declared target. Strict guided
    # decoding (named tool forcing) makes a wrong-file write ungenerable;
    # schema-advisory providers still see the strongest possible signal.
    write_pin_targets = extract_write_tool_pin_target_files(getattr(request, "context_override", None))
    if write_pin_targets:
        tool_definitions = pin_write_tool_file_param_to_targets(tool_definitions, write_pin_targets)
    tool_filter_original_definitions: list[dict[str, Any]] | None = None
    tool_filter_reason = ""
    # Prong A (I3-r23): a from-scratch leaf step writes on turn 1. Keep a
    # minimal execution schema so weak Directors still receive schema-backed
    # read/locate tools referenced by the prompt, while mutation gates require
    # the emitted batch to contain a write.
    _co_dbg = getattr(request, "context_override", None)
    logger.info(
        "PRONG_A_TRACE: ctx_override_dict=%s has_construction_step=%s keys=%s",
        isinstance(_co_dbg, dict),
        isinstance(_co_dbg, dict) and isinstance(_co_dbg.get("construction_step"), dict),
        list(_co_dbg.keys())[:14] if isinstance(_co_dbg, dict) else None,
    )
    _from_scratch_target = resolve_from_scratch_write_target(
        getattr(request, "context_override", None), str(request.workspace or kernel.workspace or ".")
    )
    if _from_scratch_target:
        tool_filter_original_definitions = list(tool_definitions)
        tool_filter_reason = "from_scratch_write_target"
        tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
        logger.info(
            "first-turn minimal execution schema for from-scratch leaf step: target=%s",
            _from_scratch_target,
        )
    else:
        # R7 (I3-r28): repair/bounce turn on an EXISTING target edits in place —
        # drop the whole-file rewrite verb so the weak model fixes the named
        # failure instead of rewriting the file smaller (live r28 main.js
        # 5762B->3095B). Mutually exclusive with the from-scratch branch above.
        _repair_target = resolve_repair_edit_target(
            getattr(request, "context_override", None), str(request.workspace or kernel.workspace or ".")
        )
        if _repair_target:
            tool_filter_original_definitions = list(tool_definitions)
            tool_filter_reason = "repair_preserve_edit_target"
            tool_definitions = restrict_tool_definitions_to_edit(tool_definitions)
            logger.info(
                "repair-turn edit-only for existing target: target=%s",
                _repair_target,
            )
        elif should_use_weak_director_slim_tool_schema(
            role=role,
            profile=profile,
            context_override=getattr(request, "context_override", None),
            workspace=str(request.workspace or kernel.workspace or "."),
            tool_definitions=tool_definitions,
        ):
            tool_filter_original_definitions = list(tool_definitions)
            tool_filter_reason = "weak_director_slim_tool_schema"
            tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
            logger.info(
                "weak-director slim tool schema enabled: role=%s model=%s",
                role,
                getattr(profile, "model", ""),
            )
    tool_definitions = _apply_forced_transaction_tool_definitions(
        tool_definitions,
        getattr(request, "context_override", None),
    )
    tool_filter_audit: dict[str, Any] | None = None
    if tool_filter_original_definitions is not None:
        tool_filter_audit = build_tool_filter_audit(
            filter_reason=tool_filter_reason,
            original_tool_definitions=tool_filter_original_definitions,
            filtered_tool_definitions=tool_definitions,
            messages=messages,
        )
        if tool_filter_audit.get("status") == "conflict":
            try:
                context_gateway.record_projection_outcome(
                    success=False,
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after tool-filter conflict", exc_info=True)
            filter_error_metadata: dict[str, Any] = {"tool_filter_audit": tool_filter_audit}
            if profile.provider_id:
                filter_error_metadata["provider_id"] = str(profile.provider_id).strip()
            if profile.model:
                filter_error_metadata["model"] = str(profile.model).strip()
            removed_required = ", ".join(tool_filter_audit.get("removed_prompt_required_tool_names") or [])
            return RoleTurnResult(
                content="",
                error=f"Tool schema filter conflict: removed prompt-required tools: {removed_required}",
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
        tk_result = await tk.execute(turn_id, messages, tool_definitions)
    except Exception as exc:
        logger.exception("TransactionKernel execute failed: turn_id=%s", turn_id)
        try:
            context_gateway.record_projection_outcome(
                success=False,
                tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logger.debug("Projection outcome feedback failed after TransactionKernel error", exc_info=True)
        return RoleTurnResult(
            content="",
            error=f"TransactionKernel execution failed: {exc}",
            is_complete=False,
            profile_version=profile.version,
            prompt_fingerprint=fingerprint,
            tool_policy_id=profile.tool_policy.policy_id,
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
        handoff_pack = kernel._build_context_handoff_pack(tk_result, role, request)
        metadata["handoff_pack"] = handoff_pack.to_dict()
        metadata["transaction_kind"] = "handoff_workflow"

    error_msg: str | None = None
    is_complete = True
    if kind == "ask_user" and isinstance(finalization, dict):
        # SUSPENDED state: model needs user clarification. Map to error for backward
        # compat in the legacy kernel core facade (callers check error to retry).
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
    turn_history, turn_events_metadata = kernel._build_turn_history_and_events(
        turn_id=turn_id,
        request=request,
        visible_content=visible_content,
        thinking=final_thinking,
        tool_results=tool_results,
    )

    kernel._commit_turn_to_snapshot(
        request=request,
        turn_id=turn_id,
        turn_history=turn_history,
        turn_events_metadata=turn_events_metadata,
        tool_results=tool_results,
        ledger=ledger,
    )

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
    """Stream execution via TransactionKernel (compatibility shim)."""
    from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import (
        build_native_tool_schemas,
        build_tool_filter_audit,
        extract_declared_step_target_files,
        pin_write_tool_file_param_to_targets,
        resolve_from_scratch_write_target,
        resolve_repair_edit_target,
        restrict_tool_definitions_to_edit,
        restrict_tool_definitions_to_write,
        should_use_weak_director_slim_tool_schema,
    )
    from polaris.cells.roles.kernel.public.service import RoleContextGateway
    from polaris.cells.roles.kernel.public.turn_events import (
        CompletionEvent,
        ContentChunkEvent,
        ErrorEvent,
        FinalizationEvent,
        ToolBatchEvent,
        TurnPhaseEvent,
    )

    tk = kernel._create_transaction_kernel(role, profile, request)
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

    tool_definitions = (
        []
        if kernel._tool_contract_requires_no_tools(request) or kernel._request_forces_no_transaction_tools(request)
        else build_native_tool_schemas(profile)
    )
    tool_definitions, runtime_tool_policy_audit = kernel._apply_runtime_tool_policy(
        request=request,
        context_result=context_result,
        tool_definitions=tool_definitions,
    )
    # Fix-11 (live I3-r9/r12): a fission step is single-file by contract —
    # pin write tools' file-param enum to the declared target. Strict guided
    # decoding (named tool forcing) makes a wrong-file write ungenerable;
    # schema-advisory providers still see the strongest possible signal.
    declared_step_targets = extract_declared_step_target_files(getattr(request, "context_override", None))
    if declared_step_targets:
        tool_definitions = pin_write_tool_file_param_to_targets(tool_definitions, declared_step_targets)
    tool_filter_original_definitions: list[dict[str, Any]] | None = None
    tool_filter_reason = ""
    # Prong A (I3-r23): from-scratch leaf -> minimal execution schema on turn 1.
    _from_scratch_target = resolve_from_scratch_write_target(
        getattr(request, "context_override", None), str(request.workspace or kernel.workspace or ".")
    )
    if _from_scratch_target:
        tool_filter_original_definitions = list(tool_definitions)
        tool_filter_reason = "from_scratch_write_target"
        tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
        logger.info(
            "first-turn minimal execution schema for from-scratch leaf step: target=%s",
            _from_scratch_target,
        )
    else:
        # R7 (I3-r28): repair/bounce turn on an EXISTING target edits in place —
        # drop the whole-file rewrite verb so the weak model fixes the named
        # failure instead of rewriting the file smaller (live r28 main.js
        # 5762B->3095B). Mutually exclusive with the from-scratch branch above.
        _repair_target = resolve_repair_edit_target(
            getattr(request, "context_override", None), str(request.workspace or kernel.workspace or ".")
        )
        if _repair_target:
            tool_filter_original_definitions = list(tool_definitions)
            tool_filter_reason = "repair_preserve_edit_target"
            tool_definitions = restrict_tool_definitions_to_edit(tool_definitions)
            logger.info(
                "repair-turn edit-only for existing target: target=%s",
                _repair_target,
            )
        elif should_use_weak_director_slim_tool_schema(
            role=role,
            profile=profile,
            context_override=getattr(request, "context_override", None),
            workspace=str(request.workspace or kernel.workspace or "."),
            tool_definitions=tool_definitions,
        ):
            tool_filter_original_definitions = list(tool_definitions)
            tool_filter_reason = "weak_director_slim_tool_schema"
            tool_definitions = restrict_tool_definitions_to_write(tool_definitions)
            logger.info(
                "weak-director slim tool schema enabled: role=%s model=%s",
                role,
                getattr(profile, "model", ""),
            )
    tool_definitions = _apply_forced_transaction_tool_definitions(
        tool_definitions,
        getattr(request, "context_override", None),
    )
    tool_filter_audit: dict[str, Any] | None = None
    if tool_filter_original_definitions is not None:
        tool_filter_audit = build_tool_filter_audit(
            filter_reason=tool_filter_reason,
            original_tool_definitions=tool_filter_original_definitions,
            filtered_tool_definitions=tool_definitions,
            messages=messages,
        )
        if tool_filter_audit.get("status") == "conflict":
            try:
                context_gateway.record_projection_outcome(
                    success=False,
                    tokens_used=int(getattr(context_result, "token_estimate", 0) or 0),
                )
            except (AttributeError, RuntimeError, TypeError, ValueError):
                logger.debug("Projection outcome feedback failed after stream tool-filter conflict", exc_info=True)
            removed_required = ", ".join(tool_filter_audit.get("removed_prompt_required_tool_names") or [])
            error_event: dict[str, Any] = {
                "type": "error",
                "error": f"Tool schema filter conflict: removed prompt-required tools: {removed_required}",
                "error_type": "tool_schema_filter_conflict",
                "turn_id": turn_id,
                "metadata": {"tool_filter_audit": tool_filter_audit},
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
    async for event in tk.execute_stream(turn_id, messages, tool_definitions):
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
            # Backward compat: failed / suspended completions map to error events
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
            turn_history, turn_events_metadata = kernel._build_turn_history_and_events(
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
