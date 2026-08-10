"""Runtime-task lifecycle contracts for task_runtime.

Create/Update/List/Get runtime task commands, FactoryRun binding, Reopen,
OwnerRework preparation, execution facts, lifecycle events, and results.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from polaris.cells.runtime.task_runtime.public.contracts._helpers import (
    _factory_run_id_from_task_row,
    _require_non_empty,
    _to_dict_copy,
    _workflow_run_id_from_task_row,
)

OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1 = "task-runtime.owner-rework-execution-authorization/1"
SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1 = "task-runtime.same-task-local-rework-authorization/1"
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


@dataclass(frozen=True)
class PrepareSameTaskLocalReworkCommandV1:
    """Apply one workflow-owned completion action to the exact Director task."""

    schema_version: str
    workspace: str
    factory_run_id: str
    external_task_id: str
    completion_contract_hash: str
    action_id: str
    diagnostic_id: str
    obligation_id: str
    action_kind: str
    owner_snapshot_hash: str
    owner_bundle_hash: str
    dispatch_claim: Mapping[str, Any]
    diagnostic: Mapping[str, Any]
    max_rework_attempts: int = 3

    def __post_init__(self) -> None:
        schema_version = _require_non_empty("schema_version", self.schema_version)
        if schema_version != SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1:
            raise ValueError(f"schema_version must be {SAME_TASK_LOCAL_REWORK_AUTHORIZATION_SCHEMA_V1!r}")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "factory_run_id", _require_non_empty("factory_run_id", self.factory_run_id))
        object.__setattr__(self, "external_task_id", _require_non_empty("external_task_id", self.external_task_id))
        object.__setattr__(
            self,
            "completion_contract_hash",
            _require_non_empty("completion_contract_hash", self.completion_contract_hash).lower(),
        )
        for name in (
            "completion_contract_hash",
            "action_id",
            "owner_snapshot_hash",
            "owner_bundle_hash",
        ):
            value = _require_non_empty(name, getattr(self, name)).lower()
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError(f"{name} must be a sha256 hex digest")
            object.__setattr__(self, name, value)
        for name in ("diagnostic_id", "obligation_id", "action_kind"):
            object.__setattr__(self, name, _require_non_empty(name, getattr(self, name)))
        claim = _to_dict_copy(self.dispatch_claim)
        diagnostic = _to_dict_copy(self.diagnostic)
        if not claim:
            raise ValueError("dispatch_claim must not be empty")
        if not diagnostic:
            raise ValueError("diagnostic must not be empty")
        object.__setattr__(self, "dispatch_claim", claim)
        object.__setattr__(self, "diagnostic", diagnostic)
        max_attempts = int(self.max_rework_attempts)
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_rework_attempts must be between 1 and 5")
        object.__setattr__(self, "max_rework_attempts", max_attempts)


SameTaskLocalReworkPreparationCodeV1 = Literal[
    "same_task_local_rework_authorization_malformed",
    "same_task_local_rework_receipt_mismatch",
    "same_task_local_rework_task_not_found",
    "same_task_local_rework_task_identity_conflict",
    "same_task_local_rework_factory_run_mismatch",
    "same_task_local_rework_active_lease_conflict",
    "same_task_local_rework_budget_exhausted",
    "same_task_local_rework_already_prepared",
    "same_task_local_rework_prepared",
]


@dataclass(frozen=True)
class SameTaskLocalReworkPreparationResultV1:
    ok: bool
    code: SameTaskLocalReworkPreparationCodeV1
    reason: str
    external_task_id: str
    runtime_task_id: str = ""
    reopened: bool = False
    idempotent: bool = False
    rework_attempt: int = 0
    task_row: Mapping[str, Any] = field(default_factory=dict)
    execution_event: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))
        object.__setattr__(self, "external_task_id", _require_non_empty("external_task_id", self.external_task_id))
        object.__setattr__(self, "runtime_task_id", str(self.runtime_task_id or "").strip())
        object.__setattr__(self, "task_row", _to_dict_copy(self.task_row))
        object.__setattr__(self, "execution_event", _to_dict_copy(self.execution_event))


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
