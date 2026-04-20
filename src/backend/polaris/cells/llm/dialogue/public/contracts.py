"""Public contracts for `llm.dialogue` cell."""

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
class InvokeRoleDialogueCommandV1:
    workspace: str
    role: str
    message: str
    stream: bool = False
    context: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "message", _require_non_empty("message", self.message))
        object.__setattr__(self, "context", _to_dict_copy(self.context))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class InvokeDocsDialogueCommandV1:
    workspace: str
    message: str
    fields: Mapping[str, str] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)
    stream: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "message", _require_non_empty("message", self.message))
        object.__setattr__(self, "fields", dict(self.fields))
        object.__setattr__(self, "state", _to_dict_copy(self.state))


@dataclass(frozen=True)
class ValidateRoleOutputQueryV1:
    role: str
    output: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "output", _require_non_empty("output", self.output))


@dataclass(frozen=True)
class DialogueTurnCompletedEventV1:
    event_id: str
    workspace: str
    role: str
    status: str
    completed_at: str
    run_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))


@dataclass(frozen=True)
class DialogueTurnResultV1:
    ok: bool
    status: str
    workspace: str
    role: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "content", str(self.content))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class LlmDialogueError(RuntimeError):
    """Structured contract error for `llm.dialogue`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_dialogue_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class ILlmDialogueService(Protocol):
    async def invoke_role_dialogue(
        self,
        command: InvokeRoleDialogueCommandV1,
    ) -> DialogueTurnResultV1:
        """Invoke role dialogue."""

    async def invoke_docs_dialogue(
        self,
        command: InvokeDocsDialogueCommandV1,
    ) -> DialogueTurnResultV1:
        """Invoke docs dialogue."""

    def validate_role_output(self, query: ValidateRoleOutputQueryV1) -> Mapping[str, Any]:
        """Validate role output format."""


__all__ = [
    "DialogueTurnCompletedEventV1",
    "DialogueTurnResultV1",
    "ILlmDialogueService",
    "InvokeDocsDialogueCommandV1",
    "InvokeRoleDialogueCommandV1",
    "LlmDialogueError",
    "ValidateRoleOutputQueryV1",
]
