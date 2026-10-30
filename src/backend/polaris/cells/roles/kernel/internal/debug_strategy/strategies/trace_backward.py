"""Trace Backward Strategy - 反向追溯策略。

从错误点回溯数据流，找出问题的根本原因。
这是"先调查后修复"原则的核心体现。
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


class TraceBackwardStrategy(BaseDebugStrategy):
    """反向追溯策略：从错误点回溯数据流。

    适用于：
    - 运行时错误
    - 逻辑错误
    - 数据流问题

    四阶段实现：
    1. 根因调查：收集错误点信息
    2. 模式分析：识别数据流模式
    3. 假设测试：验证数据流假设
    4. 实施：修复根因
    """

    _HANDLED_PATTERNS: ClassVar[set[str]] = {
        "runtime_error",
        "logic_error",
        "attribute_error",
        "key_error",
        "index_error",
        "type_error",
        "value_error",
    }

    def __init__(self) -> None:
        super().__init__(DebugStrategy.TRACE_BACKWARD)

    @property
    def name(self) -> str:
        return "反向追溯策略"

    @property
    def description(self) -> str:
        return "从错误点回溯数据流，找出问题的根本原因"

    def can_handle(self, context: ErrorContext) -> bool:
        """判断是否能处理此错误。

        适用于大多数运行时和逻辑错误。
        """
        error_type_lower = context.error_type.lower()
        return any(pattern in error_type_lower for pattern in self._HANDLED_PATTERNS)

    def generate_plan(self, context: ErrorContext) -> DebugPlan:
        """生成反向追溯调试计划。"""
        steps = []

        # Phase 1: 根因调查 - 收集错误点信息
        steps.extend(self._generate_investigation_steps(context))

        # Phase 2: 模式分析 - 识别数据流
        steps.extend(self._generate_analysis_steps(context))

        # Phase 3: 假设测试 - 验证数据流假设
        steps.extend(self._generate_hypothesis_steps(context))

        # Phase 4: 实施 - 修复根因
        steps.extend(self._generate_implementation_steps(context))

        return DebugPlan(
            plan_id=f"trace_backward_{uuid.uuid4().hex[:8]}",
            strategy=DebugStrategy.TRACE_BACKWARD,
            steps=steps,
            estimated_time=self._estimate_time(steps),
            rollback_plan="回滚到修改前的文件状态，使用git checkout或文件备份",
            success_criteria=[
                "错误不再复现",
                "相关功能测试通过",
                "数据流验证正确",
            ],
            failure_criteria=[
                "错误仍然存在",
                "引入新的错误",
                "无法定位根因",
            ],
        )

    def _generate_investigation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成根因调查步骤。"""
        steps = []

        # 步骤1：记录错误现场
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="记录错误现场信息",
                commands=[
                    f"# 错误类型: {context.error_type}",
                    f"# 错误消息: {context.error_message}",
                    f"# 堆栈跟踪:\n{context.stack_trace}",
                ],
                expected_outcome="完整记录错误现场的所有信息",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤2：定位错误发生点
        if context.file_path and context.line_number:
            steps.append(
                DebugStep(
                    phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                    description="读取错误发生点的代码上下文",
                    commands=[
                        f"read_file --path {context.file_path} --offset {max(1, context.line_number - 5)} --limit 20",
                    ],
                    expected_outcome="获取错误发生点的前后代码上下文",
                    rollback_commands=[],
                    timeout_seconds=30,
                )
            )

        # 步骤3：查看相关文件变更
        if context.recent_changes:
            steps.append(
                DebugStep(
                    phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                    description="查看最近的代码变更",
                    commands=[
                        "git diff HEAD~5 --name-only",
                        f"git log --oneline -10 -- {context.file_path or '.'}",
                    ],
                    expected_outcome="识别最近可能导致错误的变更",
                    rollback_commands=[],
                    timeout_seconds=30,
                )
            )

        return steps

    def _generate_analysis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成模式分析步骤。"""
        steps = []

        # 步骤4：分析数据流
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="分析错误点的数据流",
                commands=[
                    "# 识别数据来源",
                    "# 1. 变量在哪里被定义？",
                    "# 2. 变量在哪里被修改？",
                    "# 3. 数据经过了哪些转换？",
                    f"search_code --pattern 'def .*{context.error_message[:20]}' --path .",
                ],
                expected_outcome="理解数据是如何流向错误点的",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        # 步骤5：识别调用链
        if context.file_path:
            # 预处理文件路径用于搜索模式
            file_path_no_ext = context.file_path.replace(".py", "")
            file_path_module = file_path_no_ext.replace("/", ".")
            file_path_name = file_path_no_ext.split("/")[-1]
            steps.append(
                DebugStep(
                    phase=DebugPhase.PATTERN_ANALYSIS,
                    description="追溯函数调用链",
                    commands=[
                        f"# 查找调用 {context.file_path} 中函数的位置",
                        f"search_code --pattern 'from.*{file_path_module}' --path .",
                        f"search_code --pattern 'import.*{file_path_name}' --path .",
                    ],
                    expected_outcome="理解函数调用链和依赖关系",
                    rollback_commands=[],
                    timeout_seconds=60,
                )
            )

        return steps

    def _generate_hypothesis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成假设测试步骤。"""
        steps = []

        # 步骤6：生成假设
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="生成可能的根因假设",
                commands=[
                    "# 基于收集的信息生成假设：",
                    "# 假设1: 输入数据格式不正确",
                    "# 假设2: 边界条件未处理",
                    "# 假设3: 状态被意外修改",
                    "# 假设4: 异步时序问题",
                    "# 假设5: 依赖版本不兼容",
                ],
                expected_outcome="列出所有可能的根因假设",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤7：设计验证实验
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="设计验证实验",
                commands=[
                    "# 对每个假设设计验证方法：",
                    "# 1. 添加日志输出关键变量值",
                    "# 2. 编写最小复现测试",
                    "# 3. 检查边界条件处理",
                    "# 4. 验证状态变化",
                ],
                expected_outcome="有明确的验证方法确认或排除假设",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        return steps

    def _generate_implementation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成实施步骤。"""
        steps = []

        # 步骤8：实施修复
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="实施根因修复",
                commands=[
                    "# 基于验证结果实施修复：",
                    "# 1. 修复根因（不是症状）",
                    "# 2. 添加防御性检查",
                    "# 3. 更新相关文档",
                ],
                expected_outcome="修复根因，消除错误",
                rollback_commands=[
                    f"git checkout -- {context.file_path}" if context.file_path else "# 手动回滚修改",
                ],
                timeout_seconds=120,
            )
        )

        # 步骤9：验证修复
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="验证修复效果",
                commands=[
                    "# 运行测试验证修复：",
                    "pytest -xvs test_regression.py  # 回归测试",
                    "pytest -xvs test_specific.py    # 特定测试",
                    "# 手动验证错误场景",
                ],
                expected_outcome="错误不再复现，所有测试通过",
                rollback_commands=[],
                timeout_seconds=120,
            )
        )

        return steps

    def _estimate_time(self, steps: list[DebugStep]) -> int:
        """估算调试时间。"""
        total_seconds = sum(step.timeout_seconds for step in steps)
        return max(5, total_seconds // 60)  # 最少5分钟


__all__ = ["TraceBackwardStrategy"]
