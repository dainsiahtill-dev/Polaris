from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, cast, get_args

if TYPE_CHECKING:
    from .service import TaskRuntimeExecutionAttemptAuthorityV1


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _to_detached_dict(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy nested evidence so a verdict cannot retain caller-owned state."""

    return deepcopy(dict(payload or {}))


_SUCCESS_READINESS_EVIDENCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "parent_registry_source_head_seq",
        "operation_source_head_seq",
    }
)


def _readiness_evidence_key(key: object) -> str:
    """Require stable string keys while preserving typed failure diagnostics."""

    if not isinstance(key, str):
        raise TypeError("readiness evidence keys must be strings")
    return key


def _freeze_readiness_evidence(value: Any, *, active_object_ids: set[int]) -> Any:
    """Detach and recursively freeze one readiness-evidence value."""

    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        object_id = id(value)
        if object_id in active_object_ids:
            raise ValueError("readiness evidence must not contain cycles")
        active_object_ids.add(object_id)
        try:
            if isinstance(value, Mapping):
                return MappingProxyType(
                    {
                        _readiness_evidence_key(key): _freeze_readiness_evidence(
                            item,
                            active_object_ids=active_object_ids,
                        )
                        for key, item in value.items()
                    }
                )
            if isinstance(value, (list, tuple)):
                return tuple(_freeze_readiness_evidence(item, active_object_ids=active_object_ids) for item in value)
            return frozenset(_freeze_readiness_evidence(item, active_object_ids=active_object_ids) for item in value)
        finally:
            active_object_ids.remove(object_id)
    if isinstance(value, bytearray):
        return bytes(value)
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    raise TypeError("readiness evidence values must be immutable data")


def _to_immutable_evidence(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return deeply detached, immutable readiness evidence."""

    source = {} if payload is None else payload
    frozen = _freeze_readiness_evidence(source, active_object_ids=set())
    return cast(Mapping[str, Any], frozen)


def _task_row_fact(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return detached execution-fact metadata from a projected task row."""

    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    fact = metadata_map.get("task_runtime_execution_fact")
    return fact if isinstance(fact, Mapping) else {}


def _workflow_run_id_from_task_row(row: Mapping[str, Any]) -> str:
    """Return canonical workflow run identity from a projected task row."""

    fact_map = _task_row_fact(row)
    return str(row.get("workflow_run_id") or row.get("run_id") or fact_map.get("run_id") or "").strip()


def _factory_run_id_from_task_row(row: Mapping[str, Any]) -> str:
    """Return canonical Factory portfolio-run identity from a task row."""

    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    fact_map = _task_row_fact(row)
    return str(
        row.get("factory_run_id") or metadata_map.get("factory_run_id") or fact_map.get("factory_run_id") or ""
    ).strip()


OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1 = "task-runtime.owner-rework-execution-authorization/1"
_OWNER_REWORK_TASK_ROLES = frozenset({"owner", "requester"})
TASK_RUNTIME_EXECUTION_STREAM_V1: Final[str] = "task_runtime.execution"
TASK_RUNTIME_EXECUTION_SOURCE_V1: Final[str] = "runtime.task_runtime"
TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1 = "task-runtime.execution-fact/1"
_TERMINAL_EXECUTION_STATES = frozenset({"cancelled", "completed", "failed", "timed_out", "timeout"})


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


RuntimeTaskFactoryRunBindingCodeV1 = Literal[
    "factory_run_bound",
    "factory_run_binding_recovered",
    "factory_run_already_bound",
    "task_not_found",
    "factory_run_binding_conflict",
    "execution_event_append_failed",
]

ExpiredFactoryRunSessionFenceCodeV1 = Literal[
    "expired_sessions_fenced",
    "no_expired_sessions",
    "active_session_conflict",
    "session_fence_failed",
]


@dataclass(frozen=True)
class FenceExpiredFactoryRunSessionsCommandV1:
    """Fence expired active sessions before Factory stale-owner recovery.

    Expiry only revokes renewal/commit authority. This command is the explicit
    state transition that makes matching expired sessions non-active; callers
    must still query settlement evidence before releasing workspace authority.
    """

    workspace: str
    factory_run_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))


@dataclass(frozen=True)
class ExpiredFactoryRunSessionFenceResultV1:
    """Typed, observation-safe result of expired-session fencing."""

    ok: bool
    code: ExpiredFactoryRunSessionFenceCodeV1
    workspace: str
    factory_run_id: str
    fenced_session_ids: tuple[str, ...] = ()
    conflicts: tuple[Mapping[str, Any], ...] = ()
    execution_events: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        success_code = self.code in {"expired_sessions_fenced", "no_expired_sessions"}
        if self.ok != success_code:
            raise ValueError("ok must match expired-session fencing result code")
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )
        object.__setattr__(
            self,
            "fenced_session_ids",
            tuple(_require_non_empty("session_id", value) for value in self.fenced_session_ids),
        )
        object.__setattr__(
            self,
            "conflicts",
            tuple(_to_dict_copy(value) for value in self.conflicts),
        )
        object.__setattr__(
            self,
            "execution_events",
            tuple(_to_dict_copy(value) for value in self.execution_events),
        )
        if self.ok and self.conflicts:
            raise ValueError("successful expired-session fencing cannot contain conflicts")

    def to_record(self) -> dict[str, Any]:
        """Return a detached UTF-8 JSON-compatible control-plane record."""

        return {
            "schema_version": "task-runtime.expired-factory-run-session-fence/1",
            "ok": self.ok,
            "code": self.code,
            "workspace": self.workspace,
            "factory_run_id": self.factory_run_id,
            "fenced_session_ids": list(self.fenced_session_ids),
            "fenced_session_count": len(self.fenced_session_ids),
            "conflicts": [dict(value) for value in self.conflicts],
            "execution_events": [dict(value) for value in self.execution_events],
        }


_FACTORY_RUN_BINDING_SUCCESS_CODES = frozenset(
    {
        "factory_run_bound",
        "factory_run_binding_recovered",
        "factory_run_already_bound",
    }
)


@dataclass(frozen=True)
class BindRuntimeTaskToFactoryRunCommandV1:
    """Bind one canonical TaskRuntime row to one Factory portfolio run.

    This command deliberately exposes no arbitrary metadata payload. Factory
    run identity is a write-once authority field, not a general task-row patch.
    """

    workspace: str
    task_id: str
    factory_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )


@dataclass(frozen=True)
class RuntimeTaskFactoryRunBindingResultV1:
    """Typed outcome for write-once Factory portfolio-run binding."""

    ok: bool
    code: RuntimeTaskFactoryRunBindingCodeV1
    reason: str
    workspace: str
    task_id: str
    factory_run_id: str
    existing_factory_run_id: str = ""
    row_updated: bool = False
    event_recorded: bool = False
    idempotent: bool = False
    task_row: Mapping[str, Any] = field(default_factory=dict)
    execution_event: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        code = _require_non_empty("code", self.code)
        success_code = code in _FACTORY_RUN_BINDING_SUCCESS_CODES
        if self.ok != success_code:
            raise ValueError("ok must match Factory run-binding result code")
        if self.ok and not self.event_recorded:
            raise ValueError("successful Factory run binding requires execution-fact evidence")
        if self.idempotent != (code == "factory_run_already_bound"):
            raise ValueError("idempotent must be true only for factory_run_already_bound")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )
        object.__setattr__(
            self,
            "existing_factory_run_id",
            str(self.existing_factory_run_id or "").strip(),
        )
        object.__setattr__(self, "task_row", _to_dict_copy(self.task_row))
        object.__setattr__(self, "execution_event", _to_dict_copy(self.execution_event))

    def to_record(self) -> dict[str, Any]:
        """Return a detached JSON-serializable control-plane record."""

        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "workspace": self.workspace,
            "task_id": self.task_id,
            "factory_run_id": self.factory_run_id,
            "existing_factory_run_id": self.existing_factory_run_id,
            "row_updated": self.row_updated,
            "event_recorded": self.event_recorded,
            "idempotent": self.idempotent,
            "task_row": _to_dict_copy(self.task_row),
            "execution_event": _to_dict_copy(self.execution_event),
        }


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
    handoff: Mapping[str, Any]
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
        handoff = _to_dict_copy(self.handoff)
        if not handoff:
            raise ValueError("handoff must be a non-empty serialized handoff record")
        object.__setattr__(self, "handoff", handoff)
        object.__setattr__(self, "claimed_item", _to_dict_copy(self.claimed_item))
        object.__setattr__(self, "counterparty_item", _to_dict_copy(self.counterparty_item))


@dataclass(frozen=True)
class PrepareOwnerReworkExecutionCommandV1:
    """Request TaskRuntime preparation after TaskMarket has granted a claim."""

    authorization: OwnerReworkExecutionAuthorizationV1

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, OwnerReworkExecutionAuthorizationV1):
            raise ValueError("authorization must be OwnerReworkExecutionAuthorizationV1")


OwnerReworkExecutionPreparationCodeV1 = Literal[
    "owner_rework_authorization_malformed",
    "owner_rework_workspace_mismatch",
    "owner_rework_handoff_mismatch",
    "owner_rework_claim_evidence_invalid",
    "owner_rework_counterparty_evidence_invalid",
    "owner_rework_handoff_evidence_invalid",
    "runtime_task_not_found",
    "runtime_task_invalid",
    "owner_rework_authorization_conflict",
    "owner_rework_execution_already_prepared",
    "runtime_execution_lease_conflict",
    "owner_rework_execution_prepared",
]


@dataclass(frozen=True)
class OwnerReworkExecutionPreparationResultV1:
    """Typed outcome of TaskRuntime's owner-rework execution preparation."""

    ok: bool
    code: OwnerReworkExecutionPreparationCodeV1
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
class ObservableTaskRowsProjectionV1:
    """Observable TaskRuntime rows with explicit authority provenance."""

    workspace: str
    source: str
    authoritative: bool
    degraded: bool
    rows: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    readiness: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        source = _require_non_empty("source", self.source)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "rows", tuple(_to_dict_copy(row) for row in self.rows))
        object.__setattr__(self, "readiness", _to_dict_copy(self.readiness))
        if self.authoritative and self.degraded:
            raise ValueError("authoritative projection cannot be degraded")
        if self.authoritative and source != "task_runtime.execution_fact":
            raise ValueError("authoritative projection source must be task_runtime.execution_fact")
        if not self.authoritative and not self.degraded:
            raise ValueError("non-authoritative projection must be degraded")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached transport projection for HTTP and audit consumers."""

        return {
            "workspace": self.workspace,
            "source": self.source,
            "authoritative": self.authoritative,
            "degraded": self.degraded,
            "rows": [_to_dict_copy(row) for row in self.rows],
            "readiness": _to_dict_copy(self.readiness),
        }

    def rows_for_factory_run(self, factory_run_id: str) -> tuple[dict[str, Any], ...]:
        """Return detached rows bound exactly to one Factory portfolio run.

        Rows without a Factory run identity are excluded. They remain visible
        through ``rows`` for diagnostics, but cannot authorize run-scoped
        scheduling, completion, or QA decisions.
        """

        requested_factory_run_id = _require_non_empty("factory_run_id", factory_run_id)
        return tuple(
            _to_dict_copy(row) for row in self.rows if _factory_run_id_from_task_row(row) == requested_factory_run_id
        )

    def to_authority_dict(self, *, factory_run_id: str | None = None) -> dict[str, Any]:
        """Return the compact authority surface used by control-plane gates.

        Large task contracts and prompts are intentionally excluded. Completion
        consumers need only stable task identity, state, fact sequence, and
        provenance. This keeps the audit bundle bounded while preserving every
        field needed to fail closed. When ``factory_run_id`` is provided, rows
        from other Factory runs are excluded before projection; unbound rows
        are excluded because they cannot authorize a run-scoped decision.
        """

        requested_factory_run_id = str(factory_run_id or "").strip()
        source_rows: tuple[Mapping[str, Any], ...] = (
            self.rows_for_factory_run(requested_factory_run_id) if requested_factory_run_id else self.rows
        )
        projected_rows: list[dict[str, Any]] = []
        for row in source_rows:
            metadata = row.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else {}
            workflow_run_id = _workflow_run_id_from_task_row(row)
            projected_rows.append(
                {
                    "task_id": str(row.get("task_id") or row.get("id") or "").strip(),
                    "workflow_run_id": workflow_run_id,
                    "factory_run_id": _factory_run_id_from_task_row(row),
                    "status": str(row.get("status") or "").strip().lower(),
                    "execution_state": str(row.get("execution_state") or row.get("status") or "").strip().lower(),
                    "fact_event_seq": row.get("fact_event_seq"),
                    "source": str(metadata_map.get("source") or "").strip(),
                    "status_source": str(metadata_map.get("status_source") or "").strip(),
                }
            )
        return {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "workspace": self.workspace,
            "source": self.source,
            "authoritative": self.authoritative,
            "degraded": self.degraded,
            "requested_factory_run_id": requested_factory_run_id,
            "total_row_count": len(self.rows),
            "row_count": len(projected_rows),
            "rows": projected_rows,
            "readiness": _to_dict_copy(self.readiness),
        }


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionFactV1:
    """Immutable typed projection for one ``task_runtime.execution`` fact.

    ``transition_id`` identifies the state transition independently from the
    FactStream envelope. Its derived idempotency key is therefore stable
    across optimistic-CAS retries. Terminal session transitions persist this
    identity in the session record; non-terminal callers mint a fresh identity
    for every event, including every heartbeat.
    """

    transition_id: str
    event_type: str
    workspace: str
    task_id: str
    status: str
    execution_state: str
    occurred_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transition_id",
            _require_non_empty("transition_id", self.transition_id),
        )
        object.__setattr__(
            self,
            "event_type",
            _require_non_empty("event_type", self.event_type).lower(),
        )
        object.__setattr__(
            self,
            "workspace",
            _require_non_empty("workspace", self.workspace),
        )
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status).lower())
        object.__setattr__(
            self,
            "execution_state",
            _require_non_empty("execution_state", self.execution_state).lower(),
        )
        object.__setattr__(
            self,
            "occurred_at",
            _require_non_empty("occurred_at", self.occurred_at),
        )
        object.__setattr__(self, "payload", _to_dict_copy(self.payload))

    @classmethod
    def from_payload(
        cls,
        *,
        transition_id: str,
        payload: Mapping[str, Any],
    ) -> TaskRuntimeExecutionFactV1:
        """Validate and detach the service-layer execution projection."""

        detached = _to_dict_copy(payload)
        return cls(
            transition_id=transition_id,
            event_type=str(detached.get("event_type") or ""),
            workspace=str(detached.get("workspace") or ""),
            task_id=str(detached.get("task_id") or ""),
            status=str(detached.get("status") or ""),
            execution_state=str(detached.get("execution_state") or ""),
            occurred_at=str(detached.get("timestamp") or ""),
            payload=detached,
        )

    @property
    def terminal(self) -> bool:
        """Return whether this fact carries a terminal execution verdict."""

        return bool({self.status, self.execution_state}.intersection(_TERMINAL_EXECUTION_STATES))

    @property
    def idempotency_key(self) -> str:
        """Return the stable FactStream key for this event projection."""

        return f"{TASK_RUNTIME_EXECUTION_STREAM_V1}:{self.event_type}:{self.transition_id}"

    def to_record(self) -> dict[str, Any]:
        """Return the backward-compatible flat fact payload."""

        record = _to_dict_copy(self.payload)
        record.update(
            {
                "schema_version": TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1,
                "transition_id": self.transition_id,
                "idempotency_key": self.idempotency_key,
                "event_type": self.event_type,
                "workspace": self.workspace,
                "task_id": self.task_id,
                "status": self.status,
                "execution_state": self.execution_state,
                "timestamp": self.occurred_at,
                "terminal": self.terminal,
            }
        )
        return record


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


TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1: Final[str] = "task-runtime.execution-attempt-identity/1"


TaskRuntimeExecutionAttemptValidationCodeV1 = Literal[
    "valid",
    "workspace_mismatch",
    "task_not_found",
    "session_not_found",
    "session_corrupt",
    "session_task_mismatch",
    "session_mismatch",
    "attempt_mismatch",
    "role_mismatch",
    "worker_mismatch",
    "run_mismatch",
    "external_task_id_mismatch",
    "lease_version_mismatch",
    "session_not_active",
    "session_lease_expired",
    "file_lock_timeout",
]


TaskRuntimeExecutionAttemptSettlementOutcomeV1 = Literal["completed", "failed", "suspended"]


TaskRuntimeExecutionAttemptSettlementCodeV1 = Literal[
    "settled",
    "settlement_idempotent",
    "workspace_mismatch",
    "session_not_found",
    "session_task_mismatch",
    "session_mismatch",
    "attempt_mismatch",
    "role_mismatch",
    "worker_mismatch",
    "run_mismatch",
    "external_task_id_mismatch",
    "lease_version_mismatch",
    "session_not_active",
    "session_lease_expired",
    "file_lock_timeout",
    "session_terminal_preserved",
    "terminal_outcome_conflict",
    "settlement_parent_close_required",
    "settlement_parent_close_proof_required",
    "settlement_parent_registry_invalid",
    "settlement_parent_registry_unavailable",
    "settlement_directed_effect_unresolved",
    "settlement_effect_outcome_conflict",
    "settlement_terminal_intent_conflict",
    "settlement_parent_close_failed",
    "row_projection_failed",
]


TaskRuntimeExecutionAttemptHeartbeatCodeV1 = Literal[
    "heartbeat_renewed",
    "workspace_mismatch",
    "session_not_found",
    "session_task_mismatch",
    "session_mismatch",
    "attempt_mismatch",
    "role_mismatch",
    "worker_mismatch",
    "run_mismatch",
    "external_task_id_mismatch",
    "lease_version_mismatch",
    "session_not_active",
    "session_lease_expired",
    "terminal_fence_pending",
    "file_lock_timeout",
    "session_terminal_preserved",
    "row_projection_failed",
]


TaskRuntimeExecutionAttemptAuthoritySnapshotCodeV1 = Literal[
    "available",
    "authority_lock_timeout",
]


TaskRuntimeExecutionAttemptAuthorityHeartbeatCodeV1 = Literal[
    "heartbeat_renewed",
    "heartbeat_rejected",
    "authority_closed",
    "authority_lock_timeout",
    "authority_operation_in_progress",
    "heartbeat_missing_renewed_identity",
    "heartbeat_identity_drift",
    "heartbeat_invalid_verdict",
    "heartbeat_callback_exception",
]


TaskRuntimeExecutionAttemptAuthoritySettlementCodeV1 = Literal[
    "settled",
    "terminal_replay",
    "terminal_outcome_conflict",
    "authority_lock_timeout",
    "authority_operation_in_progress",
    "settlement_rejected",
    "settlement_verdict_drift",
    "settlement_invalid_verdict",
    "settlement_callback_exception",
]


TaskRuntimeExecutionAttemptAuthorityOpenCodeV1 = Literal[
    "valid",
    "workspace_mismatch",
    "task_not_found",
    "session_not_found",
    "session_corrupt",
    "session_task_mismatch",
    "session_mismatch",
    "attempt_mismatch",
    "role_mismatch",
    "worker_mismatch",
    "run_mismatch",
    "external_task_id_mismatch",
    "lease_version_mismatch",
    "session_not_active",
    "session_lease_expired",
    "file_lock_timeout",
    "session_terminal_preserved",
    "terminal_outcome_conflict",
    "row_projection_failed",
    "authority_open_internal_error",
]


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptIdentityV1:
    """Canonical persisted identity for one active TaskRuntime execution attempt."""

    workspace: str
    task_id: int
    external_task_id: str
    session_id: str
    attempt: int
    role_id: str
    worker_id: str
    run_id: str
    lease_expires_at: str
    schema_version: str = TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if isinstance(self.task_id, bool) or not isinstance(self.task_id, int) or self.task_id < 1:
            raise ValueError("task_id must be an int >= 1")
        object.__setattr__(self, "external_task_id", str(self.external_task_id or "").strip())
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be an int >= 1")
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "worker_id", _require_non_empty("worker_id", self.worker_id))
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())
        object.__setattr__(
            self,
            "lease_expires_at",
            _require_non_empty("lease_expires_at", self.lease_expires_at),
        )
        schema_version = _require_non_empty("schema_version", self.schema_version)
        if schema_version != TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1:
            raise ValueError(f"schema_version must be {TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1!r}")
        object.__setattr__(self, "schema_version", schema_version)

    def to_record(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable persisted-attempt projection."""

        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "task_id": self.task_id,
            "external_task_id": self.external_task_id,
            "session_id": self.session_id,
            "attempt": self.attempt,
            "role_id": self.role_id,
            "worker_id": self.worker_id,
            "run_id": self.run_id,
            "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TaskRuntimeExecutionAttemptIdentityV1:
        """Parse exactly one canonical persisted-attempt record, fail-closed."""

        if not isinstance(record, Mapping):
            raise TypeError("execution attempt record must be a mapping")
        expected_fields = {
            "schema_version",
            "workspace",
            "task_id",
            "external_task_id",
            "session_id",
            "attempt",
            "role_id",
            "worker_id",
            "run_id",
            "lease_expires_at",
        }
        actual_fields = set(record)
        missing_fields = sorted(expected_fields - actual_fields)
        unexpected_fields = sorted(actual_fields - expected_fields)
        if missing_fields or unexpected_fields:
            raise ValueError(
                "execution attempt record fields must match canonical schema: "
                f"missing={missing_fields!r}, unexpected={unexpected_fields!r}"
            )
        string_fields = (
            "schema_version",
            "workspace",
            "external_task_id",
            "session_id",
            "role_id",
            "worker_id",
            "run_id",
            "lease_expires_at",
        )
        for field_name in string_fields:
            if not isinstance(record[field_name], str):
                raise TypeError(f"execution attempt record {field_name} must be a string")
        for field_name in ("task_id", "attempt"):
            value = record[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"execution attempt record {field_name} must be an int")
        if record["schema_version"] != TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1:
            raise ValueError("execution attempt record schema_version is unsupported")
        return cls(
            workspace=record["workspace"],
            task_id=record["task_id"],
            external_task_id=record["external_task_id"],
            session_id=record["session_id"],
            attempt=record["attempt"],
            role_id=record["role_id"],
            worker_id=record["worker_id"],
            run_id=record["run_id"],
            lease_expires_at=record["lease_expires_at"],
            schema_version=record["schema_version"],
        )


@dataclass(frozen=True, slots=True)
class SettleTaskRuntimeExecutionAttemptCommandV1:
    """Request the one canonical terminal settlement for a claimed attempt."""

    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1
    summary: str
    lock_timeout_seconds: float = 5.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.outcome not in {"completed", "failed", "suspended"}:
            raise ValueError("outcome must be completed, failed, or suspended")
        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string")
        if isinstance(self.lock_timeout_seconds, bool) or not isinstance(self.lock_timeout_seconds, (int, float)):
            raise TypeError("lock_timeout_seconds must be a finite number")
        timeout = float(self.lock_timeout_seconds)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("lock_timeout_seconds must be a finite number >= 0")
        object.__setattr__(self, "lock_timeout_seconds", timeout)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptSettlementVerdictV1:
    """Typed result for a one-winner execution-attempt settlement."""

    success: bool
    code: TaskRuntimeExecutionAttemptSettlementCodeV1
    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1
    idempotent: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.success != (self.code in {"settled", "settlement_idempotent"}):
            raise ValueError("success must match settlement verdict code")
        if self.idempotent != (self.code == "settlement_idempotent"):
            raise ValueError("idempotent must match settlement_idempotent code")
        object.__setattr__(self, "evidence", _to_dict_copy(self.evidence))

    def to_record(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "code": self.code,
            "reason": self.code,
            "workspace": self.workspace,
            "identity": self.identity.to_record(),
            "outcome": self.outcome,
            "idempotent": self.idempotent,
            "evidence": _to_dict_copy(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class HeartbeatTaskRuntimeExecutionAttemptCommandV1:
    """Request one bounded, identity-fenced TaskRuntime lease renewal."""

    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    lease_ttl_seconds: int
    lock_timeout_seconds: float
    context_summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if (
            isinstance(self.lease_ttl_seconds, bool)
            or not isinstance(self.lease_ttl_seconds, int)
            or self.lease_ttl_seconds < 1
        ):
            raise ValueError("lease_ttl_seconds must be an int >= 1")
        if isinstance(self.lock_timeout_seconds, bool) or not isinstance(
            self.lock_timeout_seconds,
            (int, float),
        ):
            raise TypeError("lock_timeout_seconds must be a finite number")
        normalized_timeout = float(self.lock_timeout_seconds)
        if not math.isfinite(normalized_timeout) or normalized_timeout < 0:
            raise ValueError("lock_timeout_seconds must be a finite number >= 0")
        object.__setattr__(self, "lock_timeout_seconds", normalized_timeout)
        if not isinstance(self.context_summary, str):
            raise TypeError("context_summary must be a string")

    def to_record(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable heartbeat command record."""

        return {
            "workspace": self.workspace,
            "identity": self.identity.to_record(),
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "lock_timeout_seconds": self.lock_timeout_seconds,
            "context_summary": self.context_summary,
        }


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
    """Typed outcome for one bounded TaskRuntime execution-attempt heartbeat."""

    success: bool
    code: TaskRuntimeExecutionAttemptHeartbeatCodeV1
    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    renewed_identity: TaskRuntimeExecutionAttemptIdentityV1 | None = None
    evidence_anchor: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.success != (self.code == "heartbeat_renewed"):
            raise ValueError("success must match heartbeat verdict code")
        if self.success and not isinstance(
            self.renewed_identity,
            TaskRuntimeExecutionAttemptIdentityV1,
        ):
            raise ValueError("successful heartbeat verdict requires renewed_identity")
        if not self.success and self.renewed_identity is not None:
            raise ValueError("rejected heartbeat verdict must not include renewed_identity")
        object.__setattr__(self, "evidence_anchor", _to_dict_copy(self.evidence_anchor))

    @property
    def reason(self) -> TaskRuntimeExecutionAttemptHeartbeatCodeV1:
        """Expose the stable failure/success reason expected by consumers."""

        return self.code

    def to_record(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable heartbeat verdict record."""

        return {
            "success": self.success,
            "code": self.code,
            "reason": self.reason,
            "workspace": self.workspace,
            "identity": self.identity.to_record(),
            "renewed_identity": (self.renewed_identity.to_record() if self.renewed_identity is not None else None),
            "evidence_anchor": _to_dict_copy(self.evidence_anchor),
        }


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptAuthoritySnapshotV1:
    """Bounded process-local snapshot of a public attempt authority handle.

    The identity remains a TaskRuntime fact projection. This snapshot is not
    durable and must not be used as a second source of execution authority.
    """

    success: bool
    code: TaskRuntimeExecutionAttemptAuthoritySnapshotCodeV1
    identity: TaskRuntimeExecutionAttemptIdentityV1 | None
    closed: bool

    def __post_init__(self) -> None:
        if self.success != (self.code == "available"):
            raise ValueError("success must match authority snapshot code")
        if self.success != isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise ValueError("available authority snapshot requires an identity")
        if not self.success and self.identity is not None:
            raise ValueError("unavailable authority snapshot must not include an identity")


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1:
    """Typed handle-level heartbeat verdict with exact TaskRuntime evidence."""

    success: bool
    code: TaskRuntimeExecutionAttemptAuthorityHeartbeatCodeV1
    identity: TaskRuntimeExecutionAttemptIdentityV1 | None
    task_runtime_verdict: TaskRuntimeExecutionAttemptHeartbeatVerdictV1 | None = None
    callback_error_type: str = ""

    def __post_init__(self) -> None:
        if self.success != (self.code == "heartbeat_renewed"):
            raise ValueError("success must match authority heartbeat code")
        if self.success and not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise ValueError("successful authority heartbeat requires an identity")
        if self.task_runtime_verdict is not None and not isinstance(
            self.task_runtime_verdict,
            TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
        ):
            raise TypeError("task_runtime_verdict must be a typed heartbeat verdict")
        if not isinstance(self.callback_error_type, str):
            raise TypeError("callback_error_type must be a string")
        if self.code == "heartbeat_callback_exception" and not self.callback_error_type:
            raise ValueError("callback exception verdict requires callback_error_type")
        if self.code != "heartbeat_callback_exception" and self.callback_error_type:
            raise ValueError("only callback exception verdicts include callback_error_type")


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1:
    """Typed handle-level settlement verdict for one terminal attempt outcome."""

    success: bool
    code: TaskRuntimeExecutionAttemptAuthoritySettlementCodeV1
    identity: TaskRuntimeExecutionAttemptIdentityV1 | None
    outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1
    task_runtime_verdict: TaskRuntimeExecutionAttemptSettlementVerdictV1 | None = None
    callback_error_type: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed", "suspended"}:
            raise ValueError("outcome must be completed, failed, or suspended")
        if self.success != (self.code in {"settled", "terminal_replay"}):
            raise ValueError("success must match authority settlement code")
        if self.success and not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise ValueError("successful authority settlement requires an identity")
        if self.task_runtime_verdict is not None and not isinstance(
            self.task_runtime_verdict,
            TaskRuntimeExecutionAttemptSettlementVerdictV1,
        ):
            raise TypeError("task_runtime_verdict must be a typed settlement verdict")
        if not isinstance(self.callback_error_type, str):
            raise TypeError("callback_error_type must be a string")
        if self.code == "settlement_callback_exception" and not self.callback_error_type:
            raise ValueError("callback exception verdict requires callback_error_type")
        if self.code != "settlement_callback_exception" and self.callback_error_type:
            raise ValueError("only callback exception verdicts include callback_error_type")


@dataclass(frozen=True, slots=True)
class ValidateTaskRuntimeExecutionAttemptQueryV1:
    """Request a read-only validation of a persisted execution attempt."""

    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    lock_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if isinstance(self.lock_timeout_seconds, bool) or not isinstance(self.lock_timeout_seconds, (int, float)):
            raise TypeError("lock_timeout_seconds must be a finite number")
        timeout = float(self.lock_timeout_seconds)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("lock_timeout_seconds must be a finite number >= 0")
        object.__setattr__(self, "lock_timeout_seconds", timeout)


@dataclass(frozen=True, slots=True)
class OpenTaskRuntimeExecutionAttemptAuthorityCommandV1:
    """Request read-only opening of a process-local authority for one attempt."""

    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    lock_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if isinstance(self.lock_timeout_seconds, bool) or not isinstance(self.lock_timeout_seconds, (int, float)):
            raise TypeError("lock_timeout_seconds must be a finite number")
        timeout = float(self.lock_timeout_seconds)
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("lock_timeout_seconds must be a finite number >= 0")
        object.__setattr__(self, "lock_timeout_seconds", timeout)

    def to_record(self) -> dict[str, Any]:
        """Return a detached UTF-8 JSON-compatible open request record."""

        return {
            "workspace": self.workspace,
            "identity": self.identity.to_record(),
            "lock_timeout_seconds": self.lock_timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptValidationVerdictV1:
    """Fail-closed, read-only authority verdict for one execution attempt."""

    valid: bool
    code: TaskRuntimeExecutionAttemptValidationCodeV1
    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.valid != (self.code == "valid"):
            raise ValueError("valid must match execution-attempt validation code")
        object.__setattr__(self, "evidence", _to_dict_copy(self.evidence))

    def to_record(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable validation result."""

        return {
            "valid": self.valid,
            "code": self.code,
            "workspace": self.workspace,
            "identity": self.identity.to_record(),
            "evidence": _to_dict_copy(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
    """Typed, non-durable authority-open result for a validated attempt.

    ``authority`` is intentionally process-local and omitted from ``to_record``.
    TaskRuntime facts remain the only durable source of execution authority.
    """

    success: bool
    code: TaskRuntimeExecutionAttemptAuthorityOpenCodeV1
    workspace: str
    identity: TaskRuntimeExecutionAttemptIdentityV1
    authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if not isinstance(self.identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.success != (self.code == "valid"):
            raise ValueError("success must match authority-open verdict code")
        if self.authority is not None:
            from .service import TaskRuntimeExecutionAttemptAuthorityV1

            if not isinstance(self.authority, TaskRuntimeExecutionAttemptAuthorityV1):
                raise TypeError("authority must be TaskRuntimeExecutionAttemptAuthorityV1 or None")
        if self.success != (self.authority is not None):
            raise ValueError("successful authority-open verdict requires exactly one authority")
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))

    def to_record(self) -> dict[str, Any]:
        """Return UTF-8 JSON-compatible evidence without serializing authority."""

        return {
            "success": self.success,
            "code": self.code,
            "workspace": self.workspace,
            "identity": self.identity.to_record(),
            "authority_opened": self.authority is not None,
            "evidence": _to_detached_dict(self.evidence),
        }


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


DIRECTED_EFFECT_OPERATION_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-operation/1"
DIRECTED_EFFECT_OPERATION_SCHEMA_V2: Final[str] = "task-runtime.directed-effect-operation/2"
DIRECTED_EFFECT_OPERATION_SCHEMA_V3: Final[str] = "task-runtime.directed-effect-operation/3"
DIRECTED_EFFECT_OPERATION_SCHEMA_V4: Final[str] = "task-runtime.directed-effect-operation/4"
DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-operation-snapshot/1"
DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-claim-grant/1"
DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-inventory-intent/1"
DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-inventory-member/1"
DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-inventory-projection/1"
DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-parent-binding/1"
DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-parent-correlation/1"
DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1: Final[str] = (
    "task-runtime.directed-effect-parent-registry-identity/1"
)
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1: Final[str] = "task-runtime.directed-effect-parent-registry/1"
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2: Final[str] = "task-runtime.directed-effect-parent-registry/2"
DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3: Final[str] = "task-runtime.directed-effect-parent-registry/3"
DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1: Final[str] = (
    "task-runtime.directed-effect-parent-registry-projection/1"
)
DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1: Final[str] = (
    "task-runtime.directed-effect-parent-readiness-projection/1"
)

DirectedEffectInventoryEffectTypeV1 = Literal["write", "async"]
DirectedEffectInventoryExecutionModeV1 = Literal["write_serial", "async_receipt"]
DirectedEffectInventoryContingencyKindV1 = Literal["forward", "rollback"]

_DIRECTED_EFFECT_INVENTORY_EFFECT_MODE_PAIRS: Final[
    frozenset[tuple[DirectedEffectInventoryEffectTypeV1, DirectedEffectInventoryExecutionModeV1]]
] = frozenset(
    {
        ("write", "write_serial"),
        ("async", "async_receipt"),
    }
)
_LOWERCASE_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")

DirectedEffectOperationStateV1 = Literal[
    "INTENT_COMMITTED",
    "EFFECT_STARTED",
    "RECOVERY_PENDING",
    "RECEIPT_COMMITTED",
    "CLOSED_BY_PARENT",
    "ABORTED",
    "DEAD_LETTER",
]

DirectedEffectReceiptOutcomeV1 = Literal["succeeded", "failed"]

_DIRECTED_EFFECT_OPERATION_STATES: tuple[DirectedEffectOperationStateV1, ...] = (
    "INTENT_COMMITTED",
    "EFFECT_STARTED",
    "RECOVERY_PENDING",
    "RECEIPT_COMMITTED",
    "CLOSED_BY_PARENT",
    "ABORTED",
    "DEAD_LETTER",
)

DirectedEffectAuthorityFailureCodeV1 = Literal[
    "operation_not_found",
    "parent_binding_not_found",
    "parent_binding_conflict",
    "parent_binding_version_conflict",
    "parent_binding_event_conflict",
    "parent_binding_hash_mismatch",
    "parent_admission_idempotency_conflict",
    "parent_open_conflict",
    "parent_closed",
    "parent_registry_version_conflict",
    "parent_registry_expected_seq_conflict",
    "workspace_mismatch",
    "task_mismatch",
    "execution_attempt_mismatch",
    "turn_mismatch",
    "batch_mismatch",
    "operation_identity_conflict",
    "operation_version_conflict",
    "stream_expected_seq_conflict",
    "illegal_transition",
    "idempotency_conflict",
    "deo_semantic_drift",
    "task_not_found",
    "session_not_found",
    "session_corrupt",
    "session_task_mismatch",
    "session_mismatch",
    "attempt_mismatch",
    "role_mismatch",
    "worker_mismatch",
    "run_mismatch",
    "external_task_id_mismatch",
    "lease_version_mismatch",
    "session_not_active",
    "session_lease_expired",
    "file_lock_timeout",
    "execution_attempt_validation_unknown",
    "strict_stream_corruption",
    "strict_stream_torn_tail",
    "strict_stream_unknown_schema",
    "strict_stream_overload",
    "stream_lock_timeout",
    "stream_append_failed",
    "stream_cas_exhausted",
    "fact_stream_unknown_failure",
    "stream_lock_missing",
    "guarded_reprepare_exhausted",
    "guarded_receipt_mismatch",
    "idempotency_semantic_conflict",
    "inventory_not_sealed",
    "inventory_seal_conflict",
    "inventory_requires_empty_operation_stream",
    "inventory_member_not_found",
    "inventory_member_conflict",
    "inventory_admission_incomplete",
    "inventory_admission_unexpected",
    "inventory_not_ready",
    "receipt_binding_conflict",
    "receipt_evidence_conflict",
    "recovery_evidence_conflict",
    "recovery_deadline_exceeded",
    "dead_letter_evidence_conflict",
]

DirectedEffectOperationCodeV1 = (
    Literal[
        "parent_admitted",
        "parent_idempotent_replay",
        "parent_registry_found",
        "admitted",
        "effect_claimed",
        "receipt_committed",
        "recovery_pending",
        "dead_lettered",
        "closed_by_parent",
        "aborted",
        "found",
        "idempotent_replay",
    ]
    | DirectedEffectAuthorityFailureCodeV1
)

DirectedEffectInventoryCodeV1 = (
    Literal[
        "inventory_sealed",
        "inventory_seal_idempotent_replay",
        "inventory_ready",
        "inventory_ready_idempotent_replay",
        "inventory_observed",
    ]
    | DirectedEffectAuthorityFailureCodeV1
)

_DIRECTED_EFFECT_AUTHORITY_FAILURE_CODES: Final[frozenset[str]] = frozenset(
    get_args(DirectedEffectAuthorityFailureCodeV1)
)

DirectedEffectParentReadinessCodeV1 = DirectedEffectOperationCodeV1 | Literal["readiness_observed"]


def _directed_effect_token(name: str, value: str) -> str:
    return _require_non_empty(name, value)


def _directed_effect_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an int >= 1")
    return value


def _directed_effect_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be an int >= 0")
    return value


def _directed_effect_inventory_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _directed_effect_inventory_digest(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if len(value) != 64 or any(character not in _LOWERCASE_HEX_DIGITS for character in value):
        raise ValueError(f"{name} must be exactly 64 lowercase hexadecimal characters")
    return value


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryIntentV1:
    """One immutable, execution-grade member of a sealed effect inventory."""

    ordinal: int
    tool_call_id: str
    normalized_tool_name: str
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    contingency_kind: DirectedEffectInventoryContingencyKindV1 | None = None
    schema_version: str = DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1

    def __post_init__(self) -> None:
        _directed_effect_non_negative_int("ordinal", self.ordinal)
        object.__setattr__(
            self,
            "tool_call_id",
            _directed_effect_inventory_token("tool_call_id", self.tool_call_id),
        )
        object.__setattr__(
            self,
            "normalized_tool_name",
            _directed_effect_inventory_token("normalized_tool_name", self.normalized_tool_name),
        )
        if not isinstance(self.effect_type, str):
            raise TypeError("effect_type must be a string")
        if not isinstance(self.execution_mode, str):
            raise TypeError("execution_mode must be a string")
        if (self.effect_type, self.execution_mode) not in _DIRECTED_EFFECT_INVENTORY_EFFECT_MODE_PAIRS:
            raise ValueError("effect_type and execution_mode must form a supported pair")
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _directed_effect_inventory_digest(field_name, getattr(self, field_name)),
            )
        if self.contingency_kind is not None and not isinstance(self.contingency_kind, str):
            raise TypeError("contingency_kind must be a string or None")
        if self.contingency_kind not in (None, "forward", "rollback"):
            raise ValueError("contingency_kind must be None, 'forward', or 'rollback'")
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1:
            raise ValueError("unsupported directed effect inventory intent schema")

    def to_record(self) -> dict[str, object]:
        """Return the exact canonical persisted intent projection."""

        return {
            "schema_version": self.schema_version,
            "ordinal": self.ordinal,
            "tool_call_id": self.tool_call_id,
            "normalized_tool_name": self.normalized_tool_name,
            "effect_type": self.effect_type,
            "execution_mode": self.execution_mode,
            "intended_effect_fingerprint": self.intended_effect_fingerprint,
            "policy_verdict_hash": self.policy_verdict_hash,
            "expected_receipt_binding_hash": self.expected_receipt_binding_hash,
            "contingency_kind": self.contingency_kind,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectInventoryIntentV1:
        """Parse one exact canonical inventory intent, failing closed on drift."""

        if not isinstance(record, Mapping):
            raise TypeError("directed effect inventory intent record must be a mapping")
        expected_fields = {
            "schema_version",
            "ordinal",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "contingency_kind",
        }
        actual_fields = set(record)
        if actual_fields != expected_fields:
            missing_fields = sorted(expected_fields - actual_fields)
            unexpected_fields = sorted(actual_fields - expected_fields)
            raise ValueError(
                "directed effect inventory intent record fields must match canonical schema: "
                f"missing={missing_fields!r}, unexpected={unexpected_fields!r}"
            )
        ordinal = record["ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise TypeError("directed effect inventory intent ordinal must be an int")
        string_fields = (
            "schema_version",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        )
        for field_name in string_fields:
            if not isinstance(record[field_name], str):
                raise TypeError(f"directed effect inventory intent {field_name} must be a string")
        contingency_kind = record["contingency_kind"]
        if contingency_kind is not None and not isinstance(contingency_kind, str):
            raise TypeError("directed effect inventory intent contingency_kind must be a string or None")
        if record["schema_version"] != DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1:
            raise ValueError("directed effect inventory intent schema_version is unsupported")
        return cls(
            schema_version=record["schema_version"],
            ordinal=ordinal,
            tool_call_id=record["tool_call_id"],
            normalized_tool_name=record["normalized_tool_name"],
            effect_type=cast(DirectedEffectInventoryEffectTypeV1, record["effect_type"]),
            execution_mode=cast(DirectedEffectInventoryExecutionModeV1, record["execution_mode"]),
            intended_effect_fingerprint=record["intended_effect_fingerprint"],
            policy_verdict_hash=record["policy_verdict_hash"],
            expected_receipt_binding_hash=record["expected_receipt_binding_hash"],
            contingency_kind=cast(DirectedEffectInventoryContingencyKindV1 | None, contingency_kind),
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryMemberV1:
    """One canonical sealed member with server-derived effect identities."""

    ordinal: int
    tool_call_id: str
    effect_id: str
    operation_id: str
    normalized_tool_name: str
    effect_type: DirectedEffectInventoryEffectTypeV1
    execution_mode: DirectedEffectInventoryExecutionModeV1
    intended_effect_fingerprint: str
    policy_verdict_hash: str
    expected_receipt_binding_hash: str
    contingency_kind: DirectedEffectInventoryContingencyKindV1 | None = None
    schema_version: str = DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1

    def __post_init__(self) -> None:
        intent = DirectedEffectInventoryIntentV1(
            ordinal=self.ordinal,
            tool_call_id=self.tool_call_id,
            normalized_tool_name=self.normalized_tool_name,
            effect_type=self.effect_type,
            execution_mode=self.execution_mode,
            intended_effect_fingerprint=self.intended_effect_fingerprint,
            policy_verdict_hash=self.policy_verdict_hash,
            expected_receipt_binding_hash=self.expected_receipt_binding_hash,
            contingency_kind=self.contingency_kind,
        )
        for field_name in (
            "ordinal",
            "tool_call_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "contingency_kind",
        ):
            object.__setattr__(self, field_name, getattr(intent, field_name))
        object.__setattr__(self, "effect_id", _directed_effect_inventory_token("effect_id", self.effect_id))
        object.__setattr__(
            self,
            "operation_id",
            _directed_effect_inventory_token("operation_id", self.operation_id),
        )
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1:
            raise ValueError("unsupported directed effect inventory member schema")

    def to_record(self) -> dict[str, object]:
        """Return the exact canonical persisted member projection."""

        return {
            "schema_version": self.schema_version,
            "ordinal": self.ordinal,
            "tool_call_id": self.tool_call_id,
            "effect_id": self.effect_id,
            "operation_id": self.operation_id,
            "normalized_tool_name": self.normalized_tool_name,
            "effect_type": self.effect_type,
            "execution_mode": self.execution_mode,
            "intended_effect_fingerprint": self.intended_effect_fingerprint,
            "policy_verdict_hash": self.policy_verdict_hash,
            "expected_receipt_binding_hash": self.expected_receipt_binding_hash,
            "contingency_kind": self.contingency_kind,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectInventoryMemberV1:
        """Parse one exact canonical inventory member, failing closed on drift."""

        if not isinstance(record, Mapping):
            raise TypeError("directed effect inventory member record must be a mapping")
        expected_fields = {
            "schema_version",
            "ordinal",
            "tool_call_id",
            "effect_id",
            "operation_id",
            "normalized_tool_name",
            "effect_type",
            "execution_mode",
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "contingency_kind",
        }
        actual_fields = set(record)
        if actual_fields != expected_fields:
            missing_fields = sorted(expected_fields - actual_fields)
            unexpected_fields = sorted(actual_fields - expected_fields)
            raise ValueError(
                "directed effect inventory member record fields must match canonical schema: "
                f"missing={missing_fields!r}, unexpected={unexpected_fields!r}"
            )
        schema_version = record["schema_version"]
        if not isinstance(schema_version, str):
            raise TypeError("directed effect inventory member schema_version must be a string")
        if schema_version != DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1:
            raise ValueError("directed effect inventory member schema_version is unsupported")
        intent = DirectedEffectInventoryIntentV1.from_record(
            {
                "schema_version": DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1,
                "ordinal": record["ordinal"],
                "tool_call_id": record["tool_call_id"],
                "normalized_tool_name": record["normalized_tool_name"],
                "effect_type": record["effect_type"],
                "execution_mode": record["execution_mode"],
                "intended_effect_fingerprint": record["intended_effect_fingerprint"],
                "policy_verdict_hash": record["policy_verdict_hash"],
                "expected_receipt_binding_hash": record["expected_receipt_binding_hash"],
                "contingency_kind": record["contingency_kind"],
            }
        )
        return cls(
            schema_version=schema_version,
            ordinal=intent.ordinal,
            tool_call_id=intent.tool_call_id,
            effect_id=record["effect_id"],
            operation_id=record["operation_id"],
            normalized_tool_name=intent.normalized_tool_name,
            effect_type=intent.effect_type,
            execution_mode=intent.execution_mode,
            intended_effect_fingerprint=intent.intended_effect_fingerprint,
            policy_verdict_hash=intent.policy_verdict_hash,
            expected_receipt_binding_hash=intent.expected_receipt_binding_hash,
            contingency_kind=intent.contingency_kind,
        )


@dataclass(frozen=True, slots=True)
class ParentCorrelationV1:
    """Non-authoritative caller correlation attached to one parent admission."""

    turn_id: str
    batch_id: str
    schema_version: str = DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent correlation schema")
        object.__setattr__(self, "turn_id", _directed_effect_token("turn_id", self.turn_id))
        object.__setattr__(self, "batch_id", _directed_effect_token("batch_id", self.batch_id))

    def to_record(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "turn_id": self.turn_id,
            "batch_id": self.batch_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> ParentCorrelationV1:
        expected = {"schema_version", "turn_id", "batch_id"}
        if not isinstance(record, Mapping) or set(record) != expected:
            raise ValueError("parent correlation record fields must match canonical schema")
        if any(not isinstance(record[field_name], str) for field_name in expected):
            raise TypeError("parent correlation record fields must be strings")
        return cls(
            schema_version=record["schema_version"],
            turn_id=record["turn_id"],
            batch_id=record["batch_id"],
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectParentRegistryIdentityV1:
    """Lease-independent identity for one execution-attempt parent registry."""

    workspace: str
    task_id: int
    external_task_id: str
    session_id: str
    attempt: int
    role_id: str
    worker_id: str
    run_id: str
    schema_version: str = DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent registry identity schema")
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        _directed_effect_positive_int("attempt", self.attempt)
        for field_name in ("session_id", "role_id", "worker_id"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        object.__setattr__(self, "external_task_id", str(self.external_task_id or "").strip())
        object.__setattr__(self, "run_id", str(self.run_id or "").strip())

    @classmethod
    def from_execution_attempt(
        cls,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> DirectedEffectParentRegistryIdentityV1:
        if not isinstance(identity, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("identity must be TaskRuntimeExecutionAttemptIdentityV1")
        return cls(
            workspace=identity.workspace,
            task_id=identity.task_id,
            external_task_id=identity.external_task_id,
            session_id=identity.session_id,
            attempt=identity.attempt,
            role_id=identity.role_id,
            worker_id=identity.worker_id,
            run_id=identity.run_id,
        )

    @property
    def execution_attempt_id(self) -> str:
        return f"{self.session_id}:{self.attempt}"

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "task_id": self.task_id,
            "external_task_id": self.external_task_id,
            "session_id": self.session_id,
            "attempt": self.attempt,
            "role_id": self.role_id,
            "worker_id": self.worker_id,
            "run_id": self.run_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectParentRegistryIdentityV1:
        expected = {
            "schema_version",
            "workspace",
            "task_id",
            "external_task_id",
            "session_id",
            "attempt",
            "role_id",
            "worker_id",
            "run_id",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise ValueError("parent registry identity fields must match canonical schema")
        string_fields = expected - {"task_id", "attempt"}
        if any(not isinstance(record[field_name], str) for field_name in string_fields):
            raise TypeError("parent registry identity string fields are invalid")
        for field_name in ("task_id", "attempt"):
            value = record[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"parent registry identity {field_name} must be an int")
        return cls(
            schema_version=record["schema_version"],
            workspace=record["workspace"],
            task_id=record["task_id"],
            external_task_id=record["external_task_id"],
            session_id=record["session_id"],
            attempt=record["attempt"],
            role_id=record["role_id"],
            worker_id=record["worker_id"],
            run_id=record["run_id"],
        )


@dataclass(frozen=True, slots=True)
class DirectedEffectParentBindingV1:
    """Restart-safe reference to one authoritative registry admission fact."""

    schema_version: str
    registry_identity: DirectedEffectParentRegistryIdentityV1
    registry_stream_token: str
    registry_version: int
    parent_sequence: int
    binding_id: str
    operation_stream_token: str
    binding_hash: str
    admission_idempotency_key: str
    correlation: ParentCorrelationV1
    actor: str
    source_event_id: str
    source_event_seq: int

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent binding schema")
        if not isinstance(self.registry_identity, DirectedEffectParentRegistryIdentityV1):
            raise TypeError("registry_identity must be DirectedEffectParentRegistryIdentityV1")
        if not isinstance(self.correlation, ParentCorrelationV1):
            raise TypeError("correlation must be ParentCorrelationV1")
        _directed_effect_positive_int("registry_version", self.registry_version)
        _directed_effect_positive_int("parent_sequence", self.parent_sequence)
        _directed_effect_positive_int("source_event_seq", self.source_event_seq)
        if self.source_event_seq != self.registry_version:
            raise ValueError("source_event_seq must equal registry_version")
        for field_name in (
            "registry_stream_token",
            "binding_id",
            "operation_stream_token",
            "binding_hash",
            "admission_idempotency_key",
            "actor",
            "source_event_id",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))

    @property
    def workspace(self) -> str:
        return self.registry_identity.workspace

    @property
    def task_id(self) -> int:
        return self.registry_identity.task_id

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "registry_identity": self.registry_identity.to_record(),
            "registry_stream_token": self.registry_stream_token,
            "registry_version": self.registry_version,
            "parent_sequence": self.parent_sequence,
            "binding_id": self.binding_id,
            "operation_stream_token": self.operation_stream_token,
            "binding_hash": self.binding_hash,
            "admission_idempotency_key": self.admission_idempotency_key,
            "correlation": self.correlation.to_record(),
            "actor": self.actor,
            "source_event_id": self.source_event_id,
            "source_event_seq": self.source_event_seq,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> DirectedEffectParentBindingV1:
        expected = {
            "schema_version",
            "registry_identity",
            "registry_stream_token",
            "registry_version",
            "parent_sequence",
            "binding_id",
            "operation_stream_token",
            "binding_hash",
            "admission_idempotency_key",
            "correlation",
            "actor",
            "source_event_id",
            "source_event_seq",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise ValueError("parent binding record fields must match canonical schema")
        registry_identity = record["registry_identity"]
        correlation = record["correlation"]
        if not isinstance(registry_identity, Mapping) or not isinstance(correlation, Mapping):
            raise TypeError("parent binding nested records must be mappings")
        string_fields = expected - {
            "registry_identity",
            "registry_version",
            "parent_sequence",
            "correlation",
            "source_event_seq",
        }
        if any(not isinstance(record[field_name], str) for field_name in string_fields):
            raise TypeError("parent binding string fields are invalid")
        for field_name in ("registry_version", "parent_sequence", "source_event_seq"):
            value = record[field_name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"parent binding {field_name} must be an int")
        return cls(
            schema_version=record["schema_version"],
            registry_identity=DirectedEffectParentRegistryIdentityV1.from_record(registry_identity),
            registry_stream_token=record["registry_stream_token"],
            registry_version=record["registry_version"],
            parent_sequence=record["parent_sequence"],
            binding_id=record["binding_id"],
            operation_stream_token=record["operation_stream_token"],
            binding_hash=record["binding_hash"],
            admission_idempotency_key=record["admission_idempotency_key"],
            correlation=ParentCorrelationV1.from_record(correlation),
            actor=record["actor"],
            source_event_id=record["source_event_id"],
            source_event_seq=record["source_event_seq"],
        )


@dataclass(frozen=True, slots=True)
class SealDirectedEffectInventoryCommandV1:
    """Seal the complete immutable inventory before any child effect claim."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    intents: tuple[DirectedEffectInventoryIntentV1, ...]
    expected_registry_version: int
    expected_registry_seq: int
    expected_operation_head_seq: int = 0
    actor: str = "roles.kernel"

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, str):
            raise TypeError("workspace must be a string")
        workspace = self.workspace.strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        canonical_workspace = str(Path(workspace).resolve())
        if workspace != canonical_workspace:
            raise ValueError("workspace must be canonical")
        object.__setattr__(self, "workspace", canonical_workspace)

        _directed_effect_positive_int("task_id", self.task_id)
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        if type(self.parent_binding) is not DirectedEffectParentBindingV1:
            raise TypeError("parent_binding must be exactly DirectedEffectParentBindingV1")
        if self.execution_attempt.workspace != canonical_workspace:
            raise ValueError("execution_attempt workspace must match workspace")
        if self.parent_binding.workspace != canonical_workspace:
            raise ValueError("parent_binding workspace must match workspace")
        if self.execution_attempt.task_id != self.task_id:
            raise ValueError("execution_attempt task_id must match task_id")
        if self.parent_binding.task_id != self.task_id:
            raise ValueError("parent_binding task_id must match task_id")
        expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            self.execution_attempt
        )
        if self.parent_binding.registry_identity != expected_registry_identity:
            raise ValueError("parent_binding registry identity must match execution_attempt")

        if not isinstance(self.intents, tuple):
            raise TypeError("intents must be a tuple")
        if not 1 <= len(self.intents) <= 64:
            raise ValueError("intents must contain between 1 and 64 items")
        detached_intents = tuple(intent for intent in self.intents)
        seen_tool_call_ids: set[str] = set()
        for expected_ordinal, intent in enumerate(detached_intents):
            if type(intent) is not DirectedEffectInventoryIntentV1:
                raise TypeError("each intent must be exactly DirectedEffectInventoryIntentV1")
            if intent.ordinal != expected_ordinal:
                raise ValueError("intent ordinals must be contiguous and ordered from zero")
            if intent.tool_call_id in seen_tool_call_ids:
                raise ValueError("intent tool_call_id values must be unique")
            seen_tool_call_ids.add(intent.tool_call_id)
        object.__setattr__(self, "intents", detached_intents)

        _directed_effect_positive_int("expected_registry_version", self.expected_registry_version)
        if (
            isinstance(self.expected_registry_seq, bool)
            or not isinstance(self.expected_registry_seq, int)
            or self.expected_registry_seq < 2
        ):
            raise ValueError("expected_registry_seq must be an int >= 2")
        if self.expected_registry_seq != self.expected_registry_version + 1:
            raise ValueError("expected_registry_seq must equal expected_registry_version + 1")
        if (
            isinstance(self.expected_operation_head_seq, bool)
            or not isinstance(self.expected_operation_head_seq, int)
            or self.expected_operation_head_seq != 0
        ):
            raise ValueError("expected_operation_head_seq must be exactly 0")
        object.__setattr__(self, "actor", _directed_effect_inventory_token("actor", self.actor))


def _directed_effect_inventory_identity(
    *,
    workspace: str,
    task_id: int,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    parent_binding: DirectedEffectParentBindingV1,
) -> str:
    """Validate and return one exact canonical inventory parent identity."""

    if not isinstance(workspace, str):
        raise TypeError("workspace must be a string")
    workspace_token = workspace.strip()
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    if workspace != workspace_token:
        raise ValueError("workspace must not contain surrounding whitespace")
    canonical_workspace = str(Path(workspace_token).resolve())
    if workspace_token != canonical_workspace:
        raise ValueError("workspace must be canonical")
    _directed_effect_positive_int("task_id", task_id)
    if type(execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
        raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
    if type(parent_binding) is not DirectedEffectParentBindingV1:
        raise TypeError("parent_binding must be exactly DirectedEffectParentBindingV1")
    if execution_attempt.workspace != canonical_workspace:
        raise ValueError("execution_attempt workspace must match workspace")
    if parent_binding.workspace != canonical_workspace:
        raise ValueError("parent_binding workspace must match workspace")
    if execution_attempt.task_id != task_id:
        raise ValueError("execution_attempt task_id must match task_id")
    if parent_binding.task_id != task_id:
        raise ValueError("parent_binding task_id must match task_id")
    expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(execution_attempt)
    if parent_binding.registry_identity != expected_registry_identity:
        raise ValueError("parent_binding registry identity must match execution_attempt")
    return canonical_workspace


@dataclass(frozen=True, slots=True)
class FinalizeDirectedEffectInventoryAdmissionCommandV1:
    """Request exact sealed/admitted inventory equality before effect claims."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    inventory_hash: str
    expected_registry_version: int
    expected_registry_seq: int
    expected_operation_head_seq: int
    actor: str = "roles.kernel"

    def __post_init__(self) -> None:
        canonical_workspace = _directed_effect_inventory_identity(
            workspace=self.workspace,
            task_id=self.task_id,
            execution_attempt=self.execution_attempt,
            parent_binding=self.parent_binding,
        )
        object.__setattr__(self, "workspace", canonical_workspace)
        object.__setattr__(
            self,
            "inventory_hash",
            _directed_effect_inventory_digest("inventory_hash", self.inventory_hash),
        )
        if (
            isinstance(self.expected_registry_version, bool)
            or not isinstance(self.expected_registry_version, int)
            or self.expected_registry_version < 2
        ):
            raise ValueError("expected_registry_version must be an int >= 2")
        if isinstance(self.expected_registry_seq, bool) or not isinstance(self.expected_registry_seq, int):
            raise ValueError("expected_registry_seq must be an int")
        if self.expected_registry_seq != self.expected_registry_version + 1:
            raise ValueError("expected_registry_seq must equal expected_registry_version + 1")
        _directed_effect_positive_int("expected_operation_head_seq", self.expected_operation_head_seq)
        if not isinstance(self.actor, str):
            raise TypeError("actor must be a string")
        if self.actor != "roles.kernel":
            raise ValueError("actor must be exactly 'roles.kernel'")


@dataclass(frozen=True, slots=True)
class GetDirectedEffectInventoryQueryV1:
    """Read the inventory bound to one exact attempt and parent admission."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1

    def __post_init__(self) -> None:
        canonical_workspace = _directed_effect_inventory_identity(
            workspace=self.workspace,
            task_id=self.task_id,
            execution_attempt=self.execution_attempt,
            parent_binding=self.parent_binding,
        )
        object.__setattr__(self, "workspace", canonical_workspace)


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryProjectionV1:
    """Immutable diagnostic projection of one sealed parent inventory."""

    schema_version: str
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding_id: str
    members: tuple[DirectedEffectInventoryMemberV1, ...]
    inventory_hash: str
    sealed_event_id: str
    sealed_event_seq: int
    parent_registry_source_head_seq: int
    operation_source_head_seq: int
    inventory_ready: bool
    ready_event_id: str | None
    ready_event_seq: int | None
    admitted_count: int
    missing_operation_ids: tuple[str, ...]
    unexpected_operation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1:
            raise ValueError("unsupported directed effect inventory projection schema")

        if not isinstance(self.workspace, str):
            raise TypeError("workspace must be a string")
        workspace = self.workspace.strip()
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        if self.workspace != workspace:
            raise ValueError("workspace must not contain surrounding whitespace")
        canonical_workspace = str(Path(workspace).resolve())
        if workspace != canonical_workspace:
            raise ValueError("workspace must be canonical")
        object.__setattr__(self, "workspace", canonical_workspace)
        _directed_effect_positive_int("task_id", self.task_id)
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        if self.execution_attempt.workspace != canonical_workspace:
            raise ValueError("execution_attempt workspace must match workspace")
        if self.execution_attempt.task_id != self.task_id:
            raise ValueError("execution_attempt task_id must match task_id")
        object.__setattr__(
            self,
            "parent_binding_id",
            _directed_effect_inventory_token("parent_binding_id", self.parent_binding_id),
        )

        if not isinstance(self.members, tuple):
            raise TypeError("members must be a tuple")
        if not 1 <= len(self.members) <= 64:
            raise ValueError("members must contain between 1 and 64 items")
        members = tuple(member for member in self.members)
        tool_call_ids: set[str] = set()
        effect_ids: set[str] = set()
        operation_ids: set[str] = set()
        for expected_ordinal, member in enumerate(members):
            if type(member) is not DirectedEffectInventoryMemberV1:
                raise TypeError("each member must be exactly DirectedEffectInventoryMemberV1")
            if member.ordinal != expected_ordinal:
                raise ValueError("member ordinals must be contiguous and ordered from zero")
            for field_name, seen_values in (
                ("tool_call_id", tool_call_ids),
                ("effect_id", effect_ids),
                ("operation_id", operation_ids),
            ):
                value = getattr(member, field_name)
                if value in seen_values:
                    raise ValueError(f"member {field_name} values must be unique")
                seen_values.add(value)
        object.__setattr__(self, "members", members)
        object.__setattr__(
            self,
            "inventory_hash",
            _directed_effect_inventory_digest("inventory_hash", self.inventory_hash),
        )
        object.__setattr__(
            self,
            "sealed_event_id",
            _directed_effect_inventory_token("sealed_event_id", self.sealed_event_id),
        )
        _directed_effect_positive_int("sealed_event_seq", self.sealed_event_seq)
        _directed_effect_non_negative_int(
            "parent_registry_source_head_seq",
            self.parent_registry_source_head_seq,
        )
        _directed_effect_non_negative_int(
            "operation_source_head_seq",
            self.operation_source_head_seq,
        )
        if self.sealed_event_seq > self.parent_registry_source_head_seq:
            raise ValueError("sealed_event_seq must not exceed parent_registry_source_head_seq")
        if type(self.inventory_ready) is not bool:
            raise TypeError("inventory_ready must be exactly bool")

        if self.inventory_ready:
            if self.ready_event_id is None or self.ready_event_seq is None:
                raise ValueError("ready inventory requires ready event id and sequence")
            object.__setattr__(
                self,
                "ready_event_id",
                _directed_effect_inventory_token("ready_event_id", self.ready_event_id),
            )
            _directed_effect_positive_int("ready_event_seq", self.ready_event_seq)
            if self.ready_event_seq <= self.sealed_event_seq:
                raise ValueError("ready_event_seq must follow sealed_event_seq")
            if self.ready_event_seq > self.parent_registry_source_head_seq:
                raise ValueError("ready_event_seq must not exceed parent_registry_source_head_seq")
        elif self.ready_event_id is not None or self.ready_event_seq is not None:
            raise ValueError("non-ready inventory must not carry a ready event")

        _directed_effect_non_negative_int("admitted_count", self.admitted_count)
        missing_operation_ids = self._validated_operation_ids(
            "missing_operation_ids",
            self.missing_operation_ids,
        )
        unexpected_operation_ids = self._validated_operation_ids(
            "unexpected_operation_ids",
            self.unexpected_operation_ids,
        )
        member_operation_ids = tuple(member.operation_id for member in members)
        member_operation_id_set = set(member_operation_ids)
        missing_set = set(missing_operation_ids)
        unexpected_set = set(unexpected_operation_ids)
        if not missing_set.issubset(member_operation_id_set):
            raise ValueError("missing_operation_ids must be a subset of member operation ids")
        expected_missing_order = tuple(
            operation_id for operation_id in member_operation_ids if operation_id in missing_set
        )
        if missing_operation_ids != expected_missing_order:
            raise ValueError("missing_operation_ids must be unique and follow member order")
        if len(unexpected_set) != len(unexpected_operation_ids):
            raise ValueError("unexpected_operation_ids must be unique")
        if unexpected_set & member_operation_id_set:
            raise ValueError("unexpected_operation_ids must be disjoint from member operation ids")
        expected_admitted_count = len(members) - len(missing_operation_ids)
        if self.admitted_count != expected_admitted_count:
            raise ValueError("admitted_count must equal members minus missing operations")
        if self.inventory_ready and (missing_operation_ids or unexpected_operation_ids):
            raise ValueError("ready inventory must have exact complete admission")
        minimum_operation_head_seq = self.admitted_count + len(unexpected_operation_ids)
        if self.operation_source_head_seq < minimum_operation_head_seq:
            raise ValueError("operation_source_head_seq must cover admitted and unexpected operations")
        object.__setattr__(self, "missing_operation_ids", missing_operation_ids)
        object.__setattr__(self, "unexpected_operation_ids", unexpected_operation_ids)

    @staticmethod
    def _validated_operation_ids(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
        if not isinstance(values, tuple):
            raise TypeError(f"{name} must be a tuple")
        return tuple(_directed_effect_inventory_token(name, value) for value in values)


_DIRECTED_EFFECT_INVENTORY_SUCCESS_CODES: Final[frozenset[str]] = frozenset(
    {
        "inventory_sealed",
        "inventory_seal_idempotent_replay",
        "inventory_ready",
        "inventory_ready_idempotent_replay",
        "inventory_observed",
    }
)


@dataclass(frozen=True, slots=True)
class DirectedEffectInventoryResultV1:
    """Typed inventory command/query result with immutable diagnostic evidence."""

    ok: bool
    code: DirectedEffectInventoryCodeV1
    projection: DirectedEffectInventoryProjectionV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.ok) is not bool:
            raise TypeError("ok must be exactly bool")
        if not isinstance(self.code, str):
            raise TypeError("code must be a string")
        success = self.code in _DIRECTED_EFFECT_INVENTORY_SUCCESS_CODES
        failure = self.code in _DIRECTED_EFFECT_AUTHORITY_FAILURE_CODES
        if not success and not failure:
            raise ValueError("code must be an inventory success or directed effect authority failure")
        if self.ok != success:
            raise ValueError("ok must match inventory success code")
        if self.ok != (self.projection is not None):
            raise ValueError("successful inventory result requires exactly one projection")
        if self.projection is not None and type(self.projection) is not DirectedEffectInventoryProjectionV1:
            raise TypeError("projection must be exactly DirectedEffectInventoryProjectionV1 or None")
        if not isinstance(self.evidence, Mapping):
            raise TypeError("evidence must be a mapping")
        object.__setattr__(self, "evidence", _to_immutable_evidence(self.evidence))


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationIdentityV1:
    """Canonical TaskRuntime identity for one child directed effect."""

    workspace: str
    task_id: int
    execution_attempt_id: str
    parent_binding_id: str
    parent_sequence: int
    tool_call_id: str
    effect_id: str
    operation_id: str
    operation_stream_token: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        _directed_effect_positive_int("parent_sequence", self.parent_sequence)
        for field_name in (
            "execution_attempt_id",
            "parent_binding_id",
            "tool_call_id",
            "effect_id",
            "operation_id",
            "operation_stream_token",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))

    def to_record(self) -> dict[str, object]:
        return {
            "workspace": self.workspace,
            "task_id": self.task_id,
            "execution_attempt_id": self.execution_attempt_id,
            "parent_binding_id": self.parent_binding_id,
            "parent_sequence": self.parent_sequence,
            "tool_call_id": self.tool_call_id,
            "effect_id": self.effect_id,
            "operation_id": self.operation_id,
            "operation_stream_token": self.operation_stream_token,
        }


@dataclass(frozen=True, slots=True)
class DirectedEffectClaimGrantV1:
    """One hash-bound claim capability returned only by the original claim."""

    schema_version: str
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    operation: DirectedEffectOperationIdentityV1
    member: DirectedEffectInventoryMemberV1
    inventory_hash: str
    operation_version: int
    claim_event_id: str
    claim_event_seq: int
    operation_source_head_seq: int
    parent_registry_source_head_seq: int
    grant_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be a string")
        if self.schema_version != DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1:
            raise ValueError("unsupported directed effect claim grant schema")
        if type(self.execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
            raise TypeError("execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1")
        if type(self.parent_binding) is not DirectedEffectParentBindingV1:
            raise TypeError("parent_binding must be exactly DirectedEffectParentBindingV1")
        if type(self.operation) is not DirectedEffectOperationIdentityV1:
            raise TypeError("operation must be exactly DirectedEffectOperationIdentityV1")
        if type(self.member) is not DirectedEffectInventoryMemberV1:
            raise TypeError("member must be exactly DirectedEffectInventoryMemberV1")

        expected_registry_identity = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(
            self.execution_attempt
        )
        if self.parent_binding.registry_identity != expected_registry_identity:
            raise ValueError("parent_binding registry identity must match execution_attempt")
        if self.operation.workspace != self.execution_attempt.workspace:
            raise ValueError("operation workspace must match execution_attempt")
        if self.operation.task_id != self.execution_attempt.task_id:
            raise ValueError("operation task_id must match execution_attempt")
        if self.operation.execution_attempt_id != expected_registry_identity.execution_attempt_id:
            raise ValueError("operation execution_attempt_id must match execution_attempt")
        if self.operation.parent_binding_id != self.parent_binding.binding_id:
            raise ValueError("operation parent_binding_id must match parent_binding")
        if self.operation.parent_sequence != self.parent_binding.parent_sequence:
            raise ValueError("operation parent_sequence must match parent_binding")
        if self.operation.operation_stream_token != self.parent_binding.operation_stream_token:
            raise ValueError("operation stream must match parent_binding")
        if self.operation.tool_call_id != self.member.tool_call_id:
            raise ValueError("operation tool_call_id must match inventory member")
        if self.operation.effect_id != self.member.effect_id:
            raise ValueError("operation effect_id must match inventory member")
        if self.operation.operation_id != self.member.operation_id:
            raise ValueError("operation operation_id must match inventory member")

        object.__setattr__(
            self,
            "inventory_hash",
            _directed_effect_inventory_digest("inventory_hash", self.inventory_hash),
        )
        if (
            isinstance(self.operation_version, bool)
            or not isinstance(self.operation_version, int)
            or self.operation_version < 2
        ):
            raise ValueError("operation_version must be an int >= 2")
        object.__setattr__(
            self,
            "claim_event_id",
            _directed_effect_inventory_token("claim_event_id", self.claim_event_id),
        )
        _directed_effect_positive_int("claim_event_seq", self.claim_event_seq)
        _directed_effect_positive_int("operation_source_head_seq", self.operation_source_head_seq)
        if self.claim_event_seq != self.operation_source_head_seq:
            raise ValueError("claim_event_seq must equal operation_source_head_seq")
        if self.operation_source_head_seq < self.operation_version:
            raise ValueError("operation_source_head_seq must be >= operation_version")
        _directed_effect_positive_int(
            "parent_registry_source_head_seq",
            self.parent_registry_source_head_seq,
        )
        minimum_parent_registry_head_seq = self.parent_binding.source_event_seq + 2
        if self.parent_registry_source_head_seq < minimum_parent_registry_head_seq:
            raise ValueError("parent registry head must cover parent binding, inventory seal, and inventory ready")
        object.__setattr__(
            self,
            "grant_hash",
            _directed_effect_inventory_digest("grant_hash", self.grant_hash),
        )
        if self.grant_hash != self._canonical_grant_hash():
            raise ValueError("grant_hash must match the canonical unsigned grant record")

    def _unsigned_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "execution_attempt": self.execution_attempt.to_record(),
            "parent_binding": self.parent_binding.to_record(),
            "operation": self.operation.to_record(),
            "member": self.member.to_record(),
            "inventory_hash": self.inventory_hash,
            "operation_version": self.operation_version,
            "claim_event_id": self.claim_event_id,
            "claim_event_seq": self.claim_event_seq,
            "operation_source_head_seq": self.operation_source_head_seq,
            "parent_registry_source_head_seq": self.parent_registry_source_head_seq,
        }

    def _canonical_grant_hash(self) -> str:
        encoded = json.dumps(
            self._unsigned_record(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_record(self) -> dict[str, object]:
        """Return the canonical signed grant record."""

        return {**self._unsigned_record(), "grant_hash": self.grant_hash}


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationSnapshotV1:
    """Rebuildable, non-authoritative cached projection of a DEO aggregate."""

    schema_version: str
    source_head_seq: int
    last_event_id: str
    operation: DirectedEffectOperationIdentityV1
    state: DirectedEffectOperationStateV1
    version: int

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1:
            raise ValueError("unsupported directed effect operation snapshot schema")
        _directed_effect_non_negative_int("source_head_seq", self.source_head_seq)
        object.__setattr__(self, "last_event_id", _directed_effect_token("last_event_id", self.last_event_id))
        if not isinstance(self.operation, DirectedEffectOperationIdentityV1):
            raise TypeError("operation must be DirectedEffectOperationIdentityV1")
        if self.state not in {
            "INTENT_COMMITTED",
            "EFFECT_STARTED",
            "RECOVERY_PENDING",
            "RECEIPT_COMMITTED",
            "CLOSED_BY_PARENT",
            "ABORTED",
            "DEAD_LETTER",
        }:
            raise ValueError("unsupported directed effect operation state")
        _directed_effect_positive_int("version", self.version)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_head_seq": self.source_head_seq,
            "last_event_id": self.last_event_id,
            "operation": self.operation.to_record(),
            "state": self.state,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class DirectedEffectParentRegistryProjectionV1:
    """Read-only strict reconstruction of one attempt-scoped parent registry."""

    schema_version: str
    registry_identity: DirectedEffectParentRegistryIdentityV1
    registry_stream_token: str
    registry_version: int
    source_head_seq: int
    next_expected_seq: int
    next_parent_sequence: int
    open_binding: DirectedEffectParentBindingV1 | None
    admissions_by_idempotency_key: Mapping[str, DirectedEffectParentBindingV1] = field(default_factory=dict)
    bindings_by_id: Mapping[str, DirectedEffectParentBindingV1] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent registry projection schema")
        if not isinstance(self.registry_identity, DirectedEffectParentRegistryIdentityV1):
            raise TypeError("registry_identity must be DirectedEffectParentRegistryIdentityV1")
        object.__setattr__(
            self,
            "registry_stream_token",
            _directed_effect_token("registry_stream_token", self.registry_stream_token),
        )
        _directed_effect_non_negative_int("registry_version", self.registry_version)
        _directed_effect_non_negative_int("source_head_seq", self.source_head_seq)
        _directed_effect_positive_int("next_expected_seq", self.next_expected_seq)
        _directed_effect_positive_int("next_parent_sequence", self.next_parent_sequence)
        if self.registry_version != self.source_head_seq:
            raise ValueError("registry_version must equal source_head_seq")
        if self.next_expected_seq != self.source_head_seq + 1:
            raise ValueError("next_expected_seq must equal source_head_seq + 1")
        admissions = dict(self.admissions_by_idempotency_key)
        bindings = dict(self.bindings_by_id)
        if any(not isinstance(value, DirectedEffectParentBindingV1) for value in admissions.values()):
            raise TypeError("admissions_by_idempotency_key values must be parent bindings")
        if any(not isinstance(value, DirectedEffectParentBindingV1) for value in bindings.values()):
            raise TypeError("bindings_by_id values must be parent bindings")
        if self.open_binding is not None and not isinstance(self.open_binding, DirectedEffectParentBindingV1):
            raise TypeError("open_binding must be DirectedEffectParentBindingV1 or None")
        object.__setattr__(self, "admissions_by_idempotency_key", admissions)
        object.__setattr__(self, "bindings_by_id", bindings)

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "registry_identity": self.registry_identity.to_record(),
            "registry_stream_token": self.registry_stream_token,
            "registry_version": self.registry_version,
            "source_head_seq": self.source_head_seq,
            "next_expected_seq": self.next_expected_seq,
            "next_parent_sequence": self.next_parent_sequence,
            "open_binding": self.open_binding.to_record() if self.open_binding is not None else None,
            "admissions_by_idempotency_key": {
                key: value.to_record() for key, value in self.admissions_by_idempotency_key.items()
            },
            "bindings_by_id": {key: value.to_record() for key, value in self.bindings_by_id.items()},
        }


@dataclass(frozen=True, slots=True)
class AdmitDirectedEffectParentCommandV1:
    """CAS admission request for one attempt-scoped parent registry."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    correlation: ParentCorrelationV1
    admission_idempotency_key: str
    expected_version: int
    expected_seq: int
    actor: str = "task_runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.correlation, ParentCorrelationV1):
            raise TypeError("correlation must be ParentCorrelationV1")
        object.__setattr__(
            self,
            "admission_idempotency_key",
            _directed_effect_token("admission_idempotency_key", self.admission_idempotency_key),
        )
        object.__setattr__(self, "actor", _directed_effect_token("actor", self.actor))
        _directed_effect_non_negative_int("expected_version", self.expected_version)
        _directed_effect_positive_int("expected_seq", self.expected_seq)


@dataclass(frozen=True, slots=True)
class AdmitDirectedEffectParentBatchCommandV1:
    """Admit one canonical batch, rolling over only a receipt-complete predecessor.

    TaskRuntime owns the predecessor close and derives every registry CAS value
    while holding the active execution-attempt locks. Callers provide identity
    and correlation only; they cannot manufacture a parent sequence or head.
    """

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    correlation: ParentCorrelationV1
    admission_idempotency_key: str
    actor: str = "task_runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.correlation, ParentCorrelationV1):
            raise TypeError("correlation must be ParentCorrelationV1")
        object.__setattr__(
            self,
            "admission_idempotency_key",
            _directed_effect_token("admission_idempotency_key", self.admission_idempotency_key),
        )
        object.__setattr__(self, "actor", _directed_effect_token("actor", self.actor))


@dataclass(frozen=True, slots=True)
class GetDirectedEffectParentRegistryQueryV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")


@dataclass(frozen=True, slots=True)
class EnrollDirectedEffectParentRegistryStreamCommandV1:
    """Explicitly enroll the parent-registry stream for one validated attempt.

    This is a maintenance command. Its receipt is observational FactStream
    evidence and never grants business-operation authority.
    """

    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1

    def __post_init__(self) -> None:
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")


@dataclass(frozen=True, slots=True)
class EnrollDirectedEffectOperationStreamCommandV1:
    """Explicitly enroll one durable parent binding's operation stream.

    The command is intentionally complete: the attempt and parent binding are
    both revalidated against strict registry facts before FactStream enrollment.
    """

    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1

    def __post_init__(self) -> None:
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")


DirectedEffectStreamEnrollmentCodeV1 = (
    DirectedEffectOperationCodeV1
    | Literal[
        "parent_registry_stream_enrolled",
        "operation_stream_enrolled",
    ]
)


@dataclass(frozen=True, slots=True)
class DirectedEffectStreamEnrollmentResultV1:
    """Typed result for explicit DEO dynamic-stream maintenance enrollment."""

    ok: bool
    code: DirectedEffectStreamEnrollmentCodeV1
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1 | None = None
    receipt: Mapping[str, Any] | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        success_codes = {"parent_registry_stream_enrolled", "operation_stream_enrolled"}
        if self.ok != (self.code in success_codes):
            raise ValueError("ok must match directed effect stream enrollment result code")
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.parent_binding is not None and not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1 or None")
        if self.code == "parent_registry_stream_enrolled" and self.parent_binding is not None:
            raise ValueError("parent registry enrollment result must not carry a parent binding")
        if self.code == "operation_stream_enrolled" and self.parent_binding is None:
            raise ValueError("operation stream enrollment result requires a parent binding")
        if self.code in success_codes and self.receipt is None:
            raise ValueError("successful stream enrollment result requires an observational receipt")
        if self.receipt is not None:
            object.__setattr__(self, "receipt", _to_detached_dict(self.receipt))
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))


@dataclass(frozen=True, slots=True)
class _DirectedEffectOperationCommandBaseV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    tool_call_id: str
    effect_id: str
    expected_version: int
    expected_seq: int
    actor: str = "task_runtime"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")
        for field_name in ("tool_call_id", "effect_id", "actor"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        _directed_effect_non_negative_int("expected_version", self.expected_version)
        _directed_effect_positive_int("expected_seq", self.expected_seq)


@dataclass(frozen=True, slots=True)
class AdmitDirectedEffectOperationCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Admit the one legal ``ABSENT -> INTENT_COMMITTED`` transition."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class ClaimDirectedEffectCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Claim the one legal ``INTENT_COMMITTED -> EFFECT_STARTED`` transition."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class AbortDirectedEffectOperationCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Abort the one legal ``INTENT_COMMITTED -> ABORTED`` transition."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "reason",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class CommitDirectedEffectReceiptCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Commit one durable physical-effect receipt to an ``EFFECT_STARTED`` operation."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    receipt_ref: str = ""
    receipt_hash: str = ""
    receipt_binding_hash: str = ""
    receipt_outcome: DirectedEffectReceiptOutcomeV1 = "succeeded"

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "receipt_hash",
            "receipt_binding_hash",
        ):
            object.__setattr__(
                self, field_name, _directed_effect_inventory_digest(field_name, getattr(self, field_name))
            )
        object.__setattr__(self, "receipt_ref", _directed_effect_token("receipt_ref", self.receipt_ref))
        if self.receipt_outcome not in ("succeeded", "failed"):
            raise ValueError("receipt_outcome must be 'succeeded' or 'failed'")


@dataclass(frozen=True, slots=True)
class MarkDirectedEffectRecoveryPendingCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Move ``EFFECT_STARTED`` to finite, evidence-bound recovery."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    reason: str = ""
    recovery_evidence_ref: str = ""
    recovery_evidence_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "recovery_evidence_hash",
        ):
            object.__setattr__(
                self, field_name, _directed_effect_inventory_digest(field_name, getattr(self, field_name))
            )
        for field_name in ("reason", "recovery_evidence_ref"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class DeadLetterDirectedEffectOperationCommandV1(_DirectedEffectOperationCommandBaseV1):
    """Resolve ``RECOVERY_PENDING`` to an evidence-bound terminal dead letter."""

    intended_effect_fingerprint: str = ""
    policy_verdict_hash: str = ""
    expected_receipt_binding_hash: str = ""
    reason: str = ""
    resolution_evidence_ref: str = ""
    resolution_evidence_hash: str = ""

    def __post_init__(self) -> None:
        _DirectedEffectOperationCommandBaseV1.__post_init__(self)
        for field_name in (
            "intended_effect_fingerprint",
            "policy_verdict_hash",
            "expected_receipt_binding_hash",
            "resolution_evidence_hash",
        ):
            object.__setattr__(
                self, field_name, _directed_effect_inventory_digest(field_name, getattr(self, field_name))
            )
        for field_name in ("reason", "resolution_evidence_ref"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class GetDirectedEffectOperationQueryV1:
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1
    tool_call_id: str
    effect_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")
        for field_name in ("tool_call_id", "effect_id"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class GetDirectedEffectParentReadinessQueryV1:
    """Read one parent-bound operation-stream diagnostic without authority effects."""

    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding: DirectedEffectParentBindingV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1")


@dataclass(frozen=True, slots=True)
class DirectedEffectParentReadinessStateCountV1:
    """One immutable final-state count from a strict parent operation scan."""

    state: DirectedEffectOperationStateV1
    count: int

    def __post_init__(self) -> None:
        if self.state not in _DIRECTED_EFFECT_OPERATION_STATES:
            raise ValueError("state must be an existing directed effect operation state")
        _directed_effect_non_negative_int("count", self.count)


@dataclass(frozen=True, slots=True)
class DirectedEffectParentReadinessProjectionV1:
    """Immutable, non-authoritative diagnostic aggregate for one parent."""

    schema_version: str
    workspace: str
    task_id: int
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1
    parent_binding_id: str
    parent_registry_stream_token: str
    parent_registry_source_head_seq: int
    operation_stream_token: str
    operation_source_head_seq: int
    operation_count: int
    state_counts: tuple[DirectedEffectParentReadinessStateCountV1, ...]
    enforcement: Literal["not_enabled"] = "not_enabled"

    def __post_init__(self) -> None:
        if self.schema_version != DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1:
            raise ValueError("unsupported directed effect parent readiness projection schema")
        object.__setattr__(self, "workspace", _directed_effect_token("workspace", self.workspace))
        _directed_effect_positive_int("task_id", self.task_id)
        if not isinstance(self.execution_attempt, TaskRuntimeExecutionAttemptIdentityV1):
            raise TypeError("execution_attempt must be TaskRuntimeExecutionAttemptIdentityV1")
        if self.workspace != self.execution_attempt.workspace or self.task_id != self.execution_attempt.task_id:
            raise ValueError("readiness projection workspace and task must match execution_attempt")
        for field_name in (
            "parent_binding_id",
            "parent_registry_stream_token",
            "operation_stream_token",
        ):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        _directed_effect_non_negative_int("parent_registry_source_head_seq", self.parent_registry_source_head_seq)
        _directed_effect_non_negative_int("operation_source_head_seq", self.operation_source_head_seq)
        _directed_effect_non_negative_int("operation_count", self.operation_count)
        if self.enforcement != "not_enabled":
            raise ValueError("enforcement must be not_enabled")
        if not isinstance(self.state_counts, tuple):
            raise TypeError("state_counts must be a tuple")
        if any(not isinstance(item, DirectedEffectParentReadinessStateCountV1) for item in self.state_counts):
            raise TypeError("state_counts must contain DirectedEffectParentReadinessStateCountV1")
        if tuple(item.state for item in self.state_counts) != _DIRECTED_EFFECT_OPERATION_STATES:
            raise ValueError("state_counts must contain each directed effect state in canonical order")
        if sum(item.count for item in self.state_counts) != self.operation_count:
            raise ValueError("state_counts must sum to operation_count")


@dataclass(frozen=True, slots=True)
class DirectedEffectParentReadinessResultV1:
    """Typed outcome for the read-only parent operation-stream diagnostic."""

    ok: bool
    code: DirectedEffectParentReadinessCodeV1
    projection: DirectedEffectParentReadinessProjectionV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ok != (self.code == "readiness_observed"):
            raise ValueError("ok must match readiness_observed")
        if self.ok != (self.projection is not None):
            raise ValueError("successful readiness result requires a projection")
        if self.projection is not None and not isinstance(self.projection, DirectedEffectParentReadinessProjectionV1):
            raise TypeError("projection must be DirectedEffectParentReadinessProjectionV1 or None")
        evidence = _to_immutable_evidence(self.evidence)
        if self.ok:
            projection = cast(DirectedEffectParentReadinessProjectionV1, self.projection)
            expected_source_heads = {
                "parent_registry_source_head_seq": projection.parent_registry_source_head_seq,
                "operation_source_head_seq": projection.operation_source_head_seq,
            }
            if set(evidence) != _SUCCESS_READINESS_EVIDENCE_KEYS or any(
                isinstance(evidence[key], bool) or not isinstance(evidence[key], int) or evidence[key] != expected_value
                for key, expected_value in expected_source_heads.items()
            ):
                raise ValueError("successful readiness evidence must match diagnostic schema")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True, slots=True)
class DirectedEffectOperationResultV1:
    ok: bool
    code: DirectedEffectOperationCodeV1
    operation: DirectedEffectOperationIdentityV1 | None = None
    parent_binding: DirectedEffectParentBindingV1 | None = None
    parent_registry: DirectedEffectParentRegistryProjectionV1 | None = None
    state: DirectedEffectOperationStateV1 | None = None
    version: int = 0
    snapshot: DirectedEffectOperationSnapshotV1 | None = None
    idempotent: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)
    claim_grant: DirectedEffectClaimGrantV1 | None = None

    def __post_init__(self) -> None:
        success_codes = {
            "parent_admitted",
            "parent_idempotent_replay",
            "admitted",
            "effect_claimed",
            "receipt_committed",
            "recovery_pending",
            "dead_lettered",
            "closed_by_parent",
            "aborted",
            "found",
            "idempotent_replay",
        }
        if self.ok != (self.code in success_codes):
            raise ValueError("ok must match directed effect operation result code")
        if self.operation is not None and not isinstance(self.operation, DirectedEffectOperationIdentityV1):
            raise TypeError("operation must be DirectedEffectOperationIdentityV1 or None")
        if self.snapshot is not None and not isinstance(self.snapshot, DirectedEffectOperationSnapshotV1):
            raise TypeError("snapshot must be DirectedEffectOperationSnapshotV1 or None")
        if self.parent_binding is not None and not isinstance(self.parent_binding, DirectedEffectParentBindingV1):
            raise TypeError("parent_binding must be DirectedEffectParentBindingV1 or None")
        if self.parent_registry is not None and not isinstance(
            self.parent_registry, DirectedEffectParentRegistryProjectionV1
        ):
            raise TypeError("parent_registry must be DirectedEffectParentRegistryProjectionV1 or None")
        if self.claim_grant is not None and type(self.claim_grant) is not DirectedEffectClaimGrantV1:
            raise TypeError("claim_grant must be exactly DirectedEffectClaimGrantV1 or None")
        if (self.code == "effect_claimed") != (self.claim_grant is not None):
            raise ValueError("effect_claimed requires exactly one claim_grant")
        parent_success = self.code in {"parent_admitted", "parent_idempotent_replay"}
        if self.ok and parent_success != (self.parent_binding is not None and self.operation is None):
            raise ValueError("parent admission success requires exactly one parent binding")
        if self.ok and not parent_success and (self.operation is None or self.state is None or self.version < 1):
            raise ValueError("successful directed effect operation result requires aggregate state")
        if self.idempotent != (self.code in {"idempotent_replay", "parent_idempotent_replay"}):
            raise ValueError("idempotent must match an idempotent replay code")
        if self.claim_grant is not None:
            if self.code != "effect_claimed":
                raise ValueError("only effect_claimed may carry a claim_grant")
            if self.operation != self.claim_grant.operation:
                raise ValueError("operation must match claim_grant operation")
            if self.state != "EFFECT_STARTED":
                raise ValueError("claim_grant requires EFFECT_STARTED state")
            if self.version != self.claim_grant.operation_version:
                raise ValueError("version must match claim_grant operation_version")
            if self.parent_binding is not None and self.parent_binding != self.claim_grant.parent_binding:
                raise ValueError("parent_binding must match claim_grant parent_binding")
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))


@dataclass(frozen=True, slots=True)
class ReconcileAmbiguousDirectedEffectsCommandV1:
    """Request one bounded Factory-startup recovery sweep without effect replay.

    TaskRuntime mints and persists the actual maintenance authority under its
    workspace recovery lock.  Callers deliberately cannot supply an owner PID,
    epoch, lease id, or token.
    """

    workspace: str
    reason: str
    factory_run_id: str = ""
    actor: str = "factory.settlement.startup"
    authority_kind: Literal["factory_settlement_startup"] = "factory_settlement_startup"
    max_sessions: int = 256
    max_operations: int = 4096
    deadline_seconds: float = 30.0
    lock_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        workspace = str(Path(self.workspace).expanduser().resolve())
        if self.workspace != workspace:
            raise ValueError("workspace must be canonical")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "reason", _directed_effect_token("reason", self.reason))
        object.__setattr__(self, "factory_run_id", str(self.factory_run_id or "").strip())
        object.__setattr__(self, "actor", _directed_effect_token("actor", self.actor))
        if self.actor != "factory.settlement.startup":
            raise ValueError("actor must be factory.settlement.startup")
        if self.authority_kind != "factory_settlement_startup":
            raise ValueError("authority_kind must be factory_settlement_startup")
        _directed_effect_positive_int("max_sessions", self.max_sessions)
        _directed_effect_positive_int("max_operations", self.max_operations)
        for field_name in ("deadline_seconds", "lock_timeout_seconds"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be a finite positive number")
            normalized = float(value)
            if not math.isfinite(normalized) or normalized <= 0:
                raise ValueError(f"{field_name} must be a finite positive number")
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class DirectedEffectRecoverySweepItemV1:
    """One TaskRuntime recovery/dead-letter fact exposed to read-only sinks."""

    factory_run_id: str
    session_id: str
    task_id: int
    operation_id: str
    code: Literal["recovery_pending", "dead_lettered"]
    state: Literal["RECOVERY_PENDING", "DEAD_LETTER"]
    version: int
    event_id: str
    evidence_ref: str
    evidence_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "factory_run_id", str(self.factory_run_id or "").strip())
        for field_name in ("session_id", "operation_id", "event_id", "evidence_ref"):
            object.__setattr__(self, field_name, _directed_effect_token(field_name, getattr(self, field_name)))
        _directed_effect_positive_int("task_id", self.task_id)
        _directed_effect_positive_int("version", self.version)
        expected_code = {"RECOVERY_PENDING": "recovery_pending", "DEAD_LETTER": "dead_lettered"}.get(self.state)
        if self.code != expected_code:
            raise ValueError("recovery sweep state and code must agree")
        object.__setattr__(
            self,
            "evidence_hash",
            _directed_effect_inventory_digest("evidence_hash", self.evidence_hash),
        )

    def to_record(self) -> dict[str, object]:
        evidence_prefix = "recovery" if self.state == "RECOVERY_PENDING" else "resolution"
        return {
            "schema_version": "roles.adapters.directed_effect_recovery_fact.v1",
            "authoritative": True,
            "durable": True,
            "factory_run_id": self.factory_run_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "operation_id": self.operation_id,
            "code": self.code,
            "state": self.state,
            "version": self.version,
            "event_id": self.event_id,
            f"{evidence_prefix}_evidence_ref": self.evidence_ref,
            f"{evidence_prefix}_evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class DirectedEffectRecoverySweepResultV1:
    """Bounded startup/stale-owner recovery report."""

    ok: bool
    code: Literal["reconciled", "partial_failure"]
    workspace: str
    scanned_session_count: int
    items: tuple[DirectedEffectRecoverySweepItemV1, ...] = ()
    failures: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.ok != (self.code == "reconciled"):
            raise ValueError("ok must match recovery sweep code")
        workspace = str(Path(self.workspace).expanduser().resolve())
        if self.workspace != workspace:
            raise ValueError("workspace must be canonical")
        _directed_effect_non_negative_int("scanned_session_count", self.scanned_session_count)
        if not isinstance(self.items, tuple) or any(
            type(item) is not DirectedEffectRecoverySweepItemV1 for item in self.items
        ):
            raise TypeError("items must contain exact DirectedEffectRecoverySweepItemV1 values")
        if not isinstance(self.failures, tuple) or any(not isinstance(item, Mapping) for item in self.failures):
            raise TypeError("failures must be a tuple of mappings")
        object.__setattr__(self, "failures", tuple(_to_immutable_evidence(item) for item in self.failures))


@dataclass(frozen=True, slots=True)
class DirectedEffectParentRegistryResultV1:
    ok: bool
    code: DirectedEffectOperationCodeV1
    registry: DirectedEffectParentRegistryProjectionV1 | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ok != (self.code == "parent_registry_found"):
            raise ValueError("ok must match parent registry result code")
        if self.ok != (self.registry is not None):
            raise ValueError("successful parent registry result requires a projection")
        if self.registry is not None and not isinstance(self.registry, DirectedEffectParentRegistryProjectionV1):
            raise TypeError("registry must be DirectedEffectParentRegistryProjectionV1 or None")
        object.__setattr__(self, "evidence", _to_detached_dict(self.evidence))


__all__ = [
    "DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1",
    "DIRECTED_EFFECT_INVENTORY_INTENT_SCHEMA_V1",
    "DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1",
    "DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V1",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V2",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V3",
    "DIRECTED_EFFECT_OPERATION_SCHEMA_V4",
    "DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_CORRELATION_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_IDENTITY_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1",
    "DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2",
    "DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3",
    "OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1",
    "TASK_RUNTIME_EXECUTION_ATTEMPT_IDENTITY_SCHEMA_V1",
    "TASK_RUNTIME_EXECUTION_FACT_SCHEMA_V1",
    "TASK_RUNTIME_EXECUTION_SOURCE_V1",
    "TASK_RUNTIME_EXECUTION_STREAM_V1",
    "AbortDirectedEffectOperationCommandV1",
    "AdmitDirectedEffectOperationCommandV1",
    "AdmitDirectedEffectParentBatchCommandV1",
    "AdmitDirectedEffectParentCommandV1",
    "BindRuntimeTaskToFactoryRunCommandV1",
    "ClaimDirectedEffectCommandV1",
    "CommitDirectedEffectReceiptCommandV1",
    "CreateRuntimeTaskCommandV1",
    "DeadLetterDirectedEffectOperationCommandV1",
    "DirectedEffectAuthorityFailureCodeV1",
    "DirectedEffectClaimGrantV1",
    "DirectedEffectInventoryCodeV1",
    "DirectedEffectInventoryContingencyKindV1",
    "DirectedEffectInventoryEffectTypeV1",
    "DirectedEffectInventoryExecutionModeV1",
    "DirectedEffectInventoryIntentV1",
    "DirectedEffectInventoryMemberV1",
    "DirectedEffectInventoryProjectionV1",
    "DirectedEffectInventoryResultV1",
    "DirectedEffectOperationCodeV1",
    "DirectedEffectOperationIdentityV1",
    "DirectedEffectOperationResultV1",
    "DirectedEffectOperationSnapshotV1",
    "DirectedEffectOperationStateV1",
    "DirectedEffectParentBindingV1",
    "DirectedEffectParentReadinessCodeV1",
    "DirectedEffectParentReadinessProjectionV1",
    "DirectedEffectParentReadinessResultV1",
    "DirectedEffectParentReadinessStateCountV1",
    "DirectedEffectParentRegistryIdentityV1",
    "DirectedEffectParentRegistryProjectionV1",
    "DirectedEffectParentRegistryResultV1",
    "DirectedEffectReceiptOutcomeV1",
    "DirectedEffectStreamEnrollmentCodeV1",
    "DirectedEffectStreamEnrollmentResultV1",
    "EnrollDirectedEffectOperationStreamCommandV1",
    "EnrollDirectedEffectParentRegistryStreamCommandV1",
    "ExpiredFactoryRunSessionFenceCodeV1",
    "ExpiredFactoryRunSessionFenceResultV1",
    "FenceExpiredFactoryRunSessionsCommandV1",
    "FinalizeDirectedEffectInventoryAdmissionCommandV1",
    "GetDirectedEffectInventoryQueryV1",
    "GetDirectedEffectOperationQueryV1",
    "GetDirectedEffectParentReadinessQueryV1",
    "GetDirectedEffectParentRegistryQueryV1",
    "GetRuntimeTaskQueryV1",
    "HeartbeatTaskRuntimeExecutionAttemptCommandV1",
    "ListRuntimeTasksQueryV1",
    "MarkDirectedEffectRecoveryPendingCommandV1",
    "ObservableTaskRowsProjectionV1",
    "OpenTaskRuntimeExecutionAttemptAuthorityCommandV1",
    "OwnerReworkExecutionAuthorizationV1",
    "OwnerReworkExecutionPreparationCodeV1",
    "OwnerReworkExecutionPreparationResultV1",
    "ParentCorrelationV1",
    "PrepareOwnerReworkExecutionCommandV1",
    "ReopenRuntimeTaskCommandV1",
    "RuntimeTaskFactoryRunBindingCodeV1",
    "RuntimeTaskFactoryRunBindingResultV1",
    "RuntimeTaskLifecycleEventV1",
    "RuntimeTaskResultV1",
    "RuntimeTaskRuntimeError",
    "SealDirectedEffectInventoryCommandV1",
    "SettleTaskRuntimeExecutionAttemptCommandV1",
    "TaskRuntimeExecutionAttemptAuthorityHeartbeatCodeV1",
    "TaskRuntimeExecutionAttemptAuthorityHeartbeatVerdictV1",
    "TaskRuntimeExecutionAttemptAuthorityOpenCodeV1",
    "TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1",
    "TaskRuntimeExecutionAttemptAuthoritySettlementCodeV1",
    "TaskRuntimeExecutionAttemptAuthoritySettlementVerdictV1",
    "TaskRuntimeExecutionAttemptAuthoritySnapshotCodeV1",
    "TaskRuntimeExecutionAttemptAuthoritySnapshotV1",
    "TaskRuntimeExecutionAttemptHeartbeatCodeV1",
    "TaskRuntimeExecutionAttemptHeartbeatVerdictV1",
    "TaskRuntimeExecutionAttemptIdentityV1",
    "TaskRuntimeExecutionAttemptSettlementCodeV1",
    "TaskRuntimeExecutionAttemptSettlementOutcomeV1",
    "TaskRuntimeExecutionAttemptSettlementVerdictV1",
    "TaskRuntimeExecutionAttemptValidationCodeV1",
    "TaskRuntimeExecutionAttemptValidationVerdictV1",
    "TaskRuntimeExecutionFactV1",
    "UpdateRuntimeTaskCommandV1",
    "ValidateTaskRuntimeExecutionAttemptQueryV1",
]
