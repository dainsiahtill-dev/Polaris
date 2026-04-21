"""Strategy Engine - 调试策略引擎主类。

根据错误类型自动选择最佳调试策略。
"""

from __future__ import annotations

import logging
from typing import ClassVar

from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    ErrorClassification,
    ErrorContext,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.base import (
    BaseDebugStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.binary_search import (
    BinarySearchStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.conditional_wait import (
    ConditionalWaitStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.defense_in_depth import (
    DefenseInDepthStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.pattern_match import (
    PatternMatchStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.strategies.trace_backward import (
    TraceBackwardStrategy,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import ErrorCategory

logger = logging.getLogger(__name__)


class DebugStrategyEngine:
    """调试策略引擎：根据错误类型自动选择最佳调试策略。

    实现Superpowers的"Systematic Debugging"核心设计：
    - 四阶段调试流程
    - 防御性编程四层验证
    - 条件等待技术
    """

    # 策略优先级（从高到低）
    _STRATEGY_PRIORITY: ClassVar[list[type[BaseDebugStrategy]]] = [
        ConditionalWaitStrategy,  # 时序问题优先
        PatternMatchStrategy,  # 模式匹配最快
        DefenseInDepthStrategy,  # 防御问题
        BinarySearchStrategy,  # 回归问题
        TraceBackwardStrategy,  # 通用兜底
    ]

    def __init__(self) -> None:
        """初始化策略引擎。"""
        self._strategies: list[BaseDebugStrategy] = [
            ConditionalWaitStrategy(),
            PatternMatchStrategy(),
            DefenseInDepthStrategy(),
            BinarySearchStrategy(),
            TraceBackwardStrategy(),
        ]
        logger.debug("DebugStrategyEngine initialized with %d strategies", len(self._strategies))

    def select_strategy(self, context: ErrorContext) -> DebugPlan:
        """根据错误上下文选择最佳调试策略。

        Args:
            context: 错误上下文

        Returns:
            最佳调试计划
        """
        logger.info("Selecting strategy for error: %s", context.error_type)

        # 尝试每个策略
        for strategy in self._strategies:
            if strategy.can_handle(context):
                logger.info("Selected strategy: %s", strategy.name)
                return strategy.generate_plan(context)

        # 如果没有策略能处理，使用默认的反向追溯策略
        logger.warning("No specific strategy found, using default TraceBackwardStrategy")
        return TraceBackwardStrategy().generate_plan(context)

    def classify_error(self, context: ErrorContext) -> ErrorClassification:
        """错误分类（增强现有ErrorClassifier）。

        Args:
            context: 错误上下文

        Returns:
            错误分类结果
        """
        category = self._classify_error(context)
        severity = self._determine_severity(context)
        root_cause = self._infer_root_cause(context, category)

        # 生成调试计划
        debug_plan = self.select_strategy(context)

        # 建议的策略列表
        suggested_strategies = [s.strategy_type for s in self._strategies if s.can_handle(context)]

        return ErrorClassification(
            category=category,
            severity=severity,
            root_cause_likely=root_cause,
            debug_plan=debug_plan,
            suggested_strategies=suggested_strategies,
        )

    def _classify_error(self, context: ErrorContext) -> ErrorCategory:
        """根据错误上下文分类错误。"""
        error_type_lower = context.error_type.lower()
        error_msg_lower = context.error_message.lower()

        # 时序相关错误
        timing_patterns = ["timeout", "async", "race", "timing", "wait", "lock"]
        if any(p in error_type_lower or p in error_msg_lower for p in timing_patterns):
            return ErrorCategory.TIMING_ERROR

        # 语法错误
        syntax_patterns = ["syntax", "parse", "indent", "invalid syntax"]
        if any(p in error_type_lower or p in error_msg_lower for p in syntax_patterns):
            return ErrorCategory.SYNTAX_ERROR

        # 资源错误
        resource_patterns = ["resource", "memory", "disk", "file", "not found"]
        if any(p in error_type_lower or p in error_msg_lower for p in resource_patterns):
            return ErrorCategory.RESOURCE_ERROR

        # 权限错误
        permission_patterns = ["permission", "access", "denied", "unauthorized"]
        if any(p in error_type_lower or p in error_msg_lower for p in permission_patterns):
            return ErrorCategory.PERMISSION_ERROR

        # 网络错误
        network_patterns = ["network", "connection", "socket", "http", "url"]
        if any(p in error_type_lower or p in error_msg_lower for p in network_patterns):
            return ErrorCategory.NETWORK_ERROR

        # 逻辑错误（默认）
        return ErrorCategory.LOGIC_ERROR

    def _determine_severity(self, context: ErrorContext) -> str:
        """确定错误严重程度。"""
        error_msg_lower = context.error_message.lower()

        # Critical: 系统级错误
        critical_patterns = ["system", "kernel", "panic", "crash", "fatal"]
        if any(p in error_msg_lower for p in critical_patterns):
            return "critical"

        # High: 功能完全不可用
        high_patterns = ["cannot", "unable", "failed", "error"]
        if any(p in error_msg_lower for p in high_patterns):
            return "high"

        # Medium: 部分功能受影响
        medium_patterns = ["warning", "deprecated", "slow"]
        if any(p in error_msg_lower for p in medium_patterns):
            return "medium"

        # Low: 轻微问题
        return "low"

    def _infer_root_cause(self, context: ErrorContext, category: ErrorCategory) -> str:
        """推断可能的根因。"""
        root_causes = {
            ErrorCategory.SYNTAX_ERROR: "代码语法不正确或格式错误",
            ErrorCategory.RUNTIME_ERROR: "运行时条件未满足",
            ErrorCategory.LOGIC_ERROR: "程序逻辑存在缺陷",
            ErrorCategory.TIMING_ERROR: "时序或竞态条件问题",
            ErrorCategory.RESOURCE_ERROR: "资源不可用或耗尽",
            ErrorCategory.PERMISSION_ERROR: "权限不足或访问控制问题",
            ErrorCategory.NETWORK_ERROR: "网络连接或通信问题",
            ErrorCategory.UNKNOWN_ERROR: "未知原因，需要进一步调查",
        }
        return root_causes.get(category, "需要进一步调查")

    def get_available_strategies(self) -> list[dict[str, str]]:
        """获取所有可用策略的信息。

        Returns:
            策略信息列表
        """
        return [
            {
                "name": s.name,
                "description": s.description,
                "type": s.strategy_type.value,
            }
            for s in self._strategies
        ]


__all__ = ["DebugStrategyEngine"]
