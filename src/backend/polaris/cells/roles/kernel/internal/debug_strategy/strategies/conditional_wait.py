"""Conditional Wait Strategy - 条件等待策略。

解决时序/竞态问题，通过条件等待确保操作在正确时机执行。
这是Superpowers精华中的关键技术。
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    DebugStep,
    ErrorContext,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.base import (
    BaseDebugStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import (
    DebugPhase,
    DebugStrategy,
)


class ConditionalWaitStrategy(BaseDebugStrategy):
    """条件等待策略：解决时序/竞态问题。

    适用于：
    - 异步操作问题
    - 竞态条件
    - 时序依赖问题
    - 资源未就绪问题
    """

    _HANDLED_PATTERNS: ClassVar[set[str]] = {
        "timeout",
        "async_error",
        "race_condition",
        "timing_error",
        "resource_not_ready",
        "connection_refused",
        "service_unavailable",
        "lock_error",
    }

    def __init__(self) -> None:
        super().__init__(DebugStrategy.CONDITIONAL_WAIT)

    @property
    def name(self) -> str:
        return "条件等待策略"

    @property
    def description(self) -> str:
        return "解决时序/竞态问题，通过条件等待确保操作在正确时机执行"

    def can_handle(self, context: ErrorContext) -> bool:
        """判断是否能处理此错误。"""
        error_type_lower = context.error_type.lower()
        error_msg_lower = context.error_message.lower()

        # 检查错误类型
        type_match = any(pattern in error_type_lower for pattern in self._HANDLED_PATTERNS)

        # 检查错误消息中的时序相关关键词
        timing_keywords = ["timeout", "not ready", "busy", "locked", "waiting", "async", "race"]
        msg_match = any(kw in error_msg_lower for kw in timing_keywords)

        return type_match or msg_match

    def generate_plan(self, context: ErrorContext) -> DebugPlan:
        """生成条件等待调试计划。"""
        steps = []

        # Phase 1: 根因调查 - 识别时序问题
        steps.extend(self._generate_investigation_steps(context))

        # Phase 2: 模式分析 - 分析时序依赖
        steps.extend(self._generate_analysis_steps(context))

        # Phase 3: 假设测试 - 验证时序假设
        steps.extend(self._generate_hypothesis_steps(context))

        # Phase 4: 实施 - 添加条件等待
        steps.extend(self._generate_implementation_steps(context))

        return DebugPlan(
            plan_id=f"conditional_wait_{uuid.uuid4().hex[:8]}",
            strategy=DebugStrategy.CONDITIONAL_WAIT,
            steps=steps,
            estimated_time=self._estimate_time(steps),
            rollback_plan="移除添加的等待逻辑，恢复原始时序",
            success_criteria=[
                "时序问题被解决",
                "操作在正确时机执行",
                "无竞态条件",
                "性能影响可接受",
            ],
            failure_criteria=[
                "等待条件不正确",
                "引入新的死锁",
                "性能严重下降",
                "问题间歇性出现",
            ],
        )

    def _generate_investigation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成根因调查步骤。"""
        steps = []

        # 步骤1：记录时序问题特征
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="记录时序问题的特征",
                commands=[
                    f"# 错误类型: {context.error_type}",
                    f"# 错误消息: {context.error_message}",
                    "# 时序问题特征：",
                    "# - 是否间歇性出现？",
                    "# - 是否与系统负载相关？",
                    "# - 是否只在特定时机出现？",
                ],
                expected_outcome="确认是时序/竞态问题",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤2：识别相关资源
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="识别相关的资源和依赖",
                commands=[
                    "# 识别时序依赖：",
                    "# - 哪些资源需要就绪？",
                    "# - 哪些操作需要等待？",
                    "# - 依赖的顺序是什么？",
                    f"search_code --pattern 'async|await|sleep|wait' --path {context.file_path or '.'}",
                ],
                expected_outcome="识别出所有相关的异步操作和资源依赖",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        return steps

    def _generate_analysis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成模式分析步骤。"""
        steps = []

        # 步骤3：分析时序依赖图
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="分析时序依赖关系",
                commands=[
                    "# 构建时序依赖图：",
                    "# 1. 列出所有相关操作",
                    "# 2. 确定操作顺序",
                    "# 3. 识别竞态条件",
                    "# 4. 找出缺失的等待点",
                ],
                expected_outcome="清晰的时序依赖图和等待点列表",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        # 步骤4：确定等待条件
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="确定正确的等待条件",
                commands=[
                    "# 等待条件设计：",
                    "# - 条件应该检查什么？",
                    "# - 超时时间设为多少？",
                    "# - 重试策略是什么？",
                    "# - 失败如何处理？",
                ],
                expected_outcome="明确的等待条件定义",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        return steps

    def _generate_hypothesis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成假设测试步骤。"""
        steps = []

        # 步骤5：生成时序假设
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="生成时序问题假设",
                commands=[
                    "# 可能的时序问题：",
                    "# 假设1: 资源未就绪就使用",
                    "# 假设2: 异步操作未等待完成",
                    "# 假设3: 竞态条件导致状态不一致",
                    "# 假设4: 超时时间设置过短",
                    "# 假设5: 锁的粒度或顺序有问题",
                ],
                expected_outcome="列出所有可能的时序问题原因",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤6：验证等待策略
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="验证条件等待策略",
                commands=[
                    "# 测试等待策略：",
                    "# 1. 添加临时日志追踪时序",
                    "# 2. 测试不同的等待时间",
                    "# 3. 验证条件检查逻辑",
                    "# 4. 测试边界情况",
                ],
                expected_outcome="确认等待策略能解决问题",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        return steps

    def _generate_implementation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成实施步骤。"""
        steps = []

        # 步骤7：实现条件等待
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="实现条件等待逻辑",
                commands=[
                    "# 实现等待模式：",
                    "# 1. 添加条件检查函数",
                    "# 2. 实现带超时的等待循环",
                    "# 3. 添加适当的重试逻辑",
                    "# 4. 处理超时和错误情况",
                    "",
                    "# 示例模式：",
                    "# while not condition() and not timeout:",
                    "#     sleep(poll_interval)",
                    "# if timeout:",
                    "#     raise TimeoutError(...)",
                ],
                expected_outcome="正确实现条件等待逻辑",
                rollback_commands=[
                    f"git checkout -- {context.file_path}" if context.file_path else "# 手动移除等待代码",
                ],
                timeout_seconds=120,
            )
        )

        # 步骤8：验证和优化
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="验证和优化等待策略",
                commands=[
                    "# 验证等待策略：",
                    "# 1. 测试正常场景",
                    "# 2. 测试超时场景",
                    "# 3. 测试并发场景",
                    "# 4. 测量性能影响",
                    "# 5. 优化等待时间",
                ],
                expected_outcome="等待策略正确且性能可接受",
                rollback_commands=[],
                timeout_seconds=90,
            )
        )

        return steps

    def _estimate_time(self, steps: list[DebugStep]) -> int:
        """估算调试时间。"""
        total_seconds = sum(step.timeout_seconds for step in steps)
        return max(10, total_seconds // 60)


__all__ = ["ConditionalWaitStrategy"]
