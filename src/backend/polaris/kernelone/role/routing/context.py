"""Routing Context and User Preference Data Structures."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.role.routing.semantic.inferrer import SemanticIntentInferer

logger = logging.getLogger(__name__)


@dataclass
class UserPreference:
    """用户沟通风格偏好"""

    verbose_level: str = "medium"  # low, medium, high
    communication_style: str = "direct"  # direct, formal, casual
    formality: str = "neutral"  # casual, neutral, formal
    persona_style_preference: str = ""


@dataclass
class RoutingContext:
    """路由上下文

    包含任务类型、领域、意图、会话阶段、工作区状态等完整路由决策所需信息。
    """

    task_type: str  # new_crate, refactor, bug_fix, ...
    domain: str  # python, typescript, rust, ...
    intent: str  # implement, design, analyze, review, ...
    constraints: dict[str, Any] = field(default_factory=dict)
    user_preference: UserPreference = field(default_factory=UserPreference)
    session_id: str = ""
    # v1.1 新增字段
    session_phase: str = "ideation"  # ideation→blueprint→execution→verification
    workspace_state: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, message: str, **kwargs: Any) -> RoutingContext:
        """从消息推断上下文字段"""
        # 这会调用 SemanticIntentInferer
        inferer = SemanticIntentInferer()
        result = inferer.infer(message)
        return cls(task_type=result.task_type, domain=result.domain, intent=result.intent, **kwargs)


@dataclass
class IntentInferenceResult:
    """两段式意图推断结果"""

    intent: str
    domain: str
    task_type: str
    confidence: float  # 0.0 - 1.0
    method: str  # "rule_based" | "semantic_llm"
