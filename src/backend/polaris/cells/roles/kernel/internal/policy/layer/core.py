"""Policy Layer core dataclasses.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 Policy Layer - core module

核心数据结构：
- CanonicalToolCall: 策略层工具调用描述符
- PolicyViolation: 策略违规记录
- EvaluationResult: 单个工具调用评估结果
- PolicyResult: 整体评估结果

NOTE: CanonicalToolCall is a policy-layer-specific type with stall detection methods.
For the canonical runtime ToolCall, see polaris.kernelone.llm.contracts.tool.ToolCall.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from polaris.kernelone.llm.contracts.tool import ToolCall


# ─────────────────────────────────────────────────────────────────────────────
# CanonicalToolCall - 策略层工具调用描述符
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class CanonicalToolCall:
    """策略层工具调用描述符。

    从 TurnEngine 传入 PolicyLayer 的规范化工件。
    由 TurnEngine._parse_content_and_thinking_tool_calls() 产生，
    或从流式解析产生。

    Attributes:
        tool: 工具名称（规范化后）。
        args: 工具参数字典。
        call_id: 调用 ID（可选，用于追踪）。
        raw_content: 原始文本内容（如有）。

    Note:
        This is a policy-layer-specific type with stall detection methods.
        For the canonical runtime ToolCall, see
        polaris.kernelone.llm.contracts.tool.ToolCall.
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    raw_content: str = ""

    def tool_key(self) -> str:
        """用于 stall detection 的规范化签名。"""
        return f"{self.tool}:{json.dumps(self.args, sort_keys=True, ensure_ascii=False)}"

    def signature(self) -> str:
        """用于 stall detection 的完整签名（包括 call_id）。"""
        return f"{self.call_id or ''}:{self.tool_key()}"

    @classmethod
    def from_tool_call(cls, tool_call: ToolCall) -> CanonicalToolCall:
        """Create CanonicalToolCall from canonical ToolCall.

        Args:
            tool_call: Canonical ToolCall from kernelone.llm.contracts.tool

        Returns:
            CanonicalToolCall instance for policy layer use.
        """
        return cls(
            tool=tool_call.name,
            args=dict(tool_call.arguments),
            call_id=tool_call.id,
            raw_content=tool_call.raw,
        )

    def to_tool_call(self) -> ToolCall:
        """Convert to canonical ToolCall.

        Returns:
            ToolCall instance for runtime use.
        """
        from polaris.kernelone.llm.contracts.tool import ToolCall

        return ToolCall(
            id=self.call_id,
            name=self.tool,
            arguments=dict(self.args),
            raw=self.raw_content,
        )


# ─────────────────────────────────────────────────────────────────────────────
# PolicyViolation — 策略违规
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True, frozen=True)
class PolicyViolation:
    """单条策略违规记录。

    Attributes:
        policy: 违规的策略名称（如 "ToolPolicy", "BudgetPolicy"）。
        tool: 工具名称。
        reason: 违规原因描述。
        is_critical: 是否为严重违规（导致 turn 直接终止）。
    """

    policy: str
    tool: str
    reason: str
    is_critical: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# EvaluationResult — 单个工具调用的策略评估结果
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class EvaluationResult:
    """单个工具调用的策略评估结果。

    Attributes:
        call: 对应的工具调用描述符。
        approved: 是否批准执行。
        violations: 违规列表（如果 approved=False）。
        requires_approval: 是否需要人工审批。
        approval_reason: 审批原因（如果 requires_approval=True）。
    """

    call: CanonicalToolCall
    approved: bool
    violations: tuple[PolicyViolation, ...] = field(default_factory=tuple)
    requires_approval: bool = False
    approval_reason: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# PolicyResult — 策略层整体评估结果
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PolicyResult:
    """PolicyLayer.evaluate() 的整体返回结果。

    Attributes:
        approved_calls: 批准执行的工具调用列表（按原顺序）。
        blocked_calls: 被拦截的工具调用列表（按原顺序）。
        requires_approval: 需要人工审批的调用列表。
        stop_reason: 非空时表示整体停止原因（由 BudgetPolicy 或 SafetyState 设置）。
        violations: 所有违规记录。
        budget_state: 评估后的预算快照（用于下次 evaluate 调用时传入）。
        evaluated_at: 评估时间戳（UTC）。
        exploration_stats: 探索工具统计（ExplorationToolPolicy 提供）。
    """

    approved_calls: list[CanonicalToolCall] = field(default_factory=list)
    blocked_calls: list[CanonicalToolCall] = field(default_factory=list)
    requires_approval: list[CanonicalToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    violations: tuple[PolicyViolation, ...] = field(default_factory=tuple)
    budget_state: dict[str, Any] = field(default_factory=dict)
    evaluated_at: datetime = field(default_factory=_utc_now)
    exploration_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def has_blocked(self) -> bool:
        return len(self.blocked_calls) > 0

    @property
    def has_approval_required(self) -> bool:
        return len(self.requires_approval) > 0

    @property
    def should_stop(self) -> bool:
        return self.stop_reason is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved_count": len(self.approved_calls),
            "blocked_count": len(self.blocked_calls),
            "approval_required_count": len(self.requires_approval),
            "stop_reason": self.stop_reason,
            "violations": [
                {"policy": v.policy, "tool": v.tool, "reason": v.reason, "critical": v.is_critical}
                for v in self.violations
            ],
            "budget_state": self.budget_state,
            "exploration_stats": self.exploration_stats,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


__all__ = [
    "CanonicalToolCall",
    "EvaluationResult",
    "PolicyResult",
    "PolicyViolation",
]
