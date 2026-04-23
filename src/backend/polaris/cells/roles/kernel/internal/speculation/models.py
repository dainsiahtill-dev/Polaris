from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

ParseState = Literal[
    "incomplete",
    "syntactic_complete",
    "schema_valid",
    "semantically_stable",
]

SpeculateMode = Literal[
    "forbid",
    "prefetch_only",
    "dry_run_only",
    "speculative_allowed",
    "high_confidence_only",
]

SideEffect = Literal["pure", "readonly", "externally_visible", "mutating"]
CostClass = Literal["cheap", "medium", "expensive"]
Cancellability = Literal["cooperative", "best_effort", "non_cancelable"]
Reusability = Literal["cacheable", "adoptable", "non_reusable"]


@dataclass(frozen=True, slots=True)
class ToolSpecPolicy:
    """四维工具推测策略，替代简单的 READONLY_TOOLS 二分法."""

    tool_name: str
    side_effect: SideEffect
    cost: CostClass
    cancellability: Cancellability
    reusability: Reusability
    speculate_mode: SpeculateMode
    min_stability_score: float = 0.82
    timeout_ms: int = 1200
    max_parallel: int = 2
    cache_ttl_ms: int = 3000


@dataclass(slots=True)
class FieldMutation:
    """记录候选字段的变更历史."""

    field_path: str
    old_value: Any
    new_value: Any
    ts_monotonic: float


@dataclass(slots=True)
class CandidateToolCall:
    """从流式增量中提取的候选工具调用."""

    candidate_id: str
    stream_id: str
    turn_id: str
    tool_name: str | None = None
    partial_args: dict[str, Any] = field(default_factory=dict)
    parse_state: ParseState = "incomplete"
    stability_score: float = 0.0
    semantic_hash: str = ""
    last_mutation_at: float = 0.0
    mutation_history: list[FieldMutation] = field(default_factory=list)
    schema_valid: bool = False
    end_tag_seen: bool = False
    first_seen_at: float = 0.0
    updated_at: float = 0.0


class ShadowTaskState(str, Enum):
    """Shadow task 显式状态机."""

    CREATED = "created"
    ELIGIBLE = "eligible"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    ADOPTED = "adopted"
    EXPIRED = "expired"


@dataclass(slots=True)
class ShadowTaskRecord:
    """注册表中的影子任务记录."""

    task_id: str
    origin_turn_id: str
    origin_candidate_id: str
    tool_name: str
    normalized_args: dict[str, Any]
    spec_key: str
    env_fingerprint: str
    policy_snapshot: ToolSpecPolicy
    state: ShadowTaskState = field(default=ShadowTaskState.CREATED)
    started_at: float | None = None
    finished_at: float | None = None
    future: asyncio.Task[Any] | None = None
    result: Any = None
    error: str | None = None
    cost_estimate: float = 0.0
    cancel_reason: str | None = None
    adopted_by_call_id: str | None = None
    expiry_at: float | None = None


@dataclass(slots=True, frozen=True)
class BudgetSnapshot:
    """Speculation budget 准入快照."""

    mode: str  # turbo / balanced / safe
    active_shadow_tasks: int
    abandonment_ratio: float
    timeout_ratio: float
    queue_pressure: float
    cpu_pressure: float
    memory_pressure: float
    external_quota_pressure: float
    wrong_adoption_count: int = 0


class SalvageDecision(str, Enum):
    """Cancel-or-Salvage 三选一决策."""

    CANCEL_NOW = "cancel_now"
    LET_FINISH_AND_CACHE = "let_finish_and_cache"
    JOIN_AUTHORITATIVE = "join_authoritative"


class CancelToken:
    """轻量级取消标记，供 ShadowTask runner 协程检查."""

    __slots__ = ("_cancelled", "_reason")

    def __init__(self) -> None:
        self._cancelled: bool = False
        self._reason: str | None = None

    def cancel(self, reason: str) -> None:
        """标记取消并记录原因."""
        self._cancelled = True
        self._reason = reason

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel_after(self, timeout_seconds: float, *, reason: str = "timeout") -> asyncio.TimerHandle | None:
        """Schedule automatic cancellation after *timeout_seconds*.

        Returns the :class:`asyncio.TimerHandle` so the caller can call
        ``.cancel()`` on it to disarm the timer, or ``None`` when no running
        event loop is available.
        """
        try:
            loop = asyncio.get_running_loop()
            return loop.call_later(timeout_seconds, self.cancel, reason)
        except RuntimeError:
            return None


def check_cancel(token: CancelToken | None) -> None:
    """在工具 runner 的关键位置调用，主动响应取消."""
    if token is not None and token.cancelled:
        raise asyncio.CancelledError(token.reason)
