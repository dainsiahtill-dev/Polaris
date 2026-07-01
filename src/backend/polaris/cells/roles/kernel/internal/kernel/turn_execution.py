"""Single-turn and streaming TransactionKernel execution.

Owns the canonical free-function execution paths consumed by
``RoleExecutionKernel.run`` and ``RoleExecutionKernel.run_stream``. The public
kernel entrypoint calls these functions directly so transaction execution has
one implementation surface.

Behavior notes:
- The turn body and the stream body share ContextOS/tool-surface setup through
  ``kernel.transaction_invocation_setup``. They remain separate adapter
  functions because non-streaming turns commit ContextOS snapshots and return a
  ``RoleTurnResult``, while streaming turns translate typed stream events into
  public stream dictionaries through ``kernel.stream_event_projection``.
- §8 governance note: the embedded weak-Director Prong-A / R7 / Fix-11
  write-vs-edit heuristics live in the planner. They are not deleted here (a
  separate governance pass owns that decision).
- Function-local lazy imports for ContextGateway live in
  ``transaction_invocation_setup`` to keep circular-import avoidance and
  monkeypatch-target ordering in one owner.
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
from polaris.cells.roles.kernel.internal.kernel.output_parser_provider import get_output_parser
from polaris.cells.roles.kernel.internal.kernel.role_result_projection import (
    role_result_metadata_from_profile,
    role_turn_completion_result,
    role_turn_error_result,
    tool_calls_from_batch_receipt,
    tool_results_from_batch_receipt,
)
from polaris.cells.roles.kernel.internal.kernel.stream_event_projection import StreamEventProjector
from polaris.cells.roles.kernel.internal.kernel.task_boundary import append_role_turn_task_boundary_verdict
from polaris.cells.roles.kernel.internal.kernel.tool_dispatch_projection import (
    append_tool_dispatch_dropped_control_plane_events,
    llm_metadata_from_ledger_on_error,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.kernel.transaction_invocation_setup import build_transaction_invocation_setup
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import _resolve_transaction_turn_id
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest, RoleTurnResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.kernelone.events.uep_publisher import UEPEventPublisher

logger = logging.getLogger(__name__)


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
    tk = create_transaction_kernel(kernel, role, profile, request)
    turn_id = _resolve_transaction_turn_id(request, observer_run_id)

    invocation_setup = await build_transaction_invocation_setup(
        kernel=kernel,
        role=role,
        profile=profile,
        request=request,
        system_prompt=system_prompt,
        mode="turn",
        restore_delivery_mode_marker=True,
        emit_delivery_mode_trace=True,
        emit_prong_trace=True,
    )
    context_gateway = invocation_setup.context_gateway
    context_result = invocation_setup.context_result
    messages = invocation_setup.messages
    tool_surface = invocation_setup.tool_surface
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
        return role_turn_error_result(
            error=tool_surface.conflict_error,
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
            profile=profile,
            fingerprint=fingerprint,
        )

    try:
        tk_result = await tk.execute(
            turn_id,
            messages,
            tool_definitions,
            tool_choice_override=tool_surface.tool_choice_override,
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
        error_metadata = llm_metadata_from_ledger_on_error(
            getattr(exc, "turn_ledger", None),
            messages=messages,
            tool_definitions=tool_definitions,
        )
        if bool(error_metadata.get("tool_dispatch_dropped")):
            try:
                append_tool_dispatch_dropped_control_plane_events(
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
        return role_turn_error_result(
            error=f"TransactionKernel execution failed: {exc}",
            metadata=error_metadata,
            profile=profile,
            fingerprint=fingerprint,
        )

    kind = tk_result.get("kind", "final_answer")
    visible_content = tk_result.get("visible_content", "")
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

    # Pull the ledger from the TransactionKernel result so it can be committed
    # into the ContextOS snapshot, eliminating the parallel TurnLedger state.
    ledger = tk_result.get("ledger")

    # Map tool calls/results from batch receipt
    tool_calls = tool_calls_from_batch_receipt(normalized_batch_receipt)
    tool_results = tool_results_from_batch_receipt(normalized_batch_receipt)

    # Handle structured output if response_schema was requested
    structured_output: dict[str, Any] | None = None
    if response_schema is not None and visible_content:
        try:
            candidate = get_output_parser(kernel).extract_json(visible_content)
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
    try:
        append_role_turn_task_boundary_verdict(
            role=role,
            workspace=str(request.workspace or kernel.workspace or "."),
            task_id=str(request.task_id or ""),
            run_id=str(request.run_id or turn_id),
            context_override=getattr(request, "context_override", None),
            tool_results=tool_results,
            needs_followup_workflow=bool(metadata.get("needs_followup_workflow")),
            workflow_reason=str(metadata.get("workflow_reason") or ""),
            error_message=error_msg,
            evidence_refs=[str(metadata.get("context_snapshot_ref") or "").strip()],
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug("failed to append role-turn task boundary verdict", exc_info=True)

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
    tk = create_transaction_kernel(kernel, role, profile, request)
    turn_id = str(request.run_id or stream_run_id or uuid.uuid4().hex[:12])

    invocation_setup = await build_transaction_invocation_setup(
        kernel=kernel,
        role=role,
        profile=profile,
        request=request,
        system_prompt=system_prompt,
        mode="stream",
        restore_delivery_mode_marker=False,
    )
    context_gateway = invocation_setup.context_gateway
    context_result = invocation_setup.context_result
    messages = invocation_setup.messages
    tool_surface = invocation_setup.tool_surface
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

    event_projector = StreamEventProjector(
        kernel=kernel,
        role=role,
        profile=profile,
        request=request,
        fingerprint=fingerprint,
        context_gateway=context_gateway,
        context_result=context_result,
        stream_run_id=stream_run_id,
        uep_publisher=uep_publisher,
        runtime_tool_policy_audit=runtime_tool_policy_audit,
        tool_filter_audit=tool_filter_audit,
    )

    async def _iter_transaction_stream_events():
        try:
            async for stream_event in tk.execute_stream(
                turn_id,
                messages,
                tool_definitions,
                tool_choice_override=tool_surface.tool_choice_override,
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
            error_metadata = llm_metadata_from_ledger_on_error(
                getattr(exc, "turn_ledger", None),
                messages=messages,
                tool_definitions=tool_definitions,
            )
            if bool(error_metadata.get("tool_dispatch_dropped")):
                try:
                    append_tool_dispatch_dropped_control_plane_events(
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
        projection = await event_projector.project(event)
        if projection is None:
            continue
        yield projection.event
        if projection.should_stop:
            return
