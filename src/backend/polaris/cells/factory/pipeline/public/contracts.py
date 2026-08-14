"""Public contracts for `factory.pipeline`."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _require_exact_str(name: str, value: object) -> str:
    """Accept only exact ``str`` values (reject Path/bytes/bool/int coercion)."""
    if type(value) is not str:
        raise ValueError(f"{name} must be an exact string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _normalize_order_preserving_str_tuple(
    values: object,
    field_name: str,
) -> tuple[str, ...]:
    """Normalize an exact tuple while rejecting ambiguous owner facts."""
    if type(values) is not tuple:
        raise ValueError(f"{field_name} must be an exact tuple of strings")
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        if type(item) is not str:
            raise ValueError(f"{field_name} items must be exact strings")
        token = item.strip()
        if not token:
            raise ValueError(f"{field_name} items must be non-empty strings")
        if token in seen:
            raise ValueError(f"{field_name} must not contain duplicate items")
        seen.add(token)
        out.append(token)
    return tuple(out)


FACTORY_CHAIN_PROJECTION_SOURCE = "factory.pipeline"
FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION = "factory.chain-projection.v1"


def _factory_chain_missing_stages(
    configured_stages: Sequence[str],
    completed_stages: Sequence[str],
) -> tuple[str, ...]:
    completed_set = set(completed_stages)
    return tuple(stage for stage in configured_stages if stage not in completed_set)


def compute_factory_chain_completed(
    *,
    available: bool,
    status: str,
    configured_stages: Sequence[str],
    completed_stages: Sequence[str],
    failed_stages: Sequence[str],
    event_refs: Sequence[str],
    completion_event_ref: str | None,
) -> bool:
    """Return True only when every owner completion invariant holds."""
    if not available:
        return False
    if not configured_stages:
        return False
    if status != "completed":
        return False
    if failed_stages:
        return False
    completed_set = set(completed_stages)
    if any(stage not in completed_set for stage in configured_stages):
        return False
    return bool(completion_event_ref and completion_event_ref in event_refs)


def compute_factory_chain_projection_hash(
    *,
    workspace: str,
    run_id: str,
    available: bool,
    status: str,
    configured_stages: Sequence[str],
    completed_stages: Sequence[str],
    failed_stages: Sequence[str],
    missing_stages: Sequence[str],
    chain_completed: bool,
    event_count: int,
    event_refs: Sequence[str],
    completion_event_ref: str | None,
    source: str = FACTORY_CHAIN_PROJECTION_SOURCE,
    schema_version: str = FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION,
) -> str:
    """Bind normalized owner facts into a deterministic SHA-256 projection hash."""
    payload = {
        "available": bool(available),
        "chain_completed": bool(chain_completed),
        "completed_stages": list(completed_stages),
        "configured_stages": list(configured_stages),
        "event_count": int(event_count),
        "event_refs": list(event_refs),
        "completion_event_ref": completion_event_ref,
        "failed_stages": list(failed_stages),
        "missing_stages": list(missing_stages),
        "run_id": run_id,
        "schema_version": schema_version,
        "source": source,
        "status": status,
        "workspace": workspace,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_factory_event_ref(event: Mapping[str, Any]) -> str:
    """Derive one stable event ref from owner event identity fields.

    Prefer the strict chain event hash, then ``event_id``, ``content_id``, and
    ``append_id``. When none exist, hash canonical UTF-8 JSON of the event.
    """
    if not isinstance(event, Mapping):
        raise TypeError("event must be a mapping")
    for key in ("chain_event_hash", "event_id", "content_id", "append_id"):
        if key not in event:
            continue
        value = event.get(key)
        if type(value) is not str or not value.strip():
            raise ValueError(f"factory event {key} must be a non-empty exact string")
        token = value.strip()
        if key == "chain_event_hash" and (len(token) != 64 or any(char not in "0123456789abcdef" for char in token)):
            raise ValueError("factory event chain_event_hash must be lowercase SHA-256")
        return token
    try:
        canonical = json.dumps(
            dict(event),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("factory event cannot be serialized for stable ref hashing") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY = "factory_terminal_task_runtime_projection"


class FactoryWorkspaceRunLeaseStateV1(str, Enum):
    """Durable workspace admission lifecycle owned by ``factory.pipeline``."""

    ACTIVE = "active"
    DRAINING = "draining"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class FactoryStageExecutionClaimV1:
    """Durable CAS claim for one in-flight Factory stage execution."""

    run_id: str
    stage: str
    attempt: int
    nonce: str
    claimed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "stage", _require_non_empty("stage", self.stage))
        if int(self.attempt) < 1:
            raise ValueError("attempt must be >= 1")
        object.__setattr__(self, "attempt", int(self.attempt))
        object.__setattr__(self, "nonce", _require_non_empty("nonce", self.nonce))
        object.__setattr__(self, "claimed_at", _require_non_empty("claimed_at", self.claimed_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "attempt": self.attempt,
            "nonce": self.nonce,
            "claimed_at": self.claimed_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FactoryStageExecutionClaimV1:
        return cls(
            run_id=str(payload.get("run_id") or ""),
            stage=str(payload.get("stage") or ""),
            attempt=int(payload.get("attempt") or 0),
            nonce=str(payload.get("nonce") or ""),
            claimed_at=str(payload.get("claimed_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class FactoryLifecycleOperationClaimV1:
    """Durable CAS claim for one Factory run lifecycle mutation."""

    run_id: str
    operation: str
    sequence: int
    nonce: str
    claimed_at: str
    acquired_workspace: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "operation", _require_non_empty("operation", self.operation))
        if int(self.sequence) < 1:
            raise ValueError("sequence must be >= 1")
        object.__setattr__(self, "sequence", int(self.sequence))
        object.__setattr__(self, "nonce", _require_non_empty("nonce", self.nonce))
        object.__setattr__(self, "claimed_at", _require_non_empty("claimed_at", self.claimed_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "operation": self.operation,
            "sequence": self.sequence,
            "nonce": self.nonce,
            "claimed_at": self.claimed_at,
            "acquired_workspace": self.acquired_workspace,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FactoryLifecycleOperationClaimV1:
        return cls(
            run_id=str(payload.get("run_id") or ""),
            operation=str(payload.get("operation") or ""),
            sequence=int(payload.get("sequence") or 0),
            nonce=str(payload.get("nonce") or ""),
            claimed_at=str(payload.get("claimed_at") or ""),
            acquired_workspace=bool(payload.get("acquired_workspace")),
        )


@dataclass(frozen=True, slots=True)
class FactoryWorkspaceReleaseEvidenceV1:
    """Structured proof required before workspace authority is released."""

    factory_run_id: str
    source: str
    observed_at: str
    active_session_count: int = 0
    conflict_count: int = 0
    fenced_session_ids: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "factory.workspace-release-evidence.v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "factory_run_id",
            _require_non_empty("factory_run_id", self.factory_run_id),
        )
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "observed_at", _require_non_empty("observed_at", self.observed_at))
        if int(self.active_session_count) < 0 or int(self.conflict_count) < 0:
            raise ValueError("release evidence counts must be >= 0")
        object.__setattr__(self, "active_session_count", int(self.active_session_count))
        object.__setattr__(self, "conflict_count", int(self.conflict_count))
        object.__setattr__(
            self,
            "fenced_session_ids",
            tuple(_require_non_empty("session_id", value) for value in self.fenced_session_ids),
        )
        object.__setattr__(self, "details", _to_dict_copy(self.details))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        if self.active_session_count or self.conflict_count:
            raise ValueError("workspace release evidence must prove zero active sessions and conflicts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "factory_run_id": self.factory_run_id,
            "source": self.source,
            "observed_at": self.observed_at,
            "active_session_count": self.active_session_count,
            "conflict_count": self.conflict_count,
            "fenced_session_ids": list(self.fenced_session_ids),
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FactoryWorkspaceReleaseEvidenceV1:
        fenced = payload.get("fenced_session_ids")
        details_payload = payload.get("details")
        details = details_payload if isinstance(details_payload, Mapping) else {}
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            factory_run_id=str(payload.get("factory_run_id") or ""),
            source=str(payload.get("source") or ""),
            observed_at=str(payload.get("observed_at") or ""),
            active_session_count=int(payload.get("active_session_count") or 0),
            conflict_count=int(payload.get("conflict_count") or 0),
            fenced_session_ids=(tuple(str(value) for value in fenced) if isinstance(fenced, (list, tuple)) else ()),
            details=details,
        )


@dataclass(frozen=True, slots=True)
class FactoryTerminalTaskRuntimeProjectionV1:
    """Run-bound authority snapshot captured before destructive TaskRuntime reset.

    The snapshot is a frozen read model, not a second execution fact source.
    It preserves TaskRuntime's own compact authority projection so terminal
    audit consumers can still prove the run after live task/session files are
    deliberately drained.
    """

    workspace: str
    factory_run_id: str
    captured_at: str
    projection: Mapping[str, Any]
    schema_version: str = "factory.terminal-task-runtime-projection.v1"

    def __post_init__(self) -> None:
        workspace = _require_non_empty("workspace", self.workspace)
        factory_run_id = _require_non_empty("factory_run_id", self.factory_run_id)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "factory_run_id", factory_run_id)
        object.__setattr__(self, "captured_at", _require_non_empty("captured_at", self.captured_at))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        if self.schema_version != "factory.terminal-task-runtime-projection.v1":
            raise ValueError("unsupported terminal TaskRuntime projection schema_version")

        projection = copy.deepcopy(dict(self.projection))
        if projection.get("schema_version") != "task_runtime.observable_task_rows_authority.v1":
            raise ValueError("terminal projection must use TaskRuntime authority schema")
        if str(projection.get("source") or "").strip() != "task_runtime.execution_fact":
            raise ValueError("terminal projection source must be task_runtime.execution_fact")
        if projection.get("authoritative") is not True or projection.get("degraded") is not False:
            raise ValueError("terminal projection must be authoritative and non-degraded")
        if str(projection.get("requested_factory_run_id") or "").strip() != factory_run_id:
            raise ValueError("terminal projection requested_factory_run_id must match factory_run_id")

        projection_workspace = _require_non_empty("projection.workspace", str(projection.get("workspace") or ""))
        if Path(projection_workspace).expanduser().resolve() != Path(workspace).expanduser().resolve():
            raise ValueError("terminal projection workspace must match snapshot workspace")

        rows_payload = projection.get("rows")
        if not isinstance(rows_payload, list):
            raise ValueError("terminal projection rows must be a list")

        def _projection_count(field_name: str) -> int:
            value = projection.get(field_name)
            if isinstance(value, bool):
                raise ValueError("terminal projection row counts must be integers")
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
            raise ValueError("terminal projection row counts must be integers")

        row_count = _projection_count("row_count")
        total_row_count = _projection_count("total_row_count")
        if row_count != len(rows_payload) or total_row_count < row_count:
            raise ValueError("terminal projection row counts are inconsistent")
        for row in rows_payload:
            if not isinstance(row, Mapping):
                raise ValueError("terminal projection rows must be mappings")
            if str(row.get("factory_run_id") or "").strip() != factory_run_id:
                raise ValueError("terminal projection row factory_run_id must match snapshot")
            if not str(row.get("task_id") or "").strip():
                raise ValueError("terminal projection row task_id must be non-empty")
            if str(row.get("source") or "").strip() != "task_runtime.execution_fact":
                raise ValueError("terminal projection row source must be task_runtime.execution_fact")
            if str(row.get("status_source") or "").strip() != "task_runtime.execution_fact":
                raise ValueError("terminal projection row status_source must be task_runtime.execution_fact")
            fact_event_seq = row.get("fact_event_seq")
            if not isinstance(fact_event_seq, int) or isinstance(fact_event_seq, bool) or fact_event_seq < 1:
                raise ValueError("terminal projection row fact_event_seq must be a positive integer")
        if not isinstance(projection.get("readiness"), Mapping):
            raise ValueError("terminal projection readiness must be a mapping")
        object.__setattr__(self, "projection", projection)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "factory_run_id": self.factory_run_id,
            "captured_at": self.captured_at,
            "projection": copy.deepcopy(dict(self.projection)),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FactoryTerminalTaskRuntimeProjectionV1:
        projection_payload = payload.get("projection")
        projection = projection_payload if isinstance(projection_payload, Mapping) else {}
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            workspace=str(payload.get("workspace") or ""),
            factory_run_id=str(payload.get("factory_run_id") or ""),
            captured_at=str(payload.get("captured_at") or ""),
            projection=projection,
        )


@dataclass(frozen=True, slots=True)
class FactoryWorkspaceRunLeaseV1:
    """One fenced Factory run authority for a workspace.

    ``version`` is the durable record revision. ``fencing_token`` changes only
    when ownership is newly acquired, so stale owners cannot renew, release, or
    continue stage execution after a takeover.
    """

    workspace: str
    run_id: str
    state: FactoryWorkspaceRunLeaseStateV1
    version: int
    fencing_token: int
    acquired_at: str
    updated_at: str
    expires_at: str
    released_at: str | None = None
    drain_reason: str = ""
    stage_claim_sequence: int = 0
    stage_execution_claim: FactoryStageExecutionClaimV1 | None = None
    lifecycle_claim_sequence: int = 0
    lifecycle_operation_claim: FactoryLifecycleOperationClaimV1 | None = None
    release_evidence: FactoryWorkspaceReleaseEvidenceV1 | None = None
    schema_version: str = "factory.workspace-run-lease.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "state", FactoryWorkspaceRunLeaseStateV1(self.state))
        if int(self.version) < 1:
            raise ValueError("version must be >= 1")
        if int(self.fencing_token) < 1:
            raise ValueError("fencing_token must be >= 1")
        object.__setattr__(self, "version", int(self.version))
        object.__setattr__(self, "fencing_token", int(self.fencing_token))
        object.__setattr__(self, "acquired_at", _require_non_empty("acquired_at", self.acquired_at))
        object.__setattr__(self, "updated_at", _require_non_empty("updated_at", self.updated_at))
        object.__setattr__(self, "expires_at", _require_non_empty("expires_at", self.expires_at))
        object.__setattr__(self, "released_at", str(self.released_at or "").strip() or None)
        object.__setattr__(self, "drain_reason", str(self.drain_reason or "").strip())
        if int(self.stage_claim_sequence) < 0:
            raise ValueError("stage_claim_sequence must be >= 0")
        object.__setattr__(self, "stage_claim_sequence", int(self.stage_claim_sequence))
        claim = self.stage_execution_claim
        if claim is not None and not isinstance(claim, FactoryStageExecutionClaimV1):
            raise TypeError("stage_execution_claim must be FactoryStageExecutionClaimV1 or None")
        if claim is not None and claim.run_id != self.run_id:
            raise ValueError("stage execution claim run_id must match lease run_id")
        if int(self.lifecycle_claim_sequence) < 0:
            raise ValueError("lifecycle_claim_sequence must be >= 0")
        object.__setattr__(self, "lifecycle_claim_sequence", int(self.lifecycle_claim_sequence))
        operation_claim = self.lifecycle_operation_claim
        if operation_claim is not None and not isinstance(operation_claim, FactoryLifecycleOperationClaimV1):
            raise TypeError("lifecycle_operation_claim must be FactoryLifecycleOperationClaimV1 or None")
        if operation_claim is not None and operation_claim.run_id != self.run_id:
            raise ValueError("lifecycle operation claim run_id must match lease run_id")
        release_evidence = self.release_evidence
        if release_evidence is not None and not isinstance(release_evidence, FactoryWorkspaceReleaseEvidenceV1):
            raise TypeError("release_evidence must be FactoryWorkspaceReleaseEvidenceV1 or None")
        if release_evidence is not None and release_evidence.factory_run_id != self.run_id:
            raise ValueError("release evidence factory_run_id must match lease run_id")
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical UTF-8 JSON-compatible lease record."""

        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "run_id": self.run_id,
            "state": self.state.value,
            "version": self.version,
            "fencing_token": self.fencing_token,
            "acquired_at": self.acquired_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "released_at": self.released_at,
            "drain_reason": self.drain_reason,
            "stage_claim_sequence": self.stage_claim_sequence,
            "stage_execution_claim": (
                self.stage_execution_claim.to_dict() if self.stage_execution_claim is not None else None
            ),
            "lifecycle_claim_sequence": self.lifecycle_claim_sequence,
            "lifecycle_operation_claim": (
                self.lifecycle_operation_claim.to_dict() if self.lifecycle_operation_claim is not None else None
            ),
            "release_evidence": self.release_evidence.to_dict() if self.release_evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FactoryWorkspaceRunLeaseV1:
        """Validate and restore one durable lease record."""

        claim_payload = payload.get("stage_execution_claim")
        claim = FactoryStageExecutionClaimV1.from_dict(claim_payload) if isinstance(claim_payload, Mapping) else None
        lifecycle_claim_payload = payload.get("lifecycle_operation_claim")
        lifecycle_claim = (
            FactoryLifecycleOperationClaimV1.from_dict(lifecycle_claim_payload)
            if isinstance(lifecycle_claim_payload, Mapping)
            else None
        )
        release_evidence_payload = payload.get("release_evidence")
        release_evidence = (
            FactoryWorkspaceReleaseEvidenceV1.from_dict(release_evidence_payload)
            if isinstance(release_evidence_payload, Mapping)
            else None
        )
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            workspace=str(payload.get("workspace") or ""),
            run_id=str(payload.get("run_id") or ""),
            state=FactoryWorkspaceRunLeaseStateV1(str(payload.get("state") or "")),
            version=int(payload.get("version") or 0),
            fencing_token=int(payload.get("fencing_token") or 0),
            acquired_at=str(payload.get("acquired_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            expires_at=str(payload.get("expires_at") or ""),
            released_at=str(payload.get("released_at") or "").strip() or None,
            drain_reason=str(payload.get("drain_reason") or ""),
            stage_claim_sequence=int(payload.get("stage_claim_sequence") or 0),
            stage_execution_claim=claim,
            lifecycle_claim_sequence=int(payload.get("lifecycle_claim_sequence") or 0),
            lifecycle_operation_claim=lifecycle_claim,
            release_evidence=release_evidence,
        )


@dataclass(frozen=True, slots=True)
class RecoverStaleFactoryWorkspaceOwnerCommandV1:
    """Explicit authority proof for releasing one stale Factory owner."""

    workspace: str
    run_id: str
    expected_fencing_token: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        if isinstance(self.expected_fencing_token, bool):
            raise TypeError("expected_fencing_token must be an integer")
        try:
            fencing_token = int(self.expected_fencing_token)
        except (TypeError, ValueError) as exc:
            raise TypeError("expected_fencing_token must be an integer") from exc
        if fencing_token < 1:
            raise ValueError("expected_fencing_token must be >= 1")
        object.__setattr__(self, "expected_fencing_token", fencing_token)
        reason = _require_non_empty("reason", self.reason)
        if len(reason) > 512:
            raise ValueError("reason must be at most 512 characters")
        object.__setattr__(self, "reason", reason)


@dataclass(frozen=True, slots=True)
class RecoverStaleFactoryWorkspaceOwnerResultV1:
    """Released lease returned after stale-owner recovery succeeds."""

    workspace: str
    run_id: str
    expected_fencing_token: int
    reason: str
    lease: FactoryWorkspaceRunLeaseV1
    schema_version: str = "factory.stale-workspace-owner-recovery-result.v1"

    def __post_init__(self) -> None:
        command = RecoverStaleFactoryWorkspaceOwnerCommandV1(
            workspace=self.workspace,
            run_id=self.run_id,
            expected_fencing_token=self.expected_fencing_token,
            reason=self.reason,
        )
        object.__setattr__(self, "workspace", command.workspace)
        object.__setattr__(self, "run_id", command.run_id)
        object.__setattr__(self, "expected_fencing_token", command.expected_fencing_token)
        object.__setattr__(self, "reason", command.reason)
        if not isinstance(self.lease, FactoryWorkspaceRunLeaseV1):
            raise TypeError("lease must be FactoryWorkspaceRunLeaseV1")
        if self.lease.workspace != self.workspace:
            raise ValueError("lease workspace must match result workspace")
        if self.lease.run_id != self.run_id:
            raise ValueError("lease run_id must match result run_id")
        # Restart recovery deliberately acquires one replay fence before
        # releasing the stale owner.  The command token proves which stale
        # owner may be recovered; the returned lease may therefore carry that
        # exact token (same-process recovery) or its single fenced successor
        # (restart recovery).  Any larger jump remains fail-closed.
        if self.lease.fencing_token not in {
            self.expected_fencing_token,
            self.expected_fencing_token + 1,
        }:
            raise ValueError(
                "lease fencing_token must match expected_fencing_token or its single replay-fence successor"
            )
        if self.lease.state is not FactoryWorkspaceRunLeaseStateV1.RELEASED:
            raise ValueError("stale-owner recovery result requires a released lease")
        object.__setattr__(
            self,
            "schema_version",
            _require_non_empty("schema_version", self.schema_version),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical UTF-8 JSON-compatible recovery result."""

        return {
            "schema_version": self.schema_version,
            "workspace": self.workspace,
            "run_id": self.run_id,
            "expected_fencing_token": self.expected_fencing_token,
            "reason": self.reason,
            "lease": self.lease.to_dict(),
        }


@dataclass(frozen=True)
class StartFactoryRunCommandV1:
    workspace: str
    run_name: str
    stages: tuple[str, ...]
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_name", _require_non_empty("run_name", self.run_name))
        object.__setattr__(self, "stages", tuple(str(v).strip() for v in self.stages if str(v).strip()))
        if not self.stages:
            raise ValueError("stages must not be empty")
        object.__setattr__(self, "options", _to_dict_copy(self.options))


@dataclass(frozen=True)
class CancelFactoryRunCommandV1:
    workspace: str
    run_id: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "reason", _require_non_empty("reason", self.reason))


@dataclass(frozen=True)
class GetFactoryRunStatusQueryV1:
    workspace: str
    run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))


@dataclass(frozen=True, slots=True)
class GetFactoryChainProjectionQueryV1:
    """Exact workspace/run query for the Factory chain owner projection."""

    workspace: str
    run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_str("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_exact_str("run_id", self.run_id))


@dataclass(frozen=True, slots=True)
class GetFactoryTerminalTaskRuntimeProjectionQueryV1:
    """Exact read query for the TaskRuntime authority frozen by Factory drain."""

    workspace: str
    factory_run_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_exact_str("workspace", self.workspace))
        object.__setattr__(
            self,
            "factory_run_id",
            _require_exact_str("factory_run_id", self.factory_run_id),
        )


@dataclass(frozen=True, slots=True)
class FactoryChainProjectionV1:
    """Read-only Factory-owned chain projection for one workspace run.

    ``chain_completed`` is fail-closed and self-validating: construction that
    contradicts completion evidence or the bound ``projection_hash`` is
    rejected.  This value is an *observation*, never an authorization
    capability; consumers needing Factory ownership must issue the public query
    themselves rather than accept a caller-supplied instance.
    """

    workspace: str
    run_id: str
    available: bool
    status: str
    configured_stages: tuple[str, ...]
    completed_stages: tuple[str, ...]
    failed_stages: tuple[str, ...]
    missing_stages: tuple[str, ...]
    chain_completed: bool
    event_count: int
    event_refs: tuple[str, ...]
    completion_event_ref: str | None
    projection_hash: str
    source: str = FACTORY_CHAIN_PROJECTION_SOURCE
    schema_version: str = FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        workspace = _require_exact_str("workspace", self.workspace)
        run_id = _require_exact_str("run_id", self.run_id)
        if type(self.available) is not bool:
            raise ValueError("available must be an exact bool")
        if type(self.chain_completed) is not bool:
            raise ValueError("chain_completed must be an exact bool")
        if type(self.status) is not str:
            raise ValueError("status must be an exact string")
        status = self.status.strip()
        if type(self.event_count) is not int or isinstance(self.event_count, bool):
            raise ValueError("event_count must be an exact int")
        if self.event_count < 0:
            raise ValueError("event_count must be >= 0")
        source = _require_exact_str("source", self.source)
        schema_version = _require_exact_str("schema_version", self.schema_version)
        if source != FACTORY_CHAIN_PROJECTION_SOURCE:
            raise ValueError("source must be factory.pipeline")
        if schema_version != FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION:
            raise ValueError("unsupported factory chain projection schema_version")

        configured = _normalize_order_preserving_str_tuple(
            self.configured_stages,
            "configured_stages",
        )
        completed = _normalize_order_preserving_str_tuple(
            self.completed_stages,
            "completed_stages",
        )
        failed = _normalize_order_preserving_str_tuple(
            self.failed_stages,
            "failed_stages",
        )
        missing = _normalize_order_preserving_str_tuple(
            self.missing_stages,
            "missing_stages",
        )
        event_refs = _normalize_order_preserving_str_tuple(
            self.event_refs,
            "event_refs",
        )
        completion_event_ref = self.completion_event_ref
        if completion_event_ref is not None:
            completion_event_ref = _require_exact_str("completion_event_ref", completion_event_ref)
            if completion_event_ref not in event_refs:
                raise ValueError("completion_event_ref must identify one event_ref")
            if status != "completed":
                raise ValueError("completion_event_ref requires completed Factory status")
        configured_set = set(configured)
        unknown_completed = tuple(stage for stage in completed if stage not in configured_set)
        if unknown_completed:
            raise ValueError("completed_stages must be a subset of configured_stages")
        unknown_failed = tuple(stage for stage in failed if stage not in configured_set)
        if unknown_failed:
            raise ValueError("failed_stages must be a subset of configured_stages")
        if set(completed).intersection(failed):
            raise ValueError("completed_stages and failed_stages must be disjoint")
        expected_missing = _factory_chain_missing_stages(configured, completed)
        if missing != expected_missing:
            raise ValueError(
                f"missing_stages must equal configured stages absent from completed; expected {expected_missing!r}"
            )
        if self.event_count != len(event_refs):
            raise ValueError("event_count must equal the number of stable event_refs")

        if not self.available:
            if status:
                raise ValueError("unavailable projection status must be empty")
            if configured or completed or failed or missing:
                raise ValueError("unavailable projection stages must be empty")
            if event_refs or self.event_count:
                raise ValueError("unavailable projection must not carry event refs")
            if self.chain_completed is not False:
                raise ValueError("unavailable projection cannot be chain_completed")
        else:
            if not status:
                raise ValueError("available projection status must be non-empty")

        expected_completed = compute_factory_chain_completed(
            available=self.available,
            status=status,
            configured_stages=configured,
            completed_stages=completed,
            failed_stages=failed,
            event_refs=event_refs,
            completion_event_ref=completion_event_ref,
        )
        if self.chain_completed is not expected_completed:
            raise ValueError(f"chain_completed must be {expected_completed!r} for the provided owner facts")

        projection_hash = _require_exact_str("projection_hash", self.projection_hash)
        expected_hash = compute_factory_chain_projection_hash(
            workspace=workspace,
            run_id=run_id,
            available=self.available,
            status=status,
            configured_stages=configured,
            completed_stages=completed,
            failed_stages=failed,
            missing_stages=missing,
            chain_completed=expected_completed,
            event_count=self.event_count,
            event_refs=event_refs,
            completion_event_ref=completion_event_ref,
            source=source,
            schema_version=schema_version,
        )
        if projection_hash != expected_hash:
            raise ValueError("projection_hash must bind the normalized Factory chain projection")

        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "configured_stages", configured)
        object.__setattr__(self, "completed_stages", completed)
        object.__setattr__(self, "failed_stages", failed)
        object.__setattr__(self, "missing_stages", missing)
        object.__setattr__(self, "event_refs", event_refs)
        object.__setattr__(self, "completion_event_ref", completion_event_ref)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "projection_hash", projection_hash)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical UTF-8 JSON-compatible projection payload."""
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "workspace": self.workspace,
            "run_id": self.run_id,
            "available": self.available,
            "status": self.status,
            "configured_stages": list(self.configured_stages),
            "completed_stages": list(self.completed_stages),
            "failed_stages": list(self.failed_stages),
            "missing_stages": list(self.missing_stages),
            "chain_completed": self.chain_completed,
            "event_count": self.event_count,
            "event_refs": list(self.event_refs),
            "completion_event_ref": self.completion_event_ref,
            "projection_hash": self.projection_hash,
        }


@dataclass(frozen=True)
class ListFactoryRunsQueryV1:
    workspace: str
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        if self.limit < 1:
            raise ValueError("limit must be >= 1")
        if self.offset < 0:
            raise ValueError("offset must be >= 0")


@dataclass(frozen=True)
class RunProjectionExperimentCommandV1:
    workspace: str
    scenario_id: str
    requirement: str
    project_slug: str = "projection_lab"
    use_pm_llm: bool = True
    run_verification: bool = True
    overwrite: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "requirement", _require_non_empty("requirement", self.requirement))
        object.__setattr__(self, "project_slug", _require_non_empty("project_slug", self.project_slug))
        object.__setattr__(self, "use_pm_llm", bool(self.use_pm_llm))
        object.__setattr__(self, "run_verification", bool(self.run_verification))
        object.__setattr__(self, "overwrite", bool(self.overwrite))


@dataclass(frozen=True)
class RefreshProjectionBackMappingCommandV1:
    workspace: str
    experiment_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))


@dataclass(frozen=True)
class ReprojectProjectionExperimentCommandV1:
    workspace: str
    experiment_id: str
    requirement: str
    use_pm_llm: bool = True
    run_verification: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "requirement", _require_non_empty("requirement", self.requirement))
        object.__setattr__(self, "use_pm_llm", bool(self.use_pm_llm))
        object.__setattr__(self, "run_verification", bool(self.run_verification))


@dataclass(frozen=True)
class FactoryRunStartedEventV1:
    event_id: str
    workspace: str
    run_id: str
    started_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "started_at", _require_non_empty("started_at", self.started_at))


@dataclass(frozen=True)
class FactoryRunCompletedEventV1:
    event_id: str
    workspace: str
    run_id: str
    status: str
    completed_at: str
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "completed_at", _require_non_empty("completed_at", self.completed_at))


@dataclass(frozen=True)
class FactoryRunResultV1:
    ok: bool
    workspace: str
    run_id: str
    status: str
    completed_stages: tuple[str, ...] = field(default_factory=tuple)
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "run_id", _require_non_empty("run_id", self.run_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(
            self,
            "completed_stages",
            tuple(str(v).strip() for v in self.completed_stages if str(v).strip()),
        )
        object.__setattr__(
            self,
            "artifact_paths",
            tuple(str(v).strip() for v in self.artifact_paths if str(v).strip()),
        )


@dataclass(frozen=True)
class ProjectionExperimentResultV1:
    ok: bool
    workspace: str
    experiment_id: str
    scenario_id: str
    project_root: str
    generated_files: tuple[str, ...] = field(default_factory=tuple)
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)
    cell_ids: tuple[str, ...] = field(default_factory=tuple)
    verification_ok: bool = False
    normalization_source: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "project_root", _require_non_empty("project_root", self.project_root))
        object.__setattr__(self, "generated_files", tuple(str(v) for v in self.generated_files if str(v).strip()))
        object.__setattr__(self, "artifact_paths", tuple(str(v) for v in self.artifact_paths if str(v).strip()))
        object.__setattr__(self, "cell_ids", tuple(str(v) for v in self.cell_ids if str(v).strip()))
        object.__setattr__(self, "verification_ok", bool(self.verification_ok))
        object.__setattr__(self, "normalization_source", str(self.normalization_source or "").strip())
        object.__setattr__(self, "summary", str(self.summary or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "workspace": self.workspace,
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "project_root": self.project_root,
            "generated_files": list(self.generated_files),
            "artifact_paths": list(self.artifact_paths),
            "cell_ids": list(self.cell_ids),
            "verification_ok": self.verification_ok,
            "normalization_source": self.normalization_source,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ProjectionBackMappingRefreshResultV1:
    workspace: str
    experiment_id: str
    project_root: str
    changed_files: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    added_symbols: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    removed_symbols: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    modified_symbols: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    impacted_cell_ids: tuple[str, ...] = field(default_factory=tuple)
    mapping_strategy: str = ""
    previous_mapping_strategy: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "project_root", _require_non_empty("project_root", self.project_root))
        object.__setattr__(self, "changed_files", tuple(dict(item) for item in self.changed_files))
        object.__setattr__(self, "added_symbols", tuple(dict(item) for item in self.added_symbols))
        object.__setattr__(self, "removed_symbols", tuple(dict(item) for item in self.removed_symbols))
        object.__setattr__(self, "modified_symbols", tuple(dict(item) for item in self.modified_symbols))
        object.__setattr__(self, "impacted_cell_ids", tuple(str(v) for v in self.impacted_cell_ids if str(v).strip()))
        object.__setattr__(self, "mapping_strategy", str(self.mapping_strategy or "").strip())
        object.__setattr__(self, "previous_mapping_strategy", str(self.previous_mapping_strategy or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "experiment_id": self.experiment_id,
            "project_root": self.project_root,
            "changed_files": [dict(item) for item in self.changed_files],
            "added_symbols": [dict(item) for item in self.added_symbols],
            "removed_symbols": [dict(item) for item in self.removed_symbols],
            "modified_symbols": [dict(item) for item in self.modified_symbols],
            "impacted_cell_ids": list(self.impacted_cell_ids),
            "mapping_strategy": self.mapping_strategy,
            "previous_mapping_strategy": self.previous_mapping_strategy,
        }


@dataclass(frozen=True)
class ProjectionReprojectionResultV1:
    ok: bool
    workspace: str
    experiment_id: str
    scenario_id: str
    project_root: str
    impacted_cell_ids: tuple[str, ...] = field(default_factory=tuple)
    rewritten_files: tuple[str, ...] = field(default_factory=tuple)
    artifact_paths: tuple[str, ...] = field(default_factory=tuple)
    verification_ok: bool = False
    normalization_source: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "experiment_id", _require_non_empty("experiment_id", self.experiment_id))
        object.__setattr__(self, "scenario_id", _require_non_empty("scenario_id", self.scenario_id))
        object.__setattr__(self, "project_root", _require_non_empty("project_root", self.project_root))
        object.__setattr__(self, "impacted_cell_ids", tuple(str(v) for v in self.impacted_cell_ids if str(v).strip()))
        object.__setattr__(self, "rewritten_files", tuple(str(v) for v in self.rewritten_files if str(v).strip()))
        object.__setattr__(self, "artifact_paths", tuple(str(v) for v in self.artifact_paths if str(v).strip()))
        object.__setattr__(self, "verification_ok", bool(self.verification_ok))
        object.__setattr__(self, "normalization_source", str(self.normalization_source or "").strip())
        object.__setattr__(self, "summary", str(self.summary or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "workspace": self.workspace,
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "project_root": self.project_root,
            "impacted_cell_ids": list(self.impacted_cell_ids),
            "rewritten_files": list(self.rewritten_files),
            "artifact_paths": list(self.artifact_paths),
            "verification_ok": self.verification_ok,
            "normalization_source": self.normalization_source,
            "summary": self.summary,
        }


class FactoryPipelineError(RuntimeError):
    """Raised when `factory.pipeline` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "factory_pipeline_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


class FactoryWorkspaceRunLeaseConflictError(FactoryPipelineError):
    """Raised when workspace admission or draining must fail closed."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        requested_run_id: str,
        current_lease: FactoryWorkspaceRunLeaseV1 | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        conflict_details = _to_dict_copy(details)
        conflict_details["requested_run_id"] = _require_non_empty("requested_run_id", requested_run_id)
        if current_lease is not None:
            conflict_details["current_lease"] = current_lease.to_dict()
        super().__init__(message, code=code, details=conflict_details)


class FactoryWorkspaceRunLeaseStorageError(FactoryPipelineError):
    """Raised when durable workspace lease state cannot be trusted."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message, code="factory_workspace_run_lease_storage_error", details=details)


@runtime_checkable
class IFactoryPipeline(Protocol):
    async def run_pipeline(self, project_path: str, config: Mapping[str, Any]) -> Mapping[str, Any]:
        """Compatibility API kept for existing integrations."""


@runtime_checkable
class IFactoryProjectionLab(Protocol):
    def run_projection_experiment(
        self,
        command: RunProjectionExperimentCommandV1,
    ) -> ProjectionExperimentResultV1:
        """Compile one controlled projection experiment into a workspace."""


__all__ = [
    "FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION",
    "FACTORY_CHAIN_PROJECTION_SOURCE",
    "CancelFactoryRunCommandV1",
    "FactoryChainProjectionV1",
    "FactoryLifecycleOperationClaimV1",
    "FactoryPipelineError",
    "FactoryRunCompletedEventV1",
    "FactoryRunResultV1",
    "FactoryRunStartedEventV1",
    "FactoryStageExecutionClaimV1",
    "FactoryWorkspaceReleaseEvidenceV1",
    "FactoryWorkspaceRunLeaseConflictError",
    "FactoryWorkspaceRunLeaseStateV1",
    "FactoryWorkspaceRunLeaseStorageError",
    "FactoryWorkspaceRunLeaseV1",
    "GetFactoryChainProjectionQueryV1",
    "GetFactoryRunStatusQueryV1",
    "GetFactoryTerminalTaskRuntimeProjectionQueryV1",
    "IFactoryPipeline",
    "IFactoryProjectionLab",
    "ListFactoryRunsQueryV1",
    "ProjectionBackMappingRefreshResultV1",
    "ProjectionExperimentResultV1",
    "ProjectionReprojectionResultV1",
    "RecoverStaleFactoryWorkspaceOwnerCommandV1",
    "RecoverStaleFactoryWorkspaceOwnerResultV1",
    "RefreshProjectionBackMappingCommandV1",
    "ReprojectProjectionExperimentCommandV1",
    "RunProjectionExperimentCommandV1",
    "StartFactoryRunCommandV1",
]
