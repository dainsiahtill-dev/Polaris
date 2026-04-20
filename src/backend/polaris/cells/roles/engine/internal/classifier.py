"""Task Classifier - 任务分类器

根据任务特征自动选择合适的推理引擎策略。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .base import EngineStrategy

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 任务特征模式
# ═══════════════════════════════════════════════════════════════════════════

# ReAct 适合的任务模式
REACT_PATTERNS = [
    r"探索",
    r"搜索",
    r"查找",
    r"分析",
    r"调查",
    r"了解",
    r"检查",
    r"看看",
    r"what.*is",
    r"how.*work",
    r"find.*file",
    r"search.*code",
    r"explore",
    r"investigate",
    r"debug",
    r"troubleshoot",
]

# Plan-Solve 适合的任务模式
PLAN_SOLVE_PATTERNS = [
    r"实现",
    r"创建",
    r"生成",
    r"写",
    r"构建",
    r"开发",
    r"添加",
    r"修改",
    r"重构",
    r"修复",
    r"implement",
    r"create",
    r"generate",
    r"build",
    r"develop",
    r"add.*feature",
    r"fix.*bug",
    r"refactor",
]

# ToT 适合的任务模式
TOT_PATTERNS = [
    r"设计.*架构",
    r"选择.*方案",
    r"比较.*方案",
    r"评估",
    r"优化.*策略",
    r"多个.*选择",
    r"最佳.*方案",
    r"architecture.*design",
    r"design.*system",
    r"choose.*between",
    r"evaluate.*options",
    r"optimize.*strategy",
]

# Sequential 适合的任务模式
SEQUENTIAL_PATTERNS = [
    r"执行.*命令",
    r"运行.*测试",
    r"编译",
    r"部署",
    r"run.*test",
    r"execute.*command",
    r"build",
    r"deploy",
]


class TaskClassifier:
    """任务分类器

    根据任务特征自动选择合适的推理引擎策略。

    使用示例:
        >>> classifier = TaskClassifier()
        >>> strategy = classifier.classify("探索代码库结构")
        >>> print(strategy)  # EngineStrategy.REACT
    """

    def __init__(self) -> None:
        """初始化任务分类器"""
        self._react_patterns = [re.compile(p, re.IGNORECASE) for p in REACT_PATTERNS]
        self._plan_solve_patterns = [re.compile(p, re.IGNORECASE) for p in PLAN_SOLVE_PATTERNS]
        self._tot_patterns = [re.compile(p, re.IGNORECASE) for p in TOT_PATTERNS]
        self._sequential_patterns = [re.compile(p, re.IGNORECASE) for p in SEQUENTIAL_PATTERNS]

    def classify(
        self,
        task: str,
        context: dict[str, Any] | None = None,
    ) -> EngineStrategy:
        """分类任务并返回推荐的引擎策略

        Args:
            task: 任务描述
            context: 额外上下文信息（如角色、历史等）

        Returns:
            推荐的引擎策略
        """
        scores = self._calculate_scores(task, context)

        # 修复：安全处理空分数字典
        if not scores:
            logger.warning("Empty scores dict, returning default SEQUENTIAL")
            return EngineStrategy.SEQUENTIAL

        # 返回得分最高的策略
        try:
            best_strategy = max(scores.items(), key=lambda x: x[1])[0]
        except (ValueError, KeyError):
            # 如果 max 失败，返回默认策略
            best_strategy = EngineStrategy.SEQUENTIAL

        logger.debug(f"Task classified: {best_strategy.value} (scores: {scores})")

        return best_strategy

    def _calculate_scores(
        self,
        task: str,
        context: dict[str, Any] | None,
    ) -> dict[EngineStrategy, float]:
        """计算各策略的得分

        Args:
            task: 任务描述
            context: 额外上下文

        Returns:
            各策略的得分
        """
        scores = {
            EngineStrategy.REACT: 0.0,
            EngineStrategy.PLAN_SOLVE: 0.0,
            EngineStrategy.TOT: 0.0,
            EngineStrategy.SEQUENTIAL: 0.0,
        }

        # 模式匹配得分
        scores[EngineStrategy.REACT] += self._match_patterns(task, self._react_patterns) * 2.0
        scores[EngineStrategy.PLAN_SOLVE] += self._match_patterns(task, self._plan_solve_patterns) * 2.0
        scores[EngineStrategy.TOT] += self._match_patterns(task, self._tot_patterns) * 3.0  # ToT 需要更精确匹配
        scores[EngineStrategy.SEQUENTIAL] += self._match_patterns(task, self._sequential_patterns) * 1.5

        # 上下文调整
        if context:
            scores = self._adjust_by_context(scores, context)

        # 默认分数（确保不返回 0 分）
        for strategy in scores:
            if scores[strategy] == 0.0:
                scores[strategy] = 0.1

        return scores

    def _match_patterns(self, text: str, patterns: list[re.Pattern]) -> float:
        """计算文本与模式的匹配得分

        Args:
            text: 待匹配文本
            patterns: 模式列表

        Returns:
            匹配得分
        """
        score = 0.0
        for pattern in patterns:
            if pattern.search(text):
                score += 1.0
        return score

    def _adjust_by_context(
        self,
        scores: dict[EngineStrategy, float],
        context: dict[str, Any],
    ) -> dict[EngineStrategy, float]:
        """根据上下文调整得分

        Args:
            scores: 原始得分
            context: 上下文信息

        Returns:
            调整后的得分
        """
        # 角色调整
        role = context.get("role", "").lower()
        if role == "director":
            # Director 更适合使用 Sequential
            scores[EngineStrategy.SEQUENTIAL] += 1.0
        elif role == "architect":
            # Architect 更适合使用 ToT
            scores[EngineStrategy.TOT] += 1.0
        elif role == "pm":
            # PM 更适合使用 Plan-Solve
            scores[EngineStrategy.PLAN_SOLVE] += 1.0

        # 历史任务调整
        previous_strategy = context.get("previous_strategy")
        if previous_strategy:
            # 修复：确保 previous_strategy 是 EngineStrategy 类型
            if isinstance(previous_strategy, EngineStrategy):
                # 如果之前成功，略微增加该策略的得分
                if previous_strategy in scores:
                    scores[previous_strategy] += 0.5
            elif isinstance(previous_strategy, str):
                # 如果是字符串，尝试转换为 EngineStrategy
                try:
                    prev = EngineStrategy(previous_strategy)
                    if prev in scores:
                        scores[prev] += 0.5
                except ValueError:
                    pass

        # 复杂度调整
        complexity = context.get("complexity", "medium")
        if complexity == "high":
            scores[EngineStrategy.TOT] += 1.0
            scores[EngineStrategy.REACT] += 0.5
        elif complexity == "low":
            scores[EngineStrategy.SEQUENTIAL] += 0.5

        return scores

    def get_reason(self, task: str) -> str:
        """获取分类原因

        Args:
            task: 任务描述

        Returns:
            分类原因说明
        """
        scores = self._calculate_scores(task, None)
        reasons = []

        if scores[EngineStrategy.REACT] > 1.0:
            reasons.append("任务包含探索/搜索性质")
        if scores[EngineStrategy.PLAN_SOLVE] > 1.0:
            reasons.append("任务需要实现或创建内容")
        if scores[EngineStrategy.TOT] > 1.0:
            reasons.append("任务涉及架构设计或方案选择")
        if scores[EngineStrategy.SEQUENTIAL] > 0.5:
            reasons.append("任务包含明确的执行步骤")

        if not reasons:
            reasons.append("使用默认顺序执行策略")

        return "; ".join(reasons)


# 全局分类器实例
_classifier: TaskClassifier | None = None


def get_task_classifier() -> TaskClassifier:
    """获取全局任务分类器"""
    global _classifier
    if _classifier is None:
        _classifier = TaskClassifier()
    return _classifier


def classify_task(
    task: str,
    context: dict[str, Any] | None = None,
) -> EngineStrategy:
    """对任务进行分类

    Args:
        task: 任务描述
        context: 额外上下文

    Returns:
        推荐的引擎策略
    """
    return get_task_classifier().classify(task, context)
