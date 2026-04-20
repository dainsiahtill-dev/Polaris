"""ToolRuntime - 统一工具运行时

Blueprint: §9 ToolRuntime - 统一执行入口

职责:
1. 接收工具调用列表
2. 根据 ExecutionLaneSelector 选择执行通道
3. 委托给 DirectExecutor 或 ProgrammaticExecutor
4. 收集所有结果并返回

设计约束:
- 所有工具执行结果必须通过 ToolExecutionResult 回流
- 工具执行失败不得直接把 turn 打崩
- 错误都转为 ToolResult(status=error|blocked|timeout)
- UTF-8: 是
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.single_agent.tools.contracts import (
    ExecutionLane,
    ToolExecutionResult,
    ToolStatus,
)

if TYPE_CHECKING:
    from polaris.kernelone.single_agent.runtime.direct_executor import DirectExecutor
    from polaris.kernelone.single_agent.runtime.execution_lane_selector import (
        ExecutionLaneSelector,
    )
    from polaris.kernelone.single_agent.runtime.programmatic_executor import (
        ProgrammaticExecutor,
    )
    from polaris.kernelone.single_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolRuntime:
    """统一工具运行时

    Blueprint: §9 ToolRuntime - 统一执行入口

    架构::

        ToolRuntime
        ├── ExecutionLaneSelector  (通道选择)
        ├── DirectExecutor          (直接执行通道)
        └── ProgrammaticExecutor    (程序化执行通道)

    执行流程::

        tool_calls
            │
            ▼
    ┌──────────────────────┐
    │ ExecutionLaneSelector │
    └──────────────────────┘
            │
            ▼
    ┌──────────────────────────┐
    │ ExecutionLane.DIRECT     │──→ DirectExecutor.execute()
    │ ExecutionLane.PROGRAMMATIC│──→ ProgrammaticExecutor.execute()
    └──────────────────────────┘
            │
            ▼
    list[ToolExecutionResult]

    使用示例::

        runtime = ToolRuntime(registry=registry)
        results = await runtime.execute(tool_calls, state)
    """

    def __init__(
        self,
        registry: ToolRegistry,  # TODO: Phase 5 类型
        lane_selector: ExecutionLaneSelector | None = None,
        direct_executor: DirectExecutor | None = None,
        programmatic_executor: ProgrammaticExecutor | None = None,
    ) -> None:
        """初始化工具运行时

        Args:
            registry: 工具注册表（Phase 5 实现）
            lane_selector: 可选，执行通道选择器（默认新建）
            direct_executor: 可选，直接执行器（默认新建）
            programmatic_executor: 可选，程序化执行器（默认新建）
        """
        self._registry = registry
        self._lane_selector = lane_selector
        self._direct_executor = direct_executor
        self._programmatic_executor = programmatic_executor

        # 延迟初始化（避免循环导入）
        self.__lane_selector: ExecutionLaneSelector | None = None
        self.__direct_executor: DirectExecutor | None = None
        self.__programmatic_executor: ProgrammaticExecutor | None = None

    @property
    def _ls(self) -> ExecutionLaneSelector:
        """延迟初始化的 ExecutionLaneSelector"""
        if self.__lane_selector is None:
            from polaris.kernelone.single_agent.runtime.execution_lane_selector import (
                ExecutionLaneSelector,
            )

            self.__lane_selector = self._lane_selector or ExecutionLaneSelector()
        return self.__lane_selector

    @property
    def _de(self) -> DirectExecutor:
        """延迟初始化的 DirectExecutor"""
        if self.__direct_executor is None:
            from polaris.kernelone.single_agent.runtime.direct_executor import (
                DirectExecutor,
            )

            self.__direct_executor = self._direct_executor or DirectExecutor(self._registry)
        return self.__direct_executor

    @property
    def _pe(self) -> ProgrammaticExecutor:
        """延迟初始化的 ProgrammaticExecutor"""
        if self.__programmatic_executor is None:
            from polaris.kernelone.single_agent.runtime.programmatic_executor import (
                ProgrammaticExecutor,
            )

            self.__programmatic_executor = self._programmatic_executor or ProgrammaticExecutor(self._registry)
        return self.__programmatic_executor

    async def execute(
        self,
        tool_calls: list[Any],
        state: Any,  # TODO: ConversationState
    ) -> list[ToolExecutionResult]:
        """统一执行入口

        Blueprint: §9 ToolRuntime.execute()

        执行流程:
        1. 空列表检查
        2. 选择执行通道（通过 ExecutionLaneSelector）
        3. 调用对应 executor
        4. 所有异常转为 ToolExecutionResult

        设计约束:
        - 所有工具执行结果必须通过 ToolExecutionResult 回流
        - 工具执行失败不得直接把 turn 打崩
        - 错误都转为 ToolResult(status=error|blocked|timeout)

        Args:
            tool_calls: 工具调用列表
                [
                    {
                        "tool": str,       # 工具名
                        "args": dict,      # 工具参数
                        "id": str | None,  # 可选，调用ID
                    },
                    ...
                ]
            state: ConversationState（可选）

        Returns:
            list[ToolExecutionResult]，顺序与 tool_calls 对应
        """
        # 1. 空列表检查
        if not tool_calls:
            logger.debug("[ToolRuntime] 空工具调用列表，直接返回空结果")
            return []

        # 2. 选择执行通道
        lane = self._ls.choose(tool_calls, state)

        logger.info(
            "[ToolRuntime] 执行工具调用: count=%d lane=%s",
            len(tool_calls),
            lane.value,
        )

        # 3. 调用对应 executor
        try:
            if lane == ExecutionLane.PROGRAMMATIC:
                return await self._pe.execute(tool_calls, state)
            else:
                return await self._de.execute(tool_calls, state)

        except (RuntimeError, ValueError) as e:
            # 兜底：所有未捕获异常都转为 ToolExecutionResult
            logger.error(
                "[ToolRuntime] 执行通道异常: lane=%s error=%s",
                lane.value,
                str(e),
                exc_info=True,
            )
            return [
                ToolExecutionResult(
                    tool_name="runtime",
                    status=ToolStatus.ERROR,
                    result=None,
                    error=f"ToolRuntime 执行失败: {e}",
                )
            ]

    async def execute_single(
        self,
        tool_call: Any,
        state: Any | None = None,
    ) -> ToolExecutionResult:
        """执行单个工具调用的便捷方法

        Args:
            tool_call: 单个工具调用
            state: 可选，ConversationState

        Returns:
            ToolExecutionResult
        """
        results = await self.execute([tool_call], state)
        return (
            results[0]
            if results
            else ToolExecutionResult(
                tool_name=self._extract_tool_name(tool_call),
                status=ToolStatus.ERROR,
                result=None,
                error="execute_single 返回空结果",
            )
        )

    def _extract_tool_name(self, tool_call: Any) -> str:
        """从 tool_call 中提取工具名"""
        if isinstance(tool_call, dict):
            return tool_call.get("tool") or tool_call.get("name", "unknown")
        if hasattr(tool_call, "tool"):
            return getattr(tool_call, "tool", "unknown")
        if hasattr(tool_call, "name"):
            return getattr(tool_call, "name", "unknown")
        return str(tool_call)


__all__ = ["ToolRuntime"]
