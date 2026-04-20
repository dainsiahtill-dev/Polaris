"""Elegant Uncertainty Expression Framework - 优雅的"不知道"表达."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class UncertaintyLevel(IntEnum):
    """5 levels of uncertainty expression."""

    LEVEL_1_FULL_KNOWLEDGE = 1  # "确定。这是..."
    LEVEL_2_HIGH_CONFIDENCE = 2  # "我有很高的置信度，但不完全确定..."
    LEVEL_3_MEDIUM_CONFIDENCE = 3  # "这是我的理解，但请验证..."
    LEVEL_4_LOW_CONFIDENCE = 4  # "我不确定，但我推断..."
    LEVEL_5_COMPLETE_UNCERTAINTY = 5  # "我不知道。我有以下途径..."


@dataclass(frozen=True)
class UncertaintyExpression:
    """Expression template for uncertainty levels."""

    level: UncertaintyLevel
    opening_phrase: str
    reasoning_bridge: str
    closing_action: str
    alternative_paths: tuple[str, ...] | None = None


UNCERTAINTY_EXPRESSIONS: dict[UncertaintyLevel, UncertaintyExpression] = {
    UncertaintyLevel.LEVEL_1_FULL_KNOWLEDGE: UncertaintyExpression(
        level=UncertaintyLevel.LEVEL_1_FULL_KNOWLEDGE,
        opening_phrase="确定。",
        reasoning_bridge="这是{rationale}",
        closing_action="无需进一步验证。",
    ),
    UncertaintyLevel.LEVEL_2_HIGH_CONFIDENCE: UncertaintyExpression(
        level=UncertaintyLevel.LEVEL_2_HIGH_CONFIDENCE,
        opening_phrase="我有很高的置信度，但不完全确定。",
        reasoning_bridge="根据我的知识，{assertion}，但我应该检查{caveat}",
        closing_action="建议进行最后确认。",
    ),
    UncertaintyLevel.LEVEL_3_MEDIUM_CONFIDENCE: UncertaintyExpression(
        level=UncertaintyLevel.LEVEL_3_MEDIUM_CONFIDENCE,
        opening_phrase="这是我的理解，但请验证。",
        reasoning_bridge="我认为{assertion}，这在{conditions}情况下成立",
        closing_action="但取决于具体情况。",
    ),
    UncertaintyLevel.LEVEL_4_LOW_CONFIDENCE: UncertaintyExpression(
        level=UncertaintyLevel.LEVEL_4_LOW_CONFIDENCE,
        opening_phrase="我不确定。",
        reasoning_bridge="但基于类比/推断，{assertion}",
        closing_action="强烈建议独立验证。",
    ),
    UncertaintyLevel.LEVEL_5_COMPLETE_UNCERTAINTY: UncertaintyExpression(
        level=UncertaintyLevel.LEVEL_5_COMPLETE_UNCERTAINTY,
        opening_phrase="我不知道。",
        reasoning_bridge="",
        closing_action="",
        alternative_paths=(
            "我可以帮你搜索相关文档",
            "我可以查阅代码库中的类似实现",
            "我可以总结已知的相关模式供你参考",
            "我可以先分析相关部分再给出判断",
        ),
    ),
}


def express_uncertainty(level: UncertaintyLevel, context: dict[str, Any]) -> str:
    """
    Generate elegant uncertainty expression.

    Args:
        level: Uncertainty level
        context: Dict with keys like 'topic', 'assertion', 'rationale', 'caveat', 'conditions'
    """
    expr = UNCERTAINTY_EXPRESSIONS[level]

    if level == UncertaintyLevel.LEVEL_5_COMPLETE_UNCERTAINTY:
        paths = "\n".join(f"- {p}" for p in expr.alternative_paths or [])
        return f"{expr.opening_phrase}\n我有以下途径可以找到答案：\n{paths}"

    # Format template with context
    opening = expr.opening_phrase

    reasoning = ""
    if expr.reasoning_bridge:
        try:
            reasoning = expr.reasoning_bridge.format(**context)
        except KeyError:
            reasoning = expr.reasoning_bridge

    closing = expr.closing_action

    parts = [p for p in [opening, reasoning, closing] if p]
    return "".join(parts)


def confidence_to_uncertainty_level(confidence: float) -> UncertaintyLevel:
    """Convert confidence score to uncertainty level."""
    if confidence >= 0.95:
        return UncertaintyLevel.LEVEL_1_FULL_KNOWLEDGE
    elif confidence >= 0.80:
        return UncertaintyLevel.LEVEL_2_HIGH_CONFIDENCE
    elif confidence >= 0.50:
        return UncertaintyLevel.LEVEL_3_MEDIUM_CONFIDENCE
    elif confidence >= 0.20:
        return UncertaintyLevel.LEVEL_4_LOW_CONFIDENCE
    else:
        return UncertaintyLevel.LEVEL_5_COMPLETE_UNCERTAINTY
