"""Per-tool dispatch and gateway turn-boundary helpers for RoleExecutionKernel.

Holds the body of ``RoleExecutionKernel._execute_single_tool`` and the two
gateway turn-boundary helpers (``_resolve_tool_gateway_turn_key`` and
``reset_tool_gateway_turn_boundary``) extracted verbatim (behavior-preserving)
into free functions. The class methods become thin delegating shims.

FROZEN behavior notes (do NOT change):
- ``_execute_single_tool`` remains a bound async method on the class (it is a
  monkeypatch target replaced both on the instance and on the class in tests,
  and is invoked through a weakref ``kernel._execute_single_tool`` callback from
  the tool runtime). The shim preserves that call-time indirection.
- All collaborator calls go through ``kernel._<method>`` (e.g.
  ``kernel._resolve_tool_gateway_turn_key``,
  ``kernel._cognitive_runtime_blocked_tools``,
  ``kernel._normalize_tool_policy_name``) so the monkeypatch / bound-method
  surface is unchanged.
- The function-local lazy imports (KernelToolExecutor / ToolAuthorizationError)
  are preserved verbatim to keep the original circular-import-avoidance and
  authorization-gate ordering.
- The injected-executor authorization gate (BUG FIX) and the legacy
  cached-gateway turn-boundary reset (BUG FIX) semantics are preserved exactly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from polaris.cells.roles.profile.public.service import RoleTurnRequest

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)


def resolve_tool_gateway_turn_key(request_obj: Any) -> str:
    """Resolve a stable per-turn cache key for gateway counters."""
    run_id = str(getattr(request_obj, "run_id", "") or "").strip()
    if run_id:
        return run_id
    turn_id = str(getattr(request_obj, "turn_id", "") or "").strip()
    if turn_id:
        return f"turn_id:{turn_id}"
    return f"request_obj:{id(request_obj)}"


def reset_tool_gateway_turn_boundary(kernel: RoleExecutionKernel, turn_id: str) -> None:
    """Explicitly reset cached gateway counters when the authoritative turn id changes."""
    normalized_turn_id = str(turn_id or "").strip()
    if not normalized_turn_id:
        return
    current_turn_key = f"turn_id:{normalized_turn_id}"
    if current_turn_key == kernel._cached_gateway_turn_id:
        return
    if kernel._cached_tool_gateway is not None:
        kernel._cached_tool_gateway.reset_execution_count()
        if hasattr(kernel._cached_tool_gateway, "_failure_budget") and hasattr(
            kernel._cached_tool_gateway._failure_budget, "reset"
        ):
            kernel._cached_tool_gateway._failure_budget.reset()
    kernel._cached_gateway_turn_id = current_turn_key


async def execute_single_tool(
    kernel: RoleExecutionKernel,
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Facade: 执行单个工具

    委托给 tool_executor.execute_single()

    Args:
        tool_name: 工具名称
        args: 工具参数
        context: 执行上下文，可包含 'profile' 和 'request' 用于工具执行上下文

    Returns:
        工具执行结果
    """
    request_for_policy = context.get("request") if context else None
    if request_for_policy is not None:
        cognitive_blocked_tools = kernel._cognitive_runtime_blocked_tools(cast(RoleTurnRequest, request_for_policy))
        if kernel._normalize_tool_policy_name(tool_name) in cognitive_blocked_tools:
            from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

            raise ToolAuthorizationError(f"Cognitive Runtime blocked tool '{tool_name}'")

    if kernel._injected_tool_executor is not None:
        # BUG FIX: Even injected executors must go through authorization.
        # Previously bypassed RoleToolGateway entirely — no counting, whitelist,
        # path traversal protection, or FailureBudget.
        profile = context.get("profile") if context else None
        if profile is not None:
            from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

            executor = KernelToolExecutor(kernel, kernel.workspace)
            request = context.get("request") if context else None
            if request is None:
                request = RoleTurnRequest(message="")

            # Reuse or create the cached gateway for authorization check
            current_turn_id = kernel._resolve_tool_gateway_turn_key(request)
            if kernel._cached_tool_gateway is not None and kernel._cached_gateway_profile is profile:
                gateway = kernel._cached_tool_gateway
                if current_turn_id != kernel._cached_gateway_turn_id:
                    gateway.reset_execution_count()
                    kernel._cached_gateway_turn_id = current_turn_id
            else:
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

                raise ToolAuthorizationError(reason)

        logger.debug(
            "[_execute_single_tool] _injected_tool_executor (with auth gate): tool=%s",
            tool_name,
        )
        return await kernel._injected_tool_executor.execute(tool_name, args, context=context)
    # 向后兼容：使用旧的 KernelToolExecutor
    from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

    executor = KernelToolExecutor(kernel, kernel.workspace)

    # FIX: 从context中获取profile和request，如果未提供则使用默认值
    profile = None
    request = None
    if context:
        profile = context.get("profile")
        request = context.get("request")

    # 如果没有提供profile，尝试获取第一个可用角色
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
        "[_execute_single_tool] request.run_id=%s tool=%s",
        getattr(request, "run_id", None),
        tool_name,
    )

    # Reuse cached gateway if profile matches (FailureBudget persistence for HALLUCINATION_LOOP detection)
    # BUG FIX: Reset execution_count on turn boundary to prevent cross-turn accumulation.
    # The _execution_count tracks per-turn tool calls but was never reset when the
    # gateway was reused across turns, causing permanent tool lockout.
    # Also reset FailureBudget on turn boundary to prevent stale failure state
    # from one task/turn affecting the next one.
    current_turn_id = kernel._resolve_tool_gateway_turn_key(request)
    if kernel._cached_tool_gateway is not None and kernel._cached_gateway_profile is profile:
        gateway = kernel._cached_tool_gateway
        # Reset counter and failure budget if turn boundary changed
        if current_turn_id != kernel._cached_gateway_turn_id:
            gateway.reset_execution_count()
            # Reset FailureBudget to clear stale HALLUCINATION_LOOP state
            if hasattr(gateway, "_failure_budget") and hasattr(gateway._failure_budget, "reset"):
                gateway._failure_budget.reset()
            kernel._cached_gateway_turn_id = current_turn_id
    else:
        # Create new gateway and cache it
        gateway = executor.create_gateway(
            profile=profile,
            request=request,
            tool_gateway=kernel._tool_gateway,
        )
        kernel._cached_tool_gateway = gateway
        kernel._cached_gateway_profile = profile
        kernel._cached_gateway_turn_id = current_turn_id

    return gateway.execute_tool(tool_name, args)
