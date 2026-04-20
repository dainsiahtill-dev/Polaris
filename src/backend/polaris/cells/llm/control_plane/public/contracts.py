"""Public contracts for `llm.control_plane`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class SaveLlmConfigCommandV1:
    workspace: str
    role: str
    provider_id: str
    model: str
    config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "config", _to_dict_copy(self.config))


@dataclass(frozen=True)
class InvokeLlmRoleCommandV1:
    workspace: str
    role: str
    message: str
    context: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "message", _require_non_empty("message", self.message))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class GetLlmConfigQueryV1:
    workspace: str
    role: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class GetLlmRuntimeStatusQueryV1:
    workspace: str
    role: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.role is not None:
            object.__setattr__(self, "role", _require_non_empty("role", self.role))


@dataclass(frozen=True)
class LlmConfigChangedEventV1:
    event_id: str
    workspace: str
    role: str
    provider_id: str
    model: str
    changed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "changed_at", _require_non_empty("changed_at", self.changed_at))


@dataclass(frozen=True)
class LlmInvocationCompletedEventV1:
    event_id: str
    workspace: str
    role: str
    status: str
    completed_at: str
    request_id: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class LlmConfigResultV1:
    workspace: str
    role: str
    provider_id: str
    model: str
    ready: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "provider_id", _require_non_empty("provider_id", self.provider_id))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class LlmInvocationResultV1:
    ok: bool
    workspace: str
    role: str
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if self.prompt_tokens < 0 or self.completion_tokens < 0 or self.total_tokens < 0:
            raise ValueError("token counters must be >= 0")


class LlmControlPlaneError(RuntimeError):
    """Raised when `llm.control_plane` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_control_plane_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@dataclass(frozen=True)
class LLMRequest:
    """Backward-compatible request model for existing callsites."""

    prompt: str
    system_prompt: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1000
    model: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompt", _require_non_empty("prompt", self.prompt))
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")


@dataclass(frozen=True)
class LLMResponse:
    """Backward-compatible response model for existing callsites."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0 or self.completion_tokens < 0 or self.total_tokens < 0:
            raise ValueError("token counters must be >= 0")


@runtime_checkable
class ILLMControlPlane(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Compatibility API for direct generation."""

    def stream(self, request: LLMRequest) -> AsyncGenerator[str, None]:
        """Compatibility API for direct streaming."""


__all__ = [
    "GetLlmConfigQueryV1",
    "GetLlmRuntimeStatusQueryV1",
    "ILLMControlPlane",
    "InvokeLlmRoleCommandV1",
    "LLMRequest",
    "LLMResponse",
    "LlmConfigChangedEventV1",
    "LlmConfigResultV1",
    "LlmControlPlaneError",
    "LlmInvocationCompletedEventV1",
    "LlmInvocationResultV1",
    "SaveLlmConfigCommandV1",
]
