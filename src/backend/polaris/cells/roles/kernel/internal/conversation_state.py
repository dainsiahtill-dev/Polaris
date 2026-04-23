# UTF-8 编码验证: 本文所有文本使用 UTF-8

"""ConversationState - 统一角色执行状态容器

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 ConversationState
本文档定义 TurnEngine 的统一状态容器。

职责：
    ConversationState 是 TurnEngine 所有入口的唯一状态容器。
    它包含：
    - transcript: 完整执行历史（Typed Transcript IR）
    - loaded_tools: 当前已加载的工具
    - budgets: 预算状态（ToolCall 次数、Token、Wall Time）
    - approvals: 待审批队列
    - artifacts: 产物引用
    - checkpoint_cursor: 检查点游标（用于恢复）
    - role_config: 角色配置（System Prompt、Provider、Execution Lane）

设计约束：
    1. 不可变语义（Immutable semantics）：状态更新通过 copy-on-write，
       避免在并发访问时出现竞争条件。
    2. Transcript-first：状态的核心驱动是 TranscriptDelta 的追加。
    3. Typed IR：所有历史条目使用 transcript_ir 中的 typed 类型。
    4. Phase-gated：Phase 2 提供骨架，Phase 3 接入 ProviderAdapter。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from polaris.kernelone.utils.time_utils import utc_now as _utc_now

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.public.transcript_ir import (
        ToolCall,
        ToolResult,
        TranscriptDelta,
        TranscriptItem,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Literal 类型别名
# ─────────────────────────────────────────────────────────────────────────────

ExecutionLane = Literal["direct", "programmatic"]

ToolResultStatus = Literal["success", "error", "blocked", "timeout"]


# ─────────────────────────────────────────────────────────────────────────────
# CheckpointCursor - 检查点游标
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckpointCursor:
    """检查点游标 — 用于 turn 恢复和审计追踪。

    Attributes:
        turn_id: 唯一 turn 标识符。
        transcript_length: 检查点时的 transcript 长度。
        checkpoint_index: 检查点序号（从 0 开始）。
        created_at: 检查点创建时间（UTC）。
    """

    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    transcript_length: int = 0
    checkpoint_index: int = 0
    created_at: datetime = field(default_factory=_utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "transcript_length": self.transcript_length,
            "checkpoint_index": self.checkpoint_index,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointCursor:
        raw_created = data.get("created_at")
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = _utc_now()
        return cls(
            turn_id=str(data.get("turn_id", uuid.uuid4().hex)),
            transcript_length=int(data.get("transcript_length", 0)),
            checkpoint_index=int(data.get("checkpoint_index", 0)),
            created_at=created_at,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Budgets - 预算状态
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Budgets:
    """预算状态容器。

    所有字段均为确定性值，不依赖模型推理。
    在 turn 开始时从配置初始化，运行中持续更新。

    Attributes:
        tool_call_count: 当前已执行的工具调用次数。
        max_tool_calls: 允许的最大工具调用次数（0=不限制）。
        turn_count: 当前已执行的 LLM 调用次数。
        max_turns: 允许的最大 LLM 调用次数（0=不限制）。
        total_tokens: 当前已消耗的 token 总数。
        max_tokens: 允许的最大 token 数（None=不限制）。
        wall_time_seconds: 当前已用墙上时间（秒）。
        max_wall_time_seconds: 允许的最大墙上时间（0=不限制）。
        stall_count: 连续相同工具调用的次数。
        max_stall_cycles: 允许的最大 stall 次数。
    """

    tool_call_count: int = 0
    max_tool_calls: int = 64
    turn_count: int = 0
    max_turns: int = 64
    total_tokens: int = 0
    max_tokens: int | None = None
    wall_time_seconds: float = 0.0
    max_wall_time_seconds: float = 900.0
    stall_count: int = 0
    max_stall_cycles: int = 2
    _started_at: float = field(default_factory=time.monotonic, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_count": self.tool_call_count,
            "max_tool_calls": self.max_tool_calls,
            "turn_count": self.turn_count,
            "max_turns": self.max_turns,
            "total_tokens": self.total_tokens,
            "max_tokens": self.max_tokens,
            "wall_time_seconds": round(self.wall_time_seconds, 2),
            "max_wall_time_seconds": self.max_wall_time_seconds,
            "stall_count": self.stall_count,
            "max_stall_cycles": self.max_stall_cycles,
        }

    def sync_from_env(self) -> None:
        """从环境变量同步预算配置（与 ToolLoopController 保持一致）。"""
        import os

        def _int(name: str, default: int, minimum: int, maximum: int) -> int:
            raw = os.environ.get(name, str(default))
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(parsed, maximum))

        self.max_tool_calls = _int("KERNELONE_TOOL_LOOP_MAX_TOTAL_CALLS", 64, 1, 512)
        self.max_turns = _int("KERNELONE_TOOL_LOOP_MAX_TURNS", 64, 1, 512)
        self.max_stall_cycles = _int("KERNELONE_TOOL_LOOP_MAX_STALL_CYCLES", 2, 0, 16)
        self.max_wall_time_seconds = float(_int("KERNELONE_TOOL_LOOP_MAX_WALL_TIME_SECONDS", 900, 30, 7200))

    def tick_wall_time(self) -> None:
        """更新墙上时间。"""
        elapsed = time.monotonic() - self._started_at
        self.wall_time_seconds = elapsed


# ─────────────────────────────────────────────────────────────────────────────
# ConversationState - 统一状态容器
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ConversationState:
    """统一角色执行状态容器。

    Blueprint: §11 ConversationState

    职责：
        1. 持有完整的 Typed Transcript 历史
        2. 持有当前已加载的工具规格列表
        3. 持有预算状态（Budgets）
        4. 持有待审批队列
        5. 持有产物引用
        6. 持有检查点游标

    与现有代码的关系：
    ─────────────────────
    - kernel.run() / kernel.run_stream() 中的 `history` dict 列表
      → 本类的 `transcript` 字段替代
    - tool_loop_controller 中的 `max_total_tool_calls` 等配置
      → 本类的 `budgets` 字段替代
    - tool_loop_controller 中的 `_pending_approvals` 列表
      → 本类的 `pending_approvals` 字段替代

    使用示例::

        state = ConversationState.new(role="pm", workspace="/path/to/repo")
        delta = from_assistant_message(text="...", native_tool_calls=[...])
        state.append(delta)
        if state.should_stop():
            break
    """

    # ── 核心字段 ──────────────────────────────────────────────────────────────

    role: str = ""
    """角色标识（如 "pm", "director", "architect"）。"""

    workspace: str = ""
    """当前 workspace 路径。"""

    transcript: list[TranscriptItem] = field(default_factory=list)
    """完整执行历史（Typed Transcript IR）。"""

    loaded_tools: list[str] = field(default_factory=list)
    """当前已加载的工具名称列表。"""

    budgets: Budgets = field(default_factory=Budgets)
    """预算状态。"""

    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    """待审批队列，每项包含 tool_name, args, reason, requested_at。"""

    artifacts: list[str] = field(default_factory=list)
    """产物引用列表（如 artifact://...）。"""

    checkpoint_cursor: CheckpointCursor = field(default_factory=CheckpointCursor)
    """检查点游标。"""

    # ── 角色配置字段 ─────────────────────────────────────────────────────────

    system_prompt: str = ""
    """角色 System Prompt。"""

    provider: str = "openai"
    """LLM Provider（"openai" | "anthropic"）。"""

    model: str = ""
    """模型名称。"""

    execution_lane: ExecutionLane = "direct"
    """执行通道（"direct" | "programmatic"）。"""

    # ── 元数据 ───────────────────────────────────────────────────────────────

    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """当前 turn 的唯一标识符。"""

    created_at: datetime = field(default_factory=_utc_now)
    """状态创建时间（UTC）。"""

    last_updated: datetime = field(default_factory=_utc_now)
    """最后更新时间（UTC）。"""

    # ── 工厂方法 ─────────────────────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        role: str,
        workspace: str = "",
        *,
        system_prompt: str = "",
        provider: str = "openai",
        model: str = "",
        loaded_tools: list[str] | None = None,
    ) -> ConversationState:
        """构造新的 ConversationState。

        Args:
            role: 角色标识。
            workspace: workspace 路径。
            system_prompt: 角色 System Prompt。
            provider: LLM Provider。
            model: 模型名称。
            loaded_tools: 已加载的工具名称列表。

        Returns:
            新建的 ConversationState 实例。
        """
        budgets = Budgets()
        budgets.sync_from_env()
        return cls(
            role=role,
            workspace=workspace,
            system_prompt=system_prompt,
            provider=provider,
            model=model,
            loaded_tools=loaded_tools or [],
            budgets=budgets,
            turn_id=uuid.uuid4().hex,
        )

    # ── Transcript 操作 ───────────────────────────────────────────────────────

    def append(self, delta: TranscriptDelta) -> None:
        """追加 TranscriptDelta 到历史。

        Args:
            delta: 要追加的 TranscriptDelta。
        """
        self.transcript.extend(delta.transcript_items)
        self.last_updated = _utc_now()

        # 更新 checkpoint_cursor
        self.checkpoint_cursor = CheckpointCursor(
            turn_id=self.turn_id,
            transcript_length=len(self.transcript),
            checkpoint_index=self.checkpoint_cursor.checkpoint_index + 1,
        )

    def append_item(self, item: TranscriptItem) -> None:
        """追加单个 TranscriptItem。

        Args:
            item: 要追加的条目。
        """
        self.transcript.append(item)
        self.last_updated = _utc_now()

    def last_items(self, n: int) -> list[TranscriptItem]:
        """返回最后 n 个 transcript 条目。

        Args:
            n: 要返回的条目数量。

        Returns:
            最后 n 个条目（不足时返回全部）。
        """
        return self.transcript[-n:] if n > 0 else []

    def tool_calls(self) -> list[ToolCall]:
        """返回所有 ToolCall 条目。

        Returns:
            所有 tool_calls 条目。
        """
        from polaris.cells.roles.kernel.public.transcript_ir import ToolCall

        return [item for item in self.transcript if isinstance(item, ToolCall)]

    def tool_results(self) -> list[ToolResult]:
        """返回所有 ToolResult 条目。

        Returns:
            所有 tool_results 条目。
        """
        from polaris.cells.roles.kernel.public.transcript_ir import ToolResult

        return [item for item in self.transcript if isinstance(item, ToolResult)]

    # ── 预算操作 ─────────────────────────────────────────────────────────────

    def record_tool_call(self) -> None:
        """记录一次工具调用。"""
        self.budgets.tool_call_count += 1
        self.last_updated = _utc_now()

    def record_turn(self) -> None:
        """记录一次 LLM 调用。"""
        self.budgets.turn_count += 1
        self.last_updated = _utc_now()

    def tick_wall_time(self) -> None:
        """更新墙上时间。"""
        self.budgets.tick_wall_time()
        self.last_updated = _utc_now()

    def is_within_budget(self) -> bool:
        """检查是否在预算内。

        Returns:
            True 表示在预算内，False 表示已超限。
        """
        b = self.budgets
        if b.max_tool_calls > 0 and b.tool_call_count >= b.max_tool_calls:
            return False
        if b.max_turns > 0 and b.turn_count >= b.max_turns:
            return False
        return not (b.max_wall_time_seconds > 0 and b.wall_time_seconds >= b.max_wall_time_seconds)

    # ── 停止判断 ─────────────────────────────────────────────────────────────

    def should_stop(self) -> tuple[bool, str | None]:
        """判断是否应该停止执行。

        Returns:
            (should_stop, reason): should_stop=True 表示应该停止，reason 为停止原因。
        """
        # 预算超限
        b = self.budgets
        if b.max_tool_calls > 0 and b.tool_call_count >= b.max_tool_calls:
            return True, "max_tool_calls_exceeded"
        if b.turn_count >= b.max_turns and b.max_turns > 0:
            return True, "max_turns_exceeded"
        if b.max_wall_time_seconds > 0 and b.wall_time_seconds >= b.max_wall_time_seconds:
            return True, "max_wall_time_exceeded"
        if b.stall_count >= b.max_stall_cycles:
            return True, "tool_loop_stalled"
        return False, None

    # ── 审批操作 ─────────────────────────────────────────────────────────────

    def add_approval(self, tool_name: str, args: dict[str, Any], reason: str) -> None:
        """添加待审批项。

        Args:
            tool_name: 工具名称。
            args: 工具参数。
            reason: 审批原因。
        """
        self.pending_approvals.append(
            {
                "tool_name": tool_name,
                "args": args,
                "reason": reason,
                "requested_at": _utc_now().isoformat(),
            }
        )

    def resolve_approval(
        self,
        tool_name: str,
        *,
        approved: bool,
    ) -> bool | None:
        """解决审批项。

        Args:
            tool_name: 工具名称。
            approved: 是否批准。

        Returns:
            True/False 表示已解决，None 表示未找到对应项。
        """
        for i, item in enumerate(self.pending_approvals):
            if item.get("tool_name") == tool_name:
                self.pending_approvals.pop(i)
                return approved
        return None

    # ── 产物操作 ─────────────────────────────────────────────────────────────

    def add_artifact(self, ref: str) -> None:
        """添加产物引用。

        Args:
            ref: 产物引用 URI。
        """
        if ref not in self.artifacts:
            self.artifacts.append(ref)

    # ── 序列化 ───────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于检查点持久化）。

        Returns:
            包含所有状态的字典。
        """

        return {
            "role": self.role,
            "workspace": self.workspace,
            "transcript": [item.to_dict() for item in self.transcript],
            "loaded_tools": list(self.loaded_tools),
            "budgets": self.budgets.to_dict(),
            "pending_approvals": list(self.pending_approvals),
            "artifacts": list(self.artifacts),
            "checkpoint_cursor": self.checkpoint_cursor.to_dict(),
            "system_prompt": self.system_prompt,
            "provider": self.provider,
            "model": self.model,
            "execution_lane": self.execution_lane,
            "turn_id": self.turn_id,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationState:
        """从字典构造（用于检查点加载）。

        Args:
            data: 序列化后的字典。

        Returns:
            ConversationState 实例。
        """
        from polaris.cells.roles.kernel.public.transcript_ir import (
            ControlEvent,
            ReasoningSummary,
            ToolCall,
            ToolResult,
        )

        # 反序列化 transcript
        transcript: list = []
        for item_data in data.get("transcript", []):
            if "tool_name" in item_data and "call_id" in item_data:
                if "status" in item_data or "content" in item_data:
                    transcript.append(ToolResult.from_dict(item_data))
                else:
                    transcript.append(ToolCall.from_dict(item_data))
            elif "event_type" in item_data:
                transcript.append(ControlEvent.from_dict(item_data))
            elif "content" in item_data:
                transcript.append(ReasoningSummary.from_dict(item_data))

        # 反序列化 budgets
        budget_data = data.get("budgets", {})
        budgets = Budgets(
            tool_call_count=int(budget_data.get("tool_call_count", 0)),
            max_tool_calls=int(budget_data.get("max_tool_calls", 64)),
            turn_count=int(budget_data.get("turn_count", 0)),
            max_turns=int(budget_data.get("max_turns", 64)),
            total_tokens=int(budget_data.get("total_tokens", 0)),
            max_tokens=budget_data.get("max_tokens"),
            wall_time_seconds=float(budget_data.get("wall_time_seconds", 0.0)),
            max_wall_time_seconds=float(budget_data.get("max_wall_time_seconds", 900.0)),
            stall_count=int(budget_data.get("stall_count", 0)),
            max_stall_cycles=int(budget_data.get("max_stall_cycles", 2)),
        )

        # 反序列化 checkpoint_cursor
        cursor_data = data.get("checkpoint_cursor", {})
        cursor = CheckpointCursor.from_dict(cursor_data) if cursor_data else CheckpointCursor()

        raw_created = data.get("created_at")
        created_at = datetime.fromisoformat(raw_created) if isinstance(raw_created, str) else _utc_now()

        raw_updated = data.get("last_updated")
        last_updated = datetime.fromisoformat(raw_updated) if isinstance(raw_updated, str) else _utc_now()

        return cls(
            role=str(data.get("role", "")),
            workspace=str(data.get("workspace", "")),
            transcript=transcript,
            loaded_tools=list(data.get("loaded_tools", [])),
            budgets=budgets,
            pending_approvals=list(data.get("pending_approvals", [])),
            artifacts=list(data.get("artifacts", [])),
            checkpoint_cursor=cursor,
            system_prompt=str(data.get("system_prompt", "")),
            provider=str(data.get("provider", "openai")),
            model=str(data.get("model", "")),
            execution_lane=cast(
                "ExecutionLane",
                str(data.get("execution_lane", "direct"))
                if data.get("execution_lane") in ("direct", "programmatic")
                else "direct",
            ),
            turn_id=str(data.get("turn_id", uuid.uuid4().hex)),
            created_at=created_at,
            last_updated=last_updated,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 公开导出
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    "Budgets",
    "CheckpointCursor",
    "ConversationState",
]
