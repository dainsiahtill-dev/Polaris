"""TransactionKernel invocation setup.

This owner prepares the ContextOS messages and provider tool surface needed to
call TransactionKernel. It deliberately does not execute the model, dispatch
tools, commit snapshots, or build RoleTurnResult objects.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from polaris.cells.roles.kernel.internal.kernel.context_gateway_config_builder import build_context_gateway_config
from polaris.cells.roles.kernel.internal.kernel.delivery_mode import (
    _context_requests_materialize_delivery,
    _ensure_context_delivery_mode_marker,
    _ensure_platform_tool_contract_metadata,
    _latest_user_content_preview,
    _text_requests_materialize_delivery,
)
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.kernel.internal.transaction.tool_surface import (
    TransactionToolSurfacePlan,
    plan_transaction_tool_surface,
)
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TransactionInvocationSetup:
    """Prepared inputs for one TransactionKernel invocation.

    Attributes:
        context_gateway: Gateway instance used for projection feedback.
        context_result: ContextOS result that produced the provider messages.
        messages: Provider messages after Polaris metadata normalization.
        tool_surface: Provider tool schemas, tool-choice override, and audit
            metadata for this invocation.
    """

    context_gateway: Any
    context_result: Any
    messages: list[dict[str, Any]]
    tool_surface: TransactionToolSurfacePlan


async def build_transaction_invocation_setup(
    *,
    kernel: RoleExecutionKernel,
    role: str,
    profile: RoleProfile,
    request: RoleTurnRequest,
    system_prompt: str,
    mode: Literal["turn", "stream"],
    restore_delivery_mode_marker: bool,
    emit_delivery_mode_trace: bool = False,
    emit_prong_trace: bool = False,
) -> TransactionInvocationSetup:
    """Build ContextOS messages and provider tool surface for TransactionKernel.

    Boundary:
        Performs ContextOS context assembly and deterministic prompt metadata
        normalization only. Transaction execution, tool side effects, snapshot
        commit, and result projection remain in their dedicated owners.

    Complexity:
        O(m + t) time and memory where ``m`` is provider message size and ``t``
        is provider tool schema size.
    """

    from polaris.cells.roles.kernel.public.service import RoleContextGateway

    controller = ToolLoopController.from_request(request=request, profile=profile)
    context_request = controller.build_context_request()
    context_gateway = RoleContextGateway(
        profile,
        kernel.workspace,
        config=build_context_gateway_config(kernel.context_gateway_config_factory, role, profile, request),
    )
    # ADR-0090 I4.3: gateway budgets AND prepends the role system prompt - no
    # second projection pass.
    context_result = await context_gateway.build_context(context_request, system_prompt=system_prompt)
    messages: list[dict[str, Any]] = list(context_result.messages)
    if restore_delivery_mode_marker:
        messages = _ensure_context_delivery_mode_marker(
            messages,
            getattr(request, "context_override", None),
            getattr(request, "message", None),
        )
    messages = _ensure_platform_tool_contract_metadata(
        messages,
        getattr(request, "context_override", None),
    )
    if emit_delivery_mode_trace and os.getenv("KERNELONE_DELIVERY_MODE_TRACE") == "1":
        _log_delivery_mode_trace(role, request, messages)
    if emit_prong_trace:
        _log_prong_trace(request)

    tool_surface = plan_transaction_tool_surface(
        role=role,
        profile=profile,
        request=request,
        context_result=context_result,
        messages=messages,
        workspace=str(request.workspace or kernel.workspace or "."),
        mode=mode,
    )
    return TransactionInvocationSetup(
        context_gateway=context_gateway,
        context_result=context_result,
        messages=messages,
        tool_surface=tool_surface,
    )


def _log_delivery_mode_trace(role: str, request: RoleTurnRequest, messages: list[dict[str, Any]]) -> None:
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


def _log_prong_trace(request: RoleTurnRequest) -> None:
    context_override = getattr(request, "context_override", None)
    logger.info(
        "PRONG_A_TRACE: ctx_override_dict=%s has_construction_step=%s keys=%s",
        isinstance(context_override, dict),
        isinstance(context_override, dict) and isinstance(context_override.get("construction_step"), dict),
        list(context_override.keys())[:14] if isinstance(context_override, dict) else None,
    )
