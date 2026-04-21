"""Pattern Match Strategy - 模式匹配策略。

对比工作示例找差异，通过模式识别定位问题。
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


class PatternMatchStrategy(BaseDebugStrategy):
    """模式匹配策略：对比工作示例找差异。

    适用于：
    - 配置错误
    - API使用错误
    - 语法/格式错误
    - 已知模式的变体
    """

    _HANDLED_PATTERNS: ClassVar[set[str]] = {
        "syntax_error",
        "config_error",
        "api_error",
        "format_error",
        "validation_error",
        "import_error",
        "module_not_found",
    }

    def __init__(self) -> None:
        super().__init__(DebugStrategy.PATTERN_MATCH)

    @property
    def name(self) -> str:
        return "模式匹配策略"

    @property
    def description(self) -> str:
        return "对比工作示例找差异，通过模式识别定位问题"

    def can_handle(self, context: ErrorContext) -> bool:
        """判断是否能处理此错误。"""
        error_type_lower = context.error_type.lower()
        return any(pattern in error_type_lower for pattern in self._HANDLED_PATTERNS)

    def generate_plan(self, context: ErrorContext) -> DebugPlan:
        """生成模式匹配调试计划。"""
        steps = []

        # Phase 1: 根因调查 - 收集错误模式
        steps.extend(self._generate_investigation_steps(context))

        # Phase 2: 模式分析 - 对比已知模式
        steps.extend(self._generate_analysis_steps(context))

        # Phase 3: 假设测试 - 验证模式假设
        steps.extend(self._generate_hypothesis_steps(context))

        # Phase 4: 实施 - 应用正确模式
        steps.extend(self._generate_implementation_steps(context))

        return DebugPlan(
            plan_id=f"pattern_match_{uuid.uuid4().hex[:8]}",
            strategy=DebugStrategy.PATTERN_MATCH,
            steps=steps,
            estimated_time=self._estimate_time(steps),
            rollback_plan="恢复原始配置或代码，撤销格式变更",
            success_criteria=[
                "错误模式被正确识别",
                "应用正确模式后错误消失",
                "与已知工作示例一致",
            ],
            failure_criteria=[
                "无法找到匹配模式",
                "模式应用后错误仍然存在",
                "引入新的不匹配",
            ],
        )

    def _generate_investigation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成根因调查步骤。"""
        steps = []

        # 步骤1：记录错误特征
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="记录错误的精确特征",
                commands=[
                    f"# 错误类型: {context.error_type}",
                    f"# 错误消息: {context.error_message}",
                    "# 提取关键特征：",
                    "# - 错误代码/位置",
                    "# - 涉及的文件/模块",
                    "# - 触发条件",
                ],
                expected_outcome="精确描述错误的特征",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤2：查找相似错误
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="在代码库中查找相似错误",
                commands=[
                    f"search_code --pattern '{context.error_message[:30]}' --path .",
                    "# 检查错误日志历史",
                    "# 查看是否有类似错误记录",
                ],
                expected_outcome="找到相似错误或相关记录",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        return steps

    def _generate_analysis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成模式分析步骤。"""
        steps = []

        # 步骤3：对比工作示例
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="对比工作示例和当前实现",
                commands=[
                    "# 查找正确使用的示例：",
                    "search_code --pattern 'correct_usage_pattern' --path .",
                    "# 对比差异：",
                    "# 1. 语法差异",
                    "# 2. 参数差异",
                    "# 3. 配置差异",
                    "# 4. 版本差异",
                ],
                expected_outcome="识别出当前实现与正确模式的差异",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        # 步骤4：分析错误模式库
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="查询已知错误模式",
                commands=[
                    "# 检查常见错误模式：",
                    "# - 拼写错误",
                    "# - 大小写问题",
                    "# - 路径分隔符",
                    "# - 编码问题",
                    "# - 版本兼容性",
                ],
                expected_outcome="匹配到已知的错误模式",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        return steps

    def _generate_hypothesis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成假设测试步骤。"""
        steps = []

        # 步骤5：生成模式假设
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="基于模式对比生成假设",
                commands=[
                    "# 可能的模式问题：",
                    "# 假设1: 使用了错误的API版本",
                    "# 假设2: 配置格式不正确",
                    "# 假设3: 缺少必要的依赖",
                    "# 假设4: 环境变量未设置",
                ],
                expected_outcome="列出所有可能的模式不匹配原因",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤6：验证模式修复
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="验证模式修复方案",
                commands=[
                    "# 对每个假设进行验证：",
                    "# 1. 应用正确的模式",
                    "# 2. 测试是否修复",
                    "# 3. 确认没有引入新问题",
                ],
                expected_outcome="确认正确的模式修复方案",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        return steps

    def _generate_implementation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成实施步骤。"""
        steps = []

        # 步骤7：应用正确模式
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="应用正确的模式",
                commands=[
                    "# 根据验证结果应用修复：",
                    "# 1. 修正语法/格式",
                    "# 2. 更新配置",
                    "# 3. 修正API调用",
                    "# 4. 添加缺失的依赖",
                ],
                expected_outcome="代码符合正确模式",
                rollback_commands=[
                    f"git checkout -- {context.file_path}" if context.file_path else "# 手动回滚",
                ],
                timeout_seconds=90,
            )
        )

        # 步骤8：验证修复
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="验证模式修复",
                commands=[
                    "# 验证修复效果：",
                    "# 1. 运行相关测试",
                    "# 2. 检查是否匹配正确模式",
                    "# 3. 确认没有回归",
                ],
                expected_outcome="错误消失且符合正确模式",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        return steps

    def _estimate_time(self, steps: list[DebugStep]) -> int:
        """估算调试时间。"""
        total_seconds = sum(step.timeout_seconds for step in steps)
        return max(5, total_seconds // 60)


__all__ = ["PatternMatchStrategy"]
