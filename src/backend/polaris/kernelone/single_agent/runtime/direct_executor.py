"""DirectExecutor - 直接执行通道

Blueprint: §9.1.A DirectExecutor

适用场景:
- 工具少（<= 3）
- 结果小（< 10KB）
- 不需要聚合
- 无复杂分支

标准流程:
    model -> tool_call -> runtime execute -> tool_result -> model

设计约束:
- 所有工具执行结果必须通过 ToolExecutionResult 回流
- 工具执行失败不得直接把 turn 打崩
- 错误都转为 ToolResult(status=error|blocked|timeout)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.single_agent.tools.contracts import ToolExecutionResult, ToolStatus

if TYPE_CHECKING:
    from polaris.kernelone.single_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class DirectExecutor:
    """直接执行通道

    Blueprint: §9.1.A DirectExecutor

    标准流程: model -> tool_call -> runtime execute -> tool_result -> model

    设计约束:
    - 顺序执行所有工具调用（不并发）
    - 每个工具调用的错误都捕获并转为 ToolExecutionResult
    - 工具执行失败不得直接把 turn 打崩
    """

    def __init__(
        self,
        registry: ToolRegistry,  # TODO: Phase 5 类型
    ) -> None:
        self._registry = registry

    async def execute(
        self,
        tool_calls: list[Any],
        state: Any,  # TODO: ConversationState
    ) -> list[ToolExecutionResult]:
        """同步顺序执行所有工具调用

        Blueprint: §9.1.A DirectExecutor.execute()

        设计约束:
        - 工具执行失败不得直接把 turn 打崩
        - 所有错误都转成 ToolExecutionResult(status=error|blocked|timeout)

        Args:
            tool_calls: 工具调用列表，每个元素为 dict
                {
                    "tool": str,       # 工具名
                    "args": dict,      # 工具参数
                    "id": str | None,  # 可选，调用ID（用于结果匹配）
                }
            state: ConversationState（可选）

        Returns:
            list[ToolExecutionResult]，顺序与 tool_calls 对应
        """
        results: list[ToolExecutionResult] = []
        for call in tool_calls:
            result = await self._execute_single(call, state)
            results.append(result)
        return results

    async def _execute_single(
        self,
        tool_call: Any,
        state: Any,
    ) -> ToolExecutionResult:
        """执行单个工具调用

        Args:
            tool_call: 单个工具调用 dict
            state: ConversationState

        Returns:
            ToolExecutionResult
        """
        tool_name = self._extract_tool_name(tool_call)
        tool_args = self._extract_tool_args(tool_call)

        logger.debug(
            "[DirectExecutor] 执行工具调用: tool=%s args_keys=%s",
            tool_name,
            list(tool_args.keys()) if isinstance(tool_args, dict) else "N/A",
        )

        try:
            # Phase 5: 通过 ToolRegistry 执行
            # result = await self._registry.execute(tool_name, tool_args, state)
            raise NotImplementedError(
                "Phase 5 (ToolRegistry) 未完成，DirectExecutor.execute() 依赖 ToolRegistry.execute() 方法"
            )

        except NotImplementedError:
            # Phase 5 尚未实现时的降级路径
            return ToolExecutionResult(
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                result=None,
                error="ToolRegistry 未实现（Phase 5 依赖未满足）",
            )

        except (RuntimeError, ValueError) as e:
            # 所有异常都转为 ToolExecutionResult，不打崩 turn
            error_msg = str(e)
            status = self._classify_error(e)

            logger.warning(
                "[DirectExecutor] 工具执行失败: tool=%s error=%s status=%s",
                tool_name,
                error_msg,
                status.value,
                exc_info=True,
            )

            return ToolExecutionResult(
                tool_name=tool_name,
                status=status,
                result=None,
                error=error_msg,
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

    def _extract_tool_args(self, tool_call: Any) -> dict[str, Any]:
        """从 tool_call 中提取工具参数"""
        if isinstance(tool_call, dict):
            return tool_call.get("args") or tool_call.get("arguments", {})
        if hasattr(tool_call, "args"):
            return getattr(tool_call, "args", {})
        if hasattr(tool_call, "arguments"):
            return getattr(tool_call, "arguments", {})
        return {}

    def _classify_error(self, error: Exception) -> ToolStatus:
        """将异常分类为 ToolStatus

        分类规则:
        - PermissionError / ToolAuthorizationError → BLOCKED
        - TimeoutError → TIMEOUT
        - KeyboardInterrupt / SystemExit → CANCELLED
        - 其他 → ERROR
        """
        error_type = type(error).__name__
        error_msg = str(error).lower()

        if isinstance(error, PermissionError):
            return ToolStatus.BLOCKED

        if "authorization" in error_msg or "permission" in error_msg:
            return ToolStatus.BLOCKED

        if isinstance(error, TimeoutError):
            return ToolStatus.TIMEOUT

        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            return ToolStatus.CANCELLED

        logger.debug(
            "[DirectExecutor] 异常分类: %s (%s) -> ERROR",
            error_type,
            error_msg[:100],
        )
        return ToolStatus.ERROR


__all__ = ["DirectExecutor"]
