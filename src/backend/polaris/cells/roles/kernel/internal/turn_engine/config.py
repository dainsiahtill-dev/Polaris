"""TurnEngine 配置与安全状态模块。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §10 TurnEngine - Wave 1 Config Extraction

职责：
    从 turn_engine.py 提取配置与安全状态相关类，支持模块化重构。
    保持向后兼容，TurnEngine 通过导入使用。

设计原则：
    1. 单一职责：配置与状态管理独立模块。
    2. 向后兼容：不修改现有 TurnEngine 行为。
    3. 类型安全：完整的类型注解和 docstring。

提取内容：
    - TurnEngineConfig：运行时配置（max_turns, max_total_tool_calls 等）。
    - SafetyState：工具循环安全状态跟踪。

Phase 3 目标：
    SafetyState 将与 PolicyLayer 合并。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TurnEngineConfig - 运行时配置
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TurnEngineConfig:
    """TurnEngine 运行时配置。

    这些值从环境变量读取 defaults，Phase 3 由 PolicyLayer 覆盖。

    Attributes:
        max_turns: 单次请求内允许的最大 LLM 调用次数。
        max_total_tool_calls: 单次请求内允许的最大工具调用总次数。
        max_stall_cycles: 连续相同工具循环的容许次数，超出则停止。
        max_wall_time_seconds: 单次请求的最大墙上时间（秒）。
        enable_streaming: 是否启用流式输出。
    """

    max_turns: int = 64
    """单次请求内允许的最大 LLM 调用次数。"""

    max_total_tool_calls: int = 64
    """单次请求内允许的最大工具调用总次数。"""

    max_stall_cycles: int = 2
    """连续相同工具循环的容许次数，超出则停止。"""

    max_wall_time_seconds: int = 900
    """单次请求的最大墙上时间（秒）。"""

    enable_streaming: bool = True
    """是否启用流式输出。"""

    # ── 环境变量读取 ────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> TurnEngineConfig:
        """从环境变量构造配置（与 ToolLoopController 保持一致）。

        环境变量：
            - POLARIS_TOOL_LOOP_MAX_TOTAL_CALLS: max_turns 和 max_total_tool_calls
            - POLARIS_TOOL_LOOP_MAX_STALL_CYCLES: max_stall_cycles
            - POLARIS_TOOL_LOOP_MAX_WALL_TIME_SECONDS: max_wall_time_seconds
            - POLARIS_TURN_ENGINE_STREAM: enable_streaming

        Returns:
            TurnEngineConfig 实例，使用环境变量值或默认值。
        """

        def _int(
            name: str,
            *,
            default: int,
            minimum: int,
            maximum: int,
        ) -> int:
            """读取整数环境变量，并约束在 [minimum, maximum] 范围内。"""
            raw = os.environ.get(name, str(default))
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        return cls(
            max_turns=_int("POLARIS_TOOL_LOOP_MAX_TOTAL_CALLS", default=64, minimum=1, maximum=512),
            max_total_tool_calls=_int("POLARIS_TOOL_LOOP_MAX_TOTAL_CALLS", default=64, minimum=1, maximum=512),
            max_stall_cycles=_int("POLARIS_TOOL_LOOP_MAX_STALL_CYCLES", default=2, minimum=0, maximum=16),
            max_wall_time_seconds=_int(
                "POLARIS_TOOL_LOOP_MAX_WALL_TIME_SECONDS",
                default=900,
                minimum=30,
                maximum=7200,
            ),
            enable_streaming=os.environ.get("POLARIS_TURN_ENGINE_STREAM", "true").lower() in ("true", "1", "yes"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# SafetyState - 工具循环安全状态
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SafetyState:
    """工具循环安全状态。

    Phase 2: 仅用于骨架占位，逻辑从 ToolLoopController 迁移。
    Phase 3: 与 PolicyLayer 合并。

    Attributes:
        total_tool_calls: 已执行的工具调用总数。
        stall_count: 连续重复循环计数。
        last_cycle_signature: 上一个循环的签名（用于检测 stall）。
        started_at: 开始时间（monotonic clock）。
    """

    total_tool_calls: int = 0
    """已执行的工具调用总数。"""

    stall_count: int = 0
    """连续重复循环计数。"""

    last_cycle_signature: str = ""
    """上一个循环的签名（用于检测 stall）。"""

    started_at: float = field(default_factory=time.monotonic)
    """开始时间（monotonic clock）。"""

    def check(self, config: TurnEngineConfig) -> str | None:
        """检查安全状态是否违规。

        Args:
            config: TurnEngineConfig 实例，提供阈值配置。

        Returns:
            None 表示安全，str 表示安全违规原因。
        """
        # 当前骨架：stall 检测（与 ToolLoopController._build_cycle_signature 对齐）
        # TODO [Phase3]: 替换为 PolicyLayer.stop_reason(state, config)
        if self.stall_count > config.max_stall_cycles:
            return f"tool_loop_stalled: repeats={self.stall_count + 1}"
        return None

    def update_signature(
        self,
        tool_calls: list[Any],
        tool_results: list[Any],
    ) -> None:
        """更新 stall 检测签名。

        Args:
            tool_calls: 当前轮次的工具调用列表。
            tool_results: 当前轮次的工具结果列表。
        """
        payload = {
            "calls": [str(getattr(c, "tool", str(c))) for c in tool_calls],
            "results": [str(r.get("tool", "")) if isinstance(r, dict) else str(r) for r in tool_results],
        }
        sig = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

        if sig == self.last_cycle_signature:
            self.stall_count += 1
        else:
            self.stall_count = 0
        self.last_cycle_signature = sig


__all__ = [
    "SafetyState",
    "TurnEngineConfig",
]
