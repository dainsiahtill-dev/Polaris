"""Single-tool execution owner for Role Kernel tool runtime calls."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.kernel.internal.kernel.tool_gateway_turn_key import (
    resolve_explicit_turn_key,
    resolve_tool_gateway_turn_key,
)
from polaris.cells.roles.kernel.internal.kernel.tool_policy import (
    _cognitive_runtime_blocked_tools,
    _normalize_tool_policy_name,
)
from polaris.cells.roles.profile.public.service import RoleTurnRequest

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)


def resolve_authorized_tool_gateway(
    kernel: RoleExecutionKernel,
    *,
    profile: RoleProfile,
    request: RoleTurnRequest,
) -> object:
    """Return the same per-turn gateway used by physical tool execution."""

    from polaris.cells.roles.kernel.internal.kernel.tool_executor import (
        KernelToolExecutor,
    )

    executor = KernelToolExecutor(kernel, kernel.workspace)
    current_turn_id = resolve_tool_gateway_turn_key(request)
    if (
        kernel._cached_tool_gateway is not None
        and kernel._cached_gateway_profile is profile
        and current_turn_id == kernel._cached_gateway_turn_id
    ):
        return kernel._cached_tool_gateway
    _reset_cached_gateway(kernel)
    gateway = executor.create_gateway(
        profile=profile,
        request=request,
        tool_gateway=kernel._tool_gateway,
    )
    kernel._cached_tool_gateway = gateway
    kernel._cached_gateway_profile = profile
    kernel._cached_gateway_turn_id = current_turn_id
    return gateway


async def execute_single_tool(
    kernel: RoleExecutionKernel,
    *,
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one tool through the authorized tool path."""
    request_for_policy = context.get("request") if context else None
    if request_for_policy is not None:
        cognitive_blocked_tools = _cognitive_runtime_blocked_tools(cast(RoleTurnRequest, request_for_policy))
        if _normalize_tool_policy_name(tool_name) in cognitive_blocked_tools:
            from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

            raise ToolAuthorizationError(f"Cognitive Runtime blocked tool '{tool_name}'")

    if kernel._injected_tool_executor is not None:
        # Even injected executors must go through authorization so counting,
        # capability scope, path guards, and FailureBudget stay authoritative.
        profile = context.get("profile") if context else None
        if profile is not None:
            from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

            executor = KernelToolExecutor(kernel, kernel.workspace)
            request = context.get("request") if context else None
            if request is None:
                request = RoleTurnRequest(message="")

            current_turn_id = resolve_tool_gateway_turn_key(request)
            if (
                kernel._cached_tool_gateway is not None
                and kernel._cached_gateway_profile is profile
                and current_turn_id == kernel._cached_gateway_turn_id
            ):
                gateway = kernel._cached_tool_gateway
            else:
                _reset_cached_gateway(kernel)
                gateway = executor.create_gateway(
                    profile=profile,
                    request=request,
                    tool_gateway=kernel._tool_gateway,
                )
                kernel._cached_tool_gateway = gateway
                kernel._cached_gateway_profile = profile
                kernel._cached_gateway_turn_id = current_turn_id

            can_execute, reason = gateway.check_tool_permission(tool_name, args)
            if not can_execute:
                from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

                raise ToolAuthorizationError(f"{reason}: tool={tool_name!r}")

        logger.debug(
            "[execute_single_tool] _injected_tool_executor (with auth gate): tool=%s",
            tool_name,
        )
        return await kernel._injected_tool_executor.execute(tool_name, args, context=context)

    from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

    executor = KernelToolExecutor(kernel, kernel.workspace)

    profile = None
    request = None
    if context:
        profile = context.get("profile")
        request = context.get("request")

    if profile is None:
        available_roles = ["director", "pm", "architect", "chief_engineer", "qa"]
        for role in available_roles:
            try:
                profile = kernel.registry.get_profile_or_raise(role)
                break
            except ValueError:
                continue

    if profile is None:
        raise ValueError("No available role profile found for tool execution")

    if request is None:
        request = RoleTurnRequest(message="")

    logger.debug(
        "[execute_single_tool] request.run_id=%s tool=%s",
        getattr(request, "run_id", None),
        tool_name,
    )

    current_turn_id = resolve_tool_gateway_turn_key(request)
    if (
        kernel._cached_tool_gateway is not None
        and kernel._cached_gateway_profile is profile
        and current_turn_id == kernel._cached_gateway_turn_id
    ):
        gateway = kernel._cached_tool_gateway
    else:
        _reset_cached_gateway(kernel)
        gateway = executor.create_gateway(
            profile=profile,
            request=request,
            tool_gateway=kernel._tool_gateway,
        )
        kernel._cached_tool_gateway = gateway
        kernel._cached_gateway_profile = profile
        kernel._cached_gateway_turn_id = current_turn_id

    return gateway.execute_tool(tool_name, args)


def reset_cached_tool_gateway_turn_boundary(kernel: RoleExecutionKernel, turn_id: str) -> None:
    """Reset cached gateway counters when the authoritative turn id changes."""
    current_turn_key = resolve_explicit_turn_key(turn_id)
    if not current_turn_key:
        return
    if current_turn_key == kernel._cached_gateway_turn_id:
        return
    reset_cached = getattr(kernel._cached_tool_gateway, "reset_execution_count", None)
    if callable(reset_cached):
        reset_cached()
    cached_failure_budget = getattr(kernel._cached_tool_gateway, "_failure_budget", None)
    reset_failure_budget = getattr(cached_failure_budget, "reset", None)
    if callable(reset_failure_budget):
        reset_failure_budget()
    kernel._cached_gateway_turn_id = current_turn_key


def _reset_cached_gateway(kernel: RoleExecutionKernel) -> None:
    reset_cached = getattr(kernel._cached_tool_gateway, "reset_execution_count", None)
    if callable(reset_cached):
        reset_cached()
    cached_failure_budget = getattr(kernel._cached_tool_gateway, "_failure_budget", None)
    reset_failure_budget = getattr(cached_failure_budget, "reset", None)
    if callable(reset_failure_budget):
        reset_failure_budget()
    close_cached = getattr(kernel._cached_tool_gateway, "close", None)
    if callable(close_cached):
        close_cached()


__all__ = [
    "execute_single_tool",
    "reset_cached_tool_gateway_turn_boundary",
    "resolve_authorized_tool_gateway",
]
