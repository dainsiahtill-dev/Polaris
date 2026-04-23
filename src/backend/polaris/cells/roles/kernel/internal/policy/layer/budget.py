"""BudgetPolicy - 预算执行策略。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 BudgetPolicy

基于 ConversationState.Budgets 执行预算检查。
维护 BudgetState（copy-on-write）以支持跨 evaluate 调用的累积。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.security.dangerous_patterns import is_dangerous_command as _is_dangerous_command

from .core import CanonicalToolCall, PolicyViolation


@dataclass(slots=True)
class BudgetPolicyConfig:
    """预算策略配置。"""

    max_tool_calls: int = 64
    max_turns: int = 64
    max_wall_time_seconds: float = 900.0
    max_stall_cycles: int = 2
    max_tokens: int | None = None


class BudgetPolicy:
    """预算执行策略。

    Blueprint: §11 ApprovalPolicy + BudgetPolicy

    基于 ConversationState.Budgets 执行预算检查。
    维护 BudgetState（copy-on-write）以支持跨 evaluate 调用的累积。

    Phase 3 从环境变量和 ConversationState.Budgets 初始化。
    Phase 4 可从 Profile 初始化。
    Phase 5 新增自适应 stall 检测（根据任务进度动态调整阈值）。
    """

    def __init__(
        self,
        max_tool_calls: int = 64,
        max_turns: int = 64,
        max_wall_time_seconds: float = 900.0,
        max_stall_cycles: int = 2,
        max_tokens: int | None = None,
        # Phase 5 新增: 自适应 stall 检测参数
        enable_adaptive_stall: bool = True,
        min_stall_cycles: int = 3,
        max_stall_cycles_limit: int = 8,
    ) -> None:
        self.max_tool_calls = max_tool_calls
        if max_turns <= 0:
            raise ValueError(f"max_turns must be positive, got {max_turns}")
        self.max_turns = max_turns
        self.max_wall_time_seconds = max_wall_time_seconds
        self.max_stall_cycles = max_stall_cycles
        self.max_tokens = max_tokens
        # Phase 5 新增
        self.enable_adaptive_stall = enable_adaptive_stall
        self.min_stall_cycles = min_stall_cycles
        self.max_stall_cycles_limit = max_stall_cycles_limit

    @classmethod
    def from_env(cls) -> BudgetPolicy:
        """从环境变量构造（与 TurnEngineConfig 保持一致）。"""

        def _int(
            name: str,
            *,
            default: int,
            minimum: int,
            maximum: int,
        ) -> int:
            raw = os.environ.get(name, str(default))
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        def _float(
            name: str,
            *,
            default: float,
            minimum: float,
            maximum: float,
        ) -> float:
            raw = os.environ.get(name, str(default))
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        def _bool(name: str, default: bool) -> bool:
            raw = os.environ.get(name, str(default)).lower()
            return raw in ("true", "1", "yes", "on")

        return cls(
            max_tool_calls=_int("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", default=64, minimum=1, maximum=512),
            max_turns=_int("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", default=64, minimum=1, maximum=512),
            max_stall_cycles=_int("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", default=2, minimum=0, maximum=16),
            max_wall_time_seconds=_float(
                "KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS",
                default=900.0,
                minimum=30.0,
                maximum=7200.0,
            ),
            # Phase 5 新增环境变量
            enable_adaptive_stall=_bool("KERNELONE_ADAPTIVE_STALL", True),
            min_stall_cycles=_int("KERNELONE_STALL_MIN_CYCLES", default=3, minimum=0, maximum=16),
            max_stall_cycles_limit=_int("KERNELONE_STALL_MAX_CYCLES", default=8, minimum=2, maximum=32),
        )

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None = None) -> BudgetPolicy:
        """从 metadata 构造预算策略。

        用于评测场景需要更高的工具调用限制。

        Args:
            metadata: 包含预算配置的字典，支持：
                - max_total_tool_calls: 最大工具调用次数
                - max_tool_calls: 同上（别名）
                - max_stall_cycles: 最大 stall 循环次数
                - enable_adaptive_stall: 启用自适应 stall 检测
                - min_stall_cycles: 任务前期使用的最小 stall 阈值
                - max_stall_cycles_limit: 任务后期使用的最大 stall 阈值
        """
        if not metadata:
            return cls.from_env()

        def _int(key: str, default: int, minimum: int, maximum: int) -> int:
            val = metadata.get(key)
            if val is None:
                return default
            try:
                parsed = int(val)
            except (TypeError, ValueError):
                return default
            return max(minimum, min(parsed, maximum))

        max_calls = metadata.get("max_total_tool_calls") or metadata.get("max_tool_calls")
        max_stall = metadata.get("max_stall_cycles")

        return cls(
            max_tool_calls=_int("max_total_tool_calls", 64, 1, 1024)
            if max_calls is None
            else max(1, min(int(max_calls), 1024)),
            max_turns=_int("max_turns", 64, 1, 512) if max_calls is None else max(1, min(int(max_calls), 512)),
            max_stall_cycles=_int("max_stall_cycles", 2, 0, 32)
            if max_stall is None
            else max(0, min(int(max_stall), 32)),
            max_wall_time_seconds=_int("max_wall_time_seconds", 900, 30, 7200),
            # Phase 5 新增参数
            enable_adaptive_stall=metadata.get("enable_adaptive_stall", True),
            min_stall_cycles=_int("min_stall_cycles", 3, 0, 16),
            max_stall_cycles_limit=_int("max_stall_cycles_limit", 8, 2, 32),
        )

    def _compute_adaptive_stall_threshold(self, tool_call_count: int) -> int:
        """根据任务进度动态计算 stall 阈值。

        自适应策略：
        - 如果自适应被禁用，返回 max_stall_cycles
        - 如果 max_stall_cycles <= 2（默认或更严格），尊重该设置
        - 任务前期 (<50% 预算): 使用 max_stall_cycles（不增加）
        - 任务中期 (50%-80%): 可能轻微增加阈值
        - 任务后期 (>80%): 允许更多 stall 循环，确保任务能完成

        注意：此方法只增加阈值，不减少用户设置的严格限制。

        Args:
            tool_call_count: 当前已执行的工具调用次数。

        Returns:
            动态计算的 stall 阈值。
        """
        if not self.enable_adaptive_stall:
            return self.max_stall_cycles

        # 如果 max_stall_cycles <= 2（默认值为 2），尊重用户的严格设置
        if self.max_stall_cycles <= 2:
            return self.max_stall_cycles

        # 计算任务进度
        progress_ratio = tool_call_count / max(1, self.max_tool_calls)

        if progress_ratio < 0.5:
            # 任务前期：使用原始阈值
            return self.max_stall_cycles
        elif progress_ratio < 0.8:
            # 任务中期：保持原始阈值
            return self.max_stall_cycles
        else:
            # 任务后期：允许适度增加阈值（最多 +2）
            return min(self.max_stall_cycles + 2, self.max_stall_cycles_limit)

    def evaluate(
        self,
        calls: list[CanonicalToolCall],
        *,
        tool_call_count: int = 0,
        turn_count: int = 0,
        total_tokens: int = 0,
        wall_time_seconds: float = 0.0,
        stall_count: int = 0,
        last_cycle_signature: str = "",
        task_metadata: dict[str, Any] | None = None,
    ) -> tuple[list[CanonicalToolCall], list[CanonicalToolCall], str | None, list[PolicyViolation]]:
        """评估预算约束。

        Args:
            calls: 待评估的工具调用列表。
            tool_call_count: 当前已执行的工具调用次数。
            turn_count: 当前已执行的 LLM 调用次数。
            total_tokens: 当前已消耗的 token 数。
            wall_time_seconds: 当前已用的墙上时间（秒）。
            stall_count: 连续相同工具循环次数。
            last_cycle_signature: 上次循环签名（用于 stall 检测）。

        Returns:
            (approved_calls, blocked_calls, stop_reason, violations)
        """
        approved: list[CanonicalToolCall] = []
        blocked: list[CanonicalToolCall] = []
        stop_reason: str | None = None
        violations: list[PolicyViolation] = []

        # 计算本次调用后的预算
        remaining_calls = self.max_tool_calls - tool_call_count
        proposed_calls = len(calls)

        # 硬性预算检查
        if remaining_calls <= 0:
            stop_reason = "max_tool_calls_exceeded"
            violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool="*",
                    reason=f"max_tool_calls exceeded: {tool_call_count}/{self.max_tool_calls}",
                    is_critical=True,
                )
            )
            blocked.extend(calls)
            return approved, blocked, stop_reason, violations

        if proposed_calls > remaining_calls:
            # 部分批准（只拦截超出的部分）
            to_approve = calls[:remaining_calls]
            to_block = calls[remaining_calls:]
            approved.extend(to_approve)
            blocked.extend(to_block)
            violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool="*",
                    reason=(f"partial block: {proposed_calls} calls proposed, only {remaining_calls} remaining budget"),
                    is_critical=False,
                )
            )
            return approved, blocked, None, violations

        # 计算自适应 stall 阈值
        effective_stall_threshold = self._compute_adaptive_stall_threshold(tool_call_count)

        # Stall 检测（使用自适应阈值）
        if stall_count > effective_stall_threshold:
            stop_reason = f"tool_loop_stalled: repeats={stall_count + 1} (adaptive limit={effective_stall_threshold})"
            violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool="*",
                    reason=stop_reason,
                    is_critical=True,
                )
            )
            blocked.extend(calls)
            return approved, blocked, stop_reason, violations

        # Wall time 检查
        if wall_time_seconds >= self.max_wall_time_seconds:
            stop_reason = "max_wall_time_exceeded"
            violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool="*",
                    reason=f"wall_time exceeded: {wall_time_seconds:.1f}s / {self.max_wall_time_seconds}s",
                    is_critical=True,
                )
            )
            blocked.extend(calls)
            return approved, blocked, stop_reason, violations

        # Turn 预算检查（在 token 之前）
        if self.max_turns > 0 and turn_count >= self.max_turns:
            stop_reason = "max_turns_exceeded"
            violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool="*",
                    reason=f"max_turns exceeded: {turn_count}/{self.max_turns}",
                    is_critical=True,
                )
            )
            blocked.extend(calls)
            return approved, blocked, stop_reason, violations

        # Token 预算检查
        if self.max_tokens is not None and total_tokens >= self.max_tokens:
            stop_reason = "max_tokens_exceeded"
            violations.append(
                PolicyViolation(
                    policy="BudgetPolicy",
                    tool="*",
                    reason=f"token budget exceeded: {total_tokens}/{self.max_tokens}",
                    is_critical=True,
                )
            )
            blocked.extend(calls)
            return approved, blocked, stop_reason, violations

        # 全部批准
        approved.extend(calls)
        return approved, blocked, None, violations

    def budget_snapshot(
        self,
        tool_call_count: int,
        turn_count: int,
        total_tokens: int,
        wall_time_seconds: float,
        stall_count: int,
    ) -> dict[str, Any]:
        """返回当前预算快照（用于 PolicyResult.budget_state）。"""
        return {
            "tool_call_count": tool_call_count,
            "turn_count": turn_count,
            "total_tokens": total_tokens,
            "wall_time_seconds": round(wall_time_seconds, 2),
            "stall_count": stall_count,
            "max_tool_calls": self.max_tool_calls,
            "max_turns": self.max_turns,
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "max_tokens": self.max_tokens,
            "max_stall_cycles": self.max_stall_cycles,
        }

    @staticmethod
    def compute_cycle_signature(
        calls: list[CanonicalToolCall],
        results: list[dict[str, Any]],
    ) -> str:
        """计算工具循环签名（用于 stall detection）。"""
        payload = {
            "calls": [c.tool_key() for c in calls],
            "results": [str(r.get("tool", "")) if isinstance(r, dict) else str(r) for r in results],
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @staticmethod
    def is_dangerous_command(command: str) -> bool:
        """检查命令是否包含危险模式。"""
        return _is_dangerous_command(command)


__all__ = [
    "BudgetPolicy",
    "BudgetPolicyConfig",
]
