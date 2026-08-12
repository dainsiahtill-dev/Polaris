"""Module-level helpers and private collaborators for task_runtime service."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Mapping, NoReturn, TypedDict, cast

from polaris.cells.events.fact_stream.public.contracts import (
    FactStreamError,
)
from polaris.cells.runtime.task_runtime.internal.task_board import (
    TaskStatus,
)

from ..directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
)
from ..execution_session import (
    TaskExecutionSession,
    _json_compatible_copy,
    sanitize_summary,
    terminal_task_status_value_for_session_status,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.public.contracts import (
        DirectedEffectRecoverySweepItemV1,
        RuntimeTaskFactoryRunBindingCodeV1,
        RuntimeTaskFactoryRunBindingResultV1,
    )

_TASK_ID_PATTERN = re.compile(r"^task-(\d+)(?:-|$)", re.IGNORECASE)

_TASK_SESSION_FILE_PATTERN = re.compile(r"^task_(\d+)\.session\.json$")

_FACT_APPEND_CAS_MAX_ATTEMPTS = 64

_EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS = 5.0

_OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1 = "task-runtime.owner-rework-execution-authorization/1"

_OWNER_REWORK_HANDOFFS_METADATA_KEY = "owner_rework_handoffs"

_OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY = "owner_rework_execution_authorization"

_SAME_TASK_LOCAL_REWORK_AUTHORIZATIONS_METADATA_KEY = "same_task_local_rework_authorizations"

_OWNER_REWORK_ROUTE_SCHEMA_V1 = "task-market.owner-rework-route/1"

_OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE = "resolved_only"

_PENDING_TERMINAL_INTENT_SCHEMA_V1 = "task-runtime.pending-terminal-intent/1"

_PENDING_TERMINAL_INTENT_METADATA_KEY = "pending_terminal_intent"

_DEPENDENCY_SATISFACTION_METADATA_KEY = "task_runtime_dependency_satisfaction"

_DEPENDENCY_SATISFACTION_SCHEMA_V1 = "task-runtime.dependency-satisfaction/1"

_DIRECTED_EFFECT_RECOVERY_LEASE_SCHEMA_V1 = "task-runtime.directed-effect-recovery-lease/1"

_DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH = "runtime/tasks/directed_effect_recovery.lease.json"

_REEXECUTION_METADATA_DROP_KEYS = frozenset(
    {
        "adapter_phase",
        "claim_attempt",
        "claimed_at",
        "claimed_by",
        "director_claimable_task_ids",
        "factory_stage",
        "last_claimed_by",
        "last_context_summary",
        "last_execution_error",
        "last_execution_summary",
        "resume_available",
        "resume_count",
        "resume_state",
        "runtime_execution",
        _DEPENDENCY_SATISFACTION_METADATA_KEY,
        "workflow_run_id",
    }
)


def _canonical_sha256(value: object) -> str:
    """Hash one JSON value using the stable UTF-8 representation."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TaskExecutionSessionWriteConflictError(RuntimeError):
    """Raised when an execution-session file changes before replacement."""


@dataclass(frozen=True, slots=True)
class _LockedSessionSuspendResult:
    """Result of a locked per-session bulk suspend attempt."""

    session: TaskExecutionSession | None
    session_written: bool
    blocker: DirectedEffectSettlementPreBarrierVerdictV1 | None = None


@dataclass(frozen=True, slots=True)
class _DirectedEffectRecoverySessionSweep:
    """One session's recovery facts while both session locks remain held."""

    items: tuple[DirectedEffectRecoverySweepItemV1, ...] = ()
    failures: tuple[dict[str, Any], ...] = ()
    scanned_session_count: int = 0
    scanned_operation_count: int = 0
    stop_sweep: bool = False


@dataclass(frozen=True, slots=True)
class _DirectedEffectRecoveryTaskCatalog:
    """Bounded task/session discovery facts for one recovery lease."""

    task_rows_by_id: Mapping[int, dict[str, Any]]
    task_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _PreparedTerminalSettlement:
    """Durable terminal intent and its TaskRuntime-owned parent authority."""

    repository: DirectedEffectOperationRepository
    intent: Mapping[str, Any]
    terminal_intent_hash: str


@dataclass(frozen=True, slots=True)
class _DependencySatisfactionDecision:
    """TaskRuntime-owned proof that a failed task may release dependents."""

    evidence: Mapping[str, Any]


class _ExecutionEventFailureEvidence(TypedDict):
    """Detached, source-aware append failure evidence owned by TaskRuntime."""

    schema_version: Literal["task-runtime.execution-event-failure/1"]
    source: Literal["fact_stream", "task_runtime"]
    stage: str
    code: str
    error_type: str
    details: dict[str, Any]


class _ExecutionEventProjectionEvidence(TypedDict):
    """Detached evidence for a durable fact lacking its realtime projection."""

    schema_version: Literal["task-runtime.execution-event-projection/1"]
    source: Literal["task_runtime"]
    stage: Literal["event_publish"]
    code: Literal["factory_execution_event_publish_returned_false"]
    status: Literal["not_published"]
    details: dict[str, Any]


def _execution_event_failure_evidence(
    exc: RuntimeError | ValueError,
    *,
    stage: str,
) -> _ExecutionEventFailureEvidence:
    """Project append failure evidence without classifying generic errors as FactStream failures."""

    if isinstance(exc, FactStreamError):
        return {
            "schema_version": "task-runtime.execution-event-failure/1",
            "source": "fact_stream",
            "stage": stage,
            "code": exc.code,
            "error_type": type(exc).__name__,
            "details": _json_compatible_copy(dict(exc.details)),
        }

    generic_code = "task_runtime_execution_event_runtime_error"
    if isinstance(exc, ValueError):
        generic_code = "task_runtime_execution_event_validation_error"
    return {
        "schema_version": "task-runtime.execution-event-failure/1",
        "source": "task_runtime",
        "stage": stage,
        "code": generic_code,
        "error_type": type(exc).__name__,
        "details": {"message": sanitize_summary(exc, max_chars=300)},
    }


def _execution_event_projection_evidence(
    *,
    factory_run_id: str,
    fact_event_id: str,
    fact_stream: str,
    fact_event_seq: int | None,
) -> _ExecutionEventProjectionEvidence:
    """Build detached evidence for a durable fact whose realtime wakeup was declined."""

    durable_fact: dict[str, Any] = {
        "event_id": fact_event_id,
        "stream": fact_stream,
    }
    if fact_event_seq is not None:
        durable_fact["event_seq"] = fact_event_seq
    return {
        "schema_version": "task-runtime.execution-event-projection/1",
        "source": "task_runtime",
        "stage": "event_publish",
        "code": "factory_execution_event_publish_returned_false",
        "status": "not_published",
        "details": {
            "factory_run_id": factory_run_id,
            "durable_fact": durable_fact,
        },
    }


def _normalize_owner_rework_handoff_record(value: object) -> dict[str, Any]:
    """Validate TaskMarket's serialized handoff at the TaskRuntime boundary."""

    if not isinstance(value, Mapping):
        raise ValueError("owner-rework handoff evidence must be a mapping")

    def required_text(field: str) -> str:
        normalized = str(value.get(field) or "").strip()
        if not normalized:
            raise ValueError(f"owner-rework handoff evidence is missing {field}")
        return normalized

    schema_version = required_text("schema_version")
    if schema_version != _OWNER_REWORK_ROUTE_SCHEMA_V1:
        raise ValueError("owner-rework handoff evidence schema is unsupported")
    dependency_mode = required_text("dependency_mode")
    if dependency_mode != _OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE:
        raise ValueError("owner-rework handoff dependency mode is unsupported")
    owner_reopened = value.get("owner_reopened")
    if not isinstance(owner_reopened, bool):
        raise ValueError("owner-rework handoff owner_reopened must be a bool")

    failure_metadata = value.get("failure_metadata")
    evidence_metadata = value.get("evidence_metadata")
    metadata = value.get("metadata")
    if not isinstance(failure_metadata, Mapping) or not failure_metadata:
        raise ValueError("owner-rework handoff failure_metadata must be a non-empty mapping")
    if not isinstance(evidence_metadata, Mapping) or not evidence_metadata:
        raise ValueError("owner-rework handoff evidence_metadata must be a non-empty mapping")
    if not isinstance(metadata, Mapping):
        raise ValueError("owner-rework handoff metadata must be a mapping")

    return {
        "schema_version": schema_version,
        "handoff_id": required_text("handoff_id"),
        "owner_task_id": required_text("owner_task_id"),
        "requester_task_id": required_text("requester_task_id"),
        "owner_previous_status": required_text("owner_previous_status"),
        "requester_previous_status": required_text("requester_previous_status"),
        "owner_reopened": owner_reopened,
        "dependency_mode": dependency_mode,
        "failure_metadata": dict(failure_metadata),
        "evidence_metadata": dict(evidence_metadata),
        "metadata": dict(metadata),
        "routed_at": required_text("routed_at"),
    }


def _build_factory_run_binding_result(
    *,
    ok: bool,
    code: RuntimeTaskFactoryRunBindingCodeV1,
    reason: str,
    workspace: str,
    task_id: str,
    factory_run_id: str,
    existing_factory_run_id: str = "",
    row_updated: bool = False,
    event_recorded: bool = False,
    idempotent: bool = False,
    task_row: Mapping[str, Any] | None = None,
    execution_event: Mapping[str, Any] | None = None,
) -> RuntimeTaskFactoryRunBindingResultV1:
    """Build the public binding result without importing contracts at module load."""

    from polaris.cells.runtime.task_runtime.public.contracts import (
        RuntimeTaskFactoryRunBindingResultV1,
    )

    return RuntimeTaskFactoryRunBindingResultV1(
        ok=ok,
        code=code,
        reason=reason,
        workspace=workspace,
        task_id=task_id,
        factory_run_id=factory_run_id,
        existing_factory_run_id=existing_factory_run_id,
        row_updated=row_updated,
        event_recorded=event_recorded,
        idempotent=idempotent,
        task_row=dict(task_row or {}),
        execution_event=dict(execution_event or {}),
    )


def _raise_retired_entity_api(method: str, replacement: str) -> NoReturn:
    """Fail closed when callers try to use retired Task entity APIs."""

    raise RuntimeError(f"TaskRuntimeService.{method} is retired; use {replacement}()")


def _terminal_task_status_for_session(status: Any) -> TaskStatus | None:
    """Adapt canonical session-terminal projection values to TaskBoard enums."""

    task_status_value = terminal_task_status_value_for_session_status(status)
    if not task_status_value:
        return None
    try:
        return TaskStatus(task_status_value)
    except ValueError:
        logger.warning("Unknown task status projected from session status: %r", task_status_value)
        return None


def _is_terminal_task_row_update_status(status: TaskStatus | str | None) -> bool:
    """Return whether a public row update is attempting to write a terminal state."""

    if status is None:
        return False
    if isinstance(status, TaskStatus):
        return cast(bool, status.is_terminal)
    token = str(status or "").strip()
    if not token:
        return False
    try:
        return cast(bool, TaskStatus(token).is_terminal)
    except ValueError:
        return False


def _is_execution_task_row_update_status(status: TaskStatus | str | None) -> bool:
    """Return whether a public row update is attempting to write execution state."""

    if status is None:
        return False
    if isinstance(status, TaskStatus):
        return status in {TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS}
    token = str(status or "").strip()
    if not token:
        return False
    try:
        return TaskStatus(token) in {TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS}
    except ValueError:
        return False
