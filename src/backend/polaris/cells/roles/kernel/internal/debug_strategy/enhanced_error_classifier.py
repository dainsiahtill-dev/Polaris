"""Enhanced Error Classifier - 增强版错误分类器。

集成DebugStrategyEngine，提供系统化调试能力。

这是现有ErrorClassifier的增强版本，不破坏现有契约。
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.debug_strategy import (
    DebugStrategyEngine,
    ErrorContext,
)


class EnhancedErrorClassifier:
    """增强版错误分类器。

    在现有ErrorClassifier基础上集成DebugStrategyEngine，
    提供系统化的调试策略支持。

    Usage:
        classifier = EnhancedErrorClassifier()
        classification = classifier.classify_with_strategy(
            error=exception,
            context={"error_type": "runtime", "file_path": "main.py"}
        )
        # classification 包含 debug_plan
    """

    def __init__(self) -> None:
        """初始化增强版分类器。"""
        self.strategy_engine = DebugStrategyEngine()

    def classify_with_strategy(
        self,
        error: Exception,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """分类错误并附加调试策略。

        Args:
            error: 异常对象
            context: 错误上下文字典

        Returns:
            包含分类和调试计划的字典
        """
        # 基本分类
        basic_classification = self._basic_classify(error)

        # 构建ErrorContext
        error_context = ErrorContext.from_dict(
            {
                "error_type": type(error).__name__,
                "error_message": str(error),
                "stack_trace": (getattr(error, "__traceback__", None) and self._format_traceback(error.__traceback__))
                or "",
                **context,
            }
        )

        # 使用策略引擎生成调试计划
        debug_plan = self.strategy_engine.select_strategy(error_context)
        full_classification = self.strategy_engine.classify_error(error_context)

        return {
            "basic_classification": basic_classification,
            "category": full_classification.category.value,
            "severity": full_classification.severity,
            "root_cause_likely": full_classification.root_cause_likely,
            "debug_plan": {
                "plan_id": debug_plan.plan_id,
                "strategy": debug_plan.strategy.value,
                "estimated_time": debug_plan.estimated_time,
                "rollback_plan": debug_plan.rollback_plan,
                "step_count": len(debug_plan.steps),
                "phases": list({step.phase.value for step in debug_plan.steps}),
            },
            "suggested_strategies": [s.value for s in full_classification.suggested_strategies],
        }

    def _basic_classify(self, error: Exception) -> str:
        """基本错误分类。

        Args:
            error: 异常对象

        Returns:
            错误类型字符串
        """
        error_type = type(error).__name__
        error_msg = str(error).lower()

        # 简单的分类逻辑
        if "syntax" in error_msg or "indent" in error_msg:
            return "syntax_error"
        elif "timeout" in error_msg or "timed out" in error_msg:
            return "timeout_error"
        elif "permission" in error_msg or "access" in error_msg:
            return "permission_error"
        elif "not found" in error_msg or "does not exist" in error_msg:
            return "not_found_error"
        elif "assert" in error_msg:
            return "assertion_error"
        else:
            return f"{error_type.lower()}_error"

    @staticmethod
    def _format_traceback(tb: Any) -> str:
        """格式化堆栈跟踪。

        Args:
            tb: traceback对象

        Returns:
            格式化的堆栈跟踪字符串
        """
        import traceback

        if tb is None:
            return ""

        try:
            return "".join(traceback.format_tb(tb))
        except Exception:  # noqa: BLE001
            return str(tb)

    def get_strategy_info(self) -> list[dict[str, str]]:
        """获取所有可用策略的信息。

        Returns:
            策略信息列表
        """
        return self.strategy_engine.get_available_strategies()


__all__ = ["EnhancedErrorClassifier"]
