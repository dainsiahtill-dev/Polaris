"""TaskRuntime-owned Directed Effect Operation v1 authority.

One lease-independent execution-attempt registry is the sole authority for
parent existence and OPEN state. Each admitted parent owns a separate child
operation stream. Registry and operation snapshots are never authorization
inputs; all decisions rebuild bounded strict FactStream partitions.

This package is the lossless successor of the former ``directed_effect_operation``
module. It re-exports every previously-public symbol from the same import path.
"""

from __future__ import annotations

import hashlib
import json

# --- inspect / path-depth compatibility with historical monofile ---
import linecache as _linecache
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, Path as _Path
from typing import Any, Literal, TypeAlias, cast

from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    AppendIfGuardedSnapshotCommandV1,
    EnrollFactStreamStreamsCommandV1,
    FactStreamError,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotV1,
    QueryFactEventsV1,
    ReadGuardedFactSnapshotCommandV1,
    append_fact_event,
    append_if_guarded_snapshot,
    enroll_fact_stream_streams,
    query_fact_events,
    read_guarded_fact_snapshot,
)

from ...public.contracts import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_MEMBER_SCHEMA_V1,
    DIRECTED_EFFECT_INVENTORY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V1,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V2,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V3,
    DIRECTED_EFFECT_OPERATION_SCHEMA_V4,
    DIRECTED_EFFECT_OPERATION_SNAPSHOT_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_READINESS_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_PROJECTION_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V2,
    DIRECTED_EFFECT_PARENT_REGISTRY_SCHEMA_V3,
    AbortDirectedEffectOperationCommandV1,
    AdmitDirectedEffectOperationCommandV1,
    AdmitDirectedEffectParentBatchCommandV1,
    AdmitDirectedEffectParentCommandV1,
    ClaimDirectedEffectCommandV1,
    CommitDirectedEffectReceiptCommandV1,
    DeadLetterDirectedEffectOperationCommandV1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryCodeV1,
    DirectedEffectInventoryIntentV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectInventoryProjectionV1,
    DirectedEffectInventoryResultV1,
    DirectedEffectOperationCodeV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectOperationResultV1,
    DirectedEffectOperationSnapshotV1,
    DirectedEffectOperationStateV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentReadinessProjectionV1,
    DirectedEffectParentReadinessResultV1,
    DirectedEffectParentReadinessStateCountV1,
    DirectedEffectParentRegistryIdentityV1,
    DirectedEffectParentRegistryProjectionV1,
    DirectedEffectParentRegistryResultV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    FinalizeDirectedEffectInventoryAdmissionCommandV1,
    GetDirectedEffectInventoryQueryV1,
    GetDirectedEffectOperationQueryV1,
    GetDirectedEffectParentReadinessQueryV1,
    GetDirectedEffectParentRegistryQueryV1,
    MarkDirectedEffectRecoveryPendingCommandV1,
    ParentCorrelationV1,
    SealDirectedEffectInventoryCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    TaskRuntimeExecutionAttemptValidationCodeV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)
from ._helpers import (
    _APPEND_FACT_FAILURE_CODES,
    _DIRECTED_EFFECT_ADMISSION_SET_HASH_SCHEMA_V1,
    _DIRECTED_EFFECT_INVENTORY_HASH_SCHEMA_V1,
    _EXECUTION_ATTEMPT_FAILURE_CODES,
    _GUARDED_REPREPARE_DRIFT_CODES,
    _MAX_GUARDED_ATTEMPTS,
    _MAX_OPERATION_EVENTS,
    _MAX_REGISTRY_EVENTS,
    _OPERATION_EVENT_PREFIX,
    _PARENT_ADMITTED_EVENT_TYPE,
    _PARENT_CLOSED_EVENT_TYPE,
    _PARENT_INVENTORY_READY_EVENT_TYPE,
    _PARENT_INVENTORY_SEALED_EVENT_TYPE,
    _READ_FACT_FAILURE_CODES,
    _TERMINAL_STATES,
    DirectedEffectSettlementPreBarrierVerdictV1,
    _admission_set_hash,
    _Aggregate,
    _AuthorityCommand,
    _binding_id,
    _canonical_json,
    _CloseDirectedEffectByParentCommandV1,
    _CloseDirectedEffectByParentForBatchCommandV1,
    _Command,
    _CommandKind,
    _CommittedTransition,
    _DirectedEffectRecoveryCursor,
    _DirectedEffectRecoveryRepositorySweep,
    _DirectedEffectRecoverySweepPreparation,
    _FactOperation,
    _hash_token,
    _inventory_effect_id,
    _inventory_hash,
    _inventory_member,
    _InventoryGuardedCommand,
    _InventoryOperationProjection,
    _is_canonical_sha256,
    _is_timezone_aware_timestamp,
    _new_append_attempt_nonce,
    _NormalizedDirectedEffectReplayDescriptorV1,
    _NormalizedDirectedEffectTransitionV1,
    _operation_event_type,
    _operation_id,
    _operation_stream_token,
    _OperationStreamReduction,
    _ParentBindingReadCommand,
    _ParentRegistry,
    _ParentRegistryBoundCommand,
    _ParentSettlementGuardedClose,
    _ParentSettlementPreparation,
    _ReadCommand,
    _ReadyDirectedEffectInventory,
    _ReadyGatedCommand,
    _ReadyOperationContext,
    _registry_fact_idempotency_key,
    _registry_stream_token,
    _RegistryAdmission,
    _SealedDirectedEffectInventory,
    _SettlementPreBarrierCode,
    _StreamKind,
    _StreamRead,
    _StrictInventoryConfirmation,
    _StrictOperationProjection,
)
from ._repository import DirectedEffectOperationRepository

_SURFACE_PATH = _Path(__file__).resolve().parent / "_module_surface.source"
_SURFACE_TEXT = _SURFACE_PATH.read_text(encoding="utf-8")
# Historical monofile path: .../internal/directed_effect_operation.py
# parents[4] == polaris package root (used by characterization path math).
_FAKE_MONOFILE = str(_Path(__file__).resolve().parent.parent / "directed_effect_operation.py")
_SURFACE_LINES = _SURFACE_TEXT.splitlines(keepends=True)
if _SURFACE_LINES and not _SURFACE_LINES[-1].endswith("\n"):
    _SURFACE_LINES[-1] = _SURFACE_LINES[-1] + "\n"
_linecache.cache[_FAKE_MONOFILE] = (
    sum(len(line) for line in _SURFACE_LINES),
    None,
    _SURFACE_LINES,
    _FAKE_MONOFILE,
)
__file__ = _FAKE_MONOFILE
