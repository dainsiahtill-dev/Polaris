"""Binary Search Strategy - 二分定位策略。

快速缩小问题范围，通过二分法定位引入问题的变更。
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


class BinarySearchStrategy(BaseDebugStrategy):
    """二分定位策略：快速缩小问题范围。

    适用于：
    - 回归错误（之前工作正常）
    - 范围较大的问题
    - 需要快速定位的场景
    """

    _HANDLED_PATTERNS: ClassVar[set[str]] = {
        "regression",
        "unexpected_behavior",
        "performance_degradation",
        "compatibility_issue",
    }

    def __init__(self) -> None:
        super().__init__(DebugStrategy.BINARY_SEARCH)

    @property
    def name(self) -> str:
        return "二分定位策略"

    @property
    def description(self) -> str:
        return "通过二分法快速缩小问题范围，定位引入问题的变更"

    def can_handle(self, context: ErrorContext) -> bool:
        """判断是否能处理此错误。"""
        error_type_lower = context.error_type.lower()
        has_regression_indicators = bool(context.recent_changes)
        return has_regression_indicators or any(pattern in error_type_lower for pattern in self._HANDLED_PATTERNS)

    def generate_plan(self, context: ErrorContext) -> DebugPlan:
        """生成二分定位调试计划。"""
        steps = []

        # Phase 1: 根因调查 - 确定搜索范围
        steps.extend(self._generate_investigation_steps(context))

        # Phase 2: 模式分析 - 确定二分点
        steps.extend(self._generate_analysis_steps(context))

        # Phase 3: 假设测试 - 验证二分假设
        steps.extend(self._generate_hypothesis_steps(context))

        # Phase 4: 实施 - 精确定位并修复
        steps.extend(self._generate_implementation_steps(context))

        return DebugPlan(
            plan_id=f"binary_search_{uuid.uuid4().hex[:8]}",
            strategy=DebugStrategy.BINARY_SEARCH,
            steps=steps,
            estimated_time=self._estimate_time(steps),
            rollback_plan="回滚到已知工作正常的版本",
            success_criteria=[
                "精确定位引入问题的变更",
                "修复后回归测试通过",
                "问题范围被有效缩小",
            ],
            failure_criteria=[
                "无法确定搜索范围",
                "二分过程中断",
                "定位到多个可能原因",
            ],
        )

    def _generate_investigation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成根因调查步骤。"""
        steps = []

        # 步骤1：确定已知的好版本和坏版本
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="确定已知的正常版本和错误版本",
                commands=[
                    "# 确定时间范围：",
                    "git log --oneline --since='1 week ago'",
                    "# 标记已知的好版本（错误出现前的版本）",
                    "# 标记已知的坏版本（当前版本）",
                ],
                expected_outcome="确定二分搜索的起止点",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤2：收集变更历史
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="收集相关变更历史",
                commands=[
                    "# 列出相关文件的变更：",
                    f"git log --oneline -- {context.file_path or '.'}",
                    "# 查看最近的合并请求",
                    "# 检查依赖更新",
                ],
                expected_outcome="获得完整的变更历史列表",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        return steps

    def _generate_analysis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成模式分析步骤。"""
        steps = []

        # 步骤3：计算中点并测试
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="执行二分搜索第一步",
                commands=[
                    "# 计算中点：",
                    "git bisect start",
                    "git bisect bad HEAD  # 当前版本有错误",
                    "git bisect good <known_good_commit>  # 已知正常版本",
                    "# 测试中点版本",
                ],
                expected_outcome="确定中点版本是否正常",
                rollback_commands=["git bisect reset"],
                timeout_seconds=60,
            )
        )

        # 步骤4：迭代二分
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="迭代二分直到定位问题",
                commands=[
                    "# 根据测试结果标记：",
                    "# 如果中点版本有错误：git bisect bad",
                    "# 如果中点版本正常：git bisect good",
                    "# 重复直到定位到具体提交",
                ],
                expected_outcome="定位到引入问题的具体提交",
                rollback_commands=["git bisect reset"],
                timeout_seconds=120,
            )
        )

        return steps

    def _generate_hypothesis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成假设测试步骤。"""
        steps = []

        # 步骤5：分析问题提交
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="分析问题提交的内容",
                commands=[
                    "# 查看问题提交的详细变更：",
                    "git show <bad_commit>",
                    "# 识别关键变更：",
                    "# - 修改了哪些文件？",
                    "# - 修改了多少行？",
                    "# - 修改的目的是什么？",
                ],
                expected_outcome="理解问题提交的具体变更",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        # 步骤6：验证假设
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="验证问题假设",
                commands=[
                    "# 测试假设：",
                    "# 1. 回滚该提交测试",
                    "# 2. 只修改关键部分测试",
                    "# 3. 验证修复方案",
                ],
                expected_outcome="确认问题根因",
                rollback_commands=["git checkout -- ."],
                timeout_seconds=60,
            )
        )

        return steps

    def _generate_implementation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成实施步骤。"""
        steps = []

        # 步骤7：修复问题提交
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="修复定位到的问题",
                commands=[
                    "# 基于二分定位结果修复：",
                    "# 1. 理解原始修改意图",
                    "# 2. 修正实现方式",
                    "# 3. 保持其他功能正常",
                ],
                expected_outcome="修复问题同时保持其他功能",
                rollback_commands=["git checkout -- ."],
                timeout_seconds=90,
            )
        )

        # 步骤8：验证修复
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="验证修复效果",
                commands=[
                    "# 全面验证：",
                    "# 1. 原错误场景测试",
                    "# 2. 回归测试",
                    "# 3. 相关功能测试",
                ],
                expected_outcome="错误修复且无回归",
                rollback_commands=[],
                timeout_seconds=90,
            )
        )

        return steps

    def _estimate_time(self, steps: list[DebugStep]) -> int:
        """估算调试时间。"""
        total_seconds = sum(step.timeout_seconds for step in steps)
        return max(10, total_seconds // 60)  # 二分搜索通常需要更多时间


__all__ = ["BinarySearchStrategy"]
