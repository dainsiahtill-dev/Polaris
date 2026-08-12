"""Factory Run Service - formal service for unattended development with persistence.

This package is the lossless successor of the former ``factory_run_service``
module. It re-exports every previously-public symbol from the same import path so
that ``import ...factory_run_service`` and ``from ...factory_run_service import X``
keep resolving identically for all external importers.

This package is the durable-lifecycle orchestrator (``FactoryRunService``) plus a
thin re-export shim. The data-contracts and shared cancel-registry foundation now
live in :mod:`factory_run_models`, and the production stage executor god-class
lives in :mod:`factory_stage_executor`. Both are re-exported here so the original
import path resolves identically for every existing caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os  # re-exported for lossless surface + test monkeypatch of ``os.name``
import re  # re-exported for lossless surface compatibility
import shutil  # re-exported for lossless surface + test monkeypatch of ``shutil.which``
import subprocess  # re-exported for lossless surface compatibility
import threading  # re-exported for lossless surface compatibility
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field  # re-exported for lossless surface
from datetime import datetime, timezone  # re-exported for lossless surface
from enum import Enum  # re-exported for lossless surface
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol  # Protocol re-exported for lossless surface

from polaris.cells.chief_engineer.blueprint.public import GenerateTaskBlueprintCommandV1, generate_task_blueprint
from polaris.cells.control_plane.run_ledger.public import (
    FactorySettlementBarrierResultV1,
    query_factory_settlement_barrier,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    EnsureSegmentedFactLedgerCommandV1,
    SegmentedFactLedgerReadyV1,
    bootstrap_fact_stream_workspace,
    ensure_segmented_fact_ledger,
    fact_stream_bootstrap_streams,
)
from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    contains_factory_role_evidence_runtime_authority,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
    SettleFactoryPhysicalAttemptV1,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA,
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    AppendFactoryProviderAttemptRecoveryTerminalV1,
    FactoryProviderAttemptLifecycleReplaySnapshotV1,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    append_factory_provider_attempt_recovery_terminal,
    factory_provider_attempt_lifecycle_stream,
    query_factory_provider_attempt_lifecycle_replay,
)
from polaris.cells.runtime.task_runtime.public.contracts import (
    FenceExpiredFactoryRunSessionsCommandV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    fence_expired_factory_run_sessions,
    query_factory_run_settlement,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.kernelone.storage import resolve_logical_path, resolve_storage_roots
from polaris.kernelone.utils import utc_now_iso

from ..factory_event_chain import (
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
)
from ..factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptLiveControlPort,
)
from ..factory_physical_attempt_replay import (
    FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA,
    FactoryPhysicalAttemptReplayError,
    FactoryPhysicalAttemptReplayFenceV1,
    FactoryPhysicalAttemptReplayPolicyV1,
    build_factory_physical_attempt_replay_candidate,
)
from ..factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FactoryRoleEvidenceAuthorityPort,
    FactoryRoleEvidenceReplaySnapshotV1,
    FactoryRoleEvidenceStageAuthorityV1,
    factory_role_evidence_authority_stream,
    query_factory_role_evidence_replay_snapshot,
)
from ..factory_role_evidence_source_resolver import CanonicalFactoryRoleEvidenceSourceAuthority
from ..factory_run_admission import FactoryWorkspaceRunAdmission
from ..factory_run_models import (
    _FACTORY_CANCEL_EVENTS,
    _FACTORY_CANCEL_EVENTS_GUARD,
    _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS,
    _PM_ARCHITECT_DOC_MAX_CHARS,
    _PM_DIRECTIVE_MAX_CHARS,
    _PM_DIRECTIVE_META_LINE_PATTERN,
    _PM_ORIGINAL_DIRECTIVE_MAX_CHARS,
    _PM_PLAN_META_DIAGNOSTIC_MARKERS,
    _QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING,
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS,
    SUPPORTED_FACTORY_STAGES,
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    FactoryStageExecutor,
    StageResult,
    _factory_cancel_key,
    _register_factory_cancel_event,
    _signal_factory_cancel_event,
    _unregister_factory_cancel_event,
)
from ..factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
    CEBlueprintArtifactBindingV1,
    CEReviewManifestArtifactBindingV1,
    FactoryStageArtifactBindingError,
    FactoryStageArtifactBindingsV1,
    PMContractArtifactBindingV1,
    PMStageEventArtifactBindingV1,
    RevalidatedPMStageArtifactBindingV1,
    build_chief_engineer_stage_artifact_bindings,
    build_pm_stage_artifact_bindings,
    revalidate_pm_stage_artifact_binding,
)
from ..factory_stage_executor import OrchestrationStageExecutor
from ..factory_stage_persistence import (
    FactoryLastStageCommitV1,
    FactoryStagePersistenceCommittedV1,
    FactoryStagePersistenceError,
    bounded_redacted_error,
    build_stage_persistence_intent,
    canonical_checkpoint_sha256,
    canonical_run_snapshot_sha256,
    reduce_factory_stage_persistence,
    validate_committed_checkpoint_hashes,
    validate_current_stage_commit_pointer,
)
from ._helpers import (
    _AUTOMATIC_ROUTER_MUTATION_GUARD_MATRIX,
    _CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY,
    _CHILD_SESSIONS_SETTLED_METADATA_KEY,
    _FACTORY_FANOUT_MAX_PAYLOAD_BYTES,
    _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    _STAGE_IN_FLIGHT_METADATA_KEY,
    _WORKSPACE_LEASE_METADATA_KEY,
    _FactoryProviderAttemptRecoveryFence,
    _FactoryStageCancellationCutError,
    _FactoryStageCommitArbitration,
    logger,
)
from ._service import FactoryRunService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceReleaseEvidenceV1,
        FactoryWorkspaceRunLeaseV1,
    )


def _factory_jetstream_fanout_timeout_seconds() -> float:
    """Resolve the JetStream fanout timeout for ``_append_event``.

    Defined here (not imported from ``factory_run_models``) so it reads the
    module-level ``_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS`` bound in THIS
    module. This preserves the original single-file behavior where the helper
    and constant were co-located, keeping the constant monkeypatch-able via
    ``factory_run_service._FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS``.
    """
    raw = os.getenv("KERNELONE_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS")
    if raw is None:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS
    try:
        return max(float(raw), 0.05)
    except ValueError:
        return _FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS


# NOTE: ``__all__`` intentionally re-exports the symbols, stdlib modules, and
# private constants/helpers that the original single-file module bound at module
# scope. Keeping them here preserves the historical public+private import surface
# (callers / tests import these from ``factory_run_service``) and keeps the names
# from being stripped by ruff as "unused" — they are deliberate re-exports.
__all__ = [
    "DEFAULT_DIRECTOR_MAX_PARALLELISM",
    "DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS",
    "SUPPORTED_FACTORY_STAGES",
    "TERMINAL_RUN_STATUSES",
    "_FACTORY_CANCEL_EVENTS",
    "_FACTORY_CANCEL_EVENTS_GUARD",
    "_FACTORY_JETSTREAM_FANOUT_TIMEOUT_SECONDS",
    "_PM_ARCHITECT_DOC_MAX_CHARS",
    "_PM_DIRECTIVE_MAX_CHARS",
    "_PM_DIRECTIVE_META_LINE_PATTERN",
    "_PM_ORIGINAL_DIRECTIVE_MAX_CHARS",
    "_PM_PLAN_META_DIAGNOSTIC_MARKERS",
    "_QA_LLM_JUDGEMENT_UNAVAILABLE_WARNING",
    "_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS",
    "_WORKSPACE_VALIDATION_TIMEOUT_SECONDS",
    "CommandResult",
    "Enum",
    "FactoryConfig",
    "FactoryRun",
    "FactoryRunService",
    "FactoryRunStatus",
    "FactoryStageExecutor",
    "GenerateTaskBlueprintCommandV1",
    "KernelFileSystem",
    "OrchestrationStageExecutor",
    "Protocol",
    "StageResult",
    "TaskRuntimeService",
    "_factory_cancel_key",
    "_factory_jetstream_fanout_timeout_seconds",
    "_register_factory_cancel_event",
    "_signal_factory_cancel_event",
    "_unregister_factory_cancel_event",
    "asdict",
    "dataclass",
    "datetime",
    "field",
    "generate_task_blueprint",
    "get_default_adapter",
    "os",
    "re",
    "resolve_logical_path",
    "resolve_storage_roots",
    "shutil",
    "subprocess",
    "threading",
    "timezone",
    "utc_now_iso",
    ]
