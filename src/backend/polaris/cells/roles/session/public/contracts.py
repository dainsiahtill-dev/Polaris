"""Public contracts for `roles.session` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


# ── Session Enums (moved from internal.conversation for public access) ───────────


class RoleHostKind(str, Enum):
    """Role host type enum."""

    WORKFLOW = "workflow"
    ELECTRON_WORKBENCH = "electron_workbench"
    TUI = "tui"
    CLI = "cli"
    API_SERVER = "api_server"
    HEADLESS = "headless"


class SessionType(str, Enum):
    """Session type enum."""

    WORKFLOW_MANAGED = "workflow_managed"
    STANDALONE = "standalone"
    WORKBENCH = "workbench"


class AttachmentMode(str, Enum):
    """Attachment mode enum."""

    ISOLATED = "isolated"
    ATTACHED_READONLY = "attached_readonly"
    ATTACHED_COLLABORATIVE = "attached_collaborative"


class SessionState(str, Enum):
    """Session state enum."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class CreateRoleSessionCommandV1:
    role: str
    workspace: str | None = None
    host_kind: str = "electron_workbench"
    session_type: str = "workbench"
    attachment_mode: str = "isolated"
    title: str | None = None
    context_config: Mapping[str, Any] = field(default_factory=dict)
    capability_profile: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        if self.workspace is not None:
            object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "host_kind", _require_non_empty("host_kind", self.host_kind))
        object.__setattr__(self, "session_type", _require_non_empty("session_type", self.session_type))
        normalized_mode = _require_non_empty("attachment_mode", self.attachment_mode)
        # Validate against AttachmentMode enum
        if normalized_mode not in AttachmentMode._value2member_map_:
            raise ValueError(f"attachment_mode must be one of: {list(AttachmentMode._value2member_map_.keys())}")
        object.__setattr__(self, "attachment_mode", normalized_mode)
        if self.title is not None:
            object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "context_config", _to_dict_copy(self.context_config))
        object.__setattr__(self, "capability_profile", _to_dict_copy(self.capability_profile))


@dataclass(frozen=True)
class UpdateRoleSessionCommandV1:
    session_id: str
    title: str | None = None
    context_config: Mapping[str, Any] | None = None
    capability_profile: Mapping[str, Any] | None = None
    state: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if self.title is not None:
            object.__setattr__(self, "title", _require_non_empty("title", self.title))
        if self.context_config is not None:
            object.__setattr__(self, "context_config", _to_dict_copy(self.context_config))
        if self.capability_profile is not None:
            object.__setattr__(self, "capability_profile", _to_dict_copy(self.capability_profile))
        if self.state is not None:
            object.__setattr__(self, "state", _require_non_empty("state", self.state))


@dataclass(frozen=True)
class AttachRoleSessionCommandV1:
    session_id: str
    run_id: str | None = None
    task_id: str | None = None
    mode: str = "attached_readonly"
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        if self.note is not None:
            object.__setattr__(self, "note", _require_non_empty("note", self.note))


@dataclass(frozen=True)
class SearchRoleSessionMemoryQueryV1:
    session_id: str
    query: str
    kind: str | None = None
    entity: str | None = None
    limit: int = 6

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "query", _require_non_empty("query", self.query))
        if self.kind is not None:
            object.__setattr__(self, "kind", _require_non_empty("kind", self.kind))
        if self.entity is not None:
            object.__setattr__(self, "entity", _require_non_empty("entity", self.entity))
        if self.limit <= 0:
            raise ValueError("limit must be > 0")


@dataclass(frozen=True)
class ReadRoleSessionArtifactQueryV1:
    session_id: str
    artifact_id: str
    start_line: int | None = None
    end_line: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "artifact_id", _require_non_empty("artifact_id", self.artifact_id))
        if self.start_line is not None and self.start_line <= 0:
            raise ValueError("start_line must be > 0 when provided")
        if self.end_line is not None and self.end_line <= 0:
            raise ValueError("end_line must be > 0 when provided")
        if self.start_line is not None and self.end_line is not None and self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")


@dataclass(frozen=True)
class ReadRoleSessionEpisodeQueryV1:
    session_id: str
    episode_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "episode_id", _require_non_empty("episode_id", self.episode_id))


@dataclass(frozen=True)
class GetRoleSessionStateQueryV1:
    session_id: str
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "path", _require_non_empty("path", self.path))


@dataclass(frozen=True)
class RoleSessionLifecycleEventV1:
    event_id: str
    session_id: str
    role: str
    status: str
    occurred_at: str
    run_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))
        if self.run_id is not None:
            object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if self.task_id is not None:
            object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))


@dataclass(frozen=True)
class RoleSessionContextQueryResultV1:
    ok: bool
    session_id: str
    payload: Any = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


@dataclass(frozen=True)
class RoleSessionResultV1:
    ok: bool
    session_id: str
    role: str
    state: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "role", _require_non_empty("role", self.role))
        object.__setattr__(self, "state", _require_non_empty("state", self.state))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class RoleSessionError(RuntimeError):
    """Structured contract error for `roles.session`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "role_session_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IRoleSessionService(Protocol):
    def create_session(self, command: CreateRoleSessionCommandV1) -> Any:
        """Create one session."""

    def update_session(self, command: UpdateRoleSessionCommandV1) -> Any:
        """Update one session."""

    def attach_session(self, command: AttachRoleSessionCommandV1) -> Any:
        """Attach one session."""


@runtime_checkable
class IRoleSessionContextMemoryService(Protocol):
    def search_memory(self, query: SearchRoleSessionMemoryQueryV1) -> RoleSessionContextQueryResultV1:
        """Search memory objects for one role session."""

    def read_artifact(self, query: ReadRoleSessionArtifactQueryV1) -> RoleSessionContextQueryResultV1:
        """Read one persisted Context OS artifact for one role session."""

    def read_episode(self, query: ReadRoleSessionEpisodeQueryV1) -> RoleSessionContextQueryResultV1:
        """Read one persisted Context OS episode for one role session."""

    def get_state(self, query: GetRoleSessionStateQueryV1) -> RoleSessionContextQueryResultV1:
        """Read one persisted Context OS state entry for one role session."""


__all__ = [
    "AttachRoleSessionCommandV1",
    "AttachmentMode",
    "CreateRoleSessionCommandV1",
    "GetRoleSessionStateQueryV1",
    "IRoleSessionContextMemoryService",
    "IRoleSessionService",
    "ReadRoleSessionArtifactQueryV1",
    "ReadRoleSessionEpisodeQueryV1",
    "RoleHostKind",
    "RoleSessionContextQueryResultV1",
    "RoleSessionError",
    "RoleSessionLifecycleEventV1",
    "RoleSessionResultV1",
    "SearchRoleSessionMemoryQueryV1",
    "SessionState",
    "SessionType",
    "UpdateRoleSessionCommandV1",
]
