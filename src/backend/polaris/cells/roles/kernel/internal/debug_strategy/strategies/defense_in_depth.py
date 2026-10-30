"""Defense In Depth Strategy - 防御深度策略。

四层验证法：输入验证→前置条件→不变量断言→后置条件验证。
这是Superpowers精华中的核心防御性编程技术。
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
    DefenseLayer,
)


class DefenseInDepthStrategy(BaseDebugStrategy):
    """防御深度策略：四层验证法。

    适用于：
    - 边界条件错误
    - 输入验证缺失
    - 状态不一致
    - 安全相关问题
    - 需要增强健壮性的代码

    四层验证：
    1. 输入验证层：验证所有输入
    2. 前置条件层：检查操作前提
    3. 不变量断言层：维护关键不变量
    4. 后置条件验证层：验证操作结果
    """

    _HANDLED_PATTERNS: ClassVar[set[str]] = {
        "assertion_error",
        "validation_error",
        "boundary_error",
        "security_error",
        "input_error",
        "state_error",
        "integrity_error",
    }

    def __init__(self) -> None:
        super().__init__(DebugStrategy.DEFENSE_IN_DEPTH)

    @property
    def name(self) -> str:
        return "防御深度策略"

    @property
    def description(self) -> str:
        return "四层验证法：输入验证→前置条件→不变量断言→后置条件验证"

    def can_handle(self, context: ErrorContext) -> bool:
        """判断是否能处理此错误。"""
        error_type_lower = context.error_type.lower()
        return any(pattern in error_type_lower for pattern in self._HANDLED_PATTERNS)

    def generate_plan(self, context: ErrorContext) -> DebugPlan:
        """生成防御深度调试计划。"""
        steps = []

        # Phase 1: 根因调查 - 识别缺失的防御层
        steps.extend(self._generate_investigation_steps(context))

        # Phase 2: 模式分析 - 分析防御缺口
        steps.extend(self._generate_analysis_steps(context))

        # Phase 3: 假设测试 - 验证防御假设
        steps.extend(self._generate_hypothesis_steps(context))

        # Phase 4: 实施 - 添加四层防御
        steps.extend(self._generate_implementation_steps(context))

        return DebugPlan(
            plan_id=f"defense_in_depth_{uuid.uuid4().hex[:8]}",
            strategy=DebugStrategy.DEFENSE_IN_DEPTH,
            steps=steps,
            estimated_time=self._estimate_time(steps),
            rollback_plan="回滚添加的防御代码，恢复原始实现",
            success_criteria=[
                "四层防御全部到位",
                "边界条件被正确处理",
                "输入验证完善",
                "不变量得到维护",
            ],
            failure_criteria=[
                "防御层不完整",
                "引入过度防御导致性能问题",
                "防御逻辑本身有bug",
            ],
        )

    def _generate_investigation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成根因调查步骤。"""
        steps = []

        # 步骤1：分析错误暴露的防御缺口
        steps.append(
            DebugStep(
                phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                description="分析错误暴露的防御缺口",
                commands=[
                    f"# 错误类型: {context.error_type}",
                    f"# 错误消息: {context.error_message}",
                    "# 分析哪一层防御缺失：",
                    "# - 输入是否被验证？",
                    "# - 前置条件是否被检查？",
                    "# - 不变量是否被维护？",
                    "# - 后置条件是否被验证？",
                ],
                expected_outcome="识别缺失的防御层",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤2：收集相关代码
        if context.file_path:
            steps.append(
                DebugStep(
                    phase=DebugPhase.ROOT_CAUSE_INVESTIGATION,
                    description="收集相关函数/方法的完整代码",
                    commands=[
                        f"read_file --path {context.file_path}",
                        "# 识别关键函数和边界",
                        "# 列出所有输入参数",
                        "# 识别状态依赖",
                    ],
                    expected_outcome="获得需要添加防御的完整代码上下文",
                    rollback_commands=[],
                    timeout_seconds=45,
                )
            )

        return steps

    def _generate_analysis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成模式分析步骤。"""
        steps = []

        # 步骤3：分析四层防御缺口
        steps.append(
            DebugStep(
                phase=DebugPhase.PATTERN_ANALYSIS,
                description="分析四层防御的具体缺口",
                commands=[
                    f"# 逐层分析 {context.file_path or '目标代码'}：",
                    "",
                    "# 第1层 - 输入验证：",
                    "# - 参数类型是否正确？",
                    "# - 参数范围是否合法？",
                    "# - 字符串长度/格式是否有效？",
                    "# - 集合是否为空？",
                    "",
                    "# 第2层 - 前置条件：",
                    "# - 对象状态是否正确？",
                    "# - 依赖是否已初始化？",
                    "# - 资源是否可用？",
                    "",
                    "# 第3层 - 不变量断言：",
                    "# - 关键变量是否保持有效？",
                    "# - 数据结构是否一致？",
                    "# - 状态转换是否合法？",
                    "",
                    "# 第4层 - 后置条件：",
                    "# - 返回值是否正确？",
                    "# - 副作用是否符合预期？",
                    "# - 状态是否正确更新？",
                ],
                expected_outcome="明确每层防御的具体缺口",
                rollback_commands=[],
                timeout_seconds=60,
            )
        )

        return steps

    def _generate_hypothesis_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成假设测试步骤。"""
        steps = []

        # 步骤4：生成防御假设
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="生成防御增强假设",
                commands=[
                    "# 可能的防御缺口：",
                    "# 假设1: 缺少输入参数验证",
                    "# 假设2: 边界条件未处理",
                    "# 假设3: 状态转换未检查",
                    "# 假设4: 返回值未验证",
                    "# 假设5: 并发访问未保护",
                ],
                expected_outcome="列出所有可能的防御缺口",
                rollback_commands=[],
                timeout_seconds=30,
            )
        )

        # 步骤5：设计防御验证实验
        steps.append(
            DebugStep(
                phase=DebugPhase.HYPOTHESIS_TESTING,
                description="设计防御验证实验",
                commands=[
                    "# 验证实验设计：",
                    "# 1. 构造边界输入测试",
                    "# 2. 构造无效输入测试",
                    "# 3. 构造并发访问测试",
                    "# 4. 构造状态异常测试",
                    "# 5. 验证防御是否触发",
                ],
                expected_outcome="有明确的验证方法确认防御有效性",
                rollback_commands=[],
                timeout_seconds=45,
            )
        )

        return steps

    def _generate_implementation_steps(self, context: ErrorContext) -> list[DebugStep]:
        """生成实施步骤。"""
        steps = []

        # 步骤6：实现第1-2层防御（输入验证和前置条件）
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="实现输入验证和前置条件检查",
                commands=[
                    "# 第1层 - 输入验证：",
                    "# if not isinstance(param, expected_type):",
                    "#     raise TypeError(...)",
                    "# if param < min_value or param > max_value:",
                    "#     raise ValueError(...)",
                    "",
                    "# 第2层 - 前置条件：",
                    "# if not self._initialized:",
                    "#     raise RuntimeError('Object not initialized')",
                    "# if resource is None:",
                    "#     raise ResourceError('Resource not available')",
                ],
                expected_outcome="输入验证和前置条件检查到位",
                rollback_commands=[
                    f"git checkout -- {context.file_path}" if context.file_path else "# 手动回滚",
                ],
                timeout_seconds=90,
            )
        )

        # 步骤7：实现第3-4层防御（不变量和后置条件）
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="实现不变量断言和后置条件验证",
                commands=[
                    "# 第3层 - 不变量断言：",
                    "# assert len(self._items) >= 0, 'Items should not be negative'",
                    "# assert self._state in VALID_STATES, f'Invalid state: {self._state}'",
                    "",
                    "# 第4层 - 后置条件：",
                    "# result = self._process()",
                    "# if result is None:",
                    "#     raise ProcessingError('Processing returned None')",
                    "# assert result >= 0, 'Result should be non-negative'",
                ],
                expected_outcome="不变量断言和后置条件验证到位",
                rollback_commands=[
                    f"git checkout -- {context.file_path}" if context.file_path else "# 手动回滚",
                ],
                timeout_seconds=90,
            )
        )

        # 步骤8：验证四层防御
        steps.append(
            DebugStep(
                phase=DebugPhase.IMPLEMENTATION,
                description="验证四层防御完整性",
                commands=[
                    "# 防御验证清单：",
                    f"# [{DefenseLayer.INPUT_VALIDATION.name}] 输入是否都被验证？",
                    f"# [{DefenseLayer.PRECONDITION_CHECK.name}] 前置条件是否都检查？",
                    f"# [{DefenseLayer.INVARIANT_ASSERTION.name}] 关键不变量是否断言？",
                    f"# [{DefenseLayer.POSTCONDITION_VERIFY.name}] 后置条件是否验证？",
                    "",
                    "# 运行测试：",
                    "pytest -xvs test_defense.py",
                    "# 测试边界条件",
                    "# 测试无效输入",
                    "# 测试异常情况",
                ],
                expected_outcome="四层防御全部通过验证",
                rollback_commands=[],
                timeout_seconds=90,
            )
        )

        return steps

    def _estimate_time(self, steps: list[DebugStep]) -> int:
        """估算调试时间。"""
        total_seconds = sum(step.timeout_seconds for step in steps)
        return max(15, total_seconds // 60)  # 防御深度需要更多时间


__all__ = ["DefenseInDepthStrategy"]
