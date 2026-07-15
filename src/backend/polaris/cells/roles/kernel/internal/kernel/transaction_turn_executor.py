"""Application service for TransactionKernel-backed role turns.

``RoleExecutionKernel`` is the public API shell; ``TransactionKernel`` is the
single commit point for LLM/tool execution. This module owns only the
application-service boundary between those two layers:

* prepare ContextOS/tool-surface input for a turn;
* call exactly one ``TransactionKernel`` execution method;
* project typed transaction results back into role-runtime contracts.

It deliberately does not perform direct tool, file, command, network, or ledger
side effects. Those stay inside ``TransactionKernel`` and its controlled runtime
ports. The non-streaming and streaming flows instantiate this service instead of
reaching through module-level helper boundaries.

Complexity:
    Each method is O(m + t + e), where ``m`` is prompt/message size, ``t`` is
    tool schema size, and ``e`` is the number of transaction events/results
    observed. Additional memory is O(m + t) for invocation setup and O(1) per
    streamed event.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from polaris.cells.roles.kernel.internal.kernel.transaction_turn_id import (
    _bind_transaction_attempt,
    _require_bound_transaction_attempt,
    _resolve_transaction_turn_id,
    _start_transaction_invocation,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest, RoleTurnResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.kernelone.events.uep_publisher import UEPEventPublisher

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TransactionTurnExecutor:
    """Execute role turns through the canonical ``TransactionKernel`` boundary.

    Boundary:
        The executor is an application service. It may construct invocation
        setup and result projections, but it must not execute tools directly or
        become a second transaction/ledger owner. All execution side effects
        must pass through the ``TransactionKernel`` returned by
        ``create_transaction_kernel``.
    """

    kernel: RoleExecutionKernel

    async def execute_turn(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
        system_prompt: str,
        fingerprint: Any,
        observer_run_id: str,
        response_schema: type | None,
    ) -> RoleTurnResult:
        """Execute one non-streaming role turn via ``TransactionKernel``."""
        _require_bound_transaction_attempt(
            request,
            role=role,
            workspace=self.kernel.workspace,
        )
        tk = create_transaction_kernel(self.kernel, role, profile, request)
        turn_id = _resolve_transaction_turn_id(request, observer_run_id)

        invocation_setup = await build_transaction_invocation_setup(
            kernel=self.kernel,
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
                workspace=str(request.workspace or self.kernel.workspace or "."),
            )

        return build_transaction_turn_completion_result(
            kernel=self.kernel,
            role=role,
            request=request,
            profile=profile,
            fingerprint=fingerprint,
            turn_id=turn_id,
            tk_result=tk_result,
            response_schema=response_schema,
            runtime_tool_policy_audit=runtime_tool_policy_audit,
            tool_filter_audit=tool_filter_audit or {},
            context_gateway=context_gateway,
            context_result=context_result,
        )

    async def execute_stream(
        self,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
        system_prompt: str,
        fingerprint: Any,
        stream_run_id: str,
        uep_publisher: UEPEventPublisher,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute one streaming role turn via ``TransactionKernel``."""
        invocation_id = _start_transaction_invocation(
            request,
            role=role,
            workspace=self.kernel.workspace,
        )
        _bind_transaction_attempt(request, invocation_id=invocation_id, attempt=0)
        _require_bound_transaction_attempt(
            request,
            role=role,
            workspace=self.kernel.workspace,
        )
        tk = create_transaction_kernel(self.kernel, role, profile, request)
        turn_id = _resolve_transaction_turn_id(request, stream_run_id)

        invocation_setup = await build_transaction_invocation_setup(
            kernel=self.kernel,
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
                workspace=self.kernel.workspace,
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
            kernel=self.kernel,
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

        async for event in self._iter_transaction_stream_events(
            tk=tk,
            turn_id=turn_id,
            role=role,
            profile=profile,
            request=request,
            messages=messages,
            tool_definitions=tool_definitions,
            tool_choice_override=tool_surface.tool_choice_override,
            tool_filter_audit=tool_filter_audit or {},
            context_gateway=context_gateway,
            context_result=context_result,
        ):
            projection = await event_projector.project(event)
            if projection is None:
                continue
            yield projection.event
            if projection.should_stop:
                return

    async def _iter_transaction_stream_events(
        self,
        *,
        tk: Any,
        turn_id: str,
        role: str,
        profile: RoleProfile,
        request: RoleTurnRequest,
        messages: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        tool_choice_override: Any,
        tool_filter_audit: dict[str, Any],
        context_gateway: Any,
        context_result: Any,
    ) -> AsyncGenerator[Any, None]:
        try:
            async for stream_event in tk.execute_stream(
                turn_id,
                messages,
                tool_definitions,
                tool_choice_override=tool_choice_override,
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
                workspace=str(request.workspace or self.kernel.workspace or "."),
                projection_reason="stream TransactionKernel error",
                dropped_ledger_reason="failed to append stream tool_dispatch_dropped ledger event",
            )
            raise


__all__ = ["TransactionTurnExecutor"]
