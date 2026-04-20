"""ExecutionLaneSelector - 执行通道选择器

Blueprint: §9.2 ExecutionLaneSelector

职责:
根据工具调用的特征（数量、体积、权限），选择最合适的执行通道。

判断信号:
1. tool 数量
2. 预估结果体积
3. 是否需要批处理
4. 是否需要循环
5. role 权限是否允许 programmatic execution

设计约束:
- 策略必须可审计（所有决策路径必须记录日志）
- 选择结果通过 ExecutionLane 枚举表达
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.constants import DIRECT_RESULT_SIZE_THRESHOLD_BYTES
from polaris.kernelone.single_agent.tools.contracts import ExecutionLane

logger = logging.getLogger(__name__)

# 通道选择阈值（可配置）
_DIRECT_TOOL_COUNT_THRESHOLD = 3
_DIRECT_RESULT_SIZE_THRESHOLD = DIRECT_RESULT_SIZE_THRESHOLD_BYTES


class LaneSelectionContext:
    """通道选择上下文

    携带选择决策所需的所有信息。
    """

    __slots__ = (
        "estimated_result_size",
        "requires_batch",
        "requires_loop",
        "role_allows_programmatic",
        "tool_count",
    )

    def __init__(
        self,
        *,
        tool_count: int,
        estimated_result_size: int = 0,
        requires_batch: bool = False,
        requires_loop: bool = False,
        role_allows_programmatic: bool = True,
    ) -> None:
        self.tool_count = tool_count
        self.estimated_result_size = estimated_result_size
        self.requires_batch = requires_batch
        self.requires_loop = requires_loop
        self.role_allows_programmatic = role_allows_programmatic

    def __repr__(self) -> str:
        return (
            f"LaneSelectionContext(tool_count={self.tool_count}, "
            f"estimated_result_size={self.estimated_result_size}, "
            f"requires_batch={self.requires_batch}, "
            f"requires_loop={self.requires_loop}, "
            f"role_allows_programmatic={self.role_allows_programmatic})"
        )


class ExecutionLaneSelector:
    """选择最合适的执行通道

    Blueprint: §9.2 ExecutionLaneSelector

    选择策略（按优先级）:
    1. requires_loop=True → PROGRAMMATIC
    2. tool_count > _DIRECT_TOOL_COUNT_THRESHOLD → PROGRAMMATIC
    3. estimated_result_size > _DIRECT_RESULT_SIZE_THRESHOLD → PROGRAMMATIC
    4. requires_batch=True → PROGRAMMATIC
    5. not role_allows_programmatic → DIRECT
    6. 默认 → DIRECT

    设计约束:
    - 策略必须可审计（所有决策路径必须记录日志）
    - 选择结果通过 ExecutionLane 枚举表达
    """

    def __init__(
        self,
        *,
        tool_count_threshold: int = _DIRECT_TOOL_COUNT_THRESHOLD,
        result_size_threshold: int = _DIRECT_RESULT_SIZE_THRESHOLD,
    ) -> None:
        self._tool_count_threshold = tool_count_threshold
        self._result_size_threshold = result_size_threshold

    def build_context(
        self,
        tool_calls: list[Any],
        state: Any | None = None,
    ) -> LaneSelectionContext:
        """从工具调用列表和状态构建选择上下文

        Args:
            tool_calls: 工具调用列表
            state: 可选，ConversationState

        Returns:
            LaneSelectionContext 实例
        """
        tool_count = len(tool_calls)

        # 从 state 中提取预估结果体积（如果有）
        estimated_size = 0
        if state is not None:
            estimated_size = getattr(state, "estimated_result_size", 0)

        # 检测是否需要循环（通过检查工具调用的依赖关系）
        requires_loop = self._detect_loop_requirement(tool_calls, state)

        # 检测是否需要批处理
        requires_batch = tool_count > self._tool_count_threshold

        # 从 state 中提取角色权限
        role_allows_programmatic = True
        if state is not None:
            role_allows_programmatic = getattr(state, "role_allows_programmatic", True)

        return LaneSelectionContext(
            tool_count=tool_count,
            estimated_result_size=estimated_size,
            requires_batch=requires_batch,
            requires_loop=requires_loop,
            role_allows_programmatic=role_allows_programmatic,
        )

    def choose(
        self,
        tool_calls: list[Any],
        state: Any | None = None,
    ) -> ExecutionLane:
        """选择执行通道

        Blueprint: §9.2 ExecutionLaneSelector.choose()

        策略:
        - requires_loop=True → PROGRAMMATIC
        - tool_count > 3 → PROGRAMMATIC
        - estimated_size > 10KB → PROGRAMMATIC
        - requires_batch=True → PROGRAMMATIC
        - 否则 → DIRECT

        Args:
            tool_calls: 工具调用列表
            state: 可选，ConversationState

        Returns:
            ExecutionLane.DIRECT 或 ExecutionLane.PROGRAMMATIC
        """
        if not tool_calls:
            logger.debug("[ExecutionLaneSelector] 空工具调用列表，选择 DIRECT")
            return ExecutionLane.DIRECT

        ctx = self.build_context(tool_calls, state)
        return self._choose_from_context(ctx)

    def _choose_from_context(self, ctx: LaneSelectionContext) -> ExecutionLane:
        """根据上下文选择执行通道"""
        # 1. requires_loop → PROGRAMMATIC
        if ctx.requires_loop:
            logger.info(
                "[ExecutionLaneSelector] requires_loop=True，选择 PROGRAMMATIC (context=%r)",
                ctx,
            )
            return ExecutionLane.PROGRAMMATIC

        # 2. tool_count 超过阈值 → PROGRAMMATIC
        if ctx.tool_count > self._tool_count_threshold:
            logger.info(
                "[ExecutionLaneSelector] tool_count=%d > threshold=%d，选择 PROGRAMMATIC (context=%r)",
                ctx.tool_count,
                self._tool_count_threshold,
                ctx,
            )
            return ExecutionLane.PROGRAMMATIC

        # 3. 预估结果体积超过阈值 → PROGRAMMATIC
        if ctx.estimated_result_size > self._result_size_threshold:
            logger.info(
                "[ExecutionLaneSelector] estimated_result_size=%d > threshold=%d，选择 PROGRAMMATIC (context=%r)",
                ctx.estimated_result_size,
                self._result_size_threshold,
                ctx,
            )
            return ExecutionLane.PROGRAMMATIC

        # 4. requires_batch=True → PROGRAMMATIC
        if ctx.requires_batch:
            logger.info(
                "[ExecutionLaneSelector] requires_batch=True，选择 PROGRAMMATIC (context=%r)",
                ctx,
            )
            return ExecutionLane.PROGRAMMATIC

        # 5. role 不允许 PROGRAMMATIC → DIRECT
        if not ctx.role_allows_programmatic:
            logger.info(
                "[ExecutionLaneSelector] role_allows_programmatic=False，选择 DIRECT (context=%r)",
                ctx,
            )
            return ExecutionLane.DIRECT

        # 6. 默认 → DIRECT
        logger.debug(
            "[ExecutionLaneSelector] 满足所有 DIRECT 条件，选择 DIRECT (context=%r)",
            ctx,
        )
        return ExecutionLane.DIRECT

    def _detect_loop_requirement(self, tool_calls: list[Any], state: Any | None = None) -> bool:
        """检测工具调用列表是否需要循环

        实现策略:
        - 检查工具调用的 args 中是否有循环控制标记
        - 检查是否有条件分支标记
        - 从 state.role_allows_programmatic 推断（Phase 5）
        - 未来可扩展为 AST 分析

        Args:
            tool_calls: 工具调用列表
            state: 可选，ConversationState

        Returns:
            True 如果需要循环执行
        """
        # 1. 从 state 推断
        if state is not None and getattr(state, "requires_loop", False):
            return True

        # 2. 从 tool_calls 的 args 中检测循环标记
        loop_markers = {
            "iterate",
            "loop",
            "repeat",
            "for_each",
            "batch",
            "map",
            "while",
            "continue_on_error",
        }

        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            args = call.get("args") or {}
            if isinstance(args, dict):
                for key in args:
                    if any(marker in str(key).lower() for marker in loop_markers):
                        return True

        return False


__all__ = [
    "ExecutionLaneSelector",
    "LaneSelectionContext",
]
