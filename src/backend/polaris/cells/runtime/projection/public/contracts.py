from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


@dataclass(frozen=True)
class RuntimeProjectionQueryV1:
    scope: str = "runtime"


@dataclass(frozen=True)
class RuntimeProjectionResultV1:
    payload: dict[str, Any]


@dataclass(frozen=True)
class RuntimeProjectedEventV1:
    scope: str
    channels: tuple[str, ...] = ()


class RuntimeObserverEventTypeV1(StrEnum):
    """Observer-facing projection event types carried by runtime.v2."""

    LLM_WAITING = "llm_waiting"
    LLM_COMPLETED = "llm_completed"
    LLM_FAILED = "llm_failed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING_CHUNK = "thinking_chunk"
    THINKING_PREVIEW = "thinking_preview"
    CONTENT_CHUNK = "content_chunk"
    CONTENT_PREVIEW = "content_preview"
    ERROR = "error"


@dataclass(frozen=True)
class RuntimeObserverEventV1:
    """Structured observer event projected from canonical runtime facts."""

    run_id: str
    role: str
    event_type: RuntimeObserverEventTypeV1
    content: str = ""
    task_id: str = ""
    attempt: int = 0
    tool_name: str = ""
    tool_args: dict[str, Any] | None = None
    tool_status: str = ""
    tool_success: bool | None = None
    tool_result_raw: Any = None


class RuntimeProjectionError(Exception):
    """Raised when projection assembly fails."""


__all__ = [
    "RuntimeObserverEventTypeV1",
    "RuntimeObserverEventV1",
    "RuntimeProjectedEventV1",
    "RuntimeProjectionError",
    "RuntimeProjectionQueryV1",
    "RuntimeProjectionResultV1",
]
