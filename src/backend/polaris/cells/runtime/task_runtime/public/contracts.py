from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.cells.runtime.task_market.public.contracts import OwnerReworkHandoffV1

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1 = "task-runtime.owner-rework-execution-authorization/1"
_OWNER_REWORK_TASK_ROLES = frozenset({"owner", "requester"})


@dataclass(frozen=True)
class CreateRuntimeTaskCommandV1:
    task_id: str
    workspace: str
    title: str
    owner: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "title", _require_non_empty("title", self.title))
        object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))


@dataclass(frozen=True)
class UpdateRuntimeTaskCommandV1:
    task_id: str
    workspace: str
    status: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))


@dataclass(frozen=True)
class ReopenRuntimeTaskCommandV1:
    task_id: str
    workspace: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))


@dataclass(frozen=True)
class OwnerReworkExecutionAuthorizationV1:
    """Claim-scoped authority for one TaskMarket owner-rework execution.

    TaskMarket remains the authority for the owner/requester dependency graph
    and lease. TaskRuntime consumes this proof only to prepare its own
    execution row and session for the already-claimed work item.
    """

    schema_version: str
    workspace: str
    task_id: str
    lease_token: str
    worker_id: str
    worker_role: str
    task_role: str
    counterparty_task_id: str
    handoff: OwnerReworkHandoffV1
    claimed_item: Mapping[str, Any]
    counterparty_item: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        if self.schema_version != OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1:
            raise ValueError(f"schema_version must be {OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1!r}")
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "lease_token", _require_non_empty("lease_token", self.lease_token))
        object.__setattr__(self, "worker_id", _require_non_empty("worker_id", self.worker_id))
        object.__setattr__(self, "worker_role", _require_non_empty("worker_role", self.worker_role))
        task_role = _require_non_empty("task_role", self.task_role).lower()
        if task_role not in _OWNER_REWORK_TASK_ROLES:
            raise ValueError("task_role must be 'owner' or 'requester'")
        object.__setattr__(self, "task_role", task_role)
        object.__setattr__(
            self,
            "counterparty_task_id",
            _require_non_empty("counterparty_task_id", self.counterparty_task_id),
        )
        if not isinstance(self.handoff, OwnerReworkHandoffV1):
            raise ValueError("handoff must be OwnerReworkHandoffV1")
        object.__setattr__(self, "claimed_item", _to_dict_copy(self.claimed_item))
        object.__setattr__(self, "counterparty_item", _to_dict_copy(self.counterparty_item))


@dataclass(frozen=True)
class PrepareOwnerReworkExecutionCommandV1:
    """Request TaskRuntime preparation after TaskMarket has granted a claim."""

    authorization: OwnerReworkExecutionAuthorizationV1

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, OwnerReworkExecutionAuthorizationV1):
            raise ValueError("authorization must be OwnerReworkExecutionAuthorizationV1")


@dataclass(frozen=True)
class OwnerReworkExecutionPreparationResultV1:
    """Typed outcome of TaskRuntime's owner-rework execution preparation."""

    ok: bool
    code: str
    reason: str
    task_id: str
    handoff_id: str = ""
    task_role: str = ""
    runtime_task_id: str = ""
    reopened: bool = False
    idempotent: bool = False
    execution_event: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_non_empty("code", self.code))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "handoff_id", str(self.handoff_id or "").strip())
        object.__setattr__(self, "task_role", str(self.task_role or "").strip())
        object.__setattr__(self, "runtime_task_id", str(self.runtime_task_id or "").strip())
        object.__setattr__(self, "execution_event", _to_dict_copy(self.execution_event))

    def to_record(self) -> dict[str, Any]:
        """Return an observation-safe, JSON-serializable result projection."""

        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "task_id": self.task_id,
            "handoff_id": self.handoff_id,
            "task_role": self.task_role,
            "runtime_task_id": self.runtime_task_id,
            "reopened": self.reopened,
            "idempotent": self.idempotent,
            "execution_event": dict(self.execution_event),
        }


@dataclass(frozen=True)
class ListRuntimeTasksQueryV1:
    workspace: str
    statuses: tuple[str, ...] = field(default_factory=tuple)
    owner: str | None = None
    limit: int = 100
    offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "statuses", tuple(str(v) for v in self.statuses if str(v).strip()))
        if self.owner is not None:
            object.__setattr__(self, "owner", _require_non_empty("owner", self.owner))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True)
class GetRuntimeTaskQueryV1:
    task_id: str
    workspace: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))


@dataclass(frozen=True)
class RuntimeTaskLifecycleEventV1:
    event_id: str
    task_id: str
    workspace: str
    status: str
    occurred_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "occurred_at", _require_non_empty("occurred_at", self.occurred_at))
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))


@dataclass(frozen=True)
class RuntimeTaskResultV1:
    task_id: str
    workspace: str
    status: str
    version: int
    updated: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        if self.version < 0:
            raise ValueError("version must be >= 0")


class RuntimeTaskRuntimeError(RuntimeError):
    """Raised when `runtime.task_runtime` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_task_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1",
    "CreateRuntimeTaskCommandV1",
    "GetRuntimeTaskQueryV1",
    "ListRuntimeTasksQueryV1",
    "OwnerReworkExecutionAuthorizationV1",
    "OwnerReworkExecutionPreparationResultV1",
    "PrepareOwnerReworkExecutionCommandV1",
    "ReopenRuntimeTaskCommandV1",
    "RuntimeTaskLifecycleEventV1",
    "RuntimeTaskResultV1",
    "RuntimeTaskRuntimeError",
    "UpdateRuntimeTaskCommandV1",
]
