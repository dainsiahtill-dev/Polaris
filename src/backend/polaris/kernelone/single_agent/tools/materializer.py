from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

"""Tool materializer - 按需工具实例化.

UTF-8: all text literals in this file use UTF-8 encoding.
"""

if TYPE_CHECKING:
    from polaris.kernelone.single_agent.tools.contracts import AgentToolSpec

logger = logging.getLogger(__name__)


class MaterializedTool:
    """已实例化的可执行工具.

    封装一个 AgentToolSpec 与其对应的执行句柄，
    符合 AgentAccelToolExecutor.execute(tool_name, args) 接口。
    """

    __slots__ = ("_execute_fn", "spec")

    def __init__(
        self,
        spec: AgentToolSpec,
        execute_fn: Any,  # Callable[[dict], Awaitable[dict]]
    ) -> None:
        self.spec = spec
        self._execute_fn = execute_fn

    @property
    def tool_name(self) -> str:
        return self.spec.name

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """执行工具调用。

        Args:
            args: 工具参数字典。

        Returns:
            工具执行结果字典（符合 ToolExecutionResult.to_dict() 格式）。
        """
        return await self._execute_fn(args)


class ToolMaterializer:
    """按需将 AgentToolSpec 实例化为可执行工具

    Blueprint: §9 ToolRuntime 的实例化层。

    支持的工具来源：
    - builtin: 从 AgentAccelToolExecutor 加载
    - local: 从 .polaris/tools/ 动态加载（Phase 4）
    - mcp: 通过 MCP client 实例化（Phase 4）

    Phase 3 仅实现 builtin 工具的实例化。
    """

    def __init__(self) -> None:
        self._executor: Any | None = None  # AgentAccelToolExecutor

    @property
    def _tool_executor(self) -> Any:
        """惰性加载 AgentAccelToolExecutor（延迟避免循环导入）。"""
        if self._executor is None:
            try:
                from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

                self._executor = AgentAccelToolExecutor(workspace=".")
            except ImportError:
                logger.warning("AgentAccelToolExecutor not available; builtin tool execution will be unavailable")
                self._executor = None
        return self._executor

    def _normalize_builtin_args(self, spec: AgentToolSpec, args: dict[str, Any]) -> dict[str, Any]:
        """将调用参数标准化为目标工具接受的字段名。

        基于 tool_normalization.normalize_tool_arguments() 逻辑，
        但作用于运行时参数而非解析阶段。
        """
        from polaris.kernelone.llm.toolkit.tool_normalization import (
            normalize_tool_arguments,
        )

        return normalize_tool_arguments(spec.name, args)

    async def materialize(
        self,
        spec: AgentToolSpec,
        context: Any | None = None,  # TODO: ConversationState
    ) -> MaterializedTool:
        """将 AgentToolSpec 实例化为 MaterializedTool（Phase 3 实现）。

        Args:
            spec: 工具规格（来自 ToolRegistry）
            context: 可选的执行上下文（workspace 等）

        Returns:
            MaterializedTool 实例，可直接调用 execute(args)

        Raises:
            ValueError: 工具来源不支持或 executor 不可用
        """
        source = spec.source or "builtin"

        if source == "builtin":
            return self._materialize_builtin(spec, context)

        if source == "local":
            # Phase 4: 从 .polaris/tools/ 动态加载
            msg = f"ToolMaterializer: local tool materialization (Phase 4): {spec.tool_id}"
            raise NotImplementedError(msg)

        if source == "mcp":
            # Phase 4: 通过 MCP client 实例化
            msg = f"ToolMaterializer: MCP tool materialization (Phase 4): {spec.tool_id}"
            raise NotImplementedError(msg)

        raise ValueError(f"ToolMaterializer: unknown tool source '{source}': {spec.tool_id}")

    def _materialize_builtin(
        self,
        spec: AgentToolSpec,
        _context: Any | None,
    ) -> MaterializedTool:
        """实例化 builtin 工具。"""

        async def _execute_builtin(args: dict[str, Any]) -> dict[str, Any]:
            executor = self._tool_executor
            if executor is None:
                return {
                    "success": False,
                    "tool": spec.name,
                    "error": "AgentAccelToolExecutor unavailable",
                }

            normalized_args = self._normalize_builtin_args(spec, args)

            try:
                result = await executor.execute(spec.name, normalized_args)
                return result if isinstance(result, dict) else {"success": True, "result": result}
            except (RuntimeError, ValueError, TypeError) as e:
                logger.error("Builtin tool execution failed: %s(%s)", spec.name, e)
                return {
                    "success": False,
                    "tool": spec.name,
                    "error": str(e),
                }

        return MaterializedTool(spec=spec, execute_fn=_execute_builtin)

    async def materialize_many(
        self,
        specs: list[AgentToolSpec],
        context: Any | None = None,
    ) -> list[MaterializedTool]:
        """批量实例化工具（便捷方法）。

        Args:
            specs: 工具规格列表
            context: 可选的执行上下文

        Returns:
            成功实例化的工具列表（跳过不支持的来源）
        """
        materialized: list[MaterializedTool] = []
        for spec in specs:
            try:
                mat_tool = await self.materialize(spec, context)
                materialized.append(mat_tool)
            except NotImplementedError:
                # Phase 4 来源跳过，记录但不崩溃
                logger.debug("Skipped (Phase 4): %s", spec.tool_id)
        return materialized


__all__ = ["MaterializedTool", "ToolMaterializer"]
