"""ProgrammaticExecutor - 程序化执行通道

Blueprint: §9.1.B ProgrammaticExecutor

适用场景:
- 高 fan-out（工具数量 > 3）
- 大量中间结果需要筛选/聚合
- 需要条件分支/循环
- 需要工具结果预处理

支持能力:
1. 高 fan-out 批处理
2. 中间结果聚合
3. 条件分支
4. 工具结果预处理

设计约束:
- 所有工具执行结果必须通过 ToolExecutionResult 回流
- 工具执行失败不得直接把 turn 打崩
- 错误都转为 ToolExecutionResult(status=error|blocked|timeout)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.single_agent.tools.contracts import ToolExecutionResult, ToolStatus

if TYPE_CHECKING:
    from polaris.kernelone.single_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 批处理并发上限（防止资源耗尽）
_MAX_CONCURRENT = 5


class ProgrammaticExecutor:
    """程序化执行通道

    Blueprint: §9.1.B ProgrammaticExecutor

    设计约束:
    - 支持高 fan-out 批处理（并发控制）
    - 所有工具执行结果必须通过 ToolExecutionResult 回流
    - 工具执行失败不得直接把 turn 打崩
    - 错误都转为 ToolExecutionResult(status=error|blocked|timeout)
    """

    def __init__(
        self,
        registry: ToolRegistry,  # TODO: Phase 5 类型
        *,
        max_concurrent: int = _MAX_CONCURRENT,
    ) -> None:
        self._registry = registry
        self._max_concurrent = max_concurrent

    async def execute(
        self,
        tool_calls: list[Any],
        state: Any,  # TODO: ConversationState
    ) -> list[ToolExecutionResult]:
        """程序化执行入口

        Blueprint: §9.1.B ProgrammaticExecutor.execute()

        流程:
        1. 预处理工具调用（依赖分析、批次分组）
        2. 并发执行（受 max_concurrent 限制）
        3. 聚合中间结果
        4. 返回最终结果

        Args:
            tool_calls: 工具调用列表
            state: ConversationState

        Returns:
            list[ToolExecutionResult]
        """
        if not tool_calls:
            return []

        logger.info(
            "[ProgrammaticExecutor] 程序化执行开始: tool_count=%d max_concurrent=%d",
            len(tool_calls),
            self._max_concurrent,
        )

        # Phase 5: 实现程序化执行
        # 1. 预处理：依赖分析、批次分组
        # batches = self._preprocess_batches(tool_calls)
        # 2. 并发执行（带 semaphore 控制并发数）
        # results = await self._execute_batches(batches, state)
        # 3. 聚合结果
        # return self._aggregate_results(results)

        raise NotImplementedError(
            "Phase 5 (ToolRegistry) 未完成，ProgrammaticExecutor.execute() 依赖 ToolRegistry.execute() 方法"
        )

    async def _execute_batches(
        self,
        batches: list[list[Any]],
        state: Any,
    ) -> list[list[ToolExecutionResult]]:
        """分批并发执行工具调用

        Args:
            batches: 工具调用批次列表
            state: ConversationState

        Returns:
            每批次的结果列表
        """
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def execute_batch(batch: list[Any]) -> list[ToolExecutionResult]:
            async with semaphore:
                tasks = [self._execute_single(call, state) for call in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # 将异常转为 ToolExecutionResult
                return self._normalize_batch_results(results)

        batch_results: list[list[ToolExecutionResult]] = []
        for batch in batches:
            results = await execute_batch(batch)
            batch_results.append(results)

        return batch_results

    async def _execute_single(
        self,
        tool_call: Any,
        state: Any,
    ) -> ToolExecutionResult:
        """执行单个工具调用（带异常处理）

        Args:
            tool_call: 单个工具调用
            state: ConversationState

        Returns:
            ToolExecutionResult
        """
        tool_name = self._extract_tool_name(tool_call)
        self._extract_tool_args(tool_call)

        try:
            # Phase 5: 通过 ToolRegistry 执行
            # return await self._registry.execute(tool_name, tool_args, state)
            raise NotImplementedError("Phase 5 依赖未满足")

        except NotImplementedError:
            return ToolExecutionResult(
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                result=None,
                error="ToolRegistry 未实现（Phase 5 依赖未满足）",
            )

        except (RuntimeError, ValueError) as e:
            error_msg = str(e)
            status = self._classify_error(e)

            logger.warning(
                "[ProgrammaticExecutor] 工具执行失败: tool=%s error=%s status=%s",
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

    def _preprocess_batches(
        self,
        tool_calls: list[Any],
    ) -> list[list[Any]]:
        """预处理工具调用，构建执行批次

        当前策略: 简单均分（未来可扩展为依赖分析）

        Args:
            tool_calls: 工具调用列表

        Returns:
            批次列表
        """
        if len(tool_calls) <= self._max_concurrent:
            return [tool_calls]

        batches: list[list[Any]] = []
        for i in range(0, len(tool_calls), self._max_concurrent):
            batches.append(tool_calls[i : i + self._max_concurrent])

        return batches

    def _normalize_batch_results(
        self,
        results: list[Any],
    ) -> list[ToolExecutionResult]:
        """将 gather 结果中的异常转为 ToolExecutionResult"""
        normalized: list[ToolExecutionResult] = []
        for result in results:
            if isinstance(result, ToolExecutionResult):
                normalized.append(result)
            elif isinstance(result, Exception):
                status = self._classify_error(result)
                normalized.append(
                    ToolExecutionResult(
                        tool_name="unknown",
                        status=status,
                        result=None,
                        error=str(result),
                    )
                )
            else:
                normalized.append(
                    ToolExecutionResult(
                        tool_name="unknown",
                        status=ToolStatus.ERROR,
                        result=None,
                        error=f"unexpected result type: {type(result).__name__}",
                    )
                )
        return normalized

    def _aggregate_results(
        self,
        batch_results: list[list[ToolExecutionResult]],
    ) -> list[ToolExecutionResult]:
        """聚合分批执行的结果

        Args:
            batch_results: 每批次的结果

        Returns:
            扁平化的结果列表
        """
        aggregated: list[ToolExecutionResult] = []
        for batch in batch_results:
            aggregated.extend(batch)
        return aggregated

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
        """将异常分类为 ToolStatus"""
        error_msg = str(error).lower()

        if isinstance(error, PermissionError):
            return ToolStatus.BLOCKED

        if "authorization" in error_msg or "permission" in error_msg:
            return ToolStatus.BLOCKED

        if isinstance(error, TimeoutError):
            return ToolStatus.TIMEOUT

        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            return ToolStatus.CANCELLED

        return ToolStatus.ERROR


__all__ = ["ProgrammaticExecutor"]
