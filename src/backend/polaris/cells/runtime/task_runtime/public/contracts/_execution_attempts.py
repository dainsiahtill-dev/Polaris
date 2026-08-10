"""TaskRuntime execution-attempt contracts and authority verdicts.

Identity, settle/heartbeat/validate/open commands, and Authority* verdicts
for one TaskRuntime execution attempt. Also hosts RuntimeTaskRuntimeError.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from polaris.cells.runtime.task_runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_detached_dict,
    _to_dict_copy,
)

if TYPE_CHECKING:
    from ..service import TaskRuntimeExecutionAttemptAuthorityV1

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
            from ..service import TaskRuntimeExecutionAttemptAuthorityV1

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
