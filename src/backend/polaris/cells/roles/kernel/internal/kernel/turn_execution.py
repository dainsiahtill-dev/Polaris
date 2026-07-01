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
import uuid
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.stream_event_projection import StreamEventProjector
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.kernel.transaction_failure_projection import (
    build_tool_filter_conflict_result,
    build_transaction_exception_metadata,
    build_transaction_execution_error_result,
    publish_tool_filter_conflict_stream_event,
)
from polaris.cells.roles.kernel.internal.kernel.transaction_invocation_setup import build_transaction_invocation_setup
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_completion import (
    build_transaction_turn_completion_result,
)
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
        return build_tool_filter_conflict_result(
            profile=profile,
            fingerprint=fingerprint,
            tool_surface=tool_surface,
            context_gateway=context_gateway,
            context_result=context_result,
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
        return build_transaction_execution_error_result(
            exc=exc,
            role=role,
            profile=profile,
            request=request,
            fingerprint=fingerprint,
            turn_id=turn_id,
            messages=messages,
            tool_definitions=tool_definitions,
            tool_filter_audit=tool_filter_audit,
            context_gateway=context_gateway,
            context_result=context_result,
            workspace=str(request.workspace or kernel.workspace or "."),
        )

    return build_transaction_turn_completion_result(
        kernel=kernel,
        role=role,
        request=request,
        profile=profile,
        fingerprint=fingerprint,
        turn_id=turn_id,
        tk_result=tk_result,
        response_schema=response_schema,
        runtime_tool_policy_audit=runtime_tool_policy_audit,
        tool_filter_audit=tool_filter_audit,
        context_gateway=context_gateway,
        context_result=context_result,
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
        yield await publish_tool_filter_conflict_stream_event(
            workspace=kernel.workspace,
            role=role,
            stream_run_id=stream_run_id,
            turn_id=turn_id,
            tool_surface=tool_surface,
            context_gateway=context_gateway,
            context_result=context_result,
            uep_publisher=uep_publisher,
        )
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
            build_transaction_exception_metadata(
                exc=exc,
                role=role,
                profile=profile,
                request=request,
                turn_id=turn_id,
                messages=messages,
                tool_definitions=tool_definitions,
                tool_filter_audit=tool_filter_audit,
                context_gateway=context_gateway,
                context_result=context_result,
                workspace=str(request.workspace or kernel.workspace or "."),
                projection_reason="stream TransactionKernel error",
                dropped_ledger_reason="failed to append stream tool_dispatch_dropped ledger event",
            )
            raise

    async for event in _iter_transaction_stream_events():
        projection = await event_projector.project(event)
        if projection is None:
            continue
        yield projection.event
        if projection.should_stop:
            return
