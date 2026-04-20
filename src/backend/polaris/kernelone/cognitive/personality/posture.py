"""Posture Switching Engine - 交互姿态切换引擎."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InteractionPosture(str, Enum):
    """5 Interaction postures."""

    PROACTIVE_INFERENCE = "proactive_inference"
    TRANSPARENT_REASONING = "transparent_reasoning"
    ADMIT_IGNORANCE = "admit_ignorance"
    GUIDE_LEARNING = "guide_learning"
    MANAGE_EXPECTATIONS = "manage_expectations"


@dataclass(frozen=True)
class PostureGuidance:
    """Generated guidance for current posture."""

    primary_posture: InteractionPosture
    reasoning_transparency_level: str  # minimal | standard | full
    confidence_expression_required: bool
    response_template: str


def select_posture_for_intent(
    intent_type: str,
    role_id: str,
    uncertainty_level: float,
    stakes_level: str,
) -> PostureGuidance:
    """
    Select appropriate posture based on intent and context.

    Args:
        intent_type: Type of intent (create_file, modify_file, etc.)
        role_id: Role identifier
        uncertainty_level: 0.0-1.0, higher = more uncertain
        stakes_level: low | medium | high
    """
    # High uncertainty + high stakes = transparent reasoning
    if uncertainty_level > 0.6 and stakes_level == "high":
        return PostureGuidance(
            primary_posture=InteractionPosture.TRANSPARENT_REASONING,
            reasoning_transparency_level="full",
            confidence_expression_required=True,
            response_template="我的推理过程：{reasoning}。我的置信度：{confidence}。",
        )

    # Unknown/don't know = admit ignorance
    if uncertainty_level > 0.7:
        return PostureGuidance(
            primary_posture=InteractionPosture.ADMIT_IGNORANCE,
            reasoning_transparency_level="standard",
            confidence_expression_required=True,
            response_template="我不确定{topic}，因为{reasoning}。我有以下途径：{paths}",
        )

    # Low stakes, low uncertainty = proactive
    if stakes_level == "low" and uncertainty_level < 0.4 and role_id == "scout":
        return PostureGuidance(
            primary_posture=InteractionPosture.PROACTIVE_INFERENCE,
            reasoning_transparency_level="standard",
            confidence_expression_required=False,
            response_template="基于你的描述，我推断你想要{goal}。让我先确认：{assumption}？",
        )

    # Planning intent = manage expectations
    if intent_type == "plan":
        return PostureGuidance(
            primary_posture=InteractionPosture.MANAGE_EXPECTATIONS,
            reasoning_transparency_level="standard",
            confidence_expression_required=True,
            response_template="关于{topic}，我理解你希望{expectation}。关于质量和时间：{constraints}",
        )

    # Default to transparent reasoning
    return PostureGuidance(
        primary_posture=InteractionPosture.TRANSPARENT_REASONING,
        reasoning_transparency_level="standard",
        confidence_expression_required=True,
        response_template="我的推理过程：{reasoning}。我的置信度：{confidence}。",
    )
