"""ApprovalPolicy - 人工审批策略。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 ApprovalPolicy

基于配置列表决定哪些工具需要人工审批。
"""

from __future__ import annotations

import fnmatch
import os

from .core import CanonicalToolCall, PolicyViolation


class ApprovalPolicy:
    """人工审批策略。

    Blueprint: §11 ApprovalPolicy

    Phase 3: 基于配置列表决定哪些工具需要人工审批。
    Phase 4: 支持基于参数内容动态决定（如写文件内容包含敏感信息）。

    审批行为：
        - requires_approval 的调用被标记为 requires_approval=True，
          不直接 blocked，而是返回给上层（TurnEngine）暂停等待人工确认。
        - 上层通过 ApprovalPolicy.approve() / reject() 注册审批结果。
    """

    def __init__(
        self,
        *,
        require_approval_for: list[str] | None = None,
        require_approval_patterns: list[str] | None = None,
    ) -> None:
        """构造审批策略。

        Args:
            require_approval_for: 必须审批的工具名列表（精确匹配）。
            require_approval_patterns: 必须审批的工具名模式列表（fnmatch 风格）。
        """
        self.require_approval_for = set(require_approval_for or [])
        self.require_approval_patterns = require_approval_patterns or []
        # 待审批队列: call_id -> CanonicalToolCall
        self._pending: dict[str, CanonicalToolCall] = {}

    @classmethod
    def from_env(cls) -> ApprovalPolicy:
        """从环境变量构造（Phase 3 默认空策略）。"""
        raw = os.environ.get("KERNELONE_REQUIRE_APPROVAL_FOR", "").strip()
        if not raw:
            return cls()
        tools = [t.strip() for t in raw.split(",") if t.strip()]
        return cls(require_approval_for=tools)

    def evaluate(
        self,
        calls: list[CanonicalToolCall],
    ) -> tuple[list[CanonicalToolCall], list[CanonicalToolCall], list[PolicyViolation]]:
        """评估哪些调用需要人工审批。

        Args:
            calls: 待评估的工具调用列表。

        Returns:
            (auto_approved_calls, requires_approval_calls, violations)
        """
        auto_approved: list[CanonicalToolCall] = []
        requires_approval: list[CanonicalToolCall] = []
        violations: list[PolicyViolation] = []

        for call in calls:
            tool_lower = call.tool.lower()
            if self._requires_approval(tool_lower):
                requires_approval.append(call)
                self._pending[call.call_id or call.tool_key()] = call
                violations.append(
                    PolicyViolation(
                        policy="ApprovalPolicy",
                        tool=call.tool,
                        reason="requires_human_approval",
                        is_critical=False,
                    )
                )
            else:
                auto_approved.append(call)

        return auto_approved, requires_approval, violations

    def _requires_approval(self, tool_name: str) -> bool:
        """判断工具是否需要审批。"""
        if tool_name in {t.lower() for t in self.require_approval_for}:
            return True
        return any(fnmatch.fnmatch(tool_name, pattern.lower()) for pattern in self.require_approval_patterns)

    def approve(self, call_id: str) -> bool:
        """注册审批通过。返回 True 表示找到并批准。"""
        if call_id in self._pending:
            del self._pending[call_id]
            return True
        return False

    def reject(self, call_id: str, reason: str = "") -> bool:
        """注册审批拒绝。返回 True 表示找到并拒绝。"""
        if call_id in self._pending:
            del self._pending[call_id]
            return True
        return False

    def clear_pending(self) -> None:
        """清除所有待审批项（turn 结束时调用）。"""
        self._pending.clear()

    @property
    def pending_count(self) -> int:
        return len(self._pending)


__all__ = [
    "ApprovalPolicy",
]
