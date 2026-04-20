"""Public contracts for `roles.runtime` cell.

The contracts in this module define the stable boundary for role runtime
execution, status query, and event/result payloads.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from polaris.cells.roles.runtime.internal.agent_runtime_base import RoleAgent
from polaris.cells.roles.runtime.internal.protocol_fsm import (
    create_protocol_fsm,
)
from polaris.kernelone.roles.shared_contracts import (
    AgentMessage,
    AgentStatus,
    MessageType,
    register_protocol_fsm_factory,
)


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _normalize_optional_domain(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    if not token:
        return None
    return token


def _normalize_history(history: Any) -> tuple[tuple[str, str], ...]:
    if history is None:
        return ()
    if isinstance(history, str | bytes):
        raise ValueError("history must be an iterable of (role, content) entries")

    try:
        iterator = iter(history)
    except TypeError as exc:
        raise ValueError("history must be an iterable of (role, content) entries") from exc

    normalized: list[tuple[str, str]] = []
    for index, item in enumerate(iterator):
        role = ""
        content = ""
        if isinstance(item, Mapping):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("message") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = str(item[0] or "").strip()
            content = str(item[1] or "").strip()

        if not role or not content:
            raise ValueError(f"history entries must provide non-empty role and content (index={index})")
        normalized.append((role, content))

    return tuple(normalized)


@dataclass(frozen=True)
class ExecuteRoleTaskCommandV1:
    """Execute one role task under the runtime role kernel."""

    role: str
    task_id: str
    workspace: str
    objective: str
    run_id: str | None = None
    session_id: str | None = None
    domain: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    stream: bool = False
    host_kind: str | None = None  # Task #2: unified host protocol

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "objective", _require_non_empty("objective", self.objective))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0 when provided")


@dataclass(frozen=True)
class ExecuteRoleSessionCommandV1:
    """Execute one user turn on an existing role session."""

    role: str
    session_id: str
    workspace: str
    user_message: str
    run_id: str | None = None
    task_id: str | None = None
    domain: str | None = None
    history: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = True
    stream_options: StreamTurnOptions | None = None
    host_kind: str | None = None  # Task #2: unified host protocol

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "user_message", _require_non_empty("user_message", self.user_message))
        object.__setattr__(self, "domain", _normalize_optional_domain(self.domain))
        object.__setattr__(self, "history", _normalize_history(self.history))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.stream_options is not None and not isinstance(self.stream_options, StreamTurnOptions):
            raise TypeError("stream_options must be a StreamTurnOptions instance")


@dataclass(frozen=True)
class GetRoleRuntimeStatusQueryV1:
    """Query role runtime health/status for one workspace."""

    workspace: str
    role: str | None = None
    include_agent_health: bool = True
    include_queue: bool = True
    include_tools: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class RoleTaskStartedEventV1:
    """Event emitted when role runtime starts a task."""

    event_id: str
    role: str
    task_id: str
    workspace: str
    started_at: str
    run_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class RoleTaskCompletedEventV1:
    """Event emitted when role runtime completes a task."""

    event_id: str
    role: str
    task_id: str
    workspace: str
    status: str
    completed_at: str
    run_id: str | None = None
    session_id: str | None = None
    output_summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class RoleExecutionResultV1:
    """Unified role execution result for task/session calls."""

    ok: bool
    status: str
    role: str
    workspace: str
    task_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    output: str = ""
    thinking: str | None = None
    tool_calls: tuple[str, ...] = field(default_factory=tuple)
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    usage: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    # 完整回话历史 (role, content) 对列表 — 用于非流式模式下的 session 持久化
    turn_history: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "tool_calls", tuple(str(v) for v in self.tool_calls))
        object.__setattr__(self, "artifacts", tuple(str(v) for v in self.artifacts))
        object.__setattr__(self, "usage", _to_dict_copy(self.usage))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "turn_history", list(self.turn_history))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


# ── Stream contract types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamTurnOptions:
    """Options for streamed role chat turns (Task #2)."""

    stream: bool = True
    context: dict[str, Any] | None = None
    history_limit: int | None = None
    prompt_appendix: str | None = None


class StandardStreamEvent(dict):
    """Dict-subclass canonical stream event for the contracts layer (Task #2).

    Mirrors the dataclass in ``console_protocol`` but as a dict so callers
    that expect ``isinstance(result, dict)`` receive a compatible type.
    """

    def __init__(
        self,
        type: str = "",
        data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            type=str(type),
            data=dict(data) if data else {},
            metadata=dict(metadata) if metadata else {},
        )

    @property
    def event_type(self) -> str:
        return self["type"]

    @property
    def event_data(self) -> dict[str, Any]:
        return self["data"]


class RoleRuntimeError(RuntimeError):
    """Structured runtime contract error for roles.runtime."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "role_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_message = _require_non_empty("message", message)
        super().__init__(normalized_message)
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


@runtime_checkable
class IRoleRuntime(Protocol):
    """Public role runtime interface.

    Notes:
    - `execute_role` is retained as a compatibility method for older callsites.
    - New code should use `execute_role_task` or `execute_role_session`.
    """

    async def execute_role_task(
        self,
        command: ExecuteRoleTaskCommandV1,
    ) -> RoleExecutionResultV1:
        """Execute one task command."""

    async def execute_role_session(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> RoleExecutionResultV1:
        """Execute one session-turn command."""

    async def get_runtime_status(
        self,
        query: GetRoleRuntimeStatusQueryV1,
    ) -> Mapping[str, Any]:
        """Return runtime status snapshot."""

    async def execute_role(
        self,
        role_id: str,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Compatibility method for pre-contract callsites."""

    def stream_chat_turn(
        self,
        command: ExecuteRoleSessionCommandV1,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream role chat turn events as an async iterator."""


__all__ = [
    # ── Cross-Cell Agent Types ───────────────────────────────────────────────
    # These types are the stable public contract for the agent runtime.
    # qa.audit_verdict (and future cross-cell callers) import from here
    # instead of roles.runtime.public.service, which pulls in 8+ internal modules.
    "AgentMessage",
    "AgentStatus",
    # ── Execution Contracts ────────────────────────────────────────────────
    "ExecuteRoleSessionCommandV1",
    "ExecuteRoleTaskCommandV1",
    "GetRoleRuntimeStatusQueryV1",
    "IRoleRuntime",
    "MessageType",
    "RoleAgent",
    "RoleExecutionResultV1",
    "RoleRuntimeError",
    "RoleTaskCompletedEventV1",
    "RoleTaskStartedEventV1",
    # ── Stream Contract Types (Task #2) ───────────────────────────────────
    "StandardStreamEvent",
    "StreamTurnOptions",
    "create_protocol_fsm",
]


register_protocol_fsm_factory(create_protocol_fsm)
