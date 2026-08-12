"""Stable internal service exports for `runtime.task_runtime`.

This package is the lossless successor of the former ``service`` module.
It re-exports every previously-public symbol from the same import path so
that ``import ...internal.service`` and ``from ...internal.service import X``
keep resolving identically for all external importers.
"""

from __future__ import annotations

# Re-export stdlib / typing names that were previously importable from the module
import hashlib
import json
import logging
import os
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
    AdmitDirectedEffectParentBatchCommandV1,
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

from ..directed_effect_operation import (
    DirectedEffectOperationRepository,
    DirectedEffectSettlementPreBarrierVerdictV1,
)
from ..execution_session import (
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
    project_task_runtime_realtime_event_payload,
    sanitize_summary,
    task_row_status_counts,
    terminal_session_timestamp,
    terminal_task_status_value_for_session_status,
    utc_now,
    utc_now_iso,
)
from ._helpers import (
    _DEPENDENCY_SATISFACTION_METADATA_KEY,
    _DEPENDENCY_SATISFACTION_SCHEMA_V1,
    _DIRECTED_EFFECT_RECOVERY_LEASE_LOGICAL_PATH,
    _DIRECTED_EFFECT_RECOVERY_LEASE_SCHEMA_V1,
    _EXECUTION_ATTEMPT_SETTLEMENT_LOCK_TIMEOUT_SECONDS,
    _FACT_APPEND_CAS_MAX_ATTEMPTS,
    _OWNER_REWORK_EXECUTION_AUTHORIZATION_METADATA_KEY,
    _OWNER_REWORK_EXECUTION_AUTHORIZATION_SCHEMA_V1,
    _OWNER_REWORK_HANDOFFS_METADATA_KEY,
    _OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
    _OWNER_REWORK_ROUTE_SCHEMA_V1,
    _PENDING_TERMINAL_INTENT_METADATA_KEY,
    _PENDING_TERMINAL_INTENT_SCHEMA_V1,
    _REEXECUTION_METADATA_DROP_KEYS,
    _SAME_TASK_LOCAL_REWORK_AUTHORIZATIONS_METADATA_KEY,
    _TASK_ID_PATTERN,
    _TASK_SESSION_FILE_PATTERN,
    TaskExecutionSessionWriteConflictError,
    _build_factory_run_binding_result,
    _canonical_sha256,
    _DependencySatisfactionDecision,
    _DirectedEffectRecoverySessionSweep,
    _DirectedEffectRecoveryTaskCatalog,
    _execution_event_failure_evidence,
    _execution_event_projection_evidence,
    _ExecutionEventFailureEvidence,
    _ExecutionEventProjectionEvidence,
    _is_execution_task_row_update_status,
    _is_terminal_task_row_update_status,
    _LockedSessionSuspendResult,
    _normalize_owner_rework_handoff_record,
    _PreparedTerminalSettlement,
    _raise_retired_entity_api,
    _terminal_task_status_for_session,
    logger,
)
from ._service import TaskRuntimeService


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
