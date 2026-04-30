"""Tool Loop - 工具调用循环

负责：
- 工具网关缓存管理
- 单工具执行
- 批量工具执行
- 写预算分割
- 工具事件发射
- 工具调用解析
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal._tool_gateway_di import _DelegatingToolGateway
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
    from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult
    from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest

logger = logging.getLogger(__name__)


class ToolLoop:
    """工具调用循环管理器

    负责管理工具网关生命周期、工具执行、预算控制等。
    """

    __slots__ = ("_cached_gateway_profile", "_cached_gateway_turn_id", "_cached_tool_gateway", "_kernel")

    def __init__(self, kernel: RoleExecutionKernel) -> None:
        """初始化工具循环管理器

        Args:
            kernel: RoleExecutionKernel 实例
        """
        self._kernel = kernel
        self._cached_tool_gateway: Any | None = None
        self._cached_gateway_profile: Any | None = None
        self._cached_gateway_turn_id: str | None = None

    def reset_cache(self) -> None:
        """重置缓存的工具网关状态（跨回合边界时调用）"""
        self._cached_tool_gateway = None
        self._cached_gateway_profile = None

    @staticmethod
    def resolve_tool_gateway_turn_key(request_obj: Any) -> str:
        """Resolve a stable per-turn cache key for gateway counters."""
        run_id = str(getattr(request_obj, "run_id", "") or "").strip()
        if run_id:
            return run_id
        turn_id = str(getattr(request_obj, "turn_id", "") or "").strip()
        if turn_id:
            return f"turn_id:{turn_id}"
        return f"request_obj:{id(request_obj)}"

    def reset_tool_gateway_turn_boundary(self, turn_id: str) -> None:
        """Explicitly reset cached gateway counters when the authoritative turn id changes."""
        normalized_turn_id = str(turn_id or "").strip()
        if not normalized_turn_id:
            return
        current_turn_key = f"turn_id:{normalized_turn_id}"
        if current_turn_key == self._cached_gateway_turn_id:
            return
        if self._cached_tool_gateway is not None:
            self._cached_tool_gateway.reset_execution_count()
            if hasattr(self._cached_tool_gateway, "_failure_budget") and hasattr(
                self._cached_tool_gateway._failure_budget, "reset"
            ):
                self._cached_tool_gateway._failure_budget.reset()
        self._cached_gateway_turn_id = current_turn_key

    async def execute_single_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Facade: 执行单个工具

        Args:
            tool_name: 工具名称
            args: 工具参数
            context: 执行上下文，可包含 'profile' 和 'request' 用于工具执行上下文

        Returns:
            工具执行结果
        """
        if self._kernel._injected_tool_executor is not None:
            profile = context.get("profile") if context else None
            if profile is not None:
                from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

                executor = KernelToolExecutor(self._kernel, self._kernel.workspace)
                request = context.get("request") if context else None
                if request is None:
                    request = RoleTurnRequest(message="")

                current_turn_id = self.resolve_tool_gateway_turn_key(request)
                if self._cached_tool_gateway is not None and self._cached_gateway_profile is profile:
                    gateway = self._cached_tool_gateway
                    if current_turn_id != self._cached_gateway_turn_id:
                        gateway.reset_execution_count()
                        self._cached_gateway_turn_id = current_turn_id
                else:
                    gateway = executor.create_gateway(
                        profile=profile,
                        request=request,
                        tool_gateway=self._kernel._tool_gateway,
                    )
                    self._cached_tool_gateway = gateway
                    self._cached_gateway_profile = profile
                    self._cached_gateway_turn_id = current_turn_id

                can_execute, reason = gateway.check_tool_permission(tool_name, args)
                if not can_execute:
                    from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError

                    raise ToolAuthorizationError(reason)

            logger.debug(
                "[_execute_single_tool] _injected_tool_executor (with auth gate): tool=%s",
                tool_name,
            )
            return await self._kernel._injected_tool_executor.execute(tool_name, args, context=context)

        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self._kernel, self._kernel.workspace)

        profile = None
        request = None
        if context:
            profile = context.get("profile")
            request = context.get("request")

        if profile is None:
            available_roles = ["director", "pm", "architect", "chief_engineer", "qa"]
            for role in available_roles:
                try:
                    profile = self._kernel.registry.get_profile_or_raise(role)
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

        current_turn_id = self.resolve_tool_gateway_turn_key(request)
        if self._cached_tool_gateway is not None and self._cached_gateway_profile is profile:
            gateway = self._cached_tool_gateway
            if current_turn_id != self._cached_gateway_turn_id:
                gateway.reset_execution_count()
                if hasattr(gateway, "_failure_budget") and hasattr(gateway._failure_budget, "reset"):
                    gateway._failure_budget.reset()
                self._cached_gateway_turn_id = current_turn_id
        else:
            gateway = executor.create_gateway(
                profile=profile,
                request=request,
                tool_gateway=self._kernel._tool_gateway,
            )
            self._cached_tool_gateway = gateway
            self._cached_gateway_profile = profile
            self._cached_gateway_turn_id = current_turn_id

        return gateway.execute_tool(tool_name, args)

    def create_gateway(
        self,
        profile: RoleProfile,
        request: RoleTurnRequest,
    ) -> RoleToolGateway | _DelegatingToolGateway:
        """Create one per-request tool gateway (委托给 KernelToolExecutor)."""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self._kernel, self._kernel.workspace)
        return executor.create_gateway(profile, request, self._kernel._tool_gateway)

    async def execute_tools(
        self, profile: RoleProfile, request: RoleTurnRequest, tool_calls: list[ToolCallResult]
    ) -> list[dict[str, Any]]:
        """执行工具调用（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self._kernel, self._kernel.workspace)
        return await executor.execute_tools(profile, request, tool_calls, self._kernel._tool_gateway)

    @staticmethod
    def split_tool_calls_by_write_budget(
        role_id: str,
        tool_calls: list[ToolCallResult],
    ) -> tuple[list[ToolCallResult], list[ToolCallResult], int]:
        """Split tool calls into executable and deferred by write-call budget."""
        from polaris.cells.roles.kernel.internal.kernel.helpers import resolve_role_write_call_limit

        if not tool_calls:
            return [], [], 0
        write_limit = resolve_role_write_call_limit(role_id)
        if write_limit <= 0:
            return list(tool_calls), [], 0

        executable: list[ToolCallResult] = []
        deferred: list[ToolCallResult] = []
        write_calls_seen = 0
        for call in tool_calls:
            tool_name = str(getattr(call, "tool", "") or "").strip().lower()
            is_write_tool = tool_name in WRITE_TOOLS
            if is_write_tool and write_calls_seen >= write_limit:
                deferred.append(call)
                continue
            executable.append(call)
            if is_write_tool:
                write_calls_seen += 1

        return executable, deferred, write_limit

    def emit_tool_execute_events(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
        emit_event: Any,
    ) -> None:
        """发射工具执行前事件（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self._kernel, self._kernel.workspace)
        executor.emit_tool_execute_events(profile, run_id, task_id, attempt, mode_value, tool_calls, emit_event)

    def emit_tool_result_events_and_collect_errors(
        self,
        profile: RoleProfile,
        run_id: str,
        task_id: str | None,
        attempt: int,
        mode_value: str,
        tool_calls: list[ToolCallResult],
        executed_tool_results: list[dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """发射工具结果事件并收集错误（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(self._kernel, self._kernel.workspace)
        return executor.emit_tool_result_events_and_collect_errors(
            profile, run_id, task_id, attempt, mode_value, tool_calls, executed_tool_results, self._kernel._emit_event
        )

    @staticmethod
    def append_deferred_notice(
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
        executed_tool_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """追加 deferred notice（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        return KernelToolExecutor.append_deferred_notice(deferred_tool_calls, write_call_limit, executed_tool_results)

    @staticmethod
    def log_deferred_write_calls(
        role_id: str,
        deferred_tool_calls: list[ToolCallResult],
        write_call_limit: int,
    ) -> None:
        """记录 deferred write calls（委托给 KernelToolExecutor）"""
        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        KernelToolExecutor.log_deferred_write_calls(role_id, deferred_tool_calls, write_call_limit)

    def parse_content_and_thinking_tool_calls(
        self,
        content: str,
        thinking: str | None,
        profile: Any,
        native_tool_calls: list[dict[str, Any]] | None,
        native_tool_provider: str,
    ) -> list[Any]:
        """Parse tool calls from content and thinking, filtering out thinking-only calls.

        Args:
            content: Raw text content from LLM
            thinking: Thinking content (may contain [TOOL_CALL]...[/TOOL_CALL] markers)
            profile: Role profile for allowed tool names
            native_tool_calls: Native tool calls from provider
            native_tool_provider: Provider hint for parsing

        Returns:
            List of parsed and filtered ToolCallResult objects
        """
        result: list[ToolCallResult] = []
        seen: set[tuple[str, str]] = set()

        valid_parsed = self._kernel._get_output_parser().parse_tool_calls(
            content or "",
            native_tool_calls=native_tool_calls,
            native_provider=native_tool_provider,
        )
        for call in valid_parsed:
            key = (call.tool, str(call.args.get("path", "") or call.args.get("file", "")))
            if key not in seen:
                seen.add(key)
                result.append(call)

        return result


__all__ = ["ToolLoop"]
