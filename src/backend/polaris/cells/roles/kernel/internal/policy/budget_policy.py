"""BudgetPolicy - 预算策略

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
- 最大总 tool calls
- 最大 wall time
- token / result size / artifact 数量预算

注意：BudgetPolicy 在外部确定性层，不进模型推理。

与现有代码的关系
─────────────────
- TokenBudget (token_budget.py): token 预算分配器 → BudgetPolicy 整合其能力
- ToolLoopSafetyPolicy (tool_loop_controller.py): max_total_tool_calls /
  max_wall_time_seconds / max_stall_cycles → BudgetPolicy 吸收并扩展
- ToolLoopController.register_cycle(): 已在 tool_loop_controller.py 中实现
  类似逻辑 → 本类提供更全面的预算状态管理

与 Task #3 (TurnEngine) 的契约
──────────────────────────────────
BudgetState 作为独立的确定性状态，不依赖模型推理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now as _utc_now


@dataclass
class BudgetState:
    """预算状态

    所有字段均为确定性值，不依赖模型推理。
    在 turn 开始时从配置初始化，运行中持续更新。
    """

    total_tool_calls: int = 0
    max_tool_calls: int = 64
    wall_time_seconds: float = 0.0
    max_wall_time_seconds: float = 900.0
    total_tokens: int = 0
    max_tokens: int | None = None
    artifact_count: int = 0
    max_artifacts: int = 10

    # 以下为可选的扩展字段
    result_size_bytes: int = 0
    max_result_size_bytes: int | None = None

    # 内部状态（不暴露给模型）
    _started_at: datetime = field(default_factory=_utc_now, repr=False)
    _stall_cycles: int = field(default=0, repr=False)
    max_stall_cycles: int = 2

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于日志/trace）"""
        return {
            "total_tool_calls": self.total_tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "wall_time_seconds": round(self.wall_time_seconds, 2),
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "total_tokens": self.total_tokens,
            "max_tokens": self.max_tokens,
            "artifact_count": self.artifact_count,
            "max_artifacts": self.max_artifacts,
            "result_size_bytes": self.result_size_bytes,
            "max_result_size_bytes": self.max_result_size_bytes,
            "stall_cycles": self._stall_cycles,
        }


@dataclass
class BudgetDecision:
    """预算决定"""

    within_budget: bool
    exceeded: str | None = None  # "tool_calls"|"wall_time"|"tokens"|"artifacts"|"result_size"


class BudgetPolicy:
    """预算策略

    确定性预算执行层，在模型推理外部管理资源使用。
    BudgetState 独立维护，不进模型上下文。

    使用示例:
        >>> policy = BudgetPolicy()
        >>> policy.configure(max_tool_calls=32, max_wall_time_seconds=300)
        >>> decision = policy.evaluate()
        >>> if not decision.within_budget:
        ...     raise BudgetExceededError(decision.exceeded)
        >>> policy.record_tool_call()
    """

    def __init__(self, initial_state: BudgetState | None = None) -> None:
        """初始化预算策略

        Args:
            initial_state: 初始预算状态，默认使用默认值
        """
        self._state = initial_state or BudgetState()

    @classmethod
    def from_env(cls) -> BudgetPolicy:
        """从环境变量构建 BudgetPolicy（与 ToolLoopController 保持一致）"""
        import os

        def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
            raw = os.environ.get(name, str(default))
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        return cls(
            BudgetState(
                max_tool_calls=_read_int("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", 64, 1, 512),
                max_wall_time_seconds=_read_int("KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS", 900, 30, 7200),
            )
        )

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> BudgetPolicy:
        """从 metadata 构建 BudgetPolicy。

        用于运行时动态配置预算参数，例如评测场景需要更高的工具调用限制。

        Args:
            metadata: 包含预算配置的字典，支持以下键：
                - max_total_tool_calls: 最大工具调用次数
                - max_tool_calls: 同上（别名）
                - max_wall_time_seconds: 最大执行时间（秒）
                - max_stall_cycles: 最大 stall 循环次数
        """

        def _read_int(key: str, default: int, minimum: int, maximum: int) -> int:
            val = metadata.get(key)
            if val is None:
                return default
            try:
                parsed = int(val)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        max_calls = metadata.get("max_total_tool_calls") or metadata.get("max_tool_calls")
        max_stall = metadata.get("max_stall_cycles")

        return cls(
            BudgetState(
                max_tool_calls=_read_int("max_total_tool_calls", 64, 1, 1024)
                if max_calls is None
                else max(1, min(int(max_calls), 1024)),
                max_wall_time_seconds=_read_int("max_wall_time_seconds", 900, 30, 7200),
                max_stall_cycles=_read_int("max_stall_cycles", 2, 0, 32)
                if max_stall is None
                else max(0, min(int(max_stall), 32)),
            )
        )

    def configure(
        self,
        *,
        max_tool_calls: int | None = None,
        max_wall_time_seconds: float | None = None,
        max_tokens: int | None = None,
        max_artifacts: int | None = None,
        max_result_size_bytes: int | None = None,
        max_stall_cycles: int | None = None,
    ) -> None:
        """运行时配置预算参数"""
        if max_tool_calls is not None:
            self._state.max_tool_calls = max_tool_calls
        if max_wall_time_seconds is not None:
            self._state.max_wall_time_seconds = max_wall_time_seconds
        if max_tokens is not None:
            self._state.max_tokens = max_tokens
        if max_artifacts is not None:
            self._state.max_artifacts = max_artifacts
        if max_result_size_bytes is not None:
            self._state.max_result_size_bytes = max_result_size_bytes
        if max_stall_cycles is not None:
            self._state.max_stall_cycles = max_stall_cycles

    def evaluate(
        self,
        state: BudgetState | None = None,
    ) -> BudgetDecision:
        """评估是否在预算内

        Args:
            state: 待评估的状态，默认使用内部状态

        Returns:
            BudgetDecision: within_budget=True 表示正常，False 表示已超限
        """
        s = state or self._state
        if s.max_tool_calls > 0 and s.total_tool_calls > s.max_tool_calls:
            return BudgetDecision(within_budget=False, exceeded="tool_calls")
        if s.max_wall_time_seconds > 0 and s.wall_time_seconds > s.max_wall_time_seconds:
            return BudgetDecision(within_budget=False, exceeded="wall_time")
        if s.max_tokens is not None and s.total_tokens > s.max_tokens:
            return BudgetDecision(within_budget=False, exceeded="tokens")
        if s.artifact_count > s.max_artifacts:
            return BudgetDecision(within_budget=False, exceeded="artifacts")
        if s.max_result_size_bytes is not None and s.result_size_bytes > s.max_result_size_bytes:
            return BudgetDecision(within_budget=False, exceeded="result_size")
        return BudgetDecision(within_budget=True)

    def record_tool_call(self) -> None:
        """记录一次工具调用"""
        self._state.total_tool_calls += 1

    def record_time(self, seconds: float) -> None:
        """记录已用时间"""
        self._state.wall_time_seconds += seconds

    def record_tokens(self, count: int) -> None:
        """记录已用 token 数"""
        self._state.total_tokens += count

    def record_artifact(self) -> None:
        """记录一个 artifact 产生"""
        self._state.artifact_count += 1

    def record_result_size(self, bytes_count: int) -> None:
        """记录结果大小（字节）"""
        self._state.result_size_bytes += bytes_count

    def sync_from_safety_policy(
        self,
        max_tool_calls: int,
        max_wall_time_seconds: int,
        max_stall_cycles: int,
    ) -> None:
        """从 ToolLoopSafetyPolicy 同步预算参数

        避免 tool_loop_controller 与本策略配置不一致。
        """
        self._state.max_tool_calls = max_tool_calls
        self._state.max_wall_time_seconds = max_wall_time_seconds
        self._state.max_stall_cycles = max_stall_cycles

    @property
    def state(self) -> BudgetState:
        """暴露当前状态（只读副本）"""
        return self._state
