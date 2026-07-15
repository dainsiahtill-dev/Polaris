from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, NoReturn, Sequence, TypedDict, cast

from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
    QueryFactStreamHeadV1,
)
from polaris.cells.events.fact_stream.public.service import (
    append_fact_event,
    query_fact_events,
    query_fact_stream_head,
)
from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    Task,
    TaskBoard,
    TaskBoardFileLockTimeoutError,
    TaskFactoryRunBindingConflictError,
    TaskStatus,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    TASK_RUNTIME_EXECUTION_SOURCE_V1,
    TASK_RUNTIME_EXECUTION_STREAM_V1,
    AdmitDirectedEffectParentCommandV1,
    DirectedEffectOperationResultV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    OwnerReworkExecutionPreparationCodeV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptAuthorityOpenCodeV1,
    TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1,
    TaskRuntimeExecutionAttemptHeartbeatCodeV1,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementCodeV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    TaskRuntimeExecutionFactV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.fs.registry import get_default_adapter
from polaris.kernelone.storage import resolve_runtime_path, resolve_storage_roots

from .directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
)
from .execution_session import (
    TaskExecutionSession,
    TaskExecutionSessionWriteReceipt,
    _coerce_fact_event_seq,
    _json_compatible_copy,
    build_task_execution_bulk_suspend_result,
    build_task_execution_claim_attempt,
    build_task_execution_claim_next_result,
    build_task_execution_claim_result,
    build_task_execution_heartbeat_result,
    build_task_execution_transition_result,
    build_task_runtime_execution_event_append_result,
    build_task_runtime_execution_event_payload,
    build_task_runtime_metadata,
    is_terminal_session_status,
    is_terminal_task_row_status,
    normalize_positive_int,
    project_task_row_execution_event,
    project_task_row_from_execution_fact_payload,
    project_task_row_runtime_state,
    sanitize_summary,
    task_row_status_counts,
    terminal_session_timestamp,
    terminal_task_status_value_for_session_status,
    utc_now,
    utc_now_iso,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from polaris.cells.runtime.task_runtime.public.contracts import (
        BindRuntimeTaskToFactoryRunCommandV1,
        ExpiredFactoryRunSessionFenceResultV1,
        FenceExpiredFactoryRunSessionsCommandV1,
        ObservableTaskRowsProjectionV1,
        OwnerReworkExecutionPreparationResultV1,
        PrepareOwnerReworkExecutionCommandV1,
        RuntimeTaskFactoryRunBindingCodeV1,
        RuntimeTaskFactoryRunBindingResultV1,
    )
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeExecutionAttemptAuthorityV1

_TASK_ID_PATTERN = re.compile(r"^task-(\d+)(?:-|$)", re.IGNORECASE)
_TASK_SESSION_FILE_PATTERN = re.compile(r"^task_(\d+)\.session\.json$")
_FACT_APPEND_CAS_MAX_ATTEMPTS = 64
_EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS = 5.0
_OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1 = "task-runtime.owner-rework-execution-authorization/1"
_OWNER_REWORK_HANDOFFS_METADATA_KEY = "owner_rework_handoffs"
_OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY = "owner_rework_execution_authorization"
_OWNER_REWORK_ROUTE_SCHEMA_V1 = "task-market.owner-rework-route/1"
_OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE = "resolved_only"
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
        "workflow_run_id",
    }
)


class TaskExecutionSessionWriteConflictError(RuntimeError):
    """Raised when an execution-session file changes before replacement."""


@dataclass(frozen=True, slots=True)
class _LockedSessionSuspendResult:
    """Result of a locked per-session bulk suspend attempt."""

    session: TaskExecutionSession | None
    session_written: bool
    blocker: DirectedEffectSettlementPreBarrierVerdictV1 | None = None


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
        return status.is_terminal
    token = str(status or "").strip()
    if not token:
        return False
    try:
        return TaskStatus(token).is_terminal
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


class TaskRuntimeService:
    """Runtime task lifecycle service for the ``runtime.task_runtime`` cell.

    Responsibilities:
    - Keep the canonical runtime taskboard rows under ``runtime/tasks/*``
    - Materialize legacy orchestration tasks into canonical task rows
    - Persist execution lease/session facts under ``runtime/tasks/*``
    - Expose a stable, resumable read model for snapshot/observer consumers
    """

    def __init__(self, workspace: str, board: TaskBoard | None = None) -> None:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise ValueError("workspace is required for TaskRuntimeService")
        self._workspace = workspace_token
        self._board = board or TaskBoard(workspace=workspace_token)
        self._kernel_fs = KernelFileSystem(workspace_token, get_default_adapter())
        # Per-task-id locks guard only the read-modify-write cycle on session
        # files. The only FactStream work permitted under this lock is the
        # narrow DEO registry admission/pre-barrier chain; projection uses a
        # distinct lock and never acquires a session lock.
        self._session_locks: dict[int, threading.RLock] = {}
        self._session_locks_meta = threading.Lock()
        self._settlement_projection_locks: dict[int, threading.RLock] = {}
        self._settlement_projection_locks_meta = threading.Lock()
        self._last_session_write_receipt: TaskExecutionSessionWriteReceipt | None = None
        self._session_write_receipts_by_identity: dict[tuple[int, str], TaskExecutionSessionWriteReceipt] = {}
        self._session_write_receipt_lock = threading.Lock()
        self._execution_fact_append_lock = threading.Lock()

    @property
    def workspace(self) -> str:
        return self._workspace

    @staticmethod
    def _after_directed_effect_linearization_lock(
        operation: Literal["parent_admission", "settlement"],
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> None:
        """Deterministic test seam after the cooperative session lock is held."""

        del operation, identity

    def admit_directed_effect_parent(
        self,
        command: AdmitDirectedEffectParentCommandV1,
    ) -> DirectedEffectOperationResultV1:
        """Linearize parent admission against every inactive session writer.

        Lock order is fixed: this service's per-task ``RLock``, the
        cooperative session-file lock, then repository-owned FactStream locks.
        No TaskBoard transaction or projection runs in the critical section.
        """

        identity = command.execution_attempt
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="workspace_mismatch",
                evidence={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )
        if command.task_id != identity.task_id:
            return DirectedEffectOperationResultV1(
                ok=False,
                code="task_mismatch",
                evidence={
                    "command_task_id": command.task_id,
                    "identity_task_id": identity.task_id,
                },
            )

        repository = DirectedEffectOperationRepository()
        timeout = _EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS
        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=timeout):
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="file_lock_timeout",
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": timeout},
            )
        try:
            remaining = timeout - (time.monotonic() - started_at)
            if remaining < 0:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="file_lock_timeout",
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": timeout},
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    self._after_directed_effect_linearization_lock(
                        "parent_admission",
                        identity,
                    )
                    return self._admit_directed_effect_parent_locked(
                        command,
                        repository=repository,
                    )
            except TaskBoardFileLockTimeoutError:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="file_lock_timeout",
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": timeout,
                    },
                )
            except OSError as exc:
                return self._directed_effect_attempt_validation_failure(
                    repository,
                    identity,
                    code="session_corrupt",
                    evidence={
                        "stage": "cooperative_session_file_lock",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
        finally:
            session_lock.release()

    def _admit_directed_effect_parent_locked(
        self,
        command: AdmitDirectedEffectParentCommandV1,
        *,
        repository: DirectedEffectOperationRepository,
    ) -> DirectedEffectOperationResultV1:
        """Validate the locked session and invoke the registry-only repository."""

        identity = command.execution_attempt
        try:
            session = self._read_session_locked(
                identity.task_id,
                raise_infrastructure_errors=True,
            )
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_corrupt",
                evidence={
                    "stage": "locked_session_read",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
        if session is None:
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_not_found",
                evidence={"session_path": self._session_logical_path(identity.task_id)},
            )
        observed = self._execution_attempt_identity_from_session(session)
        evidence = {"observed": observed.to_record()}
        mismatch_code = self._execution_attempt_mismatch_code(identity, session)
        if mismatch_code is not None:
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code=mismatch_code,
                evidence=evidence,
            )
        if session.status != "active":
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_not_active",
                evidence=evidence,
            )
        if session.is_expired(now=utc_now()):
            return self._directed_effect_attempt_validation_failure(
                repository,
                identity,
                code="session_lease_expired",
                evidence=evidence,
            )
        return repository.admit_parent_with_validated_authority(command)

    def _directed_effect_attempt_validation_failure(
        self,
        repository: DirectedEffectOperationRepository,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        code: TaskRuntimeExecutionAttemptValidationCodeV1,
        evidence: Mapping[str, Any],
    ) -> DirectedEffectOperationResultV1:
        verdict = self._execution_attempt_validation_verdict(
            valid=False,
            code=code,
            identity=identity,
            evidence=evidence,
        )
        return repository.attempt_validation_failure_result(verdict)

    def _directed_effect_inactive_pre_barrier_locked(
        self,
        session: TaskExecutionSession,
    ) -> DirectedEffectSettlementPreBarrierVerdictV1:
        """Strictly check one session while both session locks are held."""

        identity = self._execution_attempt_identity_from_session(session)
        return DirectedEffectOperationRepository().settlement_pre_barrier(identity)

    @staticmethod
    def _directed_effect_inactive_block_record(
        task_id: int,
        verdict: DirectedEffectSettlementPreBarrierVerdictV1,
    ) -> dict[str, Any]:
        """Return typed refusal evidence without projecting an inactive row."""

        evidence = dict(verdict.evidence)
        execution_event = {
            "ok": False,
            "reason": verdict.code,
            "code": verdict.code,
            "error": verdict.code,
            "evidence": evidence,
        }
        return {
            "ok": False,
            "success": False,
            "code": verdict.code,
            "reason": verdict.code,
            "task_id": str(task_id),
            "evidence": evidence,
            "execution_event": execution_event,
            "execution_events": (),
        }

    def last_session_write_receipt(self) -> TaskExecutionSessionWriteReceipt | None:
        """Return the last successful execution-session write receipt anchor."""

        with self._session_write_receipt_lock:
            return self._last_session_write_receipt

    @staticmethod
    def _owner_rework_execution_result(
        *,
        ok: bool,
        code: OwnerReworkExecutionPreparationCodeV1,
        reason: str,
        task_id: str,
        handoff_id: str = "",
        task_role: str = "",
        runtime_task_id: str = "",
        reopened: bool = False,
        idempotent: bool = False,
        execution_event: Mapping[str, Any] | None = None,
    ) -> OwnerReworkExecutionPreparationResultV1:
        """Build the public result without coupling module import initialization.

        The import is intentionally local: ``public.service`` owns the public
        facade and imports this implementation, so importing its contracts at
        module import time would create an avoidable package cycle.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            OwnerReworkExecutionPreparationResultV1,
        )

        return OwnerReworkExecutionPreparationResultV1(
            ok=ok,
            code=code,
            reason=reason,
            task_id=task_id,
            handoff_id=handoff_id,
            task_role=task_role,
            runtime_task_id=runtime_task_id,
            reopened=reopened,
            idempotent=idempotent,
            execution_event=dict(execution_event or {}),
        )

    def prepare_owner_rework_execution(
        self,
        command: PrepareOwnerReworkExecutionCommandV1,
    ) -> OwnerReworkExecutionPreparationResultV1:
        """Prepare one claimed owner/requester rework task for adapter execution.

        TaskMarket is the sole authority for owner/requester routing,
        ``resolved_only`` readiness, and claim leasing. This method consumes a
        typed snapshot of that already-granted authority and only reconciles
        TaskRuntime's task row and execution session. It never queries
        TaskMarket, recreates dependencies, or mutates TaskMarket state.

        A terminal TaskRuntime row is reopened only through the sanctioned
        runtime path, which rotates a terminal session and appends execution
        facts. A matching authorization on an already non-terminal row is a
        no-op, while a conflicting or malformed authorization fails closed.
        """

        authorization = getattr(command, "authorization", None)
        task_id = str(getattr(authorization, "task_id", "") or "").strip()
        if not task_id:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization is missing task_id",
                task_id="unknown",
            )

        schema_version = str(getattr(authorization, "schema_version", "") or "").strip()
        if schema_version != _OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization schema is invalid",
                task_id=task_id,
            )
        if str(getattr(authorization, "workspace", "") or "").strip() != self.workspace:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_workspace_mismatch",
                reason="owner rework authorization workspace does not match TaskRuntime",
                task_id=task_id,
            )

        handoff = getattr(authorization, "handoff", None)
        try:
            canonical_handoff = _normalize_owner_rework_handoff_record(handoff)
        except ValueError:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization handoff is malformed",
                task_id=task_id,
            )
        handoff_id = str(canonical_handoff["handoff_id"])

        task_role = str(getattr(authorization, "task_role", "") or "").strip().lower()
        counterparty_task_id = str(getattr(authorization, "counterparty_task_id", "") or "").strip()
        if task_role == "owner":
            expected_task_id = str(canonical_handoff["owner_task_id"])
            expected_counterparty_task_id = str(canonical_handoff["requester_task_id"])
            role_allowed = bool(canonical_handoff["owner_reopened"])
        elif task_role == "requester":
            expected_task_id = str(canonical_handoff["requester_task_id"])
            expected_counterparty_task_id = str(canonical_handoff["owner_task_id"])
            role_allowed = True
        else:
            expected_task_id = ""
            expected_counterparty_task_id = ""
            role_allowed = False
        if not role_allowed or task_id != expected_task_id or counterparty_task_id != expected_counterparty_task_id:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_handoff_mismatch",
                reason="owner rework task role or counterparty does not match handoff",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )

        worker_id = str(getattr(authorization, "worker_id", "") or "").strip()
        worker_role = str(getattr(authorization, "worker_role", "") or "").strip()
        lease_token = str(getattr(authorization, "lease_token", "") or "").strip()
        claimed_item = getattr(authorization, "claimed_item", None)
        counterparty_item = getattr(authorization, "counterparty_item", None)
        if not isinstance(claimed_item, Mapping) or not isinstance(counterparty_item, Mapping):
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_authorization_malformed",
                reason="owner rework authorization item evidence is malformed",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )
        if (
            worker_role != "director"
            or not worker_id
            or not lease_token
            or str(claimed_item.get("task_id") or "").strip() != task_id
            or str(claimed_item.get("status") or "").strip().lower() != "in_execution"
            or str(claimed_item.get("lease_token") or "").strip() != lease_token
            or str(claimed_item.get("claimed_by") or "").strip() != worker_id
        ):
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_claim_evidence_invalid",
                reason="owner rework authorization does not prove the active Director lease",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )
        if str(counterparty_item.get("task_id") or "").strip() != counterparty_task_id:
            return self._owner_rework_execution_result(
                ok=False,
                code="owner_rework_counterparty_evidence_invalid",
                reason="owner rework authorization counterparty item does not match handoff",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )

        for item in (claimed_item, counterparty_item):
            metadata = item.get("metadata")
            handoffs = metadata.get(_OWNER_REWORK_HANDOFFS_METADATA_KEY) if isinstance(metadata, Mapping) else None
            record = handoffs.get(handoff_id) if isinstance(handoffs, Mapping) else None
            try:
                item_handoff = _normalize_owner_rework_handoff_record(record)
            except ValueError:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="owner_rework_handoff_evidence_invalid",
                    reason="owner rework authorization item lacks a valid matching handoff",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                )
            if item_handoff != canonical_handoff:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="owner_rework_handoff_evidence_invalid",
                    reason="owner rework authorization item handoff conflicts with claim evidence",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                )

        observable_row = self.get_task(task_id)
        if not isinstance(observable_row, Mapping):
            return self._owner_rework_execution_result(
                ok=False,
                code="runtime_task_not_found",
                reason="TaskRuntime has no execution row for the claimed owner rework task",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )
        runtime_task_id = self.normalize_task_id(observable_row.get("id"))
        if runtime_task_id is None:
            return self._owner_rework_execution_result(
                ok=False,
                code="runtime_task_invalid",
                reason="TaskRuntime execution row has no valid task id",
                task_id=task_id,
                handoff_id=handoff_id,
                task_role=task_role,
            )

        authorization_record = {
            "schema_version": _OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
            "handoff_id": handoff_id,
            "task_id": task_id,
            "task_role": task_role,
            "counterparty_task_id": counterparty_task_id,
            "worker_id": worker_id,
            "worker_role": worker_role,
            "lease_token_sha256": hashlib.sha256(lease_token.encode("utf-8")).hexdigest(),
        }
        with self._get_session_lock(runtime_task_id):
            current_row = self.get_task(task_id)
            if not isinstance(current_row, Mapping):
                return self._owner_rework_execution_result(
                    ok=False,
                    code="runtime_task_not_found",
                    reason="TaskRuntime execution row disappeared before preparation",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                )
            current_metadata = current_row.get("metadata")
            metadata = current_metadata if isinstance(current_metadata, Mapping) else {}
            existing_record = metadata.get(_OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY)
            matching_authorization = False
            if existing_record is not None:
                if not isinstance(existing_record, Mapping):
                    return self._owner_rework_execution_result(
                        ok=False,
                        code="owner_rework_authorization_conflict",
                        reason="TaskRuntime has malformed owner rework authorization state",
                        task_id=task_id,
                        handoff_id=handoff_id,
                        task_role=task_role,
                        runtime_task_id=str(runtime_task_id),
                    )
                matching_authorization = dict(existing_record) == authorization_record
                if not matching_authorization:
                    return self._owner_rework_execution_result(
                        ok=False,
                        code="owner_rework_authorization_conflict",
                        reason="TaskRuntime task is already prepared for a different owner rework authorization",
                        task_id=task_id,
                        handoff_id=handoff_id,
                        task_role=task_role,
                        runtime_task_id=str(runtime_task_id),
                    )

            session = self._read_session(runtime_task_id)
            active_session = (
                session is not None and session.status == "active" and not session.is_expired(now=utc_now())
            )
            if active_session and matching_authorization:
                return self._owner_rework_execution_result(
                    ok=True,
                    code="owner_rework_execution_already_prepared",
                    reason="matching owner rework execution is already active",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                    idempotent=True,
                )
            if active_session:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="runtime_execution_lease_conflict",
                    reason="TaskRuntime has an active execution lease for this task",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                )

            terminal_evidence = is_terminal_task_row_status(current_row.get("status")) or (
                session is not None and is_terminal_session_status(session.status)
            )
            if matching_authorization and not terminal_evidence:
                return self._owner_rework_execution_result(
                    ok=True,
                    code="owner_rework_execution_already_prepared",
                    reason="matching owner rework execution is already prepared",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                    idempotent=True,
                )

            reopened = False
            if terminal_evidence:
                reopened_row = self.reopen_task_row(
                    runtime_task_id,
                    reason="owner_rework_execution_authorized",
                    metadata={
                        _OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY: authorization_record,
                    },
                )
                if reopened_row is None:
                    return self._owner_rework_execution_result(
                        ok=False,
                        code="runtime_task_not_found",
                        reason="TaskRuntime could not reopen the owner rework execution row",
                        task_id=task_id,
                        handoff_id=handoff_id,
                        task_role=task_role,
                        runtime_task_id=str(runtime_task_id),
                    )
                reopened = True

            metadata_update: dict[str, Any] = {
                _OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY: authorization_record,
            }
            if terminal_evidence:
                metadata_update["terminal_reset_at"] = utc_now().timestamp()
            updated_row = self.update_task_row(runtime_task_id, metadata=metadata_update)
            if updated_row is None:
                return self._owner_rework_execution_result(
                    ok=False,
                    code="runtime_task_not_found",
                    reason="TaskRuntime execution row disappeared while recording authorization",
                    task_id=task_id,
                    handoff_id=handoff_id,
                    task_role=task_role,
                    runtime_task_id=str(runtime_task_id),
                )
            prepared_row = self.get_task(task_id) or updated_row
            execution_event = self._append_execution_event(
                "owner_rework_execution_prepared",
                task_row=dict(prepared_row),
                session=self._read_session(runtime_task_id),
                details={
                    "handoff_id": handoff_id,
                    "task_role": task_role,
                    "counterparty_task_id": counterparty_task_id,
                    "lease_token_sha256": authorization_record["lease_token_sha256"],
                    "reopened": reopened,
                },
            )
        return self._owner_rework_execution_result(
            ok=True,
            code="owner_rework_execution_prepared",
            reason="TaskRuntime owner rework execution is prepared",
            task_id=task_id,
            handoff_id=handoff_id,
            task_role=task_role,
            runtime_task_id=str(runtime_task_id),
            reopened=reopened,
            execution_event=execution_event,
        )

    def _list_file_task_entities(self) -> list[Task]:
        """Return raw file-backed ``TaskBoard`` entities for owner-cell use.

        Boundary:
            This is the ``runtime.task_runtime`` owner-cell raw ``TaskBoard``
            entity boundary. It is only for mutation paths and file-backed
            projection assembly that must work with persisted ``Task`` entities.
            It is not a public read model; observable readers must use task-row
            projection APIs instead.
        """

        return self._board.list_all()

    def _reset_authority_conflicts(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        *,
        factory_run_id: str,
    ) -> list[dict[str, Any]]:
        """Return Factory authority conflicts from observable row projections.

        This is deliberately read-model-only.  Callers that need to decide
        whether a Factory run is settled must not consume raw ``TaskBoard``
        entities, because a late execution fact may supersede a row file.
        Reset keeps its raw entity traversal in the owner mutation method that
        emits its tombstone facts.
        """

        conflicts: list[dict[str, Any]] = []
        observed_task_ids: set[int] = set()
        for task_row_source in task_rows:
            task_row = dict(task_row_source)
            task_id = self.normalize_task_id(task_row.get("id"))
            if task_id is None:
                logger.warning("Skipping TaskRuntime authority row without a valid task id")
                continue
            observed_task_ids.add(task_id)
            metadata = task_row.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else {}
            row_factory_run_id = str(metadata_map.get("factory_run_id") or "").strip()
            fact_factory_run_id = self._execution_fact_factory_run_id(task_row)
            for source, owner in (
                ("task_row", row_factory_run_id),
                ("execution_fact", fact_factory_run_id),
            ):
                if owner and owner != factory_run_id:
                    conflicts.append(
                        {
                            "kind": "foreign_factory_run_binding",
                            "task_id": str(task_id),
                            "source": source,
                            "existing_factory_run_id": owner,
                            "requested_factory_run_id": factory_run_id,
                        }
                    )

            session = self._read_session(task_id)
            if session is None or session.status != "active":
                continue
            lease_expired = session.is_expired(now=utc_now())
            session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
            session_factory_run_id = str(
                row_factory_run_id
                or fact_factory_run_id
                or session_metadata.get("factory_run_id")
                or session.run_id
                or ""
            ).strip()
            conflicts.append(
                {
                    "kind": (
                        "active_expired_session"
                        if lease_expired
                        else (
                            "active_foreign_session"
                            if session_factory_run_id != factory_run_id
                            else "active_session_not_settled"
                        )
                    ),
                    "task_id": str(task_id),
                    "session_id": session.session_id,
                    "session_run_id": session.run_id,
                    "existing_factory_run_id": session_factory_run_id,
                    "requested_factory_run_id": factory_run_id,
                    "lease_expires_at": session.lease_expires_at,
                    "lease_expired": lease_expired,
                    "ownership": ("foreign" if session_factory_run_id != factory_run_id else "requested_factory_run"),
                }
            )
        for session_path in self._board.tasks_dir.glob("task_*.session.json"):
            match = _TASK_SESSION_FILE_PATTERN.fullmatch(session_path.name)
            if match is None:
                continue
            task_id = int(match.group(1))
            if task_id in observed_task_ids:
                continue
            session = self._read_session(task_id)
            if session is None or session.status != "active":
                continue
            lease_expired = session.is_expired(now=utc_now())
            session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
            session_factory_run_id = str(session_metadata.get("factory_run_id") or session.run_id or "").strip()
            conflicts.append(
                {
                    "kind": (
                        "active_expired_session"
                        if lease_expired
                        else (
                            "active_orphan_session"
                            if session_factory_run_id == factory_run_id
                            else "active_foreign_session"
                        )
                    ),
                    "task_id": str(task_id),
                    "session_id": session.session_id,
                    "session_run_id": session.run_id,
                    "existing_factory_run_id": session_factory_run_id,
                    "requested_factory_run_id": factory_run_id,
                    "lease_expires_at": session.lease_expires_at,
                    "lease_expired": lease_expired,
                    "ownership": ("foreign" if session_factory_run_id != factory_run_id else "requested_factory_run"),
                }
            )
        return conflicts

    @staticmethod
    def _session_factory_run_id(
        session: TaskExecutionSession,
        task_row: Mapping[str, Any] | None = None,
    ) -> str:
        """Resolve one session's Factory authority without parsing prompt text."""

        row = task_row or {}
        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        session_metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
        fact = metadata_map.get("task_runtime_execution_fact")
        fact_map = fact if isinstance(fact, Mapping) else {}
        return str(
            row.get("factory_run_id")
            or metadata_map.get("factory_run_id")
            or fact_map.get("factory_run_id")
            or session_metadata.get("factory_run_id")
            or session.run_id
            or ""
        ).strip()

    def fence_expired_factory_run_sessions(
        self,
        command: FenceExpiredFactoryRunSessionsCommandV1,
    ) -> ExpiredFactoryRunSessionFenceResultV1:
        """Fence expired active sessions under explicit Factory authority.

        The operation is fail-closed: any active unexpired or foreign session
        prevents stale-owner recovery. Expired sessions are changed to
        non-resumable suspension and carry durable execution-fact evidence.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            ExpiredFactoryRunSessionFenceResultV1,
        )

        authority = command.factory_run_id
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        reset_lock_path = tasks_dir / ".task_runtime.reset.lock"
        with self._board._file_lock(reset_lock_path):
            projection = self.query_observable_task_rows_projection()
            task_rows_by_id = {
                task_id: dict(row)
                for row in projection.rows
                if (task_id := self.normalize_task_id(row.get("id"))) is not None
            }
            task_ids = set(task_rows_by_id)
            for session_path in tasks_dir.glob("task_*.session.json"):
                match = _TASK_SESSION_FILE_PATTERN.fullmatch(session_path.name)
                if match is not None:
                    task_ids.add(int(match.group(1)))

            conflicts: list[dict[str, Any]] = []
            candidates: list[tuple[int, TaskExecutionSession]] = []
            observed_at = utc_now()
            for task_id in sorted(task_ids):
                session = self._read_session(task_id)
                if session is None or session.status != "active":
                    continue
                task_row = task_rows_by_id.get(task_id, {})
                owner = self._session_factory_run_id(session, task_row)
                expired = session.is_expired(now=observed_at)
                if owner != authority or not expired:
                    conflicts.append(
                        {
                            "kind": ("active_foreign_session" if owner != authority else "active_unexpired_session"),
                            "task_id": str(task_id),
                            "session_id": session.session_id,
                            "existing_factory_run_id": owner,
                            "requested_factory_run_id": authority,
                            "lease_expires_at": session.lease_expires_at,
                            "lease_expired": expired,
                        }
                    )
                    continue
                candidates.append((task_id, session))

            if conflicts:
                return ExpiredFactoryRunSessionFenceResultV1(
                    ok=False,
                    code="active_session_conflict",
                    workspace=str(self.workspace),
                    factory_run_id=authority,
                    conflicts=tuple(conflicts),
                )

            fenced_session_ids: list[str] = []
            execution_events: list[dict[str, Any]] = []
            fence_failures: list[dict[str, Any]] = []
            for task_id, observed_session in candidates:
                session_lock = self._get_session_lock(task_id)
                with (
                    session_lock,
                    self._board._file_lock(self._session_file_lock_path(task_id)),
                ):
                    current = self._read_session_locked(task_id)
                    if current is None:
                        fence_failures.append(
                            {
                                "kind": "session_disappeared_before_fence",
                                "task_id": str(task_id),
                                "session_id": observed_session.session_id,
                            }
                        )
                        continue
                    owner = self._session_factory_run_id(
                        current,
                        task_rows_by_id.get(task_id, {}),
                    )
                    if (
                        current.session_id != observed_session.session_id
                        or current.status != "active"
                        or owner != authority
                        or not current.is_expired(now=utc_now())
                    ):
                        fence_failures.append(
                            {
                                "kind": "session_changed_before_fence",
                                "task_id": str(task_id),
                                "session_id": current.session_id,
                                "session_status": current.status,
                                "existing_factory_run_id": owner,
                                "lease_expires_at": current.lease_expires_at,
                            }
                        )
                        continue
                    pre_barrier = self._directed_effect_inactive_pre_barrier_locked(current)
                    if not pre_barrier.allowed:
                        fence_failures.append(
                            {
                                "kind": pre_barrier.code,
                                "code": pre_barrier.code,
                                "task_id": str(task_id),
                                "session_id": current.session_id,
                                "evidence": dict(pre_barrier.evidence),
                            }
                        )
                        continue
                    previous_expiry = current.lease_expires_at
                    current.mark_suspended(reason=command.reason, resumable=False)
                    current.metadata["factory_stale_session_fence"] = {
                        "schema_version": "task-runtime.factory-stale-session-fence/1",
                        "factory_run_id": authority,
                        "reason": command.reason,
                        "previous_lease_expires_at": previous_expiry,
                        "fenced_at": current.released_at,
                    }
                    if not self._write_session_locked(current):
                        fence_failures.append(
                            {
                                "kind": "session_write_rejected",
                                "task_id": str(task_id),
                                "session_id": current.session_id,
                            }
                        )
                        continue

                fence_metadata = self._build_runtime_metadata(
                    session=current,
                    effective_status="blocked",
                    resume_state="fenced",
                    extra_metadata={
                        "factory_run_id": authority,
                        "factory_stale_session_fenced": True,
                    },
                )
                runtime_execution = dict(fence_metadata.get("runtime_execution") or {})
                runtime_execution.update(
                    {
                        "effective_status": "blocked",
                        "raw_status": "blocked",
                        "resume_state": "fenced",
                        "resume_available": False,
                    }
                )
                fence_metadata["runtime_execution"] = runtime_execution
                fence_metadata["resume_state"] = "fenced"
                fence_metadata["resume_available"] = False
                updated = self._board.update(
                    task_id,
                    status=TaskStatus.BLOCKED,
                    assignee="",
                    metadata=fence_metadata,
                )
                row = self._augment_task_row(
                    updated.to_dict() if updated is not None else {"id": task_id, "status": "blocked"}
                )
                if updated is None:
                    fence_failures.append(
                        {
                            "kind": "task_row_update_rejected",
                            "task_id": str(task_id),
                            "session_id": current.session_id,
                        }
                    )
                    continue
                row_metadata = dict(row.get("metadata") or {})
                row_runtime_execution = dict(row_metadata.get("runtime_execution") or {})
                row_runtime_execution.update(
                    {
                        "effective_status": "blocked",
                        "raw_status": "blocked",
                        "resume_state": "fenced",
                        "resume_available": False,
                    }
                )
                row_metadata["runtime_execution"] = row_runtime_execution
                row.update(
                    {
                        "status": "blocked",
                        "state": "blocked",
                        "execution_state": "blocked",
                        "running": False,
                        "resume_state": "fenced",
                        "resume_available": False,
                        "metadata": row_metadata,
                    }
                )
                event = self._append_execution_event(
                    "factory_stale_session_fenced",
                    task_row=row,
                    session=current,
                    details={
                        "factory_run_id": authority,
                        "reason": sanitize_summary(command.reason),
                        "previous_lease_expires_at": previous_expiry,
                    },
                )
                if (
                    event.get("ok") is not True
                    or not str(event.get("fact_event_id") or "").strip()
                    or _coerce_fact_event_seq(event.get("fact_event_seq")) is None
                ):
                    fence_failures.append(
                        {
                            "kind": "execution_event_append_failed",
                            "task_id": str(task_id),
                            "session_id": current.session_id,
                        }
                    )
                    continue
                fenced_session_ids.append(current.session_id)
                execution_events.append(event)

            if fence_failures:
                return ExpiredFactoryRunSessionFenceResultV1(
                    ok=False,
                    code="session_fence_failed",
                    workspace=str(self.workspace),
                    factory_run_id=authority,
                    fenced_session_ids=tuple(fenced_session_ids),
                    conflicts=tuple(fence_failures),
                    execution_events=tuple(execution_events),
                )

            return ExpiredFactoryRunSessionFenceResultV1(
                ok=True,
                code=("expired_sessions_fenced" if candidates else "no_expired_sessions"),
                workspace=str(self.workspace),
                factory_run_id=authority,
                fenced_session_ids=tuple(fenced_session_ids),
                execution_events=tuple(execution_events),
            )

    def query_factory_run_settlement(self, *, factory_run_id: str) -> dict[str, object]:
        """Return stable TaskRuntime evidence for Factory child settlement."""

        authority = str(factory_run_id or "").strip()
        if not authority:
            raise ValueError("factory_run_id must be a non-empty string")
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        reset_lock_path = tasks_dir / ".task_runtime.reset.lock"
        with self._board._file_lock(reset_lock_path):
            projection = self.query_observable_task_rows_projection()
            observable_rows = projection.rows_for_factory_run(authority)
            conflicts = self._reset_authority_conflicts(
                projection.rows,
                factory_run_id=authority,
            )
        active_sessions = [
            dict(conflict) for conflict in conflicts if str(conflict.get("kind") or "").startswith("active_")
        ]
        return {
            "schema_version": "task-runtime.factory-run-settlement/1",
            "factory_run_id": authority,
            "settled": not conflicts,
            "active_session_count": len(active_sessions),
            "active_sessions": active_sessions,
            "conflict_count": len(conflicts),
            "conflicts": [dict(conflict) for conflict in conflicts],
            "observable_source": projection.source,
            "observable_authoritative": projection.authoritative,
            "observable_row_count": len(observable_rows),
            "proof_sources": [
                "task_runtime.observable_task_rows",
                "task_runtime.execution_session_files",
            ],
        }

    @staticmethod
    def _reset_conflict_result(
        *,
        factory_run_id: str,
        conflicts: Sequence[Mapping[str, Any]],
    ) -> dict[str, object]:
        return {
            "ok": False,
            "code": "task_runtime_reset_authority_conflict",
            "reason": "TaskRuntime reset refused foreign ownership or an active execution session",
            "factory_run_id": factory_run_id,
            "conflicts": [dict(conflict) for conflict in conflicts],
            "conflict_count": len(conflicts),
            "cleared_paths": [],
            "failed_paths": [],
            "cleared_count": 0,
            "failed_count": 0,
            "tombstone_events": [],
            "tombstone_count": 0,
        }

    def reset_records(
        self,
        *,
        keep_plan: bool = False,
        factory_run_id: str | None = None,
    ) -> dict[str, object]:
        """Clear canonical taskboard rows and execution sessions.

        This intentionally lives in the runtime.task_runtime cell because
        ``runtime/tasks/*`` is task-runtime-owned state. Delivery-level reset
        orchestration may call this public capability, but other cells must not
        delete these files directly.
        """
        authority = str(factory_run_id or "").strip()
        tasks_dir = self._board.tasks_dir
        tasks_dir.mkdir(parents=True, exist_ok=True)
        reset_lock_path = tasks_dir / ".task_runtime.reset.lock"
        with self._board._file_lock(reset_lock_path):
            projection = self.query_observable_task_rows_projection()
            conflicts = self._reset_authority_conflicts(projection.rows, factory_run_id=authority)
            if conflicts:
                logger.warning(
                    "TaskRuntime reset rejected: factory_run_id=%s conflicts=%s",
                    authority or "<missing>",
                    len(conflicts),
                )
                return self._reset_conflict_result(
                    factory_run_id=authority,
                    conflicts=conflicts,
                )
            return self._reset_records_authorized(
                keep_plan=keep_plan,
                factory_run_id=authority,
            )

    def _reset_records_authorized(
        self,
        *,
        keep_plan: bool,
        factory_run_id: str,
    ) -> dict[str, object]:
        """Commit one preflight-approved reset while the stable reset lock is held."""

        cleared_paths: list[str] = []
        failed_paths: list[str] = []
        tombstone_events: list[dict[str, Any]] = []
        tombstoned_task_files: set[str] = set()

        tasks = self._list_file_task_entities()
        for task in tasks:
            task_id = int(task.id)
            task_file_name = f"task_{task_id}.json"
            task_row = self._augment_task_row(task.to_dict())
            previous_status = str(task_row.get("status") or "")
            task_metadata = dict(task_row.get("metadata") or {})
            runtime_execution = dict(task_metadata.get("runtime_execution") or {})
            runtime_execution.update(
                {
                    "effective_status": "removed",
                    "raw_status": "removed",
                    "resume_available": False,
                }
            )
            task_metadata["runtime_execution"] = runtime_execution
            tombstone_row = {
                **task_row,
                "status": "removed",
                "state": "removed",
                "execution_state": "removed",
                "running": False,
                "resume_available": False,
                "metadata": task_metadata,
            }
            event = self._append_execution_event(
                "runtime_reset_removed",
                task_row=tombstone_row,
                session=None,
                details={
                    "previous_status": previous_status,
                    "reset_keep_plan": bool(keep_plan),
                    "reset_factory_run_id": factory_run_id,
                },
            )
            fact_event_seq = _coerce_fact_event_seq(event.get("fact_event_seq"))
            if not str(event.get("fact_event_id") or "").strip() or fact_event_seq is None:
                logger.warning(
                    "TaskRuntime reset refused to delete task %s because its tombstone fact was not committed",
                    task_id,
                )
                failed_paths.append(str(self._board.tasks_dir / task_file_name))
                continue
            tombstone_events.append(event)
            tombstoned_task_files.add(task_file_name)

        with self._board.transaction():
            tasks_dir = self._board.tasks_dir
            tasks_dir.mkdir(parents=True, exist_ok=True)
            for child in sorted(tasks_dir.iterdir(), key=lambda item: str(item)):
                if keep_plan and child.name == "plan.json":
                    continue
                if child.name == ".max_id" or child.name.endswith(".lock"):
                    continue
                if (
                    child.name.startswith("task_")
                    and child.name.endswith(".json")
                    and not child.name.endswith(".session.json")
                    and child.name not in tombstoned_task_files
                ):
                    continue
                try:
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                    cleared_paths.append(str(child))
                except OSError as exc:
                    logger.warning("Failed to reset task runtime path %s: %s", child, exc)
                    failed_paths.append(str(child))

            self._board._cache.clear()
            with self._session_locks_meta:
                self._session_locks.clear()

        taskboard_event_path = Path(
            resolve_runtime_path(self._workspace, "runtime/events/taskboard.terminal.events.jsonl")
        )
        if taskboard_event_path.is_file():
            try:
                taskboard_event_path.unlink()
                cleared_paths.append(str(taskboard_event_path))
            except OSError as exc:
                logger.warning("Failed to reset taskboard event path %s: %s", taskboard_event_path, exc)
                failed_paths.append(str(taskboard_event_path))

        unique_cleared = sorted(set(cleared_paths))
        unique_failed = sorted({path for path in failed_paths if path not in set(unique_cleared)})
        return {
            "ok": not unique_failed,
            "code": "task_runtime_reset_completed" if not unique_failed else "task_runtime_reset_incomplete",
            "reason": "TaskRuntime reset completed" if not unique_failed else "TaskRuntime reset had failed paths",
            "factory_run_id": factory_run_id,
            "conflicts": [],
            "conflict_count": 0,
            "cleared_paths": unique_cleared,
            "failed_paths": unique_failed,
            "cleared_count": len(unique_cleared),
            "failed_count": len(unique_failed),
            "tombstone_events": tombstone_events,
            "tombstone_count": len(tombstone_events),
        }

    def reset_task_rows_for_reexecution(self, *, source: str = "") -> dict[str, Any]:
        """Reset current task rows to a clean pre-execution state.

        Boundary:
            Raw ``TaskBoard`` entity reads are allowed here only because this
            method is the reexecution mutation owner. It preserves task ids and
            dependency fields, removes stale execution/session state, and must
            append one ``task_runtime.execution`` fact per row mutation.
        """

        reset_files: list[str] = []
        skipped_files: list[str] = []
        deleted_session_files: list[str] = []
        execution_events: list[dict[str, Any]] = []
        for task in self._list_file_task_entities():
            task_id = int(task.id)
            task_file_name = f"task_{task_id}.json"
            try:
                previous_status = str(task.status.value if isinstance(task.status, TaskStatus) else task.status)
                replaced = self._replace_task_row_for_reexecution(task.to_dict())
            except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Failed to reset task row %s for reexecution: %s", task_id, exc)
                skipped_files.append(task_file_name)
                continue
            deleted_session = self._delete_session_file(task_id)
            if deleted_session:
                deleted_session_files.append(Path(deleted_session).name)
            row = self._augment_task_row(replaced.to_dict())
            execution_events.append(
                self._append_execution_event(
                    "reexecution_reset",
                    task_row=row,
                    session=None,
                    details={
                        "source": str(source or "runtime.task_runtime.reexecution_reset"),
                        "previous_status": previous_status,
                    },
                )
            )
            reset_files.append(task_file_name)
        return self._project_reexecution_prepare_result(
            operation="reset",
            changed_files=reset_files,
            skipped_files=skipped_files,
            deleted_session_files=deleted_session_files,
            execution_events=execution_events,
        )

    def import_task_rows_for_reexecution(
        self,
        task_rows: Sequence[Mapping[str, Any]],
        *,
        source: str = "",
        source_task_dir: str = "",
    ) -> dict[str, Any]:
        """Import existing task rows for retry/resume preparation.

        The source rows may come from a trusted runtime snapshot.  The task
        runtime cell still owns persistence: rows are normalized for
        reexecution, numeric task ids are preserved, stale sessions are removed,
        max-id bookkeeping is updated, and every imported row receives
        ``task_runtime.execution`` evidence.
        """

        imported_files: list[str] = []
        skipped_files: list[str] = []
        deleted_session_files: list[str] = []
        execution_events: list[dict[str, Any]] = []
        for payload in task_rows:
            try:
                task_id = self.normalize_task_id(payload.get("id"))
                if task_id is None:
                    raise ValueError("task row id is required")
                task_file_name = f"task_{task_id}.json"
                replaced = self._replace_task_row_for_reexecution(dict(payload))
            except (KeyError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning("Failed to import task row for reexecution: %s", exc)
                skipped_files.append(str(payload.get("id") or "unknown"))
                continue
            deleted_session = self._delete_session_file(task_id)
            if deleted_session:
                deleted_session_files.append(Path(deleted_session).name)
            row = self._augment_task_row(replaced.to_dict())
            execution_events.append(
                self._append_execution_event(
                    "reexecution_imported",
                    task_row=row,
                    session=None,
                    details={
                        "source": str(source or "runtime.task_runtime.reexecution_import"),
                        "source_task_dir": str(source_task_dir or ""),
                    },
                )
            )
            imported_files.append(task_file_name)
        return self._project_reexecution_prepare_result(
            operation="import",
            changed_files=imported_files,
            skipped_files=skipped_files,
            deleted_session_files=deleted_session_files,
            execution_events=execution_events,
        )

    @staticmethod
    def inspect_reexecution_source_task_rows(task_dir: str | Path) -> dict[str, Any]:
        """Read source task-row payloads for controlled reexecution import.

        This is a task-runtime-owned read helper for Director resume.  Factory
        and bench entrypoints may discover candidate ``runtime/tasks``
        directories, but the task row JSON loading itself remains in the owner
        cell so raw ``task_*.json`` file layout knowledge does not leak into
        orchestration code.

        The helper is intentionally read-only and does not validate authority to
        import the rows.  Mutation remains in ``import_task_rows_for_reexecution``.

        Complexity:
            O(f + b) time over task row files and their JSON bodies, O(r) memory
            for accepted row payloads.
        """

        source_dir = Path(task_dir)
        if source_dir.name != "tasks" or not source_dir.is_dir():
            return {
                "task_rows": [],
                "task_files": [],
                "task_count": 0,
                "latest_mtime": 0.0,
            }
        try:
            task_files = sorted(
                path
                for path in source_dir.glob("task_*.json")
                if path.is_file() and not path.name.endswith(".session.json")
            )
        except OSError:
            task_files = []

        rows: list[dict[str, Any]] = []
        accepted_files: list[str] = []
        latest_mtime = 0.0
        for task_file in task_files:
            try:
                latest_mtime = max(latest_mtime, float(task_file.stat().st_mtime))
                payload = json.loads(task_file.read_text(encoding="utf-8"))
            except (OSError, TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                rows.append(dict(payload))
                accepted_files.append(task_file.name)
        return {
            "task_rows": rows,
            "task_files": accepted_files,
            "task_count": len(rows),
            "latest_mtime": latest_mtime,
        }

    @staticmethod
    def normalize_task_id(task_id: Any) -> int | None:
        token = str(task_id or "").strip()
        if not token:
            return None
        if token.isdigit():
            return int(token)
        match = _TASK_ID_PATTERN.match(token)
        if match:
            return int(match.group(1))
        return None

    def _task_entity_for_transition(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve the raw task entity required by execution transitions.

        Boundary:
            Execution finalization transitions need the persisted ``Task``
            entity for legacy fallback row projection when ``TaskBoard.update``
            returns ``None``.  Keep that raw owner-cell read centralized here;
            observable readers must continue using fact-overlaid task-row
            projections.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    def _task_entity_for_claim_execution(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve raw owner-cell task entity for claim/lease execution.

        Boundary:
            Claim execution owns the lease-backed transition from a raw task row
            into an execution session. This helper is the claim/lease owner-cell
            raw ``Task`` entity boundary: it normalizes caller input and performs
            the single ``TaskBoard.get`` lookup. It is not an execution
            finalization transition boundary; finalization paths must continue
            using ``_task_entity_for_transition``. Dependency-unblock refresh
            remains owned by ``claim_execution`` because it is a claim policy
            side effect, not a raw entity lookup concern. Observable readers
            must keep using fact-overlaid task-row projections.

        Complexity:
            O(k) to normalize the task-id token plus one O(1) in-memory
            ``TaskBoard`` lookup. Invalid ids return ``(None, None)``; missing
            rows return ``(normalized_id, None)`` so claim result shapes remain
            ``invalid_task_id`` / ``task_not_found``.

        Extension point:
            Future compare-and-swap or version checks for claim/lease ownership
            should attach here before session or lease mutation, keeping version
            validation local to the owner cell without changing downstream
            claim, renew, rejection, or execution-event semantics.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    def _task_entity_for_owner_terminal_transition(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve raw owner-cell task entity for row-only terminal transitions.

        Boundary:
            Owner-cell terminal row transitions without an execution lease need
            an O(1) raw ``TaskBoard.get`` pre-read to preserve missing-row
            ``None`` semantics.  Centralizing that boundary keeps future
            compare-and-swap/version checks local to the owner cell.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    def _task_entity_for_dependency_side_effect(self, task_id: Any) -> tuple[int | None, Task | None]:
        """Resolve raw owner-cell entity for dependency fan-out side effects.

        Boundary:
            Dependency fan-out belongs to ``runtime.task_runtime`` because it
            mutates sibling rows derived from ``blocked_by`` / ``blocks``. This
            helper is the owner-cell raw ``TaskBoard`` entity boundary for the
            pre-read before those row-local writes; observable readers must keep
            using fact-overlaid task-row projections.

        Complexity:
            O(k) to normalize the task-id token plus O(1) over the in-memory
            ``TaskBoard`` cache for one numeric row id. Missing rows return
            ``(normalized_id, None)`` so callers preserve legacy skip semantics.

        Extension point:
            Future compare-and-swap or version checks should attach here before
            fan-out writes, keeping version validation local to the owner cell
            without changing downstream update/write semantics.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None
        return normalized, self._board.get(normalized)

    @staticmethod
    def _task_row_payload_for_reexecution(payload: Mapping[str, Any]) -> dict[str, Any]:
        reset = dict(payload)
        blocked_by_raw = reset.get("blocked_by")
        if not isinstance(blocked_by_raw, list):
            blocked_by_raw = reset.get("blockedBy") if isinstance(reset.get("blockedBy"), list) else []
        blocked_by_source: list[Any] = blocked_by_raw if isinstance(blocked_by_raw, list) else []
        blocked_by = list(blocked_by_source)
        reset["blocked_by"] = blocked_by
        reset["blockedBy"] = list(blocked_by)
        reset["status"] = "blocked" if blocked_by else "pending"
        reset["claimed_by"] = None
        reset["assignee"] = ""
        reset["started_at"] = None
        reset["completed_at"] = None
        reset["claimed_at"] = None
        reset["result_summary"] = ""
        reset["error_message"] = None
        metadata_raw = reset.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        for key in _REEXECUTION_METADATA_DROP_KEYS:
            metadata.pop(key, None)
        reset["metadata"] = metadata
        return reset

    def _replace_task_row_for_reexecution(self, payload: Mapping[str, Any]) -> Task:
        task = Task.from_dict(self._task_row_payload_for_reexecution(payload))
        with self._board.transaction():
            self._board._cache[int(task.id)] = task
            self._board._save_task(task)
            if int(task.id) > self._board._load_max_id():
                self._board._save_max_id(int(task.id))
        return task

    def _delete_session_file(self, task_id: int) -> str:
        session_path = Path(resolve_runtime_path(self._workspace, self._session_logical_path(task_id)))
        with self._get_session_lock(task_id):
            if not session_path.is_file():
                return ""
            session_path.unlink()
        with self._session_locks_meta:
            self._session_locks.pop(task_id, None)
        return str(session_path)

    @staticmethod
    def _project_reexecution_prepare_result(
        *,
        operation: str,
        changed_files: list[str],
        skipped_files: list[str],
        deleted_session_files: list[str],
        execution_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        failed_events = [dict(event) for event in execution_events if not bool(event.get("ok"))]
        return {
            "success": not skipped_files and not failed_events,
            "operation": operation,
            "changed_files": list(changed_files),
            "reset_files": list(changed_files) if operation == "reset" else [],
            "imported_files": list(changed_files) if operation == "import" else [],
            "skipped_files": list(skipped_files),
            "deleted_session_files": list(deleted_session_files),
            "execution_events": [dict(event) for event in execution_events],
            "failed_execution_events": failed_events,
            "changed_count": len(changed_files),
            "skipped_count": len(skipped_files),
            "deleted_session_count": len(deleted_session_files),
        }

    def task_exists(self, task_id: Any) -> bool:
        """Return whether a task row exists in the observable read model.

        Boundary:
            Public existence check.  Resolves through
            :meth:`_resolve_observable_task_row` (the same observable
            projection that powers :meth:`get_task`) so callers consult the
            fact-overlaid read model instead of the raw ``TaskBoard`` row.
            ``normalize_task_id`` semantics are preserved (an unparseable
            id returns ``False``) and the read is a strict subset of the
            ``list_observable_task_rows`` walk, so no extra fact query or
            file scan is triggered beyond what the projection already
            performs.

        Complexity:
            O(r + f) time and memory, inherited from
            :meth:`list_observable_task_rows`.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return False
        return self._resolve_observable_task_row(normalized) is not None

    @staticmethod
    def _metadata_matches_external_task_id(metadata: dict[str, Any], external_id: str) -> bool:
        token = str(external_id or "").strip()
        if not token:
            return False
        for key in ("external_task_id", "pm_task_id", "source_task_id", "task_id"):
            if str(metadata.get(key) or "").strip() == token:
                return True
        return False

    def _get_task_by_external_task_id(self, external_id: str) -> dict[str, Any] | None:
        token = str(external_id or "").strip()
        if not token:
            return None
        for row in self.list_observable_task_rows():
            if not isinstance(row, dict):
                continue
            raw_metadata = row.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            if self._metadata_matches_external_task_id(metadata, token):
                return dict(row)
        return None

    @staticmethod
    def _execution_fact_factory_run_id(task_row: Mapping[str, Any]) -> str:
        """Return Factory run identity recorded by the latest execution fact."""

        metadata = task_row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        fact = metadata_map.get("task_runtime_execution_fact")
        fact_map = fact if isinstance(fact, Mapping) else {}
        return str(fact_map.get("factory_run_id") or "").strip()

    def create(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int | str = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        _raise_retired_entity_api("create", "create_task_row")

    def create_task_row(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int | str = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a task and return the runtime row projection with event evidence."""

        _task, row, execution_event, reverse_dependency_events = self._create_with_execution_event(
            subject=subject,
            description=description,
            blocked_by=blocked_by,
            priority=priority,
            owner=owner,
            assignee=assignee,
            tags=tags,
            estimated_hours=estimated_hours,
            metadata=metadata,
        )
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(execution_event, *reverse_dependency_events),
        )

    def _create_with_execution_event(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int | str = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Task, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        task = self._board.create(
            subject=subject,
            description=description,
            blocked_by=blocked_by,
            priority=priority,
            owner=owner,
            assignee=assignee,
            tags=tags,
            estimated_hours=estimated_hours,
            metadata=metadata,
        )
        row = self._augment_task_row(task.to_dict())
        execution_event = self._append_execution_event(
            "created",
            task_row=row,
            session=None,
            details={"source": "runtime.task_runtime.create"},
        )
        reverse_dependency_events = self._apply_reverse_dependency_links(
            created_task_id=int(task.id),
            blocker_ids=self._row_blocker_ids(row),
        )
        return task, row, execution_event, reverse_dependency_events

    def ensure_task_row(
        self,
        *,
        external_task_id: str,
        subject: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
        priority: int | str = 1,
    ) -> dict[str, Any]:
        """Materialize a legacy/orchestration task into the canonical taskboard."""
        external_id = str(external_task_id or "").strip()
        if not external_id:
            raise ValueError("external_task_id is required")

        existing = self._get_task_by_external_task_id(external_id)
        if isinstance(existing, dict):
            return existing

        safe_subject = str(subject or "").strip() or external_id
        safe_description = str(description or "").strip()
        created_metadata = dict(metadata or {})
        created_metadata.setdefault("external_task_id", external_id)
        created_metadata.setdefault("source_task_id", external_id)
        created_metadata.setdefault("materialized_by", "runtime.task_runtime")
        created_metadata.setdefault("materialized_at", utc_now_iso())

        _, row, created_event, reverse_dependency_events = self._create_with_execution_event(
            subject=safe_subject,
            description=safe_description,
            priority=priority,
            metadata=created_metadata,
        )
        execution_event = self._append_execution_event(
            "materialized",
            task_row=row,
            session=None,
            details={"external_task_id": external_id},
        )
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(created_event, *reverse_dependency_events, execution_event),
        )

    def bind_task_to_factory_run(
        self,
        command: BindRuntimeTaskToFactoryRunCommandV1,
    ) -> RuntimeTaskFactoryRunBindingResultV1:
        """Bind an existing task row to one Factory run with fact evidence.

        ``ensure_task_row`` remains creation-only. This explicit boundary owns
        write-once binding, conflict detection, and recovery when a prior row
        write succeeded but its execution-fact append did not.

        Complexity:
            O(r + f + n) time and O(r + f + n) memory for observable lookup
            plus one O(n) row compare-and-set, where ``n`` is row size.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            BindRuntimeTaskToFactoryRunCommandV1,
        )

        if not isinstance(command, BindRuntimeTaskToFactoryRunCommandV1):
            raise TypeError("command must be BindRuntimeTaskToFactoryRunCommandV1")
        if Path(command.workspace).resolve() != Path(self.workspace).resolve():
            raise ValueError("command workspace must match TaskRuntimeService workspace")

        observable_row = self._resolve_observable_task_row(command.task_id)
        if observable_row is None:
            return _build_factory_run_binding_result(
                ok=False,
                code="task_not_found",
                reason="TaskRuntime row does not exist",
                workspace=self.workspace,
                task_id=command.task_id,
                factory_run_id=command.factory_run_id,
            )
        normalized_task_id = self.normalize_task_id(observable_row.get("id"))
        if normalized_task_id is None:
            return _build_factory_run_binding_result(
                ok=False,
                code="task_not_found",
                reason="TaskRuntime row has no canonical numeric identity",
                workspace=self.workspace,
                task_id=command.task_id,
                factory_run_id=command.factory_run_id,
                task_row=observable_row,
            )

        fact_factory_run_id = self._execution_fact_factory_run_id(observable_row)
        if fact_factory_run_id and fact_factory_run_id != command.factory_run_id:
            return _build_factory_run_binding_result(
                ok=False,
                code="factory_run_binding_conflict",
                reason="TaskRuntime execution fact is bound to another Factory run",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=fact_factory_run_id,
                task_row=observable_row,
            )
        try:
            mutation = self._board.bind_factory_run_id(
                normalized_task_id,
                command.factory_run_id,
            )
        except TaskFactoryRunBindingConflictError as exc:
            return _build_factory_run_binding_result(
                ok=False,
                code="factory_run_binding_conflict",
                reason=str(exc),
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=exc.existing_factory_run_id,
                task_row=observable_row,
            )
        if mutation is None:
            return _build_factory_run_binding_result(
                ok=False,
                code="task_not_found",
                reason="TaskRuntime row disappeared before Factory run binding",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
            )

        row = self._augment_task_row(mutation.task.to_dict())
        if not mutation.row_updated and fact_factory_run_id == command.factory_run_id:
            return _build_factory_run_binding_result(
                ok=True,
                code="factory_run_already_bound",
                reason="Factory run binding already has execution-fact evidence",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=command.factory_run_id,
                event_recorded=True,
                idempotent=True,
                task_row=row,
            )

        execution_event = self._append_execution_event(
            "factory_run_bound",
            task_row=row,
            session=None,
            details={
                "factory_run_id": command.factory_run_id,
                "previous_factory_run_id": mutation.previous_factory_run_id,
                "row_updated": mutation.row_updated,
            },
        )
        event_recorded = bool(execution_event.get("fact_event_id"))
        if execution_event.get("ok") is not True or not event_recorded:
            return _build_factory_run_binding_result(
                ok=False,
                code="execution_event_append_failed",
                reason="Factory run binding did not reach the execution fact stream",
                workspace=self.workspace,
                task_id=str(normalized_task_id),
                factory_run_id=command.factory_run_id,
                existing_factory_run_id=command.factory_run_id,
                row_updated=mutation.row_updated,
                event_recorded=event_recorded,
                task_row=row,
                execution_event=execution_event,
            )

        return _build_factory_run_binding_result(
            ok=True,
            code="factory_run_bound" if mutation.row_updated else "factory_run_binding_recovered",
            reason=(
                "Factory run binding persisted and recorded"
                if mutation.row_updated
                else "Factory run binding execution-fact evidence recovered"
            ),
            workspace=self.workspace,
            task_id=str(normalized_task_id),
            factory_run_id=command.factory_run_id,
            existing_factory_run_id=command.factory_run_id,
            row_updated=mutation.row_updated,
            event_recorded=True,
            task_row=row,
            execution_event=execution_event,
        )

    def get(self, task_id: Any) -> Task | None:
        _raise_retired_entity_api("get", "get_task")

    def get_task(self, task_id: Any) -> dict[str, Any] | None:
        """Return the task-runtime observable read model for a single task row.

        This is a public read projection that must surface the
        ``task_runtime.execution`` fact overlay, not raw ``TaskBoard`` state.
        It derives the returned row from ``list_observable_task_rows()`` so
        callers always observe the converged fact-overlaid read model.

        Lookup order preserves the historical external-token priority: an
        external id (matching ``external_task_id`` / ``pm_task_id`` /
        ``source_task_id`` / ``task_id`` metadata aliases on any observable
        row) wins over numeric id matching. Numeric id matching then falls
        back to ``normalize_task_id(row.get("id"))`` against the same
        observable set.

        Boundary:
            Read-only. Never writes to workspace, never mints events, never
            consults ``self._board`` directly. ``ensure_task_row()`` keeps
            using ``_get_task_by_external_task_id()`` for creation
            idempotency so this change does not affect that path.
        """
        return self._resolve_observable_task_row(task_id)

    def _resolve_observable_task_row(self, task_id: Any) -> dict[str, Any] | None:
        """Resolve one task row from the observable read model.

        Helper extracted from :meth:`get_task` so the read projection can be
        reused without reintroducing raw ``TaskBoard`` access. Walks the
        observable rows once, attempting external-token matching first and
        numeric-id matching second; returns the first match as the
        fact-overlaid row.
        """
        try:
            observable_rows = self.list_observable_task_rows()
        except ValueError as exc:
            logger.warning(
                "Failed to load observable task rows for get_task lookup: %s",
                exc,
            )
            return None

        external_token = str(task_id or "").strip()
        if external_token:
            for row in observable_rows:
                if not isinstance(row, dict):
                    continue
                raw_metadata = row.get("metadata")
                metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
                if self._metadata_matches_external_task_id(metadata, external_token):
                    return dict(row)

        normalized = self.normalize_task_id(task_id)
        if normalized is not None:
            for row in observable_rows:
                if not isinstance(row, dict):
                    continue
                if self.normalize_task_id(row.get("id")) == normalized:
                    return dict(row)

        return None

    def update(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        _raise_retired_entity_api("update", "update_task_row")

    def update_task_row(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a task and return the runtime row projection with event evidence."""

        _, row, execution_event = self._update_with_execution_event(
            task_id,
            status=status,
            assignee=assignee,
            owner=owner,
            blocked_by=blocked_by,
            metadata=metadata,
        )
        if row is None:
            return None
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=(execution_event,) if execution_event is not None else (),
        )

    def _update_with_execution_event(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        assignee: str | None = None,
        owner: str | None = None,
        blocked_by: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Task | None, dict[str, Any] | None, dict[str, Any] | None]:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None, None
        if _is_terminal_task_row_update_status(status):
            raise RuntimeError(
                "terminal_task_status_requires_task_runtime_owner_transition:"
                f"{str(status.value if isinstance(status, TaskStatus) else status).strip().lower()}"
            )
        if _is_execution_task_row_update_status(status):
            raise RuntimeError(
                "execution_task_status_requires_task_runtime_owner_transition:"
                f"{str(status.value if isinstance(status, TaskStatus) else status).strip().lower()}"
            )
        updated = self._board.update(
            normalized,
            status=status,
            assignee=assignee,
            owner=owner,
            blocked_by=blocked_by,
            metadata=metadata,
            allow_dependency_status=True,
        )
        if updated is None:
            return None, None, None
        row = self._augment_task_row(updated.to_dict())
        execution_event = self._append_execution_event(
            "updated",
            task_row=row,
            session=None,
            details={
                "status": str(status.value if isinstance(status, TaskStatus) else status or ""),
                "assignee": str(assignee or ""),
                "owner": str(owner or ""),
                "metadata_updated": metadata is not None,
            },
        )
        return updated, row, execution_event

    def update_task(
        self,
        task_id: Any,
        *,
        status: TaskStatus | str | None = None,
        metadata: dict[str, Any] | None = None,
        assignee: str | None = None,
        owner: str | None = None,
    ) -> Task | None:
        _raise_retired_entity_api("update_task", "update_task_row")

    def reopen(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Task | None:
        _raise_retired_entity_api("reopen", "reopen_task_row")

    def reopen_task_row(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Reopen a task and return the runtime row projection with event evidence."""

        _task, row, execution_event, downstream_events, blocker = self._reopen_with_execution_event(
            task_id,
            reason=reason,
            metadata=metadata,
        )
        if blocker is not None:
            normalized = self.normalize_task_id(task_id)
            if normalized is None:
                return None
            return self._directed_effect_inactive_block_record(normalized, blocker)
        if row is None:
            return None
        execution_events = ((execution_event,) if execution_event is not None else ()) + tuple(downstream_events)
        return project_task_row_execution_event(
            row,
            execution_event,
            execution_events=execution_events,
        )

    def fail_task_row_after_rework_exhausted(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
        source: str = "task_rework",
    ) -> dict[str, Any] | None:
        """Fail a terminal task after sanctioned rework retries are exhausted.

        This is the owner-cell transition for QA or task-boundary flows that
        first reopen a completed task for rework accounting, then determine
        that no more retries are allowed.  Callers must not compose this from
        ``reopen_task_row()`` plus ``update_task_row(status="failed")`` because
        that second step would be a sessionless terminal row update.

        Complexity:
            O(d) time and memory for dependency reblock projection, where d is
            the number of downstream rows blocked by the task.
        """

        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None

        _task, _reopened_row, reopened_event, downstream_events, blocker = self._reopen_with_execution_event(
            normalized,
            reason=reason,
            metadata=metadata,
        )
        if blocker is not None:
            return self._directed_effect_inactive_block_record(normalized, blocker)
        if _reopened_row is None:
            return None

        session = self._read_session(normalized)
        failure_reason = sanitize_summary(reason or "rework_retry_exhausted")
        if session is not None:
            session.mark_failed(error=failure_reason)
            self._write_session(session, allow_terminal_downgrade=True)

        updated = self._board.update(
            normalized,
            status=TaskStatus.FAILED,
            metadata=metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        failed_event = self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={
                "reason": failure_reason,
                "source": sanitize_summary(source or "task_rework"),
                "rework_exhausted": True,
            },
        )
        execution_events = tuple(
            event for event in (reopened_event, *downstream_events, failed_event) if event is not None
        )
        return project_task_row_execution_event(
            row,
            failed_event,
            execution_events=execution_events,
        )

    def cancel_task_row_for_deduplication(
        self,
        task_id: Any,
        *,
        primary_task_id: Any,
        reason: str = "duplicate_task",
        metadata: dict[str, Any] | None = None,
        source: str = "task_deduplication",
    ) -> dict[str, Any] | None:
        """Cancel a duplicate task row through the task-runtime owner.

        PM planning can discover duplicate pending task contracts after rows
        already exist.  The dedupe decision is row lifecycle metadata, not an
        execution result, but entering a terminal row state must still be owned
        by ``TaskRuntimeService`` so observers receive a first-class
        ``cancelled`` execution fact rather than a generic ``updated`` event.

        Complexity:
            O(1) task-row/session work for the target duplicate row.
        """

        normalized, task = self._task_entity_for_owner_terminal_transition(task_id)
        if normalized is None or task is None:
            return None

        cancel_reason = sanitize_summary(reason or "duplicate_task")
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return self._directed_effect_inactive_block_record(
                        normalized,
                        pre_barrier,
                    )
            if session is not None and not is_terminal_session_status(session.status):
                session.mark_suspended(reason=cancel_reason, resumable=False)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )

        merged_metadata = {
            "dedup_merged_into": primary_task_id,
            "dedup_reason": cancel_reason,
            "dedup_source": sanitize_summary(source or "task_deduplication"),
        }
        merged_metadata.update(dict(metadata or {}))
        if session is not None:
            merged_metadata = self._build_runtime_metadata(
                session=session,
                effective_status="cancelled",
                resume_state="",
                extra_metadata=merged_metadata,
            )

        updated = self._board.update(
            normalized,
            status=TaskStatus.CANCELLED,
            metadata=merged_metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        cancelled_event = self._append_execution_event(
            "cancelled",
            task_row=row,
            session=session,
            details={
                "reason": cancel_reason,
                "source": sanitize_summary(source or "task_deduplication"),
                "dedup_merged_into": str(primary_task_id or "").strip(),
            },
        )
        return project_task_row_execution_event(
            row,
            cancelled_event,
            execution_events=(cancelled_event,) if cancelled_event is not None else (),
        )

    def fail_task_row_from_role_adapter(
        self,
        task_id: Any,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
        role_id: str = "",
        source: str = "role_adapter",
        failure_class: str = "",
    ) -> dict[str, Any] | None:
        """Fail a task row because its owning role adapter failed.

        Role adapters may fail before a Director-style execution lease exists,
        for example PM contract validation or PM runtime exceptions.  Those
        terminal row transitions still belong to ``TaskRuntimeService`` so the
        failure appears as a first-class ``task_runtime.execution`` fact rather
        than a generic ``updated`` row event.

        Complexity:
            O(1) task-row/session work for the target row.
        """

        normalized, task = self._task_entity_for_owner_terminal_transition(task_id)
        if normalized is None or task is None:
            return None

        failure_reason = sanitize_summary(reason or "role_adapter_failed")
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return self._directed_effect_inactive_block_record(
                        normalized,
                        pre_barrier,
                    )
            if session is not None and not is_terminal_session_status(session.status):
                session.mark_failed(error=failure_reason)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )

        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("role_adapter_failure_reason", failure_reason)
        if role_id:
            merged_metadata.setdefault("role_adapter_failure_role", sanitize_summary(role_id))
        if failure_class:
            merged_metadata.setdefault("role_adapter_failure_class", sanitize_summary(failure_class))
        if session is not None:
            merged_metadata = self._build_runtime_metadata(
                session=session,
                effective_status="failed",
                resume_state="",
                extra_metadata=merged_metadata,
            )

        updated = self._board.update(
            normalized,
            status=TaskStatus.FAILED,
            metadata=merged_metadata,
            allow_terminal_status=True,
        )
        if updated is None:
            return None

        row = self._augment_task_row(updated.to_dict())
        failed_event = self._append_execution_event(
            "failed",
            task_row=row,
            session=session,
            details={
                "reason": failure_reason,
                "source": sanitize_summary(source or "role_adapter"),
                "role": sanitize_summary(role_id),
                "failure_class": sanitize_summary(failure_class),
            },
        )
        return project_task_row_execution_event(
            row,
            failed_event,
            execution_events=(failed_event,) if failed_event is not None else (),
        )

    def _reopen_with_execution_event(
        self,
        task_id: Any,
        *,
        reason: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[
        Task | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        list[dict[str, Any]],
        DirectedEffectSettlementPreBarrierVerdictV1 | None,
    ]:
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return None, None, None, [], None
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            session = self._read_session_locked(normalized)
            if session is not None:
                pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
                if not pre_barrier.allowed:
                    return None, None, None, [], pre_barrier
            if session is not None:
                session.mark_suspended(reason=reason or "task_reopened", resumable=True)
                self._write_session_locked(
                    session,
                    allow_terminal_downgrade=True,
                )
        task = self._board.reopen(
            normalized,
            reason=reason,
            metadata=metadata,
            allow_terminal_reopen=True,
        )
        if task is None:
            return None, None, None, [], None
        row = self._augment_task_row(task.to_dict())
        execution_event = self._append_execution_event(
            "reopened",
            task_row=row,
            session=session,
            details={"reason": sanitize_summary(reason or "task_reopened")},
        )
        downstream_events = self._apply_reopen_downstream_reblocks(
            reopened_task_id=normalized,
            dependent_ids=self._row_blocks_ids(row),
        )
        return task, row, execution_event, downstream_events, None

    def list_all(
        self,
        *,
        status: TaskStatus | None = None,
        owner: str | None = None,
        tag: str | None = None,
    ) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.list_all is retired; use list_task_rows()")

    def list_task_rows(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        """Return task rows through the execution compatibility path.

        Boundary:
            This method is a compatibility entrypoint, not the read-only
            observable task-row SSoT for status, UI, selection, or read-model
            consumers. It calls ``refresh_dependency_unblocks()`` before
            reading rows, so observation-only callers must use
            ``list_observable_task_rows()`` or a more specific projection helper
            when they need a side-effect-free task projection.

        Use this method only for execution/worker compatibility paths that
        require refresh-before-read behavior. Row construction still delegates
        to ``_list_file_task_rows(include_terminal=...)`` so the legacy
        file-backed filtering, sorting, and runtime augmentation semantics stay
        centralized there.
        """

        self.refresh_dependency_unblocks()
        return self._list_file_task_rows(include_terminal=include_terminal)

    def _list_file_task_rows(
        self,
        *,
        include_terminal: bool = True,
        augment_runtime_state: bool = True,
    ) -> list[dict[str, Any]]:
        """Return file-backed task rows without triggering ``refresh_dependency_unblocks``.

        This helper is the recursion-free source of truth for file-backed row
        projection: it preserves the existing ``include_terminal`` filtering and
        sort behaviour that ``list_task_rows`` relied on, while letting
        ``list_observable_task_rows`` and ``refresh_dependency_unblocks`` skip
        the recursive refresh side effect. Callers that already hold the
        execution-session file lock may disable runtime-state augmentation to
        avoid re-entering ``_read_session()`` from the same lock scope.

        Complexity:
            O(t log t) time where t is the number of file-backed rows.
        """

        rows: list[dict[str, Any]] = []
        for task in self._list_file_task_entities():
            row = task.to_dict()
            if augment_runtime_state:
                row = self._augment_task_row(row)
            status = str(row.get("status") or "").strip().lower()
            if (not include_terminal) and is_terminal_task_row_status(status):
                continue
            rows.append(row)
        rows.sort(key=self._row_sort_key)
        return rows

    def _transitional_task_row_read_model_rows(self) -> list[dict[str, Any]]:
        """Load transitional task-row read-model rows.

        This transitional read model loader combines file-backed TaskBoard rows
        with append-only ``task_runtime.execution`` fact rows and returns their
        observable projection. It is not a mutation, claim, or write API.

        Complexity:
            O(r + f) time and memory over file-backed rows and latest fact rows.
        """

        file_rows = self._list_file_task_rows()
        fact_rows = self.list_task_rows_from_execution_facts()
        return self._project_observable_task_rows(file_rows, fact_rows)

    def _fact_only_task_row_read_model_rows(self) -> list[dict[str, Any]]:
        """Load fact-only task-row read-model rows.

        Boundary:
            This helper reads only append-only ``task_runtime.execution`` fact
            rows and projects them through the shared observable row projector.
            It must not call file row/entity loaders, dependency refresh APIs,
            mutation APIs, claim/selection APIs, or session writers.

        Complexity:
            O(f) time and memory over latest fact rows.
        """

        fact_rows = self.list_task_rows_from_execution_facts()
        return self._project_observable_task_rows([], fact_rows)

    def task_row_read_model_fallback_coverage(self) -> dict[str, Any]:
        """Return structured read-only coverage for the transitional fallback.

        The projection reuses the same file row loader, execution-fact loader,
        and observable row overlay used by ``_transitional_task_row_read_model_rows``.
        It is intentionally side-effect free: it does not claim tasks, mutate
        sessions, append facts, or refresh dependency unblocks.

        Complexity:
            O(r + f + p) time and memory over file rows, fact rows, and projected rows.
        """

        file_rows = self._list_file_task_rows()
        fact_rows = self.list_task_rows_from_execution_facts()
        projected_rows = self._project_observable_task_rows(file_rows, fact_rows)

        file_row_ids = self._task_row_read_model_task_id_set(file_rows)
        fact_row_ids = self._task_row_read_model_task_id_set(fact_rows)
        file_row_ids_without_execution_fact = sorted(
            file_row_ids - fact_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        fact_row_ids_without_file_row = sorted(
            fact_row_ids - file_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )

        file_rows_count = len(file_rows)
        coverage_ratio = 1.0 if file_rows_count == 0 else len(file_row_ids & fact_row_ids) / file_rows_count

        return {
            "file_rows_count": file_rows_count,
            "fact_rows_count": len(fact_rows),
            "projected_rows_count": len(projected_rows),
            "file_row_ids_without_execution_fact": file_row_ids_without_execution_fact,
            "fact_row_ids_without_file_row": fact_row_ids_without_file_row,
            "coverage_ratio": coverage_ratio,
            "transitional_file_fallback_required": bool(file_row_ids_without_execution_fact),
        }

    def task_row_read_model_projection_parity_coverage(self) -> dict[str, Any]:
        """Return observable row parity coverage for fact-only cutover.

        Boundary:
            This is a read-only projection audit. It loads file-backed task rows
            and latest execution-fact rows through the existing read boundaries,
            then compares the current transitional observable projection with
            the future fact-only observable projection. It must not claim tasks,
            mutate sessions, append facts, refresh dependency unblocks, or write
            task rows.

        Complexity:
            O(r + f + p) time and memory over file rows, fact rows, and the two
            projected observable row sets.
        """

        file_rows = self._list_file_task_rows()
        fact_rows = self.list_task_rows_from_execution_facts()
        transitional_rows = self._project_observable_task_rows(file_rows, fact_rows)
        fact_only_rows = self._project_observable_task_rows([], fact_rows)

        transitional_rows_by_id = TaskRuntimeService._task_row_read_model_rows_by_task_id(
            transitional_rows,
            self._task_row_read_model_task_id,
        )
        fact_only_rows_by_id = TaskRuntimeService._task_row_read_model_rows_by_task_id(
            fact_only_rows,
            self._task_row_read_model_task_id,
        )
        transitional_row_ids = set(transitional_rows_by_id)
        fact_only_row_ids = set(fact_only_rows_by_id)
        shared_row_ids = transitional_row_ids & fact_only_row_ids

        transitional_only_row_ids = sorted(
            transitional_row_ids - fact_only_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        fact_only_row_ids_without_transitional = sorted(
            fact_only_row_ids - transitional_row_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        row_ids_with_projection_mismatch = sorted(
            (
                task_id
                for task_id in shared_row_ids
                if not TaskRuntimeService._task_row_read_model_rows_equal(
                    transitional_rows_by_id[task_id],
                    fact_only_rows_by_id[task_id],
                )
            ),
            key=self._task_row_read_model_task_id_sort_key,
        )

        parity_denominator = len(transitional_row_ids | fact_only_row_ids)
        if parity_denominator == 0:
            parity_ratio = 1.0
        else:
            parity_matches = len(shared_row_ids) - len(row_ids_with_projection_mismatch)
            parity_ratio = parity_matches / parity_denominator
        observable_projection_parity_ready = (
            not transitional_only_row_ids
            and not fact_only_row_ids_without_transitional
            and not row_ids_with_projection_mismatch
        )

        return {
            "transitional_rows_count": len(transitional_rows),
            "fact_only_rows_count": len(fact_only_rows),
            "transitional_only_row_ids": transitional_only_row_ids,
            "fact_only_row_ids": fact_only_row_ids_without_transitional,
            "row_ids_with_projection_mismatch": row_ids_with_projection_mismatch,
            "parity_ratio": parity_ratio,
            "observable_projection_parity_ready": observable_projection_parity_ready,
        }

    def projected_runtime_execution_session_fallback_coverage(self) -> dict[str, Any]:
        """Return structured coverage for projected runtime-execution sessions.

        This read model compares file-backed ``metadata.runtime_execution``
        projections with append-only execution-fact projections. It is strictly
        observational and must not refresh dependencies, append events, mutate
        task rows, claim work, or write execution sessions.

        Complexity:
            O(r + f) time and memory over file-backed and execution-fact rows.
        """

        file_rows = self._list_file_task_rows(
            include_terminal=True,
            augment_runtime_state=True,
        )
        fact_rows = self.list_task_rows_from_execution_facts()

        file_projected_session_rows = [
            row for row in file_rows if self._runtime_execution_session_from_projected_row(row) is not None
        ]
        fact_projected_session_rows = [
            row for row in fact_rows if self._runtime_execution_session_from_projected_row(row) is not None
        ]

        file_projected_session_task_ids = self._task_row_read_model_task_id_set(file_projected_session_rows)
        fact_projected_session_task_ids = self._task_row_read_model_task_id_set(fact_projected_session_rows)
        file_projected_session_task_ids_without_execution_fact = sorted(
            file_projected_session_task_ids - fact_projected_session_task_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )
        fact_projected_session_task_ids_without_file_row = sorted(
            fact_projected_session_task_ids - file_projected_session_task_ids,
            key=self._task_row_read_model_task_id_sort_key,
        )

        file_projected_session_rows_count = len(file_projected_session_rows)
        if file_projected_session_rows_count == 0:
            coverage_ratio = 1.0
        else:
            coverage_ratio = (
                len(file_projected_session_task_ids & fact_projected_session_task_ids)
                / file_projected_session_rows_count
            )

        return {
            "file_projected_session_rows_count": file_projected_session_rows_count,
            "fact_projected_session_rows_count": len(fact_projected_session_rows),
            "file_projected_session_task_ids_without_execution_fact": (
                file_projected_session_task_ids_without_execution_fact
            ),
            "fact_projected_session_task_ids_without_file_row": (fact_projected_session_task_ids_without_file_row),
            "coverage_ratio": coverage_ratio,
            "projected_session_file_fallback_required": bool(file_projected_session_task_ids_without_execution_fact),
        }

    def task_row_read_model_cutover_readiness(self) -> dict[str, Any]:
        """Return read-only readiness for future fact-only task-row cutover.

        Boundary:
            This projection only composes the task-row fallback, projected
            runtime-execution session fallback, and observable projection parity
            coverage read models. It must not call file row/entity loaders,
            refresh APIs, mutation APIs, or session writers directly; the
            underlying coverage methods remain the only data boundary for this
            readiness signal.

        Complexity:
            O(c) additional time and memory over the three coverage dictionaries,
            excluding the cost already owned by the delegated coverage methods.
        """

        task_row_read_model_fallback_coverage = self.task_row_read_model_fallback_coverage()
        task_row_read_model_projection_parity_coverage = self.task_row_read_model_projection_parity_coverage()
        projected_runtime_execution_session_fallback_coverage = (
            self.projected_runtime_execution_session_fallback_coverage()
        )

        task_row_file_fallback_required = bool(
            task_row_read_model_fallback_coverage.get("transitional_file_fallback_required")
        )
        projected_session_file_fallback_required = bool(
            projected_runtime_execution_session_fallback_coverage.get("projected_session_file_fallback_required")
        )
        observable_projection_parity_ready = bool(
            task_row_read_model_projection_parity_coverage.get("observable_projection_parity_ready")
        )

        blocking_reasons: list[str] = []
        if task_row_file_fallback_required:
            blocking_reasons.append("task_row_file_fallback_required")
        if projected_session_file_fallback_required:
            blocking_reasons.append("projected_session_file_fallback_required")
        if not observable_projection_parity_ready:
            blocking_reasons.append("observable_projection_parity_mismatch")

        return {
            "ready": (
                not task_row_file_fallback_required
                and not projected_session_file_fallback_required
                and observable_projection_parity_ready
            ),
            "blocking_reasons": blocking_reasons,
            "task_row_file_fallback_required": task_row_file_fallback_required,
            "projected_session_file_fallback_required": projected_session_file_fallback_required,
            "observable_projection_parity_ready": observable_projection_parity_ready,
            "task_row_read_model_fallback_coverage": task_row_read_model_fallback_coverage,
            "task_row_read_model_projection_parity_coverage": task_row_read_model_projection_parity_coverage,
            "projected_runtime_execution_session_fallback_coverage": (
                projected_runtime_execution_session_fallback_coverage
            ),
        }

    def _dependency_status_read_model_rows(self) -> list[dict[str, Any]]:
        """Load transitional dependency-status read-model rows.

        This helper is the dependency-status loader seam for the transitional
        read model and can be replaced when the file-backed fallback is removed.
        It is not a mutation API and does not authorize task claims, writes, or
        dependency transitions.

        Complexity:
            O(r + f) time and memory over file-backed rows and latest fact rows.
        """

        return self._transitional_task_row_read_model_rows()

    def _fact_overlaid_dependency_status_rows(self) -> dict[int, TaskStatus]:
        """Return ``task_id -> TaskStatus`` using the fact-overlay-aware read model.

        This projection consumes ``_dependency_status_read_model_rows`` so the
        transitional row loading is isolated behind one helper. It does not call
        ``list_task_rows`` (which triggers ``refresh_dependency_unblocks``) or
        ``list_observable_task_rows`` (which is the external read-only
        projection API). Callers that need to mutate persisted tasks still
        iterate the ``TaskBoard.list_all()`` output; this helper only provides
        the status anchor they should consult for
        dependency decisions.

        Unknown or non-terminal fact statuses fall back to the file-backed
        status so that the dependency decision matches what a downstream
        caller would observe.
        """

        overlay_source = self._dependency_status_read_model_rows()

        status_by_id: dict[int, TaskStatus] = {}
        for row in overlay_source:
            task_id = self.normalize_task_id(row.get("id"))
            if task_id is None:
                continue
            status_token = str(row.get("status") or "").strip().lower()
            if not status_token:
                continue
            try:
                status_by_id[task_id] = TaskStatus(status_token)
            except ValueError:
                logger.debug(
                    "Skipping unknown dependency status token from overlay for task_id=%s: %r",
                    task_id,
                    status_token,
                )
                continue
        return status_by_id

    def _project_execution_fact_event_row(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        """Project one FactStream event into the task-runtime row read model.

        The event wrapper may carry the canonical append sequence separately
        from the payload.  This helper applies the same seq carry-forward rule
        for every fact-derived TaskRuntime read model so direct lookup,
        observable rows, and list projections cannot drift.
        """

        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        fact = dict(payload)
        fact.setdefault("event_id", str(event.get("event_id") or ""))
        fact.setdefault("occurred_at", str(event.get("occurred_at") or event.get("timestamp") or ""))
        if _coerce_fact_event_seq(fact.get("fact_event_seq")) is None:
            wrapper_seq = _coerce_fact_event_seq(event.get("seq"))
            if wrapper_seq is not None:
                fact["fact_event_seq"] = wrapper_seq
        return project_task_row_from_execution_fact_payload(fact)

    def _query_execution_fact_events(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> FactStreamQueryResultV1:
        """Query the task-runtime execution FactStream through one local gateway."""

        return query_fact_events(
            QueryFactEventsV1(
                workspace=self.workspace,
                stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                limit=limit,
                offset=offset,
            )
        )

    def list_task_rows_from_execution_facts(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Return latest task-row read models from ``task_runtime.execution`` facts.

        Boundary:
            This is a read projection only. It does not authorize claims,
            writes, or dependency transitions. Transitional claim paths must
            continue to use the row/session APIs until the storage owner is fully
            event-sourced.

        The queried FactStream event wrapper exposes the canonical
        ``FactEventAppendedV1.appended_seq`` value as the ``seq`` field on
        the event envelope. When the persisted fact payload lacks a valid
        positive ``fact_event_seq`` field, the wrapper's ``seq`` is copied
        onto the payload before projection so the read-only
        ``project_task_row_from_execution_fact_payload`` can expose it as
        the row-level ``fact_event_seq`` marker. The seq is never
        fabricated — payloads that already carry a valid positive
        ``fact_event_seq`` keep that value, and missing/invalid seq values
        cause the top-level field to be omitted entirely.

        Complexity:
            O(e + t log t) time over queried events and projected tasks, O(t)
            memory for latest-by-task rows.
        """

        event_limit = max(1, int(limit))
        try:
            result = self._query_execution_fact_events(limit=event_limit)
            if result.total > len(result.events):
                latest_offset = max(0, int(result.total) - event_limit)
                if latest_offset:
                    result = self._query_execution_fact_events(limit=event_limit, offset=latest_offset)
        except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("failed to load task runtime execution fact rows: %s", exc)
            return []
        latest_by_task: dict[str, dict[str, Any]] = {}
        for event in result.events:
            if not isinstance(event, dict):
                continue
            row = self._project_execution_fact_event_row(event)
            if row is None:
                continue
            task_id = str(row.get("task_id") or row.get("id") or "").strip()
            if task_id:
                latest_by_task[task_id] = row
        rows = [
            row
            for row in latest_by_task.values()
            if str(row.get("execution_state") or row.get("status") or "").strip().lower() != "removed"
        ]
        rows.sort(key=self._row_sort_key)
        return rows

    def _find_latest_execution_fact_row_for_task(
        self,
        task_id: int,
        *,
        page_size: int = 500,
    ) -> dict[str, Any] | None:
        """Return the latest fact-derived row for one ``task_id`` via exact paging.

        Boundary:
            Read-only projection that locates the authoritative
            ``task_runtime.execution`` fact for a single task.  It must not
            write to workspace, mint new events, or fabricate session state.
            Direct ``claim_execution(task_id=...)`` callers use the returned row
            to detect a terminal verdict that the stale file row may not yet
            reflect, so we page backward through the fact stream until we find
            a matching task_id rather than scanning a single latest window.

        The projection reuses ``project_task_row_from_execution_fact_payload``
        and the wrapper-level ``seq`` carry-forward logic from
        ``list_task_rows_from_execution_facts`` so the read model stays
        consistent with the public list read model.  We also reuse
        ``_coerce_fact_event_seq`` to avoid inventing a second inconsistent
        projection for ``fact_event_seq``.

        Complexity:
            O(e * p) time where ``e`` is the events inspected per page and
            ``p`` is the number of pages walked before either finding the task
            id or exhausting the stream; O(1) additional memory.
        """

        normalized_id = self.normalize_task_id(task_id)
        if normalized_id is None:
            return None
        target_task_id = str(normalized_id).strip()
        if not target_task_id:
            return None

        per_page = max(1, int(page_size))
        try:
            first_page = self._query_execution_fact_events(limit=1, offset=0)
        except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug(
                "failed to inspect task runtime execution facts for task_id=%s: %s",
                target_task_id,
                exc,
            )
            return None

        total = int(getattr(first_page, "total", 0) or 0)
        if total <= 0:
            return None

        offset = max(0, total - per_page)
        while True:
            try:
                result = self._query_execution_fact_events(limit=per_page, offset=offset)
            except (FactStreamError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug(
                    "failed to load task runtime execution fact row for task_id=%s: %s",
                    target_task_id,
                    exc,
                )
                return None

            events = [event for event in list(getattr(result, "events", []) or []) if isinstance(event, dict)]
            for event in reversed(events):
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                payload_task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
                if payload_task_id != target_task_id:
                    continue
                row = self._project_execution_fact_event_row(event)
                if row is None:
                    return None
                if str(row.get("execution_state") or row.get("status") or "").strip().lower() == "removed":
                    return None
                return row

            if offset <= 0:
                return None
            offset = max(0, offset - per_page)

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        """Return task rows for read-only runtime projections.

        Boundary:
            This method is the task-runtime-owned read model for status,
            snapshot, and UI projection consumers. It performs a gated
            fact-only cutover: when ``task_row_read_model_cutover_readiness``
            reports ready, observable rows are projected only from append-only
            ``task_runtime.execution`` facts; otherwise the transitional
            file-backed fallback remains active. This does not change mutation,
            claim, selection, dependency-transition, or repair APIs. Execution
            paths that need to select or mutate work must continue to call the
            explicit row/session APIs.

        The implementation intentionally avoids ``refresh_dependency_unblocks``
        and ``list_task_rows`` so read-only observers cannot trigger dependency
        state writes. ``list_task_rows`` remains the compatibility entry point
        for callers that expect dependency unblocks to be refreshed before
        reading rows.

        Complexity:
            O(c) additional time and memory over cutover readiness selection,
            excluding delegated readiness checks; selected projection is O(f)
            when ready and O(r + f) while transitional fallback is required.
        """

        readiness = self.task_row_read_model_cutover_readiness()
        if readiness.get("ready") is True:
            return self._fact_only_task_row_read_model_rows()
        return self._transitional_task_row_read_model_rows()

    def query_observable_task_rows_projection(self) -> ObservableTaskRowsProjectionV1:
        """Return observable rows together with their authority provenance.

        Completion, QA, and dependency-control consumers must require
        ``authoritative``. Compatibility and UI consumers may display degraded
        rows, but the source marker prevents them from becoming a second
        execution truth.

        Complexity:
            O(r + f) time and memory in degraded migration mode; O(f) when the
            fact-only cutover is ready.
        """

        from polaris.cells.runtime.task_runtime.public.contracts import (
            ObservableTaskRowsProjectionV1,
        )

        readiness = self.task_row_read_model_cutover_readiness()
        authoritative = readiness.get("ready") is True
        if authoritative:
            rows = self._fact_only_task_row_read_model_rows()
            source = "task_runtime.execution_fact"
        else:
            rows = self._transitional_task_row_read_model_rows()
            source = "task_runtime.transitional_file_fallback"
        return ObservableTaskRowsProjectionV1(
            workspace=self.workspace,
            source=source,
            authoritative=authoritative,
            degraded=not authoritative,
            rows=tuple(rows),
            readiness=readiness,
        )

    def _project_observable_task_rows(
        self,
        file_rows: list[dict[str, Any]],
        fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge file-backed rows with execution facts without I/O or writes.

        ``file_rows`` is the transitional TaskBoard projection and
        ``fact_rows`` is the append-only ``task_runtime.execution`` projection.
        The fact rows overlay matching file rows, while facts for tasks no
        longer present in files remain observable. Inputs are shallow-copied so
        callers can safely reuse their loaded rows after projection. This
        helper is the shared pure projection point for observable rows and
        dependency-status read-model synthesis; callers load their own inputs
        so mutation paths do not depend on external read APIs.

        Complexity:
            O(r + f) time and memory over file-backed rows and latest fact rows.
        """

        if not file_rows:
            return [dict(row) for row in fact_rows]
        if not fact_rows:
            return [dict(row) for row in file_rows]
        return self._overlay_execution_fact_rows(
            [dict(row) for row in file_rows],
            [dict(row) for row in fact_rows],
        )

    @staticmethod
    def _observable_row_task_id(row: dict[str, Any]) -> str:
        metadata_raw = row.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        return str(
            row.get("task_id")
            or row.get("id")
            or row.get("taskId")
            or metadata.get("task_id")
            or metadata.get("pm_task_id")
            or metadata.get("workflow_task_id")
            or ""
        ).strip()

    def _task_row_read_model_task_id(self, row: dict[str, Any]) -> str | None:
        raw_task_id = self._observable_row_task_id(row)
        if not raw_task_id:
            return None
        normalized_task_id = self.normalize_task_id(raw_task_id)
        if normalized_task_id is not None:
            return str(normalized_task_id)
        return raw_task_id

    def _task_row_read_model_task_id_set(self, rows: list[dict[str, Any]]) -> set[str]:
        return {task_id for row in rows if (task_id := self._task_row_read_model_task_id(row))}

    @staticmethod
    def _task_row_read_model_rows_by_task_id(
        rows: list[dict[str, Any]],
        task_id_for_row: Callable[[dict[str, Any]], str | None],
    ) -> dict[str, dict[str, Any]]:
        """Return task rows keyed by normalized read-model task id.

        Boundary:
            This pure helper does not load, mutate, or normalize rows itself; it
            only applies the provided task-id reader to already projected rows.

        Complexity:
            O(r) time and memory over the provided rows.
        """

        return {task_id: row for row in rows if (task_id := task_id_for_row(row))}

    @staticmethod
    def _task_row_read_model_rows_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
        """Return full row-dict equality for observable projection parity.

        Boundary:
            This pure helper compares already loaded row dictionaries only. It
            performs no projection, I/O, mutation, or lossy field normalization.

        Complexity:
            O(s) time over the nested row payload size.
        """

        return left == right

    def _task_row_read_model_task_id_sort_key(self, task_id: str) -> tuple[int, str]:
        normalized_task_id = self.normalize_task_id(task_id)
        if normalized_task_id is not None:
            return (0, f"{normalized_task_id:010d}")
        return (1, task_id)

    def _overlay_execution_fact_rows(
        self,
        rows: list[dict[str, Any]],
        fact_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project facts as authority and retain files only for uncovered tasks.

        A task row backed by ``task_runtime.execution`` must be projected from
        that fact without merging mutable file state back into it.  Merging the
        two representations made the transitional projection structurally
        different from the fact-only projection even when every task had a
        complete fact snapshot, permanently preventing the SSoT cutover.

        File rows remain a migration fallback solely for task ids that have no
        execution fact.  Once a fact exists, its complete ``task_row_snapshot``
        and event fields are the canonical observable representation.

        Complexity:
            O(r + f) time and memory over file rows and latest fact rows.
        """

        latest_by_task: dict[str, dict[str, Any]] = {
            task_id: dict(fact_row) for fact_row in fact_rows if (task_id := self._observable_row_task_id(fact_row))
        }
        if not latest_by_task:
            return rows

        overlaid: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            row_map = dict(row)
            task_id = self._observable_row_task_id(row_map)
            fact_row = latest_by_task.get(task_id)
            if not fact_row:
                overlaid.append(row_map)
                continue
            overlaid.append(dict(fact_row))
            seen.add(task_id)

        for task_id, fact_row in latest_by_task.items():
            if task_id not in seen:
                overlaid.append(fact_row)
        return overlaid

    def select_next_task(
        self,
        *,
        requested_task_id: Any = None,
        prefer_resumable: bool = True,
    ) -> dict[str, Any] | None:
        """Return the next claimable task row, preferring resumable work.

        Selection consumes the task-runtime-owned observable read model
        (``list_observable_task_rows``) so that ``task_runtime.execution``
        fact overlays can override stale file-backed status before the
        claimable filter is applied. Requested-task lookup also resolves
        from the observable rows for the same reason; if the latest fact
        says terminal/non-claimable, the requested row is rejected even if
        the underlying file row still looks pending.

        This is a deterministic preview API. Concurrent Director fanout must
        use ``claim_next_execution`` so selection and claim stay in one retryable
        operation.
        """
        self.refresh_dependency_unblocks()
        observable_rows = self.list_observable_task_rows()
        if requested_task_id:
            normalized_requested = self.normalize_task_id(requested_task_id)
            for row in observable_rows:
                if self.normalize_task_id(row.get("id")) == normalized_requested and self._is_row_claimable(row):
                    return row
            return None

        candidates = [row for row in observable_rows if self._is_row_claimable(row)]
        if not candidates:
            return None

        def _candidate_key(row: dict[str, Any]) -> tuple[int, int, float, int]:
            resume_state = str(row.get("resume_state") or "").strip().lower()
            resume_priority = 0 if prefer_resumable and resume_state == "resumable" else 1
            try:
                priority = -int(row.get("priority") or 0)
            except (RuntimeError, ValueError):
                # Malformed priority field - fallback to 0 (lowest priority)
                logger.debug("Task priority parse failed for task_id=%s, using 0", row.get("id"))
                priority = 0
            created_at = float(row.get("created_at") or 0.0)
            row_task_id = self.normalize_task_id(row.get("id")) or 10**9
            return (resume_priority, priority, created_at, row_task_id)

        candidates.sort(key=_candidate_key)
        return candidates[0]

    def claim_next_execution(
        self,
        *,
        worker_id: str,
        role_id: str,
        run_id: str = "",
        lease_ttl_seconds: int = 120,
        selection_source: str = "",
        prefer_resumable: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically select and claim the next executable task.

        Enumerates claimable candidates in priority order and attempts to claim
        each one. If a candidate has a lease_conflict, is terminal, or is blocked,
        the next candidate is tried. This eliminates the race window between
        ``select_next_task`` and ``claim_execution``.

        Returns:
            A dict with keys:
            - success (bool): Whether a task was successfully claimed
            - task (dict | None): The claimed task row, if successful
            - session (dict | None): The execution session, if successful
            - attempts (list[dict]): Details of each claim attempt
            - reason (str): Reason for failure (if success is False)
        """
        self.refresh_dependency_unblocks()
        observable_rows = self.list_observable_task_rows()
        candidates = [row for row in observable_rows if self._is_row_claimable(row)]
        if not candidates:
            return build_task_execution_claim_next_result(
                success=False,
                reason="no_claimable_tasks",
            )

        def _candidate_key(row: dict[str, Any]) -> tuple[int, int, float, int]:
            resume_state = str(row.get("resume_state") or "").strip().lower()
            resume_priority = 0 if prefer_resumable and resume_state == "resumable" else 1
            try:
                priority = -int(row.get("priority") or 0)
            except (RuntimeError, ValueError):
                logger.debug("Task priority parse failed for task_id=%s, using 0", row.get("id"))
                priority = 0
            created_at = float(row.get("created_at") or 0.0)
            row_task_id = self.normalize_task_id(row.get("id")) or 10**9
            return (resume_priority, priority, created_at, row_task_id)

        candidates.sort(key=_candidate_key)
        attempts: list[dict[str, Any]] = []

        for candidate in candidates:
            task_id = self.normalize_task_id(candidate.get("id"))
            if task_id is None:
                continue

            claim_result = self.claim_execution(
                task_id,
                worker_id=worker_id,
                role_id=role_id,
                run_id=run_id,
                lease_ttl_seconds=lease_ttl_seconds,
                selection_source=selection_source,
                metadata=metadata,
            )

            attempts.append(build_task_execution_claim_attempt(task_id=task_id, claim_result=claim_result))

            if claim_result.get("success"):
                claim_task = claim_result.get("task")
                claim_session = claim_result.get("session")
                result = build_task_execution_claim_next_result(
                    success=True,
                    reason="",
                    task_row=claim_task if isinstance(claim_task, dict) else None,
                    session=claim_session if isinstance(claim_session, dict) else None,
                    attempts=attempts,
                )
                attempt_record = claim_result.get("execution_attempt")
                if isinstance(attempt_record, dict):
                    result["execution_attempt"] = dict(attempt_record)
                return result

            # Continue to next candidate on lease_conflict, task_terminal, task_blocked
            reason = str(claim_result.get("reason") or "").strip()
            if reason in ("lease_conflict", "task_terminal", "task_blocked"):
                continue

            # For other failures (invalid_task_id, task_not_found), also continue
            continue

        return build_task_execution_claim_next_result(
            success=False,
            reason="all_candidates_unavailable",
            attempts=attempts,
        )

    def claim_execution(
        self,
        task_id: Any,
        *,
        worker_id: str,
        role_id: str,
        run_id: str = "",
        lease_ttl_seconds: int = 120,
        selection_source: str = "",
        external_task_id: str = "",
        context_summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Claim a task for execution and persist a lease-backed session.

        Direct ``claim_execution(task_id=...)`` paths must respect both
        terminal session evidence and the authoritative
        ``task_runtime.execution`` fact stream, not only the raw
        ``TaskBoard`` file row.  Before reading or mutating the raw row we
        refresh dependency unblocks so a child whose blocker is only complete
        in the latest fact becomes claimable.  Terminal session reconciliation
        remains the first terminal authority because it can repair stale rows;
        the latest terminal fact is the fail-closed fallback when no terminal
        session handled the row.
        """
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return build_task_execution_claim_result(success=False, reason="invalid_task_id")

        # Refresh dependency unblocks before reading/mutating the raw row so
        # a child whose blocker is only complete in the latest fact can be
        # claimed.  ``refresh_dependency_unblocks`` is idempotent and uses the
        # fact-overlay-aware status projection under the hood.
        self.refresh_dependency_unblocks()
        normalized, task = self._task_entity_for_claim_execution(task_id)
        if normalized is None:
            return build_task_execution_claim_result(success=False, reason="invalid_task_id")
        if task is None:
            return build_task_execution_claim_result(success=False, reason="task_not_found")

        latest_fact_row = self._find_latest_execution_fact_row_for_task(normalized)
        # The dependency projection may read task rows, execution facts, and
        # session-backed runtime state.  Take this fail-closed read snapshot
        # before the session RMW lock: re-entering the cooperative session
        # file lock from inside that critical section is not supported.
        dependencies_unresolved = self._task_has_unresolved_dependencies(task)
        existing_session: TaskExecutionSession | None = None
        session: TaskExecutionSession | None = None
        resume_from_previous = False
        claim_renewed = False
        rejection_reason = ""
        terminal_session_to_reconcile: TaskExecutionSession | None = None
        fact_terminal_rejection = False
        fact_status = ""

        # This is the sole read-modify-write critical section for a persisted
        # session claim. The locked helpers deliberately avoid reacquiring the
        # cooperative file lock, which would otherwise split the decision and
        # write across processes (or deadlock with non-reentrant file locks).
        with (
            self._get_session_lock(normalized),
            self._board._file_lock(self._session_file_lock_path(normalized)),
        ):
            existing_session = self._read_session_locked(normalized)
            if existing_session is not None:
                terminal_session_status = _terminal_task_status_for_session(existing_session.status)
                if terminal_session_status is not None:
                    if self._row_authorizes_retry_over_terminal_session(task, existing_session):
                        existing_session = self._rotate_terminal_session_for_retry_locked(existing_session)
                    else:
                        terminal_session_to_reconcile = existing_session

            if terminal_session_to_reconcile is None:
                if latest_fact_row is not None:
                    fact_status = str(latest_fact_row.get("status") or "").strip().lower()
                    if is_terminal_task_row_status(fact_status):
                        rejection_reason = "task_terminal"
                        fact_terminal_rejection = True
                if not rejection_reason and task.is_terminal:
                    rejection_reason = "task_terminal"
                if not rejection_reason and dependencies_unresolved:
                    rejection_reason = "task_blocked"

                if not rejection_reason:
                    active_session: TaskExecutionSession | None = None
                    if (
                        existing_session is not None
                        and existing_session.status == "active"
                        and not existing_session.is_expired(now=utc_now())
                    ):
                        active_session = existing_session
                    if active_session is not None:
                        same_owner = (
                            active_session.worker_id == str(worker_id or "").strip()
                            and active_session.role_id == str(role_id or "").strip()
                        )
                        if not same_owner:
                            rejection_reason = "lease_conflict"
                        else:
                            active_session.renew(
                                lease_ttl_seconds=lease_ttl_seconds,
                                context_summary=context_summary,
                            )
                            self._write_session_locked(active_session)
                            session = active_session
                            claim_renewed = True
                    else:
                        resume_from_previous = bool(
                            existing_session is not None
                            and existing_session.resumable
                            and (
                                existing_session.status == "suspended"
                                or (existing_session.status == "active" and existing_session.is_expired(now=utc_now()))
                            )
                        )
                        attempt = self._resolve_next_attempt(task, existing_session)
                        resume_count = (
                            int(existing_session.resume_count + 1) if resume_from_previous and existing_session else 0
                        )
                        session = TaskExecutionSession.create(
                            task_id=normalized,
                            role_id=role_id,
                            worker_id=worker_id,
                            run_id=run_id,
                            lease_ttl_seconds=lease_ttl_seconds,
                            attempt=attempt,
                            resume_count=resume_count,
                            origin="resume" if resume_from_previous else "claim",
                            selection_source=selection_source,
                            external_task_id=external_task_id
                            or str(task.metadata.get("external_task_id") or "").strip(),
                            context_summary=context_summary,
                            metadata={
                                "previous_session_id": (
                                    existing_session.session_id if existing_session is not None else ""
                                ),
                            },
                        )
                        self._write_session_locked(session)

        if terminal_session_to_reconcile is not None:
            row, reconcile_error, execution_event = self._apply_terminal_session_reconcile(
                normalized,
                session=terminal_session_to_reconcile,
                extra_metadata=metadata,
            )
            if row is None:
                row = self._augment_task_row(task.to_dict())
            result = build_task_execution_claim_result(
                success=False,
                reason="task_terminal",
                task_row=row,
                session=terminal_session_to_reconcile,
                reconciled_from_terminal_session=not reconcile_error,
                reconcile_error=reconcile_error,
                execution_event=execution_event,
            )
            return self._claim_result_with_execution_attempt(result, terminal_session_to_reconcile)

        if rejection_reason:
            task_row = (
                dict(latest_fact_row)
                if fact_terminal_rejection and latest_fact_row is not None
                else self._augment_task_row(task.to_dict())
            )
            result = build_task_execution_claim_result(
                success=False,
                reason=rejection_reason,
                task_row=task_row,
                session=existing_session if rejection_reason == "lease_conflict" else None,
            )
            if fact_terminal_rejection:
                result["execution_fact_authoritative"] = True
                result["source"] = "task_runtime.execution_fact"
                result["fact_status"] = fact_status
            return self._claim_result_with_execution_attempt(
                result,
                existing_session if rejection_reason == "lease_conflict" else None,
            )

        if session is None:
            raise RuntimeError("claim execution completed without a session decision")

        updated_task = self._board.update(
            normalized,
            status=TaskStatus.IN_PROGRESS,
            assignee=str(worker_id or "").strip(),
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="in_progress",
                resume_state=("resumed" if session.resume_count > 0 else ""),
                extra_metadata=metadata,
            ),
            allow_execution_status=True,
        )
        row = self._augment_task_row(updated_task.to_dict() if updated_task is not None else task.to_dict())
        execution_event = self._append_execution_event(
            "claim_renewed" if claim_renewed else "claimed",
            task_row=row,
            session=session,
            details={
                "selection_source": selection_source,
                "resumed": resume_from_previous,
            },
        )
        result = build_task_execution_claim_result(
            success=True,
            reason="claim_renewed" if claim_renewed else "claimed",
            task_row=row,
            session=session,
            resumed=session.resume_count > 0,
            claim_applied=True,
            execution_event=execution_event,
        )
        return self._claim_result_with_execution_attempt(result, session)

    def heartbeat_execution(
        self,
        task_id: Any,
        *,
        session_id: str,
        lease_ttl_seconds: int = 120,
        context_summary: str = "",
    ) -> dict[str, Any]:
        """Renew an existing task lease."""
        normalized = self.normalize_task_id(task_id)
        if normalized is None:
            return build_task_execution_heartbeat_result(success=False, reason="invalid_task_id")

        session_lock = self._get_session_lock(normalized)
        with session_lock:
            session = self._read_session(normalized)
            if session is None:
                return build_task_execution_heartbeat_result(success=False, reason="session_not_found")
            if str(session.session_id) != str(session_id or "").strip():
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_mismatch",
                    session=session,
                )
            if session.status != "active":
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_not_active",
                    session=session,
                )
            if session.is_expired(now=utc_now()):
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_lease_expired",
                    session=session,
                )

            session.renew(
                lease_ttl_seconds=lease_ttl_seconds,
                context_summary=context_summary,
            )
            session_written = self._write_session(session)
            if not session_written:
                row = self._reconcile_terminal_task_row(normalized, session=session)
                return build_task_execution_heartbeat_result(
                    success=False,
                    reason="session_terminal_preserved",
                    task_row=row,
                    session=session,
                )
        task = self._board.update(
            normalized,
            metadata=self._build_runtime_metadata(
                session=session,
                effective_status="in_progress",
                resume_state="resumed" if session.resume_count > 0 else "",
            ),
        )
        row = self._augment_task_row(task.to_dict()) if task is not None else self.get_task(normalized)
        event_row = row if isinstance(row, dict) else {"id": normalized, "status": "in_progress"}
        execution_event = self._append_execution_event(
            "heartbeat_renewed",
            task_row=event_row,
            session=session,
            details={
                "lease_ttl_seconds": lease_ttl_seconds,
                "context_summary": sanitize_summary(context_summary),
            },
        )
        return build_task_execution_heartbeat_result(
            success=True,
            reason="heartbeat_renewed",
            task_row=row,
            session=session,
            execution_event=execution_event,
        )

    def heartbeat_execution_attempt(
        self,
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        """Atomically renew one identity-fenced execution attempt within a deadline.

        The local per-session lock and its cooperative file lock are held from
        the authoritative session read through durable renewal and fact
        projection. This prevents a validate-then-renew race between a stale
        caller and a concurrent terminal transition.
        """

        identity = command.identity
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="workspace_mismatch",
                identity=identity,
                evidence_anchor={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )

        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        lock_acquired = session_lock.acquire(timeout=command.lock_timeout_seconds)
        if not lock_acquired:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="file_lock_timeout",
                identity=identity,
                evidence_anchor={
                    "lock_scope": "local_session",
                    "lock_timeout_seconds": command.lock_timeout_seconds,
                },
            )
        try:
            remaining_seconds = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining_seconds < 0:
                return self._execution_attempt_heartbeat_verdict(
                    success=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence_anchor={
                        "lock_scope": "local_session",
                        "lock_timeout_seconds": command.lock_timeout_seconds,
                    },
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining_seconds,
                ):
                    return self._heartbeat_execution_attempt_locked(command)
            except TaskBoardFileLockTimeoutError:
                return self._execution_attempt_heartbeat_verdict(
                    success=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence_anchor={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": command.lock_timeout_seconds,
                    },
                )
        finally:
            session_lock.release()

    def _heartbeat_execution_attempt_locked(
        self,
        command: HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        """Renew one attempt while both session locks are already held."""

        identity = command.identity
        session_path = self._session_logical_path(identity.task_id)
        session = self._read_session_locked(identity.task_id)
        if session is None:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_not_found",
                identity=identity,
                evidence_anchor={"session_path": session_path},
            )

        observed_identity = self._execution_attempt_identity_from_session(session)
        evidence_anchor: dict[str, Any] = {"observed_identity": observed_identity.to_record()}
        mismatch_code = self._execution_attempt_mismatch_code(identity, session)
        if mismatch_code is not None:
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code=mismatch_code,
                identity=identity,
                evidence_anchor=evidence_anchor,
            )
        if session.status != "active":
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_not_active",
                identity=identity,
                evidence_anchor=evidence_anchor,
            )
        if session.is_expired(now=utc_now()):
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_lease_expired",
                identity=identity,
                evidence_anchor=evidence_anchor,
            )

        session.renew(
            lease_ttl_seconds=command.lease_ttl_seconds,
            context_summary=command.context_summary,
        )
        if not self._write_session_locked(session):
            row = self._reconcile_terminal_task_row(identity.task_id, session=session)
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="session_terminal_preserved",
                identity=identity,
                evidence_anchor={
                    **evidence_anchor,
                    "task_row": dict(row or {}),
                    **self._session_write_receipt_details_for_session(session),
                },
            )

        try:
            task = self._board.update(
                identity.task_id,
                metadata=self._build_runtime_metadata(
                    session=session,
                    effective_status="in_progress",
                    resume_state="resumed" if session.resume_count > 0 else "",
                ),
            )
            row = (
                project_task_row_runtime_state(
                    task.to_dict(),
                    task_status_value=task.status.value,
                    session=session,
                    terminal_session_superseded=False,
                )
                if task is not None
                else None
            )
            if not isinstance(row, dict):
                raise RuntimeError("task_row_missing_after_heartbeat_renewal")
            execution_event = self._append_execution_event(
                "heartbeat_renewed",
                task_row=row,
                session=session,
                details={
                    "lease_ttl_seconds": command.lease_ttl_seconds,
                    "context_summary": sanitize_summary(command.context_summary),
                },
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                "TaskRuntime heartbeat projection failed after renewal: task_id=%s session_id=%s error=%s",
                identity.task_id,
                identity.session_id,
                exc,
            )
            return self._execution_attempt_heartbeat_verdict(
                success=False,
                code="row_projection_failed",
                identity=identity,
                evidence_anchor={
                    **evidence_anchor,
                    **self._session_write_receipt_details_for_session(session),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )

        renewed_identity = self._execution_attempt_identity_from_session(session)
        evidence_anchor.update(self._session_write_receipt_details_for_session(session))
        evidence_anchor.update(self._row_write_receipt_details_for_task(row))
        evidence_anchor["execution_event"] = dict(execution_event)
        return self._execution_attempt_heartbeat_verdict(
            success=True,
            code="heartbeat_renewed",
            identity=identity,
            renewed_identity=renewed_identity,
            evidence_anchor=evidence_anchor,
        )

    def _execution_attempt_heartbeat_verdict(
        self,
        *,
        success: bool,
        code: TaskRuntimeExecutionAttemptHeartbeatCodeV1,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        renewed_identity: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        evidence_anchor: Mapping[str, Any] | None = None,
    ) -> TaskRuntimeExecutionAttemptHeartbeatVerdictV1:
        """Build one detached typed heartbeat outcome at the owner boundary."""

        return TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=success,
            code=code,
            workspace=self.workspace,
            identity=identity,
            renewed_identity=renewed_identity,
            evidence_anchor=dict(evidence_anchor or {}),
        )

    @staticmethod
    def _execution_attempt_mismatch_code(
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        session: TaskExecutionSession,
        *,
        allow_terminal_settlement_lease: bool = False,
    ) -> (
        Literal[
            "session_task_mismatch",
            "session_mismatch",
            "attempt_mismatch",
            "role_mismatch",
            "worker_mismatch",
            "run_mismatch",
            "external_task_id_mismatch",
            "lease_version_mismatch",
        ]
        | None
    ):
        """Return the first stable identity mismatch for a locked session."""

        if session.task_id != identity.task_id:
            return "session_task_mismatch"
        if session.session_id != identity.session_id:
            return "session_mismatch"
        if session.attempt != identity.attempt:
            return "attempt_mismatch"
        if session.role_id != identity.role_id:
            return "role_mismatch"
        if session.worker_id != identity.worker_id:
            return "worker_mismatch"
        if session.run_id != identity.run_id:
            return "run_mismatch"
        if session.external_task_id != identity.external_task_id:
            return "external_task_id_mismatch"
        expected_lease_expires_at = session.lease_expires_at
        if allow_terminal_settlement_lease and session.status != "active":
            expected_lease_expires_at = str(
                session.metadata.get("settlement_identity_lease_expires_at") or expected_lease_expires_at
            ).strip()
        if expected_lease_expires_at != identity.lease_expires_at:
            return "lease_version_mismatch"
        return None

    def settle_execution_attempt(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> dict[str, Any]:
        """Settle one attempt through a session commit then lock-free projection.

        Lock order is fixed.  The bounded session lock pair (local lock then
        cooperative session-file lock) covers session reread, identity and
        lease validation, the strict DEO registry pre-barrier,
        terminal-transition-id persistence, and winner selection. It is
        released before TaskBoard, execution-fact append, dependency, or
        runtime.v2 projection. A distinct bounded projection lock serializes
        those idempotent effects without ever acquiring a session lock.
        """

        identity = command.identity
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            return self._execution_attempt_settlement_result(
                success=False,
                code="workspace_mismatch",
                command=command,
                evidence={"service_workspace": self.workspace},
            )
        normalized, task = self._task_entity_for_transition(identity.task_id)
        if normalized is None or task is None:
            return self._execution_attempt_settlement_result(
                success=False,
                code="session_not_found",
                command=command,
            )

        started_at = time.monotonic()
        session_lock = self._get_session_lock(normalized)
        if not session_lock.acquire(timeout=command.lock_timeout_seconds):
            return self._execution_attempt_settlement_result(
                success=False,
                code="file_lock_timeout",
                command=command,
                evidence={"lock_scope": "local_session"},
            )
        try:
            remaining = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                return self._execution_attempt_settlement_result(
                    success=False,
                    code="file_lock_timeout",
                    command=command,
                    evidence={"lock_scope": "local_session"},
                )
            try:
                with self._board._file_lock(self._session_file_lock_path(normalized), timeout_seconds=remaining):
                    self._after_directed_effect_linearization_lock(
                        "settlement",
                        identity,
                    )
                    locked_result, session = self._settle_execution_attempt_locked(command)
            except TaskBoardFileLockTimeoutError:
                return self._execution_attempt_settlement_result(
                    success=False,
                    code="file_lock_timeout",
                    command=command,
                    evidence={"lock_scope": "cooperative_session_file"},
                )
        finally:
            session_lock.release()

        if session is None:
            return locked_result
        return self._project_settled_execution_attempt(
            command,
            task=task,
            session=session,
        )

    def _settle_execution_attempt_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
    ) -> tuple[dict[str, Any], TaskExecutionSession | None]:
        """Select and persist the winner without projection or external I/O.

        The caller holds both session locks.  This method may only read or
        write the one session file and may strict-read the DEO registry through
        FactStream. It deliberately returns the persisted winner snapshot for
        the second, idempotent projection phase.
        """

        identity = command.identity
        session = self._read_session_locked(identity.task_id)
        if session is None:
            return self._execution_attempt_settlement_result(False, "session_not_found", command), None
        mismatch_code = self._execution_attempt_mismatch_code(
            identity,
            session,
            allow_terminal_settlement_lease=True,
        )
        if mismatch_code is not None:
            return self._execution_attempt_settlement_result(False, mismatch_code, command, session=session), None

        expected_status = command.outcome
        if session.status == "active":
            if session.is_expired(now=utc_now()):
                return self._execution_attempt_settlement_result(
                    False,
                    "session_lease_expired",
                    command,
                    session=session,
                ), None
            pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
            if not pre_barrier.allowed:
                return self._execution_attempt_settlement_result(
                    False,
                    cast(TaskRuntimeExecutionAttemptSettlementCodeV1, pre_barrier.code),
                    command,
                    session=session,
                    evidence={"directed_effect_pre_barrier": dict(pre_barrier.evidence)},
                ), None
            session.metadata["settlement_identity_lease_expires_at"] = identity.lease_expires_at
            if command.outcome == "completed":
                session.mark_completed(result_summary=command.summary)
            elif command.outcome == "failed":
                session.mark_failed(error=command.summary)
            else:
                session.mark_suspended(reason=command.summary, resumable=True)
            if not self._write_session_locked(session):
                return self._execution_attempt_settlement_result(
                    False,
                    "session_terminal_preserved",
                    command,
                    session=session,
                ), None
        elif session.status != expected_status:
            return self._execution_attempt_settlement_result(
                False,
                "terminal_outcome_conflict",
                command,
                session=session,
                evidence={"persisted_outcome": session.status},
            ), None
        elif not str(session.terminal_transition_id or "").strip():
            # Legacy settled sessions can be recovered only after their durable
            # projection key is persisted under the same identity fence.
            session.ensure_terminal_transition_id()
            if not self._write_session_locked(session):
                return self._execution_attempt_settlement_result(
                    False,
                    "session_terminal_preserved",
                    command,
                    session=session,
                ), None

        return self._execution_attempt_settlement_result(
            True,
            "settled",
            command,
            session=session,
        ), session

    def _project_settled_execution_attempt(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        task: Task,
        session: TaskExecutionSession,
    ) -> dict[str, Any]:
        """Apply winner-only, replay-safe TaskBoard and fact projections.

        Lock order is session locks, then no lock, then projection locks.  This
        phase must never acquire a session lock or call ``_augment_task_row``;
        it uses the phase-A snapshot exclusively.  A process crash after
        session persistence leaves the canonical winner recoverable: the
        terminal transition id deduplicates the fact and the TaskBoard receipt
        identifies a completed projection.
        """

        task_id = command.identity.task_id
        started_at = time.monotonic()
        projection_lock = self._get_settlement_projection_lock(task_id)
        if not projection_lock.acquire(timeout=command.lock_timeout_seconds):
            return self._execution_attempt_settlement_result(
                False,
                "file_lock_timeout",
                command,
                session=session,
                evidence={"lock_scope": "local_settlement_projection"},
            )
        try:
            remaining = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                return self._execution_attempt_settlement_result(
                    False,
                    "file_lock_timeout",
                    command,
                    session=session,
                    evidence={"lock_scope": "local_settlement_projection"},
                )
            with self._board._file_lock(
                self._settlement_projection_file_lock_path(task_id),
                timeout_seconds=remaining,
            ):
                return self._project_settled_execution_attempt_locked(
                    command,
                    task=task,
                    session=session,
                )
        except TaskBoardFileLockTimeoutError:
            return self._execution_attempt_settlement_result(
                False,
                "file_lock_timeout",
                command,
                session=session,
                evidence={"lock_scope": "cooperative_settlement_projection"},
            )
        finally:
            projection_lock.release()

    def _project_settled_execution_attempt_locked(
        self,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        task: Task,
        session: TaskExecutionSession,
    ) -> dict[str, Any]:
        """Project one settled snapshot while the independent projection lock is held."""

        projected_row = self._board.get(command.identity.task_id)
        if projected_row is not None:
            receipt = self._settlement_projection_receipt(projected_row.to_dict())
            if self._settlement_projection_receipt_matches(receipt, command=command, session=session):
                return self._execution_attempt_settlement_result(
                    True,
                    "settlement_idempotent",
                    command,
                    session=session,
                    idempotent=True,
                    evidence={"task": projected_row.to_dict(), "projection_receipt": receipt},
                )

        if command.outcome == "completed":
            status = TaskStatus.COMPLETED
            effective_status, resume_state, assignee = "completed", "", None
            details = {"result_summary": sanitize_summary(command.summary)}
        elif command.outcome == "failed":
            status = TaskStatus.FAILED
            effective_status, resume_state, assignee = "failed", "", None
            details = {"error": sanitize_summary(command.summary)}
        else:
            status = TaskStatus.BLOCKED
            effective_status, resume_state, assignee = "pending", "resumable", ""
            details = {"reason": sanitize_summary(command.summary)}
        try:
            updated = self._board.update(
                command.identity.task_id,
                status=status,
                assignee=assignee,
                metadata=self._build_runtime_metadata(
                    session=session,
                    effective_status=effective_status,
                    resume_state=resume_state,
                    extra_metadata=dict(command.metadata),
                ),
                allow_terminal_status=command.outcome in {"completed", "failed"},
                allow_execution_status=True,
            )
            if updated is None:
                raise RuntimeError("settled task row was not found during projection")
            row = self._augment_task_row_with_session(updated.to_dict(), session=session)
            event = self._append_execution_event(command.outcome, task_row=row, session=session, details=details)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return self._execution_attempt_settlement_result(
                False,
                "row_projection_failed",
                command,
                session=session,
                evidence={"error_type": type(exc).__name__, "error_message": str(exc)},
            )
        if not bool(event.get("ok")):
            return self._execution_attempt_settlement_result(
                False,
                "row_projection_failed",
                command,
                session=session,
                evidence={"task": row, "execution_event": event},
            )
        projection_receipt = {
            "terminal_transition_id": session.terminal_transition_id,
            "outcome": command.outcome,
            "fact_event_id": str(event.get("fact_event_id") or "").strip(),
            "fact_event_seq": event.get("fact_event_seq"),
        }
        try:
            receipt_row = self._board.update(
                command.identity.task_id,
                metadata={"task_runtime_settlement": projection_receipt},
                allow_terminal_status=command.outcome in {"completed", "failed"},
                allow_execution_status=True,
            )
            if receipt_row is None:
                raise RuntimeError("settled task row was not found while recording projection receipt")
            row = self._augment_task_row_with_session(receipt_row.to_dict(), session=session)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return self._execution_attempt_settlement_result(
                False,
                "row_projection_failed",
                command,
                session=session,
                evidence={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "execution_event": event,
                    "projection_receipt": projection_receipt,
                },
            )
        result = self._execution_attempt_settlement_result(
            True,
            "settled",
            command,
            session=session,
            idempotent=False,
            evidence={"task": row, "execution_event": event, "projection_receipt": projection_receipt},
        )
        if command.outcome == "completed":
            dependency_events = self._apply_dependency_completion_side_effects(
                completed_task_id=command.identity.task_id,
                dependent_rows_before=self._dependent_rows_blocked_by(command.identity.task_id),
            )
            result["dependency_events"] = dependency_events
        return result

    @staticmethod
    def _settlement_projection_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
        """Return the detached terminal-projection receipt stored on a task row."""

        metadata = row.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        receipt = metadata_map.get("task_runtime_settlement")
        return dict(receipt) if isinstance(receipt, Mapping) else {}

    @staticmethod
    def _settlement_projection_receipt_matches(
        receipt: Mapping[str, Any],
        *,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        session: TaskExecutionSession,
    ) -> bool:
        """Return whether a durable receipt proves this winner was fully projected."""

        return (
            str(receipt.get("terminal_transition_id") or "").strip()
            == str(session.terminal_transition_id or "").strip()
            and str(receipt.get("outcome") or "").strip() == command.outcome
            and bool(str(receipt.get("fact_event_id") or "").strip())
        )

    def _execution_attempt_settlement_result(
        self,
        success: bool,
        code: TaskRuntimeExecutionAttemptSettlementCodeV1,
        command: SettleTaskRuntimeExecutionAttemptCommandV1,
        *,
        session: TaskExecutionSession | None = None,
        idempotent: bool = False,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        verdict = TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=success,
            code=code,
            workspace=self.workspace,
            identity=command.identity,
            outcome=command.outcome,
            idempotent=idempotent,
            evidence=dict(evidence or {}),
        )
        result = verdict.to_record()
        if session is not None:
            result["session"] = session.to_dict()
        result.update(dict(evidence or {}))
        return result

    def suspend_active_executions_for_run(
        self,
        run_id: str,
        *,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Suspend every active task lease owned by an orchestration run.

        Factory and orchestration cancellation is not allowed to leave task
        leases active. The role kernel guard checks these leases immediately
        before tool execution; suspending here makes late LLM responses
        fail-closed instead of writing files after the run has been cancelled.

        Boundary:
            Raw ``TaskBoard`` entity reads are allowed here only because this
            method is the run-cancellation mutation owner. Cancellation must
            suspend matching execution sessions, update the persisted task rows,
            and append ``task_runtime.execution`` facts for each row mutation.
        """

        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return build_task_execution_bulk_suspend_result(
                success=False,
                reason="invalid_run_id",
                run_id=normalized_run_id,
            )

        suspended_rows: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        execution_events: list[dict[str, Any]] = []
        for task in self._list_file_task_entities():
            task_id = self.normalize_task_id(task.id)
            if task_id is None:
                continue
            reconcile_terminal_session = False
            session_lock = self._get_session_lock(task_id)
            with (
                session_lock,
                self._board._file_lock(self._session_file_lock_path(task_id)),
            ):
                suspend_result = self._suspend_active_session_for_run_locked(
                    task_id,
                    run_id=normalized_run_id,
                    reason=reason,
                )
                if suspend_result.blocker is not None:
                    failed.append(
                        self._directed_effect_inactive_block_record(
                            task_id,
                            suspend_result.blocker,
                        )
                    )
                    continue
                session = suspend_result.session
                if session is None:
                    continue

                if not suspend_result.session_written:
                    reconcile_terminal_session = True

            if reconcile_terminal_session:
                self._reconcile_terminal_task_row(task_id, session=session)
                continue

            task_row = task.to_dict()
            existing_metadata = dict(task_row.get("metadata") or {})
            updated = self._board.update(
                task_id,
                status=TaskStatus.BLOCKED,
                assignee="",
                metadata=self._build_runtime_metadata(
                    session=session,
                    effective_status="pending",
                    resume_state="resumable",
                    extra_metadata={
                        **existing_metadata,
                        **dict(metadata or {}),
                        "cancellation_run_id": normalized_run_id,
                        "cancellation_reason": str(reason or "").strip(),
                    },
                ),
            )
            if updated is None:
                failed.append({"task_id": task_id, "reason": "task_update_failed"})
                continue
            row = self._augment_task_row(updated.to_dict())
            suspended_rows.append(row)
            execution_events.append(
                self._append_execution_event(
                    "suspended",
                    task_row=row,
                    session=session,
                    details={
                        "reason": sanitize_summary(reason),
                        "run_id": normalized_run_id,
                        "source": "runtime.task_runtime.suspend_active_executions_for_run",
                    },
                )
            )

        return build_task_execution_bulk_suspend_result(
            run_id=normalized_run_id,
            suspended_rows=suspended_rows,
            failed=failed,
            execution_events=execution_events,
        )

    def _suspend_active_session_for_run_locked(
        self,
        task_id: int,
        *,
        run_id: str,
        reason: str,
    ) -> _LockedSessionSuspendResult:
        """Suspend one active run-owned session while caller holds session locks."""

        session = self._read_session_locked(task_id)
        if session is None:
            return _LockedSessionSuspendResult(session=None, session_written=False)
        if str(session.run_id or "").strip() != run_id:
            return _LockedSessionSuspendResult(session=None, session_written=False)
        if session.status != "active":
            return _LockedSessionSuspendResult(session=None, session_written=False)

        pre_barrier = self._directed_effect_inactive_pre_barrier_locked(session)
        if not pre_barrier.allowed:
            return _LockedSessionSuspendResult(
                session=session,
                session_written=False,
                blocker=pre_barrier,
            )
        session.mark_suspended(reason=reason, resumable=True)
        return _LockedSessionSuspendResult(
            session=session,
            session_written=self._write_session_locked(session),
        )

    def list_ready(self) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.list_ready is retired; use list_ready_task_rows()")

    def wait_ready(self, timeout: float | None = None) -> bool:
        self.refresh_dependency_unblocks()
        return self._board.wait_ready(timeout=timeout)

    def add_ready_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._board.add_ready_listener(listener)

    def list_ready_task_rows(self) -> list[dict[str, Any]]:
        """Return ready rows after the compatibility dependency refresh.

        ``list_observable_task_rows`` is intentionally a read-only projection.
        Legacy worker-pool ready checks still need the old compatibility
        behaviour where dependency unblocks are refreshed before ready rows are
        selected, so the mutation stays explicit at this execution boundary.
        """

        self.refresh_dependency_unblocks()
        rows = self.list_observable_task_rows()
        ready_rows: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status in {"pending", "ready"} and not row.get("blocked_by"):
                ready_rows.append(row)
        return ready_rows

    def get_ready_tasks(self) -> list[Task]:
        raise RuntimeError("TaskRuntimeService.get_ready_tasks is retired; use list_ready_task_rows()")

    def get_observable_task_row_stats(self) -> dict[str, Any]:
        """Return status counts from the task-runtime-owned observable rows.

        Boundary:
            This is a read-only projection over ``list_observable_task_rows()``.
            It intentionally counts the latest ``task_runtime.execution`` fact
            overlay instead of treating file-backed rows as the only truth.
            Selection and mutation paths must continue to use their explicit
            row/session APIs.

        Complexity:
            O(r + c) time and memory over observable rows and delegated coverage
            dictionaries.
        """

        stats = task_row_status_counts(self.list_observable_task_rows())
        stats["read_model_fallback_coverage"] = self.task_row_read_model_fallback_coverage()
        stats["projected_runtime_execution_session_fallback_coverage"] = (
            self.projected_runtime_execution_session_fallback_coverage()
        )
        stats["read_model_cutover_readiness"] = self.task_row_read_model_cutover_readiness()
        return stats

    def get_task_row_stats(self) -> dict[str, Any]:
        """Compatibility entrypoint for observable task-row status counts."""

        return self.get_observable_task_row_stats()

    def get_stats(self) -> dict[str, Any]:
        raise RuntimeError("TaskRuntimeService.get_stats is retired; use get_task_row_stats()")

    def refresh_dependency_unblocks(self) -> dict[str, Any]:
        """Normalize stale BLOCKED rows whose dependencies are now complete.

        Boundary:
            Raw ``TaskBoard`` entity reads are allowed here only because this
            method is the dependency-maintenance mutation owner. The dependency
            status source remains fact-aware; persisted entities are used only
            for row-local ``TaskBoard.update`` mutations and event evidence.

        Dependency status is anchored on the fact-overlay-aware projection
        (``_fact_overlaid_dependency_status_rows``) so that the latest
        authoritative ``task_runtime.execution`` completion facts can
        unblock downstream rows even when the file-backed rows are stale.
        Iteration still walks persisted ``Task`` objects because the mutation
        path needs ``TaskBoard.update`` rather than projected dicts.
        """

        changed: list[int] = []
        refreshed: list[int] = []
        failed: list[dict[str, Any]] = []
        execution_events: list[dict[str, Any]] = []
        inspected = 0
        tasks = self._list_file_task_entities()
        status_by_id = self._fact_overlaid_dependency_status_rows()
        # Backwards-compatible fallback: callers may pass a metadata-derived
        # dependency token that points at a row absent from the overlay. Make
        # sure persisted terminal statuses are still visible to the blocker
        # resolver without leaking unknown status tokens.
        for task in tasks:
            status_by_id.setdefault(int(task.id), task.status)
        for task in tasks:
            inspected += 1
            if task.status != TaskStatus.BLOCKED:
                continue

            explicit_blockers = self._active_dependency_ids(task.blocked_by, status_by_id)
            if explicit_blockers:
                if explicit_blockers != list(task.blocked_by or []):
                    previous_blockers = [int(blocker) for blocker in task.blocked_by or []]
                    updated = self._board.update(
                        int(task.id),
                        blocked_by=explicit_blockers,
                        allow_dependency_status=True,
                    )
                    if updated is None:
                        failed.append({"task_id": int(task.id), "reason": "task_update_failed"})
                    else:
                        row = self._augment_task_row(updated.to_dict())
                        refreshed.append(int(task.id))
                        execution_event = self._append_execution_event(
                            "dependency_blockers_refreshed",
                            task_row=row,
                            session=None,
                            details={
                                "previous_blockers": previous_blockers,
                                "active_blockers": [int(blocker) for blocker in explicit_blockers],
                            },
                        )
                        execution_events.append(execution_event)
                        if not bool(execution_event.get("ok")):
                            failed.append(
                                {
                                    "task_id": int(task.id),
                                    "reason": "execution_event_append_failed",
                                    "failure_class": "ledger_append_failed",
                                    "event_type": "dependency_blockers_refreshed",
                                    "error": str(
                                        execution_event.get("error") or execution_event.get("publish_error") or ""
                                    ),
                                }
                            )
                continue

            metadata = task.metadata if isinstance(task.metadata, dict) else {}
            resolved_dependencies = self._metadata_dependency_task_ids(metadata)
            if resolved_dependencies and self._active_dependency_ids(resolved_dependencies, status_by_id):
                continue

            previous_blockers = [int(blocker) for blocker in task.blocked_by or []]
            updated = self._board.update(
                int(task.id),
                status=TaskStatus.PENDING,
                blocked_by=[],
                allow_dependency_status=True,
            )
            if updated is not None:
                row = self._augment_task_row(updated.to_dict())
                changed.append(int(task.id))
                execution_event = self._append_execution_event(
                    "dependencies_unblocked",
                    task_row=row,
                    session=None,
                    details={
                        "previous_blockers": previous_blockers,
                        "resolved_dependencies": [int(dep_id) for dep_id in resolved_dependencies],
                    },
                )
                execution_events.append(execution_event)
                if not bool(execution_event.get("ok")):
                    failed.append(
                        {
                            "task_id": int(task.id),
                            "reason": "execution_event_append_failed",
                            "failure_class": "ledger_append_failed",
                            "event_type": "dependencies_unblocked",
                            "error": str(execution_event.get("error") or execution_event.get("publish_error") or ""),
                        }
                    )
            else:
                failed.append({"task_id": int(task.id), "reason": "task_update_failed"})

        result: dict[str, Any] = {
            "inspected_count": inspected,
            "unblocked_count": len(changed),
            "unblocked_task_ids": changed,
            "refreshed_count": len(refreshed),
            "refreshed_task_ids": refreshed,
            "failed": failed,
            "execution_events": execution_events,
        }
        if any(str(item.get("failure_class") or "") == "ledger_append_failed" for item in failed):
            result["failure_class"] = "ledger_append_failed"
        return result

    @staticmethod
    def _active_dependency_ids(dependency_ids: list[int], status_by_id: dict[int, TaskStatus]) -> list[int]:
        active: list[int] = []
        for dependency_id in dependency_ids:
            try:
                normalized = int(dependency_id)
            except (TypeError, ValueError):
                continue
            if status_by_id.get(normalized) != TaskStatus.COMPLETED:
                active.append(normalized)
        return active

    @staticmethod
    def _metadata_dependency_task_ids(metadata: dict[str, Any]) -> list[int]:
        for key in ("resolved_depends_on_task_ids", "depends_on_task_ids"):
            raw = metadata.get(key)
            if not isinstance(raw, list):
                continue
            result: list[int] = []
            for item in raw:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value not in result:
                    result.append(value)
            if result:
                return result
        return []

    def _row_sort_key(self, row: dict[str, Any]) -> tuple[int, str]:
        task_id = self.normalize_task_id(row.get("id"))
        if task_id is not None:
            return (0, f"{task_id:010d}")
        return (1, str(row.get("id") or ""))

    def _is_row_claimable(self, row: dict[str, Any]) -> bool:
        status = str(row.get("status") or "").strip().lower()
        if status != "pending":
            return False
        blocked_by = row.get("blocked_by") if isinstance(row.get("blocked_by"), list) else row.get("blockedBy")
        return not blocked_by

    def _resolve_next_attempt(
        self,
        task: Task,
        session: TaskExecutionSession | None,
    ) -> int:
        if session is not None:
            return int(session.attempt) + 1
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        return normalize_positive_int(metadata.get("claim_attempt"), default=1)

    def _task_has_unresolved_dependencies(self, task: Task) -> bool:
        """Return whether ``task`` still has a dependency that is not completed.

        Boundary:
            Read-only.  This helper exists so the claim path can reuse the
            same fact-overlaid dependency status projection that
            :meth:`refresh_dependency_unblocks` already trusts
            (:meth:`_fact_overlaid_dependency_status_rows`).  Without it,
            ``claim_execution`` would still consult the raw ``TaskBoard``
            status for each blocker, leaving a row-only dependency decision
            seam in the claim path that can disagree with the refresh step.

        Fail-closed semantics (any of the following => unresolved / blocked):

        * ``task.blocked_by`` is missing or empty, which means there is no
          unresolved dependency for this row;
        * a dependency id cannot be coerced to a positive int (the caller
          supplied a token the runtime cannot resolve);
        * a dependency id is not present in the fact-overlaid status map
          (the row is missing or unreadable);
        * the overlaid status is anything other than ``TaskStatus.COMPLETED``
          (including non-terminal, terminal-failed, terminal-cancelled,
          and unknown tokens).

        The overlay map itself falls back to the file-backed status when no
        authoritative ``task_runtime.execution`` fact exists for a row. This
        helper intentionally does not perform its own raw ``TaskBoard`` walk:
        a missing dependency in the overlay is treated as unresolved, keeping
        the fact-overlaid projection as the single dependency status source.

        Complexity:
            O(d + r + f) time and memory where ``d`` is the number of
            blockers for ``task``, ``r`` is the number of file-backed rows,
            and ``f`` is the number of latest fact rows; bounded by the
            ``_fact_overlaid_dependency_status_rows`` walk and so amortised
            once per call.
        """

        blocked_by = task.blocked_by if task.blocked_by is not None else []
        if not blocked_by:
            return False
        try:
            status_by_id = self._fact_overlaid_dependency_status_rows()
        except (RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Failing closed on unresolved dependency check for task_id=%s: overlay unavailable: %s",
                getattr(task, "id", None),
                exc,
            )
            return True

        for dependency_id in list(blocked_by):
            try:
                dep_id_int = int(dependency_id)
            except (TypeError, ValueError):
                logger.warning("Skipping non-integer dependency_id: %r", dependency_id)
                return True
            if dep_id_int <= 0:
                logger.warning("Skipping non-positive dependency_id: %r", dependency_id)
                return True
            dependency_status = status_by_id.get(dep_id_int)
            if dependency_status is None:
                return True
            if dependency_status != TaskStatus.COMPLETED:
                return True
        return False

    def _get_session_lock(self, task_id: int) -> threading.RLock:
        """Return the per-task session lock, creating it on demand."""
        with self._session_locks_meta:
            if task_id not in self._session_locks:
                self._session_locks[task_id] = threading.RLock()
            return self._session_locks[task_id]

    def _get_settlement_projection_lock(self, task_id: int) -> threading.RLock:
        """Return the per-task lock for effects after session winner selection."""

        with self._settlement_projection_locks_meta:
            if task_id not in self._settlement_projection_locks:
                self._settlement_projection_locks[task_id] = threading.RLock()
            return self._settlement_projection_locks[task_id]

    def _session_file_lock_path(self, task_id: int) -> Path:
        """Return the cooperative cross-process lock path for one session file."""

        return Path(self._kernel_fs.resolve_path(f"runtime/tasks/.task_{int(task_id)}.session.json.lock"))

    def _settlement_projection_file_lock_path(self, task_id: int) -> Path:
        """Return the independent cooperative lock for settlement projections."""

        return Path(self._kernel_fs.resolve_path(f"runtime/tasks/.task_{int(task_id)}.settlement.lock"))

    def _session_logical_path(self, task_id: int) -> str:
        return f"runtime/tasks/task_{int(task_id)}.session.json"

    def _execution_attempt_identity_from_session(
        self,
        session: TaskExecutionSession,
    ) -> TaskRuntimeExecutionAttemptIdentityV1:
        """Project the canonical execution-attempt identity from a session."""

        return TaskRuntimeExecutionAttemptIdentityV1(
            workspace=self.workspace,
            task_id=int(session.task_id),
            external_task_id=str(session.external_task_id or "").strip(),
            session_id=session.session_id,
            attempt=int(session.attempt),
            role_id=session.role_id,
            worker_id=session.worker_id,
            run_id=session.run_id,
            lease_expires_at=session.lease_expires_at,
        )

    def _claim_result_with_execution_attempt(
        self,
        result: dict[str, Any],
        session: TaskExecutionSession | None,
    ) -> dict[str, Any]:
        """Add a stable typed attempt projection without changing claim semantics."""

        if session is not None:
            result["execution_attempt"] = self._execution_attempt_identity_from_session(session).to_record()
        return result

    def _execution_attempt_validation_verdict(
        self,
        *,
        valid: bool,
        code: TaskRuntimeExecutionAttemptValidationCodeV1,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        evidence: Mapping[str, Any] | None = None,
    ) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
        """Build a detached, fail-closed execution-attempt verdict."""

        return TaskRuntimeExecutionAttemptValidationVerdictV1(
            valid=valid,
            code=code,
            workspace=self.workspace,
            identity=identity,
            evidence=dict(evidence or {}),
        )

    def validate_execution_attempt(
        self,
        query: ValidateTaskRuntimeExecutionAttemptQueryV1,
    ) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
        """Validate one persisted execution attempt without renewing or writing it."""

        identity = query.identity
        if query.workspace != self.workspace or identity.workspace != query.workspace:
            return self._execution_attempt_validation_verdict(
                valid=False,
                code="workspace_mismatch",
                identity=identity,
                evidence={
                    "query_workspace": query.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )

        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=query.lock_timeout_seconds):
            return self._execution_attempt_validation_verdict(
                valid=False,
                code="file_lock_timeout",
                identity=identity,
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": query.lock_timeout_seconds},
            )
        try:
            remaining = query.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": query.lock_timeout_seconds},
                )
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    return self._validate_execution_attempt_locked(identity)
            except TaskBoardFileLockTimeoutError:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": query.lock_timeout_seconds,
                    },
                )
        finally:
            session_lock.release()

    def open_execution_attempt_authority(
        self,
        command: OpenTaskRuntimeExecutionAttemptAuthorityCommandV1,
    ) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
        """Open a non-durable authority only while validation locks are held.

        This operation deliberately performs no heartbeat, session write, row
        projection, or FactStream append. Constructing the local handle inside
        the validation critical section linearizes open against terminal settle.
        """

        if not isinstance(command, OpenTaskRuntimeExecutionAttemptAuthorityCommandV1):
            raise TypeError("command must be OpenTaskRuntimeExecutionAttemptAuthorityCommandV1")
        identity = command.identity
        if command.workspace != self.workspace or identity.workspace != command.workspace:
            validation = self._execution_attempt_validation_verdict(
                valid=False,
                code="workspace_mismatch",
                identity=identity,
                evidence={
                    "command_workspace": command.workspace,
                    "service_workspace": self.workspace,
                    "identity_workspace": identity.workspace,
                },
            )
            return self._execution_attempt_authority_open_verdict(validation)

        started_at = time.monotonic()
        session_lock = self._get_session_lock(identity.task_id)
        if not session_lock.acquire(timeout=command.lock_timeout_seconds):
            validation = self._execution_attempt_validation_verdict(
                valid=False,
                code="file_lock_timeout",
                identity=identity,
                evidence={"lock_scope": "local_session", "lock_timeout_seconds": command.lock_timeout_seconds},
            )
            return self._execution_attempt_authority_open_verdict(validation)
        try:
            remaining = command.lock_timeout_seconds - (time.monotonic() - started_at)
            if remaining < 0:
                validation = self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={"lock_scope": "local_session", "lock_timeout_seconds": command.lock_timeout_seconds},
                )
                return self._execution_attempt_authority_open_verdict(validation)
            try:
                with self._board._file_lock(
                    self._session_file_lock_path(identity.task_id),
                    timeout_seconds=remaining,
                ):
                    try:
                        validation = self._validate_execution_attempt_locked(
                            identity,
                            raise_infrastructure_errors=True,
                        )
                    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
                        return self._execution_attempt_authority_open_infrastructure_failure(
                            identity,
                            stage="session_read",
                            exc=exc,
                        )
                    return self._execution_attempt_authority_open_verdict(validation)
            except TaskBoardFileLockTimeoutError:
                validation = self._execution_attempt_validation_verdict(
                    valid=False,
                    code="file_lock_timeout",
                    identity=identity,
                    evidence={
                        "lock_scope": "cooperative_session_file",
                        "lock_timeout_seconds": command.lock_timeout_seconds,
                    },
                )
                return self._execution_attempt_authority_open_verdict(validation)
            except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
                return self._execution_attempt_authority_open_infrastructure_failure(
                    identity,
                    stage="cooperative_session_file_lock",
                    exc=exc,
                )
        finally:
            session_lock.release()

    def _execution_attempt_authority_open_infrastructure_failure(
        self,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        stage: str,
        exc: BaseException,
    ) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
        """Return a detached, typed refusal for authority-open infrastructure failures."""

        return TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1(
            success=False,
            code="authority_open_internal_error",
            workspace=self.workspace,
            identity=identity,
            evidence={
                "stage": stage,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )

    def _execution_attempt_authority_open_verdict(
        self,
        validation: TaskRuntimeExecutionAttemptValidationVerdictV1,
    ) -> TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1:
        """Map a locked validation result to a detached, fail-closed open verdict."""

        if not validation.valid:
            return TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1(
                success=False,
                code=cast(TaskRuntimeExecutionAttemptAuthorityOpenCodeV1, validation.code),
                workspace=self.workspace,
                identity=validation.identity,
                evidence=validation.evidence,
            )
        try:
            authority = self._create_execution_attempt_authority_locked(validation.identity)
        except Exception as exc:  # noqa: BLE001 - construction must not claim authority on failure.
            return self._execution_attempt_authority_open_infrastructure_failure(
                validation.identity,
                stage="authority_construction",
                exc=exc,
            )
        return TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1(
            success=True,
            code="valid",
            workspace=self.workspace,
            identity=validation.identity,
            authority=authority,
            evidence=validation.evidence,
        )

    @staticmethod
    def _create_execution_attempt_authority_locked(
        identity: TaskRuntimeExecutionAttemptIdentityV1,
    ) -> TaskRuntimeExecutionAttemptAuthorityV1:
        """Construct the process-local capability after the durable check passes."""

        from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeExecutionAttemptAuthorityV1

        return TaskRuntimeExecutionAttemptAuthorityV1(identity)

    def _validate_execution_attempt_locked(
        self,
        identity: TaskRuntimeExecutionAttemptIdentityV1,
        *,
        raise_infrastructure_errors: bool = False,
    ) -> TaskRuntimeExecutionAttemptValidationVerdictV1:
        """Validate one attempt while its local and cooperative locks are held."""

        with self._board.transaction():
            session_path = self._session_logical_path(identity.task_id)
            session = self._read_session_locked(
                identity.task_id,
                raise_infrastructure_errors=raise_infrastructure_errors,
            )
            if session is None:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_not_found",
                    identity=identity,
                    evidence={"task_id": identity.task_id, "session_path": session_path},
                )

            observed_identity = self._execution_attempt_identity_from_session(session)
            evidence = {"observed": observed_identity.to_record()}
            if session.task_id != identity.task_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_task_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.session_id != identity.session_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.attempt != identity.attempt:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="attempt_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.role_id != identity.role_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="role_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.worker_id != identity.worker_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="worker_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.run_id != identity.run_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="run_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.external_task_id != identity.external_task_id:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="external_task_id_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.lease_expires_at != identity.lease_expires_at:
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="lease_version_mismatch",
                    identity=identity,
                    evidence=evidence,
                )
            if session.status != "active":
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_not_active",
                    identity=identity,
                    evidence=evidence,
                )
            if session.is_expired(now=utc_now()):
                return self._execution_attempt_validation_verdict(
                    valid=False,
                    code="session_lease_expired",
                    identity=identity,
                    evidence=evidence,
                )
            return self._execution_attempt_validation_verdict(
                valid=True,
                code="valid",
                identity=identity,
                evidence=evidence,
            )

    @staticmethod
    def _session_payload_text(payload: Any) -> str:
        """Return the exact UTF-8 JSON text used by ``write_json_atomic``."""

        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    @classmethod
    def _session_payload_hash(cls, payload: Any) -> str:
        """Return the write-format UTF-8 JSON payload hash for session receipts."""

        return hashlib.sha256(cls._session_payload_text(payload).encode("utf-8")).hexdigest()

    def _read_current_session_payload_hash(self, logical_path: str) -> str:
        """Return the current UTF-8 session file hash, or empty string when absent."""

        if not self._kernel_fs.exists(logical_path):
            return ""
        try:
            session_text = self._kernel_fs.read_text(logical_path, encoding="utf-8")
            return hashlib.sha256(session_text.encode("utf-8")).hexdigest()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Failed to hash task runtime session text %s: %s", logical_path, exc)
            return ""

    def _assert_session_payload_unchanged(self, session_path: str, *, before_hash: str) -> None:
        """Fail closed if a session JSON file changed before atomic replacement."""

        current_hash = self._read_current_session_payload_hash(session_path)
        if current_hash == before_hash:
            return

        before_label = before_hash or "<absent>"
        current_label = current_hash or "<absent>"
        logger.warning(
            "TaskRuntime session write conflict: session_path=%s before_hash=%s current_hash=%s",
            session_path,
            before_label,
            current_label,
        )
        raise TaskExecutionSessionWriteConflictError(
            "TaskRuntime session write conflict: "
            f"session_path={session_path!r} before_hash={before_label!r} "
            f"current_hash={current_label!r}"
        )

    def _record_session_write_receipt(
        self,
        *,
        session: TaskExecutionSession,
        session_path: str,
        before_hash: str,
        after_hash: str,
        operation: str,
        preserved_terminal_session: bool,
    ) -> None:
        receipt = TaskExecutionSessionWriteReceipt(
            task_id=session.task_id,
            session_id=session.session_id,
            session_path=session_path,
            before_hash=before_hash,
            after_hash=after_hash,
            operation=operation,
            written_at=utc_now_iso(),
            preserved_terminal_session=preserved_terminal_session,
        )
        with self._session_write_receipt_lock:
            self._last_session_write_receipt = receipt
            task_id = self.normalize_task_id(session.task_id)
            session_id = str(session.session_id or "").strip()
            if task_id is not None and session_id:
                self._session_write_receipts_by_identity[(task_id, session_id)] = receipt

    def _read_session(self, task_id: int) -> TaskExecutionSession | None:
        """Read a session under the per-task local and cooperative file locks."""

        task_id = int(task_id)
        with (
            self._get_session_lock(task_id),
            self._board._file_lock(self._session_file_lock_path(task_id)),
        ):
            return self._read_session_locked(task_id)

    def _read_session_locked(
        self,
        task_id: int,
        *,
        raise_infrastructure_errors: bool = False,
    ) -> TaskExecutionSession | None:
        """Read a session while the caller holds both per-task session locks."""

        logical_path = self._session_logical_path(task_id)
        if not self._kernel_fs.exists(logical_path):
            return None
        try:
            payload = self._kernel_fs.read_json(logical_path)
        except (OSError, UnicodeError, RuntimeError):
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to read task runtime session %s", logical_path, exc_info=True)
            return None
        except ValueError as exc:
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to read task runtime session %s: %s", logical_path, exc)
            return None
        if not isinstance(payload, dict):
            if raise_infrastructure_errors:
                raise ValueError("task runtime session payload must be an object")
            return None
        try:
            return TaskExecutionSession.from_dict(payload)
        except (OSError, UnicodeError, RuntimeError):
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to parse task runtime session %s", logical_path, exc_info=True)
            return None
        except ValueError as exc:
            if raise_infrastructure_errors:
                raise
            logger.warning("Failed to parse task runtime session %s: %s", logical_path, exc)
            return None

    def _write_session(
        self,
        session: TaskExecutionSession,
        *,
        allow_terminal_downgrade: bool = False,
    ) -> bool:
        task_id = int(session.task_id)
        with (
            self._get_session_lock(task_id),
            self._board._file_lock(self._session_file_lock_path(task_id)),
        ):
            return self._write_session_locked(
                session,
                allow_terminal_downgrade=allow_terminal_downgrade,
            )

    def _write_session_locked(
        self,
        session: TaskExecutionSession,
        *,
        allow_terminal_downgrade: bool = False,
    ) -> bool:
        session_path = self._session_logical_path(session.task_id)
        if is_terminal_session_status(session.status):
            persisted_session = self._read_session_locked(session.task_id)
            same_session = (
                persisted_session is not None
                and str(persisted_session.session_id or "").strip() == str(session.session_id or "").strip()
            )
            persisted_transition_id = (
                str(persisted_session.terminal_transition_id or "").strip()
                if same_session and persisted_session is not None
                else ""
            )
            if persisted_transition_id:
                session.terminal_transition_id = persisted_transition_id
            else:
                session.ensure_terminal_transition_id()
        if not allow_terminal_downgrade and not is_terminal_session_status(session.status):
            terminal_session = self._find_terminal_session_snapshot_locked(session)
            if terminal_session is not None:
                self._copy_session_state(session, terminal_session)
                terminal_payload = terminal_session.to_dict()
                before_hash = self._read_current_session_payload_hash(session_path)
                after_hash = self._session_payload_hash(terminal_payload)
                self._assert_session_payload_unchanged(session_path, before_hash=before_hash)
                self._kernel_fs.write_json_atomic(
                    session_path,
                    terminal_payload,
                    indent=2,
                    ensure_ascii=False,
                )
                self._record_session_write_receipt(
                    session=terminal_session,
                    session_path=session_path,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    operation="replace",
                    preserved_terminal_session=True,
                )
                return False
        session_payload = session.to_dict()
        before_hash = self._read_current_session_payload_hash(session_path)
        after_hash = self._session_payload_hash(session_payload)
        self._assert_session_payload_unchanged(session_path, before_hash=before_hash)
        self._kernel_fs.write_json_atomic(
            session_path,
            session_payload,
            indent=2,
            ensure_ascii=False,
        )
        self._record_session_write_receipt(
            session=session,
            session_path=session_path,
            before_hash=before_hash,
            after_hash=after_hash,
            operation="replace",
            preserved_terminal_session=False,
        )
        return True

    def _find_terminal_session_snapshot_locked(
        self,
        incoming: TaskExecutionSession,
    ) -> TaskExecutionSession | None:
        """Find a terminal snapshot while the caller holds session locks."""

        disk_session = self._read_session_locked(incoming.task_id)
        if self._same_terminal_session(disk_session, incoming):
            return disk_session

        metadata_session = self._find_projected_runtime_execution_session_locked(incoming.task_id)
        if self._same_terminal_session(metadata_session, incoming):
            return metadata_session
        return None

    def _find_terminal_session_snapshot(
        self,
        incoming: TaskExecutionSession,
    ) -> TaskExecutionSession | None:
        """Find a terminal snapshot from session JSON or row projections."""

        disk_session = self._read_session(incoming.task_id)
        if self._same_terminal_session(disk_session, incoming):
            return disk_session

        metadata_session = self._find_projected_runtime_execution_session(incoming.task_id)
        if self._same_terminal_session(metadata_session, incoming):
            return metadata_session
        return None

    def _find_projected_runtime_execution_session(
        self,
        task_id: int,
    ) -> TaskExecutionSession | None:
        """Return ``metadata.runtime_execution`` from read-model projections only."""

        fact_row = self._find_latest_execution_fact_row_for_task(task_id)
        fact_session = self._runtime_execution_session_from_projected_row(fact_row)
        if fact_session is not None:
            return fact_session

        if not self._projected_runtime_execution_session_file_fallback_allowed():
            return None
        return self._find_projected_runtime_execution_session_from_file_rows(
            task_id,
            augment_runtime_state=True,
        )

    def _find_projected_runtime_execution_session_locked(
        self,
        task_id: int,
    ) -> TaskExecutionSession | None:
        """Return projected runtime metadata without session row augmentation.

        This locked path is used while session writes are evaluating terminal
        snapshots. It intentionally preserves the non-augmenting file fallback
        without consulting cutover readiness because readiness may scan
        file-backed rows and re-enter session state projection.
        """

        fact_row = self._find_latest_execution_fact_row_for_task(task_id)
        fact_session = self._runtime_execution_session_from_projected_row(fact_row)
        if fact_session is not None:
            return fact_session

        return self._find_projected_runtime_execution_session_from_file_rows(
            task_id,
            augment_runtime_state=False,
        )

    def _projected_runtime_execution_session_file_fallback_allowed(self) -> bool:
        """Gate the migration-period file fallback without reading file rows directly.

        The readiness projection owns the compatibility signal. During migration,
        malformed or older readiness payloads fail open so existing deployments do
        not lose projected runtime-execution sessions before the read model is
        fully cut over.
        """

        readiness = self.task_row_read_model_cutover_readiness()
        if not isinstance(readiness, dict):
            return True
        if "projected_session_file_fallback_required" not in readiness:
            return True
        return readiness["projected_session_file_fallback_required"] is True

    def _find_projected_runtime_execution_session_from_file_rows(
        self,
        task_id: int,
        *,
        augment_runtime_state: bool = True,
    ) -> TaskExecutionSession | None:
        """Return legacy file-row ``metadata.runtime_execution`` projection."""

        normalized_id = self.normalize_task_id(task_id)
        if normalized_id is None:
            return None
        target_task_id = str(normalized_id).strip()
        if not target_task_id:
            return None

        for row in self._list_file_task_rows(
            include_terminal=True,
            augment_runtime_state=augment_runtime_state,
        ):
            if self._observable_row_task_id(row) != target_task_id:
                continue
            return self._runtime_execution_session_from_projected_row(row)
        return None

    @staticmethod
    def _runtime_execution_session_from_projected_row(
        row: Mapping[str, Any] | None,
    ) -> TaskExecutionSession | None:
        if not isinstance(row, Mapping):
            return None
        metadata_raw = row.get("metadata")
        if not isinstance(metadata_raw, Mapping):
            return None
        runtime_execution_raw = metadata_raw.get("runtime_execution")
        if not isinstance(runtime_execution_raw, dict):
            return None
        try:
            return TaskExecutionSession.from_dict(runtime_execution_raw)
        except (TypeError, ValueError) as exc:
            logger.debug("invalid projected runtime_execution session metadata: %s", exc)
            return None

    @staticmethod
    def _same_terminal_session(
        candidate: TaskExecutionSession | None,
        incoming: TaskExecutionSession,
    ) -> bool:
        if candidate is None:
            return False
        return str(candidate.session_id or "").strip() == str(
            incoming.session_id or ""
        ).strip() and is_terminal_session_status(candidate.status)

    @staticmethod
    def _copy_session_state(target: TaskExecutionSession, source: TaskExecutionSession) -> None:
        target.status = source.status
        target.claimed_at = source.claimed_at
        target.last_heartbeat_at = source.last_heartbeat_at
        target.lease_expires_at = source.lease_expires_at
        target.attempt = source.attempt
        target.resume_count = source.resume_count
        target.resumable = source.resumable
        target.origin = source.origin
        target.selection_source = source.selection_source
        target.external_task_id = source.external_task_id
        target.context_summary = source.context_summary
        target.last_error = source.last_error
        target.last_result_summary = source.last_result_summary
        target.released_at = source.released_at
        target.terminal_transition_id = source.terminal_transition_id
        target.metadata = dict(source.metadata)

    def _reconcile_terminal_task_row(
        self,
        task_id: int,
        *,
        session: TaskExecutionSession,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row, _reconcile_error, _execution_event = self._apply_terminal_session_reconcile(
            task_id,
            session=session,
            extra_metadata=metadata,
        )
        return row

    def _task_entity_for_terminal_session_reconcile(self, task_id: int) -> Task | None:
        """Resolve raw owner-cell task entity for terminal-session reconcile.

        Boundary:
            Terminal-session reconcile is the owner-cell path that projects a
            terminal execution session back onto the persisted task row. This is
            the only raw ``TaskBoard.get`` read boundary for that reconcile
            flow; observable readers must continue using fact-overlaid row
            projections, and claim/dependency/transition helpers keep their own
            narrower raw-entity boundaries.

        Complexity:
            O(1) over the in-memory ``TaskBoard`` cache for the already
            normalized numeric ``task_id`` used by this reconcile path. Missing
            rows return ``None`` so existing ``task_not_found`` and empty-row
            fallback semantics remain unchanged.

        Extension point:
            Future terminal-session compare-and-swap, row-version validation, or
            audit receipt binding should attach here before reconcile writes,
            keeping those checks local to this owner-cell boundary without
            changing event payloads or rejection error codes.
        """

        return self._board.get(task_id)

    def _apply_terminal_session_reconcile(
        self,
        task_id: int,
        *,
        session: TaskExecutionSession,
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
        """Project a terminal session onto its task row without ever raising.

        Returns ``(row, error_code)``. ``error_code`` is empty when the row now
        reflects the terminal session (or already did); otherwise it is a
        structured token describing why reconciliation was rejected, and the
        row is returned unchanged. Board transition validation failures are
        recorded, never propagated, so lease/claim paths cannot crash on a
        stale row shape.
        """
        terminal_status = _terminal_task_status_for_session(session.status)
        if terminal_status is None:
            task = self._task_entity_for_terminal_session_reconcile(task_id)
            return (self._augment_task_row(task.to_dict()) if task is not None else None), "", None
        runtime_metadata = self._build_runtime_metadata(
            session=session,
            effective_status=terminal_status.value,
            resume_state="",
            extra_metadata=extra_metadata,
        )
        try:
            updated = self._board.update(
                task_id,
                status=terminal_status,
                metadata=runtime_metadata,
                allow_terminal_status=True,
            )
        except InvalidTaskStateTransitionError:
            task = self._task_entity_for_terminal_session_reconcile(task_id)
            if task is None:
                return None, "task_not_found", None
            if task.is_terminal:
                # Never rewrite one terminal verdict with another here:
                # reopen is the only sanctioned terminal-downgrade path.
                logger.warning(
                    "Task %s row is terminal %r but session %s is terminal %r; keeping row verdict",
                    task_id,
                    task.status.value,
                    session.session_id,
                    terminal_status.value,
                )
                return self._augment_task_row(task.to_dict()), "terminal_row_conflict", None
            try:
                forced = self._board.reconcile_terminal_status(
                    task_id,
                    terminal_status,
                    result_summary=sanitize_summary(session.last_result_summary or session.last_error),
                )
            except InvalidTaskStateTransitionError as exc:
                logger.warning(
                    "Task %s terminal reconcile to %r rejected: %s",
                    task_id,
                    terminal_status.value,
                    exc,
                )
                return self._augment_task_row(task.to_dict()), "terminal_reconcile_rejected", None
            if forced is None:
                return None, "task_not_found", None
            updated = self._board.update(task_id, metadata=runtime_metadata) or forced
        if updated is None:
            task = self._task_entity_for_terminal_session_reconcile(task_id)
            return (self._augment_task_row(task.to_dict()) if task is not None else None), "", None
        row = self._augment_task_row(updated.to_dict())
        execution_event = self._append_execution_event(
            "terminal_session_reconciled",
            task_row=row,
            session=session,
            details={
                "terminal_status": terminal_status.value,
                "source": "runtime.task_runtime.terminal_session_reconcile",
            },
        )
        return row, "", execution_event

    def _row_authorizes_retry_over_terminal_session(
        self,
        task: Task,
        session: TaskExecutionSession,
    ) -> bool:
        """Return True when a non-terminal row supersedes a terminal session.

        A row only wins over terminal session evidence when it left its
        terminal state through the sanctioned state-machine paths
        (``TaskBoard.update_status`` / ``TaskBoard.reopen`` stamp
        ``metadata.terminal_reset_at``) *after* the session reached its
        terminal state. Anything else is a stale row and the terminal session
        stays authoritative, so a genuinely completed/failed task cannot be
        re-claimed through a stale byte-level row rewrite.
        """
        if task.is_terminal:
            return False
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        raw_reset_at = metadata.get("terminal_reset_at")
        if not isinstance(raw_reset_at, (int, float, str)) or isinstance(raw_reset_at, bool):
            return False
        try:
            reset_at = float(raw_reset_at)
        except ValueError:
            return False
        if reset_at <= 0.0:
            return False
        terminal_at = terminal_session_timestamp(session)
        if terminal_at is None:
            # Fail closed: without a trustworthy terminal timestamp the
            # terminal session evidence stays authoritative.
            return False
        return reset_at > terminal_at

    def _row_mapping_authorizes_retry_over_terminal_session(
        self,
        row: Mapping[str, Any],
        session: TaskExecutionSession,
    ) -> bool:
        """Return True when a non-terminal read-model row supersedes a terminal session.

        ``_augment_task_row`` operates on the observable row projection. It must
        not re-read the private ``TaskBoard`` row just to decide whether a retry
        authorization exists, otherwise the board becomes a hidden second read
        source for runtime state. This row-oriented variant intentionally mirrors
        ``_row_authorizes_retry_over_terminal_session`` while accepting only the
        fields already present in the supplied row.
        """
        raw_status = str(row.get("status") or "").strip().lower()
        if not raw_status or is_terminal_task_row_status(raw_status):
            return False
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}
        raw_reset_at = metadata.get("terminal_reset_at")
        if not isinstance(raw_reset_at, (int, float, str)) or isinstance(raw_reset_at, bool):
            return False
        try:
            reset_at = float(raw_reset_at)
        except ValueError:
            return False
        if reset_at <= 0.0:
            return False
        terminal_at = terminal_session_timestamp(session)
        if terminal_at is None:
            # Fail closed: without a trustworthy terminal timestamp the
            # terminal session evidence stays authoritative.
            return False
        return reset_at > terminal_at

    def _rotate_terminal_session_for_retry(self, session: TaskExecutionSession) -> TaskExecutionSession:
        """Rotate a superseded terminal session via the explicit downgrade path.

        The task row deliberately left its terminal state for a retry, so the
        stale terminal session must not keep vetoing claims. Suspending it
        with ``allow_terminal_downgrade=True`` mirrors what ``reopen`` does and
        keeps the terminal-monotonic write guard intact; ``resumable=False``
        makes the retry a fresh attempt instead of a resume.
        """
        with (
            self._get_session_lock(session.task_id),
            self._board._file_lock(self._session_file_lock_path(session.task_id)),
        ):
            return self._rotate_terminal_session_for_retry_locked(session)

    def _rotate_terminal_session_for_retry_locked(
        self,
        session: TaskExecutionSession,
    ) -> TaskExecutionSession:
        """Rotate a terminal session while the caller owns both session locks."""

        session.metadata["rotated_from_terminal_status"] = str(session.status or "")
        session.metadata["rotated_reason"] = "deliberate_row_reset_retry"
        session.mark_suspended(reason="terminal_session_rotated_for_deliberate_retry", resumable=False)
        self._write_session_locked(session, allow_terminal_downgrade=True)
        return session

    def _dependent_rows_blocked_by(self, task_id: int) -> list[dict[str, Any]]:
        """Return task-row snapshots that currently depend on ``task_id``.

        Boundary:
            This is pre-mutation evidence for dependency side effects owned by
            ``TaskRuntimeService.settle_execution_attempt()``. Raw ``TaskBoard``
            updates are row-local; dependency fan-out must stay in this service
            so every cross-row mutation can emit execution facts.

        Complexity:
            O(t) time and memory over task rows in the current workspace.
        """

        rows: list[dict[str, Any]] = []
        for row in self.list_observable_task_rows():
            try:
                blockers = [int(blocker) for blocker in row.get("blocked_by") or []]
            except (TypeError, ValueError):
                blockers = []
            if task_id in blockers:
                rows.append(dict(row))
        return rows

    @staticmethod
    def _row_blocker_ids(row: dict[str, Any]) -> list[int]:
        blockers_raw = row.get("blocked_by") or row.get("blockedBy") or []
        blocker_ids: list[int] = []
        if not isinstance(blockers_raw, list):
            return blocker_ids
        for blocker in blockers_raw:
            try:
                blocker_id = int(blocker)
            except (TypeError, ValueError):
                continue
            if blocker_id not in blocker_ids:
                blocker_ids.append(blocker_id)
        return blocker_ids

    @staticmethod
    def _row_blocks_ids(row: dict[str, Any]) -> list[int]:
        blocks_raw = row.get("blocks") or []
        block_ids: list[int] = []
        if not isinstance(blocks_raw, list):
            return block_ids
        for block in blocks_raw:
            try:
                block_id = int(block)
            except (TypeError, ValueError):
                continue
            if block_id not in block_ids:
                block_ids.append(block_id)
        return block_ids

    def _apply_reverse_dependency_links(
        self,
        *,
        created_task_id: int,
        blocker_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Link a newly created dependent task into each blocker row.

        The operation is O(b) over direct blockers supplied by the task row.
        Missing blocker rows preserve legacy create semantics and are ignored,
        but every persisted reverse-link mutation emits an execution fact.
        """

        events: list[dict[str, Any]] = []
        for blocker_id in blocker_ids:
            _normalized_blocker_id, blocker = self._task_entity_for_dependency_side_effect(blocker_id)
            if blocker is None:
                continue
            before_row = self._augment_task_row(blocker.to_dict())
            previous_blocks = self._row_blocks_ids(before_row)
            if created_task_id in previous_blocks:
                continue
            next_blocks = [*previous_blocks, created_task_id]
            updated = self._board.update_blocks(blocker_id, next_blocks)
            if updated is None:
                events.append(
                    {
                        "ok": False,
                        "event_type": "reverse_dependency_link_failed",
                        "task_id": blocker_id,
                        "reason": "task_update_failed",
                        "failure_class": "task_state_write_failed",
                    }
                )
                continue
            after_row = self._augment_task_row(updated.to_dict())
            events.append(
                self._append_execution_event(
                    "reverse_dependency_linked",
                    task_row=after_row,
                    session=None,
                    details={
                        "dependent_task_id": created_task_id,
                        "previous_blocks": previous_blocks,
                        "blocks": self._row_blocks_ids(after_row),
                    },
                )
            )
        return events

    def _apply_reopen_downstream_reblocks(
        self,
        *,
        reopened_task_id: int,
        dependent_ids: list[int],
    ) -> list[dict[str, Any]]:
        """Re-block direct dependents after a prerequisite task is reopened.

        The operation is O(d) over direct dependents from the reopened task row.
        Each dependent row mutation is followed by a task-runtime execution fact
        so QA rework cannot silently rewrite downstream scheduling state.
        """

        events: list[dict[str, Any]] = []
        for dependent_id in dependent_ids:
            _normalized_dependent_id, dependent = self._task_entity_for_dependency_side_effect(dependent_id)
            if dependent is None:
                continue
            before_row = self._augment_task_row(dependent.to_dict())
            previous_blockers = self._row_blocker_ids(before_row)
            next_blockers = list(previous_blockers)
            if reopened_task_id not in next_blockers:
                next_blockers.append(reopened_task_id)
            previous_status = str(before_row.get("status") or "").strip().lower()
            next_status: TaskStatus | None = TaskStatus.BLOCKED if previous_status in {"pending", "ready"} else None
            if next_blockers == previous_blockers and next_status is None:
                continue
            updated = self._board.update(
                dependent_id,
                status=next_status,
                blocked_by=next_blockers,
                allow_dependency_status=True,
            )
            if updated is None:
                events.append(
                    {
                        "ok": False,
                        "event_type": "downstream_dependency_reblock_failed",
                        "task_id": dependent_id,
                        "reason": "task_update_failed",
                        "failure_class": "task_state_write_failed",
                    }
                )
                continue
            after_row = self._augment_task_row(updated.to_dict())
            events.append(
                self._append_execution_event(
                    "downstream_dependency_reblocked",
                    task_row=after_row,
                    session=None,
                    details={
                        "reopened_task_id": reopened_task_id,
                        "previous_status": previous_status,
                        "previous_blockers": previous_blockers,
                        "active_blockers": self._row_blocker_ids(after_row),
                    },
                )
            )
        return events

    def _apply_dependency_completion_side_effects(
        self,
        *,
        completed_task_id: int,
        dependent_rows_before: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Apply and record dependent-row changes caused by a completed task.

        The operation is O(d) over rows that explicitly blocked on the
        completed task, where d is the number of direct dependents captured
        before the parent transition. Each changed dependent row produces an
        execution event so Run Ledger projections can explain the unblock
        without re-reading raw task files.
        """

        events: list[dict[str, Any]] = []
        should_notify_ready = False
        for before_row in dependent_rows_before:
            dependent_id = self.normalize_task_id(before_row.get("id"))
            if dependent_id is None:
                continue
            previous_blockers = self._row_blocker_ids(before_row)
            active_blockers = [blocker_id for blocker_id in previous_blockers if blocker_id != completed_task_id]
            if previous_blockers == active_blockers:
                continue

            updated = self._board.update(
                dependent_id,
                blocked_by=active_blockers,
                allow_dependency_status=True,
            )
            if updated is None:
                events.append(
                    {
                        "ok": False,
                        "event_type": "dependency_row_update_failed",
                        "task_id": dependent_id,
                        "reason": "task_update_failed",
                        "failure_class": "task_state_write_failed",
                    }
                )
                continue

            after_row = self._augment_task_row(updated.to_dict())
            active_blockers = self._row_blocker_ids(after_row)
            status = str(after_row.get("status") or "").strip().lower()
            event_type = (
                "dependencies_unblocked"
                if not active_blockers and status in {"pending", "ready"}
                else "dependency_blockers_refreshed"
            )
            if event_type == "dependencies_unblocked":
                should_notify_ready = True
            events.append(
                self._append_execution_event(
                    event_type,
                    task_row=after_row,
                    session=None,
                    details={
                        "completed_task_id": completed_task_id,
                        "previous_blockers": previous_blockers,
                        "active_blockers": active_blockers,
                    },
                )
            )
        if should_notify_ready:
            self._board.notify_ready_tasks()
        return events

    @staticmethod
    def _build_terminal_execution_transition_result(
        *,
        reason: str,
        task_row: dict[str, Any],
        session: TaskExecutionSession,
        execution_event: dict[str, Any],
    ) -> dict[str, Any]:
        """Project terminal transitions through the shared execution result builder."""

        return build_task_execution_transition_result(
            success=True,
            reason=reason,
            task_row=task_row,
            session=session,
            execution_event=execution_event,
        )

    @staticmethod
    def _with_dependency_execution_events(
        result: dict[str, Any],
        dependency_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not dependency_events:
            return result
        projected = dict(result)
        projected["dependency_execution_events"] = [dict(event) for event in dependency_events]
        failed_events = [event for event in dependency_events if not bool(event.get("ok"))]
        if failed_events and bool(projected.get("success")):
            projected["requested_reason"] = str(projected.get("reason") or "")
            projected["reason"] = str(failed_events[0].get("reason") or "dependency_transition_failed")
            projected["success"] = False
            projected["failure_class"] = str(failed_events[0].get("failure_class") or "ledger_append_failed")
            projected["state_mutation_applied"] = True
        return projected

    def _append_execution_event(
        self,
        event_type: str,
        *,
        task_row: dict[str, Any],
        session: TaskExecutionSession | None,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_type_str = str(event_type or "").strip().lower() or "unknown"
        try:
            transition_id, transition_timestamp = self._execution_transition_identity(
                session=session,
            )
        except (RuntimeError, ValueError) as exc:
            event_details = self._row_write_receipt_details_for_task(task_row)
            event_details.update(self._session_write_receipt_details_for_session(session))
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                details=event_details,
                append_error=f"execution transition identity unavailable: {exc}",
                failure_evidence=_execution_event_failure_evidence(exc, stage="transition_identity"),
            )
        event_details = self._row_write_receipt_details_for_task(task_row)
        event_details.update(self._session_write_receipt_details_for_session(session))
        for key, value in dict(details or {}).items():
            if key in {"row_write_receipt", "session_write_receipt"}:
                continue
            event_details[key] = value
        payload = build_task_runtime_execution_event_payload(
            event_type=event_type,
            workspace=self.workspace,
            task_row=task_row,
            session=session,
            details=event_details,
            timestamp=transition_timestamp,
        )
        try:
            fact = TaskRuntimeExecutionFactV1.from_payload(
                transition_id=transition_id,
                payload=payload,
            )
            payload = fact.to_record()
            event_type_str = fact.event_type
            appended = self._append_execution_fact(
                event_type_str=event_type_str,
                payload=payload,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to append task runtime execution event %s: %s",
                event_type_str,
                exc,
            )
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                details=event_details,
                append_error=str(exc),
                failure_evidence=_execution_event_failure_evidence(exc, stage="fact_append"),
            )
        payload["fact_event_id"] = appended.event_id
        payload["fact_stream"] = appended.stream
        payload["fact_storage_path"] = appended.storage_path
        if appended.appended_seq is not None:
            payload["fact_event_seq"] = int(appended.appended_seq)
        try:
            published = self._publish_factory_execution_event(payload)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to publish task runtime execution event %s: %s",
                event_type_str,
                exc,
            )
            return build_task_runtime_execution_event_append_result(
                event_type=event_type_str,
                fact_event_id=appended.event_id,
                fact_stream=appended.stream,
                fact_storage_path=appended.storage_path,
                fact_event_seq=appended.appended_seq,
                details=event_details,
                publish_error=str(exc),
                failure_evidence=_execution_event_failure_evidence(exc, stage="event_publish"),
            )
        if not published:
            factory_run_id = str(payload.get("factory_run_id") or "").strip()
            if factory_run_id:
                return build_task_runtime_execution_event_append_result(
                    event_type=event_type_str,
                    fact_event_id=appended.event_id,
                    fact_stream=appended.stream,
                    fact_storage_path=appended.storage_path,
                    fact_event_seq=appended.appended_seq,
                    details=event_details,
                    publish_error="factory_execution_event_publish_returned_false",
                    projection_evidence=_execution_event_projection_evidence(
                        factory_run_id=factory_run_id,
                        fact_event_id=appended.event_id,
                        fact_stream=appended.stream,
                        fact_event_seq=appended.appended_seq,
                    ),
                )
        return build_task_runtime_execution_event_append_result(
            event_type=event_type_str,
            fact_event_id=appended.event_id,
            fact_stream=appended.stream,
            fact_storage_path=appended.storage_path,
            fact_event_seq=appended.appended_seq,
            details=event_details,
            published=published,
        )

    def _execution_transition_identity(
        self,
        *,
        session: TaskExecutionSession | None,
    ) -> tuple[str, str | None]:
        """Return one event identity and an optional stable transition time.

        New terminal transitions already carry their identifier because
        ``mark_completed``/``mark_failed`` generate it before the session write.
        The write below is a compatibility migration for terminal sessions
        persisted before the field existed. Non-terminal events always receive
        a fresh identifier so heartbeat facts can never collapse.
        """

        if session is None or not is_terminal_session_status(session.status):
            return f"task-transition-{uuid.uuid4().hex}", None

        transition_id = str(session.terminal_transition_id or "").strip()
        if not transition_id:
            transition_id = session.ensure_terminal_transition_id()
            if not self._write_session(session):
                raise TaskExecutionSessionWriteConflictError("failed to persist terminal execution transition identity")
        transition_timestamp = str(session.released_at or session.last_heartbeat_at or "").strip() or None
        return transition_id, transition_timestamp

    def _row_write_receipt_details_for_task(self, task_row: Mapping[str, Any]) -> dict[str, Any]:
        """Return row-write receipt details for this task row identity."""

        task_id = self.normalize_task_id(task_row.get("id"))
        if task_id is None:
            return {}
        receipt = self._board.row_write_receipt_for_task(task_id)
        if receipt is None:
            return {}
        return {"row_write_receipt": receipt.to_dict()}

    def _session_write_receipt_for_session(
        self,
        session: TaskExecutionSession | None,
    ) -> TaskExecutionSessionWriteReceipt | None:
        """Return the latest successful session-write receipt for one session identity."""

        if session is None:
            return None
        task_id = self.normalize_task_id(session.task_id)
        if task_id is None:
            return None
        session_id = str(session.session_id or "").strip()
        if not session_id:
            return None
        with self._session_write_receipt_lock:
            return self._session_write_receipts_by_identity.get((task_id, session_id))

    def _session_write_receipt_details_for_session(
        self,
        session: TaskExecutionSession | None,
    ) -> dict[str, Any]:
        """Return session-write receipt details for this session identity."""

        receipt = self._session_write_receipt_for_session(session)
        if receipt is None:
            return {}
        return {"session_write_receipt": receipt.to_dict()}

    def _append_execution_fact(
        self,
        *,
        event_type_str: str,
        payload: dict[str, Any],
    ) -> FactEventAppendedV1:
        """Append through the TaskRuntime CAS boundary."""

        return self._append_execution_fact_with_cas(
            event_type_str=event_type_str,
            payload=payload,
        )

    def _next_execution_fact_expected_seq(self) -> int:
        """Return the next canonical sequence for the execution fact stream.

        The value is an optimistic CAS expectation, not an allocation.  The
        FactStream remains the only sequence allocator and rejects a stale
        expectation when another writer wins the race.

        Complexity:
            O(1) retained event memory and two bounded FactStream queries.
        """

        return query_fact_stream_head(
            QueryFactStreamHeadV1(
                workspace=self.workspace,
                stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
            )
        ).next_expected_seq

    def _append_execution_fact_with_cas(
        self,
        *,
        event_type_str: str,
        payload: dict[str, Any],
    ) -> FactEventAppendedV1:
        """Append an execution fact with optimistic CAS and bounded retry.

        Local writers are serialized to avoid needless self-contention; the
        ``expected_seq`` contract still arbitrates independent services and
        processes.  Only sequence drift is retryable.  Every other FactStream
        failure propagates to the execution-event receipt as a hard append
        failure.

        Complexity:
            O(a) constant-time cursor reads and append attempts for ``a`` CAS
            retries, bounded by ``_FACT_APPEND_CAS_MAX_ATTEMPTS``; O(1)
            auxiliary memory. Cursor recovery inside FactStream is O(n) only
            when its sequence index is absent or corrupt.
        """

        idempotency_key = str(payload.get("idempotency_key") or "").strip() or None
        replay_expected_seq: int | None = None
        last_drift: FactStreamError | None = None
        with self._execution_fact_append_lock:
            for _attempt in range(_FACT_APPEND_CAS_MAX_ATTEMPTS):
                expected_seq = (
                    replay_expected_seq if replay_expected_seq is not None else self._next_execution_fact_expected_seq()
                )
                try:
                    return append_fact_event(
                        AppendFactEventCommandV1(
                            workspace=self.workspace,
                            stream=TASK_RUNTIME_EXECUTION_STREAM_V1,
                            event_type=event_type_str,
                            payload=payload,
                            source=TASK_RUNTIME_EXECUTION_SOURCE_V1,
                            run_id=str(payload.get("run_id") or "").strip() or None,
                            task_id=str(payload.get("task_id") or "").strip() or None,
                            correlation_id=str(payload.get("session_id") or "").strip() or None,
                            idempotency_key=idempotency_key,
                            expected_seq=expected_seq,
                        )
                    )
                except FactStreamError as exc:
                    if exc.code != "expected_seq_drift":
                        raise
                    last_drift = exc
                    existing_seq = _coerce_fact_event_seq(exc.details.get("existing_seq"))
                    replay_expected_seq = existing_seq if idempotency_key and existing_seq is not None else None
        raise FactStreamError(
            "task_runtime.execution CAS retry budget exhausted",
            code="execution_fact_cas_exhausted",
            details={
                "workspace": self.workspace,
                "attempts": _FACT_APPEND_CAS_MAX_ATTEMPTS,
                "last_error": str(last_drift or "expected_seq_drift"),
            },
        )

    def _publish_factory_execution_event(self, payload: dict[str, Any]) -> bool:
        factory_run_id = str(payload.get("factory_run_id") or "").strip()
        if not factory_run_id:
            return False
        fact_event_id = str(payload.get("fact_event_id") or "").strip()
        if not fact_event_id:
            raise ValueError("fact_event_id is required for runtime.v2 execution wakeup")
        fact_event_seq = _coerce_fact_event_seq(payload.get("fact_event_seq"))
        if fact_event_seq is None:
            raise ValueError("fact_event_seq is required for runtime.v2 execution wakeup")
        try:
            roots = resolve_storage_roots(self.workspace)
            workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
            if not workspace_key:
                return False
            from polaris.infrastructure.log_pipeline.jetstream_publisher import (
                get_log_jetstream_publisher,
            )

            event_payload = dict(payload)
            director_run_id = str(event_payload.get("run_id") or "").strip()
            if director_run_id and director_run_id != factory_run_id:
                event_payload["director_run_id"] = director_run_id
            event_payload["type"] = "task_runtime_execution"
            event_payload["stage"] = "director_dispatch"
            event_payload["message"] = (
                f"Director task {event_payload.get('task_id') or '<unknown>'} "
                f"{event_payload.get('event_type') or 'updated'}"
            )
            envelope = {
                "schema_version": "runtime.v2",
                "event_id": fact_event_id,
                "workspace_key": workspace_key,
                "run_id": factory_run_id,
                "channel": f"event.factory:{factory_run_id}",
                "kind": "task_runtime_execution",
                "ts": event_payload.get("timestamp") or utc_now_iso(),
                "cursor": fact_event_seq,
                "trace_id": None,
                "payload": event_payload,
                "meta": {
                    "source": TASK_RUNTIME_EXECUTION_SOURCE_V1,
                    "fact_event_id": fact_event_id,
                    "fact_event_seq": fact_event_seq,
                    "fact_stream": TASK_RUNTIME_EXECUTION_STREAM_V1,
                },
            }
            return get_log_jetstream_publisher().publish(
                subject=f"hp.runtime.{workspace_key}.event.factory.{factory_run_id}",
                payload=envelope,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Task runtime factory progress publish failed: %s", exc)
            return False

    def _augment_task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        task_id = self.normalize_task_id(row.get("id"))
        if task_id is None:
            return dict(row)

        session = self._read_session(task_id)
        return self._augment_task_row_with_session(row, session=session)

    def _augment_task_row_with_session(
        self,
        row: Mapping[str, Any],
        *,
        session: TaskExecutionSession | None,
    ) -> dict[str, Any]:
        """Project a row from a caller-owned session snapshot without locking.

        Settlement projection uses the snapshot committed by its first phase;
        re-reading here would reacquire the session lock while an outer caller
        may still own its cooperative file lock.
        """

        row_copy = dict(row)
        terminal_session_superseded = False
        if session is not None:
            terminal_session_superseded = is_terminal_session_status(
                session.status
            ) and self._row_mapping_authorizes_retry_over_terminal_session(row_copy, session)
        return project_task_row_runtime_state(
            row_copy,
            task_status_value=row_copy.get("status"),
            session=session,
            terminal_session_superseded=terminal_session_superseded,
        )

    def _build_runtime_metadata(
        self,
        *,
        session: TaskExecutionSession,
        effective_status: str,
        resume_state: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_task_runtime_metadata(
            session=session,
            effective_status=effective_status,
            resume_state=resume_state,
            extra_metadata=extra_metadata,
        )


def reset_runtime_task_records(
    workspace: str,
    *,
    keep_plan: bool = False,
    factory_run_id: str | None = None,
) -> dict[str, object]:
    """Clear runtime taskboard state through the owning cell service."""
    return TaskRuntimeService(workspace).reset_records(
        keep_plan=keep_plan,
        factory_run_id=factory_run_id,
    )


__all__ = ["TaskRuntimeService", "reset_runtime_task_records"]
