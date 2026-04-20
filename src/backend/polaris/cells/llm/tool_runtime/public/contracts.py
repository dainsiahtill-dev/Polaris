"""Public contracts for `llm.tool_runtime` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class ExecuteToolRoundCommandV1:
    workspace: str
    assistant_text: str = ""
    provider_hint: str = "auto"
    max_tool_calls: int = 4
    fail_fast: bool = False
    response_payload: Mapping[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "provider_hint", _require_non_empty("provider_hint", self.provider_hint))
        object.__setattr__(self, "response_payload", _to_dict_copy(self.response_payload))
        object.__setattr__(
            self,
            "allowed_tools",
            tuple(str(item).strip().lower() for item in self.allowed_tools if str(item).strip()),
        )
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be >= 1")


@dataclass(frozen=True)
class QueryToolRuntimePolicyV1:
    workspace: str
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class ToolRoundCompletedEventV1:
    event_id: str
    workspace: str
    status: str
    completed_at: str
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class ToolRoundResultV1:
    ok: bool
    status: str
    workspace: str
    tool_calls: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    tool_results: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    tool_feedback: str = ""
    assistant_remainder: str = ""
    should_continue: bool = False
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "tool_calls", tuple(dict(item) for item in self.tool_calls))
        object.__setattr__(self, "tool_results", tuple(dict(item) for item in self.tool_results))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class LlmToolRuntimeError(RuntimeError):
    """Structured contract error for `llm.tool_runtime`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_tool_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class ILlmToolRuntimeService(Protocol):
    def execute_round(self, command: ExecuteToolRoundCommandV1) -> ToolRoundResultV1:
        """Execute one tool round."""


__all__ = [
    "ExecuteToolRoundCommandV1",
    "ILlmToolRuntimeService",
    "LlmToolRuntimeError",
    "QueryToolRuntimePolicyV1",
    "ToolRoundCompletedEventV1",
    "ToolRoundResultV1",
]
