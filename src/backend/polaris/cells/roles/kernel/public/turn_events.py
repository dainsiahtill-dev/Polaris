"""
Turn Events - 观测事件系统

提供结构化的turn生命周期事件, 用于:
1. CLI渲染
2. 审计日志
3. 调试追踪
4. 性能监控
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TurnPhaseEvent:
    """
    阶段事件 - 标记turn的关键阶段转换

    事件序列:
    1. decision_requested -> decision_completed
    2. tool_batch_started -> tool_batch_completed (如果有工具)
    3. finalization_requested -> finalization_completed (如果finalize_mode=llm_once)
    4. workflow_handoff(async pending receipt 或显式移交)
    5. completed | failed
    """

    turn_id: str
    phase: Literal[
        "decision_requested",
        "decision_completed",
        "tool_batch_started",
        "tool_batch_completed",
        "finalization_requested",
        "finalization_completed",
        "workflow_handoff",
        "mutation_bypass_blocked",
        "completed",
        "failed",
    ]
    timestamp_ms: int
    metadata: dict[str, Any]
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        # frozen=True时需要在__init__后设置, 使用object.__setattr__
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))

    @classmethod
    def create(
        cls,
        turn_id: str,
        phase: Literal[
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_requested",
            "finalization_completed",
            "workflow_handoff",
            "mutation_bypass_blocked",
            "completed",
            "failed",
        ],
        metadata: dict | None = None,
        turn_request_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> "TurnPhaseEvent":
        """工厂方法创建事件"""
        return cls(
            turn_id=turn_id,
            phase=phase,
            timestamp_ms=int(time.time() * 1000),
            metadata=metadata or {},
            turn_request_id=turn_request_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )


@dataclass(frozen=True)
class ContentChunkEvent:
    """
    内容片段事件 - 流式输出

    关键属性:
    - is_thinking: 是否是reasoning内容
    - is_finalization: 是否是llm_once收口输出
    """

    turn_id: str
    chunk: str
    is_thinking: bool = False
    is_finalization: bool = False
    timestamp_ms: int = 0
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class ToolBatchEvent:
    """工具执行进度事件"""

    turn_id: str
    batch_id: str
    tool_name: str
    call_id: str
    status: Literal["started", "success", "error", "timeout"]
    progress: float  # 0.0-1.0, 表示整个batch的进度
    arguments: dict[str, Any] | None = None
    result: Any = None
    error: str | None = None
    execution_time_ms: int = 0
    timestamp_ms: int = 0
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class FinalizationEvent:
    """收口事件"""

    turn_id: str
    mode: Literal["none", "local", "llm_once"]
    timestamp_ms: int = 0
    metadata: dict[str, Any] | None = None
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})


@dataclass(frozen=True)
class CompletionEvent:
    """Completion event - emitted when a turn finishes.

    ADR-0080: visible_content and session_patch are injected by
    TurnTransactionController at the final yield, enabling the Orchestrator
    to extract structured SESSION_PATCH blocks from LLM output.
    """

    turn_id: str
    status: Literal["success", "failed", "handoff", "suspended"]
    duration_ms: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    monitoring: dict[str, float] | None = None
    timestamp_ms: int = 0
    # ADR-0080: raw LLM visible output injected by TurnTransactionController
    visible_content: str = ""
    # ADR-0080: session_patch pre-parsed by TurnTransactionController (optional)
    session_patch: dict[str, Any] = field(default_factory=dict)
    # Turn result kind for Orchestrator continuation decisions (kernel → orchestrator signal)
    turn_kind: str = ""  # "final_answer" | "tool_batch_with_receipt" | "handoff_workflow" | "ask_user"
    # ADR-0080: batch_receipt passed through CompletionEvent so Orchestrator can inject
    # tool results into continuation prompt (fixes "missing code context in turn 2" bug)
    batch_receipt: dict[str, Any] = field(default_factory=dict)
    # Structured error message when status == "failed" (e.g. ask_user clarification)
    error: str | None = None
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))

    @classmethod
    def create_empty(cls, turn_id: str) -> "CompletionEvent":
        """Create an empty CompletionEvent for type-safe fallbacks."""
        return cls(
            turn_id=turn_id,
            status="success",
            duration_ms=0,
            llm_calls=0,
            tool_calls=0,
            monitoring=None,
            visible_content="",
            session_patch={},
            turn_kind="final_answer",
            batch_receipt={},
            error=None,
        )


@dataclass(frozen=True)
class ErrorEvent:
    """错误事件"""

    turn_id: str
    error_type: str
    message: str
    state_at_error: str = ""  # 错误时的状态机状态
    timestamp_ms: int = 0
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class SessionStartedEvent:
    """会话开始事件 - Orchestrator 层产出"""

    session_id: str
    timestamp_ms: int = 0

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class SessionWaitingHumanEvent:
    """会话等待人工介入事件"""

    session_id: str
    reason: str
    timestamp_ms: int = 0

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class SessionCompletedEvent:
    """会话结束事件"""

    session_id: str
    reason: str | None = None
    timestamp_ms: int = 0

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class RuntimeStartedEvent:
    """工作流运行时启动事件"""

    name: str
    turn_id: str = ""
    timestamp_ms: int = 0
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


@dataclass(frozen=True)
class RuntimeCompletedEvent:
    """工作流运行时完成事件"""

    turn_id: str = ""
    timestamp_ms: int = 0
    turn_request_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def __post_init__(self) -> None:
        if self.timestamp_ms == 0:
            object.__setattr__(self, "timestamp_ms", int(time.time() * 1000))


# 联合类型, 方便类型注解
TurnEvent = (
    TurnPhaseEvent
    | ContentChunkEvent
    | ToolBatchEvent
    | FinalizationEvent
    | CompletionEvent
    | ErrorEvent
    | SessionStartedEvent
    | SessionWaitingHumanEvent
    | SessionCompletedEvent
    | RuntimeStartedEvent
    | RuntimeCompletedEvent
)


# ============ 事件序列验证器 ============


class EventSequenceValidator:
    """验证事件序列的合法性"""

    VALID_SEQUENCES: list[list[str]] = [
        # 直接回答
        ["decision_requested", "decision_completed", "completed"],
        # 工具 + none/local
        ["decision_requested", "decision_completed", "tool_batch_started", "tool_batch_completed", "completed"],
        # 工具 + llm_once
        [
            "decision_requested",
            "decision_completed",
            "tool_batch_started",
            "tool_batch_completed",
            "finalization_requested",
            "finalization_completed",
            "completed",
        ],
        # 移交workflow
        ["decision_requested", "decision_completed", "workflow_handoff"],
        # 工具执行后因 async pending receipt 移交workflow
        ["decision_requested", "decision_completed", "tool_batch_started", "tool_batch_completed", "workflow_handoff"],
    ]

    def __init__(self) -> None:
        self._events: list[TurnEvent] = []

    def add(self, event: TurnEvent) -> None:
        self._events.append(event)

    def is_valid(self) -> bool:
        """检查当前事件序列是否有效"""
        phases = [e.phase for e in self._events if isinstance(e, TurnPhaseEvent)]

        return any(self._matches(phases, valid_seq) for valid_seq in self.VALID_SEQUENCES)

    def _matches(self, actual: Sequence[str], expected: Sequence[str]) -> bool:
        """检查实际序列是否匹配期望序列(允许前缀匹配)"""
        if len(actual) > len(expected):
            return False
        return actual == expected[: len(actual)]

    def get_violations(self) -> list[str]:
        """获取违规描述"""
        violations = []
        phases = [e.phase for e in self._events if isinstance(e, TurnPhaseEvent)]

        # 检查重复
        for i, phase in enumerate(phases):
            if phase in ["decision_requested", "finalization_requested"] and phase in phases[:i]:
                violations.append(f"Duplicate {phase} detected")

        # 检查顺序
        if "tool_batch_completed" in phases and "tool_batch_started" not in phases:
            violations.append("tool_batch_completed without tool_batch_started")

        if "finalization_completed" in phases and "finalization_requested" not in phases:
            violations.append("finalization_completed without finalization_requested")

        return violations
