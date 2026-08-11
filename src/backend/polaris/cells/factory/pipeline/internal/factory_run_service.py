"""Factory Run Service - formal service for unattended development with persistence.

This module is the durable-lifecycle orchestrator (``FactoryRunService``) plus a
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

from .factory_event_chain import (
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
)
from .factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptLiveControlPort,
)
from .factory_physical_attempt_replay import (
    FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA,
    FactoryPhysicalAttemptReplayError,
    FactoryPhysicalAttemptReplayFenceV1,
    FactoryPhysicalAttemptReplayPolicyV1,
    build_factory_physical_attempt_replay_candidate,
)
from .factory_role_evidence_authority import (
    FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    FactoryRoleEvidenceAuthorityPort,
    FactoryRoleEvidenceReplaySnapshotV1,
    FactoryRoleEvidenceStageAuthorityV1,
    factory_role_evidence_authority_stream,
    query_factory_role_evidence_replay_snapshot,
)
from .factory_role_evidence_source_resolver import CanonicalFactoryRoleEvidenceSourceAuthority
from .factory_run_admission import FactoryWorkspaceRunAdmission
from .factory_run_models import (
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
from .factory_stage_artifact_bindings import (
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
from .factory_stage_executor import OrchestrationStageExecutor
from .factory_stage_persistence import (
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

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceReleaseEvidenceV1,
        FactoryWorkspaceRunLeaseV1,
    )


class _FactoryStageCancellationCutError(RuntimeError):
    """Internal cut proving outer cancellation won before marker append."""


class _FactoryStageCommitArbitration:
    """One shared linearization point for cancellation and marker durability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False

    @contextlib.contextmanager
    def commit_permit(self) -> Iterator[None]:
        """Hold the permit across marker fsync and strict post-append reread."""

        with self._lock:
            if self._cancelled:
                raise _FactoryStageCancellationCutError(
                    "outer cancellation cut won before authoritative marker durability"
                )
            yield

    def mark_cancelled(self) -> None:
        """Linearize cancellation without ever blocking the asyncio event loop."""

        with self._lock:
            self._cancelled = True


logger = logging.getLogger(__name__)

_WORKSPACE_LEASE_METADATA_KEY = "factory_workspace_run_lease"
_STAGE_IN_FLIGHT_METADATA_KEY = "factory_stage_in_flight"
_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY = FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY
_CHILD_SESSIONS_SETTLED_METADATA_KEY = "factory_child_sessions_settled"
_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY = "factory_child_session_settlement_evidence"

# Service-owned semantic audit matrix for every automatic-router write family.
# Router code may select an operation, but it cannot authorize or persist one.
_AUTOMATIC_ROUTER_MUTATION_GUARD_MATRIX: dict[str, tuple[str, ...]] = {
    "summary_projection": ("store.save_run",),
    "chief_engineer_local_rework": (
        "store.save_run",
        "_append_event",
        "reconcile_stage_execution_for_reentry",
    ),
    "chief_engineer_local_rework_reentry": ("reconcile_stage_execution_for_reentry",),
    "director_local_rework": ("store.save_run", "_append_event", "reconcile_stage_execution_for_reentry"),
    "director_local_rework_reentry": ("reconcile_stage_execution_for_reentry",),
    "quality_rework": ("store.save_run", "_append_event", "reconcile_stage_execution_for_reentry"),
    "quality_rework_reentry": ("reconcile_stage_execution_for_reentry",),
    "stage_sequence": ("execute_stage",),
    "run_configuration": ("store.save_run",),
    "delivery_loop_projection": ("store.save_run", "_append_event"),
    "success_terminalization": ("_persist_run_summary", "complete_run"),
    "failure_terminalization": (
        "reconcile_stage_execution_for_reentry",
        "store.save_run",
        "_persist_run_summary",
        "_append_event",
        "complete_run",
    ),
    "factory_failure_terminalization": ("reconcile_stage_execution_for_reentry",),
}


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


# Realtime fanout payload bound: must stay under the JetStream 256KB max-message
# limit AND the WS 1MiB frame limit. Large director stage outputs (full
# ``task_results`` reach ~2.7MB on big projects) remain durable on disk via
# ``store.append_event``; the best-effort realtime fanout only needs control
# fields plus a bounded preview. Headroom kept below 256KB for envelope fields.
_FACTORY_FANOUT_MAX_PAYLOAD_BYTES = 200_000


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


class _FactoryProviderAttemptRecoveryFence:
    """Private non-serializable capability held under Factory admission lock."""

    verification_scope = "factory"

    def __init__(self, *, factory_run_id: str, revalidate: Callable[[], None]) -> None:
        self.factory_run_id = str(factory_run_id or "").strip()
        if not self.factory_run_id:
            raise ValueError("factory_run_id_missing")
        self._revalidate = revalidate

    @contextlib.contextmanager
    def hold_recovery_terminal(
        self,
        command: AppendFactoryProviderAttemptRecoveryTerminalV1,
    ) -> Iterator[Callable[[], None]]:
        if command.attempt.factory_run_id != self.factory_run_id:
            raise FactoryPhysicalAttemptReplayError("factory_provider_attempt_recovery_fence_scope_mismatch")
        self._revalidate()
        yield self._revalidate
        self._revalidate()


class FactoryRunService:
    """Formal service for Factory runs with persistence and recovery."""

    # 细粒度锁桶数量 - 减少跨 run 的竞争
    _LOCK_BUCKETS = 64

    def __init__(
        self,
        workspace: Path,
        cache_root: Path | None = None,
        executor: FactoryStageExecutor | None = None,
        admission: FactoryWorkspaceRunAdmission | None = None,
        settlement_barrier_query: Callable[
            [str | Path, str], FactorySettlementBarrierResultV1
        ] = query_factory_settlement_barrier,
        stage_artifact_binding_builder: Callable[[str, StageResult], FactoryStageArtifactBindingsV1 | None]
        | None = None,
    ) -> None:
        from .factory_store import FactoryStore

        self.workspace = Path(workspace)
        self.cache_root = (
            Path(cache_root)
            if cache_root is not None
            else Path(resolve_storage_roots(str(self.workspace)).runtime_root)
        )
        self.store = FactoryStore(self.cache_root / "factory")
        self._admission = admission or FactoryWorkspaceRunAdmission(
            self.workspace,
            state_root=self.cache_root / "factory",
        )
        self._settlement_barrier_query = settlement_barrier_query
        # 细粒度锁: 按 run_id 哈希分片，减少竞争
        self._run_locks: list[asyncio.Lock] = [asyncio.Lock() for _ in range(self._LOCK_BUCKETS)]
        self._physical_attempt_coordinators: dict[str, FactoryPhysicalAttemptLiveControlPort] = {}
        self._executor: FactoryStageExecutor = executor or OrchestrationStageExecutor(self.workspace)
        # Explicit injected executors are test/adapter seams and cannot claim
        # production PM/CE artifacts. Production construction (executor=None)
        # always uses the strict Factory-owned binding builders below.
        self._stage_artifact_binding_builder: Callable[[str, StageResult], FactoryStageArtifactBindingsV1 | None] | None
        if stage_artifact_binding_builder is not None:
            self._stage_artifact_binding_builder = stage_artifact_binding_builder
        elif executor is not None:
            self._stage_artifact_binding_builder = lambda _run_id, _result: None
        else:
            self._stage_artifact_binding_builder = None

    def _get_run_lock(self, run_id: str) -> asyncio.Lock:
        """获取 run_id 对应的细粒度锁。

        使用哈希分片确保同一 run 的操作串行化，不同 run 可并行。
        """
        bucket = hash(run_id) % self._LOCK_BUCKETS
        return self._run_locks[bucket]

    def _physical_attempt_coordinator(self, factory_run_id: str) -> FactoryPhysicalAttemptLiveControlPort:
        """Return the one process-local coordinator owned by this Factory run.

        B3.4 keeps transport disabled until durable replay can reconstruct this
        registry after restart.  Within one live service lifetime, every stage,
        role grant, retry and fanout path for the run receives this exact object.
        """

        normalized_run_id = str(factory_run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("factory_physical_attempt_factory_run_id_missing")
        coordinator = self._physical_attempt_coordinators.get(normalized_run_id)
        if coordinator is None:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_replay_required")
        return coordinator

    def _revalidate_active_physical_attempt_stage_claim(
        self,
        grant: FactoryPhysicalAttemptGrantViewV1,
    ) -> None:
        """Re-read the exact durable workspace fence and stage claim."""

        with self._admission.hold_active_stage_claim(
            grant.factory_run_id,
            fencing_token=grant.workspace_fencing_token,
            stage=grant.stage,
            attempt=grant.stage_claim_attempt,
            nonce=grant.stage_claim_nonce,
        ):
            pass

    def _require_physical_attempt_admission_open(
        self,
        factory_run_id: str,
    ) -> FactoryPhysicalAttemptLiveControlPort:
        current = self._admission.current()
        if (
            current is not None
            and current.run_id == factory_run_id
            and (
                current.drain_reason == "factory_physical_attempt_restart_replay_fence"
                or (
                    current.release_evidence is not None
                    and current.release_evidence.details.get("physical_attempt_replay_fence") is True
                )
            )
        ):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_recovered_run_permanently_closed")
        coordinator = self._physical_attempt_coordinator(factory_run_id)
        if coordinator.admission_closed:
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_recovered_run_permanently_closed")
        return coordinator

    def _ensure_physical_attempt_replay_ledgers(self, factory_run_id: str) -> None:
        """Pre-enrol both replay streams while the new run is being created."""

        workspace = str(self.workspace.resolve())
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=workspace,
                streams=fact_stream_bootstrap_streams(),
                maintenance_reason="factory_physical_attempt_replay_pre_enrollment",
            )
        )
        streams = (
            factory_role_evidence_authority_stream(factory_run_id),
            factory_provider_attempt_lifecycle_stream(factory_run_id),
        )
        for logical_stream in streams:
            ready = ensure_segmented_fact_ledger(
                EnsureSegmentedFactLedgerCommandV1(
                    workspace=workspace,
                    logical_stream=logical_stream,
                    maintenance_reason="factory_physical_attempt_replay_pre_enrollment",
                    retention="pinned_audit_no_delete",
                )
            )
            if (
                type(ready) is not SegmentedFactLedgerReadyV1
                or ready.workspace != workspace
                or ready.logical_stream != logical_stream
                or ready.retention != "pinned_audit_no_delete"
                or ready.storage_prefix != ready.head.storage_prefix
            ):
                raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_ledger_enrollment_invalid")

    def _capture_physical_attempt_replay_fence(
        self,
        *,
        factory_run_id: str,
        lease: FactoryWorkspaceRunLeaseV1,
        deadline: float | None = None,
    ) -> FactoryPhysicalAttemptReplayFenceV1:
        """Capture the strict Factory-chain head under an exact lifecycle hold."""

        self._require_physical_attempt_replay_deadline(deadline)
        claim = lease.lifecycle_operation_claim
        if claim is None or claim.run_id != factory_run_id:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_lifecycle_claim_missing")
        events = self.store._read_authoritative_events_sync(factory_run_id)
        self._require_physical_attempt_replay_deadline(deadline)
        if not events:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_factory_head_missing")
        head = events[-1]
        head_sequence = head.get("chain_sequence")
        head_hash = head.get("chain_event_hash")
        if type(head_sequence) is not int or head_sequence < 1 or type(head_hash) is not str:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_factory_head_invalid")
        run_snapshot = self.store._read_strict_snapshot_sync(self.store.run_snapshot_ref(factory_run_id))
        self._require_physical_attempt_replay_deadline(deadline)
        try:
            persisted_run = FactoryRun.from_dict(run_snapshot)
        except (KeyError, TypeError, ValueError) as exc:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_run_snapshot_invalid") from exc
        if persisted_run.id != factory_run_id:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_run_identity_mismatch")
        current_stage = str(
            persisted_run.metadata.get("current_stage")
            or persisted_run.recovery_point
            or persisted_run.metadata.get("last_stage")
            or "start"
        ).strip()
        return FactoryPhysicalAttemptReplayFenceV1(
            schema_version=FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA,
            factory_run_id=factory_run_id,
            factory_stage_head_sequence=head_sequence,
            factory_stage_head_hash=head_hash,
            workspace_fencing_token=lease.fencing_token,
            current_stage=current_stage,
            fence_kind="lifecycle_operation",
            fence_sequence=claim.sequence,
            fence_nonce=claim.nonce,
            replay_fenced=True,
            live_mutation_forbidden=True,
        )

    def _capture_physical_attempt_replay_views(
        self,
        factory_run_id: str,
        *,
        deadline: float | None = None,
    ) -> tuple[FactoryRoleEvidenceReplaySnapshotV1, FactoryProviderAttemptLifecycleReplaySnapshotV1]:
        self._require_physical_attempt_replay_deadline(deadline)
        role_evidence = query_factory_role_evidence_replay_snapshot(
            workspace=self.workspace,
            factory_run_id=factory_run_id,
        )
        self._require_physical_attempt_replay_deadline(deadline)
        lifecycle = query_factory_provider_attempt_lifecycle_replay(
            QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(self.workspace.resolve()),
                factory_run_id=factory_run_id,
            )
        )
        self._require_physical_attempt_replay_deadline(deadline)
        return role_evidence, lifecycle

    @staticmethod
    def _require_physical_attempt_replay_deadline(deadline: float | None) -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_head_unstable")

    def _recover_physical_attempt_coordinator(
        self,
        *,
        run: FactoryRun,
        lease: FactoryWorkspaceRunLeaseV1,
    ) -> FactoryPhysicalAttemptLiveControlPort:
        """Strictly replay one run or fail closed without restoring admission."""

        existing = self._physical_attempt_coordinators.get(run.id)
        if existing is not None:
            drain = existing.close()
            if not drain.settled:
                raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_live_recovery_unsettled")
            return existing

        claim = lease.lifecycle_operation_claim
        if claim is None or claim.run_id != run.id:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_lifecycle_claim_missing")
        policy = FactoryPhysicalAttemptReplayPolicyV1()
        deadline = time.monotonic() + policy.total_deadline_seconds
        for _full_replay in range(policy.max_full_replays):
            if time.monotonic() >= deadline:
                break
            try:
                with self._admission.hold_active_lifecycle_operation_claim(
                    run.id,
                    fencing_token=lease.fencing_token,
                    operation=claim.operation,
                    sequence=claim.sequence,
                    nonce=claim.nonce,
                    allow_expired_owner=claim.operation == "recover_stale_workspace_owner",
                ) as revalidate:
                    self._require_physical_attempt_replay_deadline(deadline)
                    held_lease = revalidate()
                    self._require_physical_attempt_replay_deadline(deadline)
                    fence = self._capture_physical_attempt_replay_fence(
                        factory_run_id=run.id,
                        lease=held_lease,
                        deadline=deadline,
                    )
                    self._require_physical_attempt_replay_deadline(deadline)
                    role_evidence, lifecycle = self._capture_physical_attempt_replay_views(
                        run.id,
                        deadline=deadline,
                    )
                    candidate = build_factory_physical_attempt_replay_candidate(
                        fence=fence,
                        role_evidence=role_evidence,
                        lifecycle=lifecycle,
                    )

                    held_lease = revalidate()
                    self._require_physical_attempt_replay_deadline(deadline)
                    if (
                        self._capture_physical_attempt_replay_fence(
                            factory_run_id=run.id,
                            lease=held_lease,
                            deadline=deadline,
                        )
                        != fence
                    ):
                        continue
                    verified_role_evidence, verified_lifecycle = self._capture_physical_attempt_replay_views(
                        run.id,
                        deadline=deadline,
                    )
                    if verified_role_evidence != role_evidence or verified_lifecycle != lifecycle:
                        continue

                    coordinator, recovery_work = FactoryPhysicalAttemptLiveControlPort.from_replay_candidate(candidate)
                    current_lifecycle = verified_lifecycle
                    restart_full_replay = False
                    for work in recovery_work:
                        if time.monotonic() >= deadline:
                            restart_full_replay = True
                            break
                        held_lease = revalidate()
                        self._require_physical_attempt_replay_deadline(deadline)
                        if (
                            self._capture_physical_attempt_replay_fence(
                                factory_run_id=run.id,
                                lease=held_lease,
                                deadline=deadline,
                            )
                            != fence
                        ):
                            restart_full_replay = True
                            break
                        observed_role_evidence, observed_lifecycle = self._capture_physical_attempt_replay_views(
                            run.id,
                            deadline=deadline,
                        )
                        if observed_role_evidence != role_evidence:
                            restart_full_replay = True
                            break
                        if observed_lifecycle != current_lifecycle:
                            restart_full_replay = True
                            break

                        def revalidate_recovery_fence(
                            expected_fence: FactoryPhysicalAttemptReplayFenceV1 = fence,
                            expected_role_evidence: FactoryRoleEvidenceReplaySnapshotV1 = role_evidence,
                        ) -> None:
                            current_lease = revalidate()
                            self._require_physical_attempt_replay_deadline(deadline)
                            if (
                                self._capture_physical_attempt_replay_fence(
                                    factory_run_id=run.id,
                                    lease=current_lease,
                                    deadline=deadline,
                                )
                                != expected_fence
                            ):
                                raise RuntimeError("factory_physical_attempt_replay_head_drift")
                            observed_role, _ = self._capture_physical_attempt_replay_views(
                                run.id,
                                deadline=deadline,
                            )
                            if observed_role != expected_role_evidence:
                                raise RuntimeError("factory_physical_attempt_replay_head_drift")

                        terminal_receipt = append_factory_provider_attempt_recovery_terminal(
                            AppendFactoryProviderAttemptRecoveryTerminalV1(
                                schema_version=APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA,
                                workspace=str(self.workspace.resolve()),
                                attempt=work.attempt,
                                lease=work.lease,
                                context_snapshot_ref=work.context_snapshot_ref,
                                pin_hash=work.pin_hash,
                                expected_lifecycle_head_sequence=observed_lifecycle.captured_head.global_seq,
                                expected_lifecycle_head_hash=observed_lifecycle.captured_head.head_hash,
                            ),
                            recovery_fence=_FactoryProviderAttemptRecoveryFence(
                                factory_run_id=run.id,
                                revalidate=revalidate_recovery_fence,
                            ),
                        )
                        coordinator.settle(
                            SettleFactoryPhysicalAttemptV1(
                                schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
                                lease=work.lease,
                                terminal_receipt=terminal_receipt,
                            )
                        )
                        _, current_lifecycle = self._capture_physical_attempt_replay_views(
                            run.id,
                            deadline=deadline,
                        )
                        if (
                            current_lifecycle.captured_head.global_seq != terminal_receipt.logical_sequence
                            or current_lifecycle.captured_head.head_hash != terminal_receipt.event_hash
                        ):
                            restart_full_replay = True
                            break
                    if restart_full_replay:
                        continue

                    held_lease = revalidate()
                    self._require_physical_attempt_replay_deadline(deadline)
                    final_role_evidence, final_lifecycle = self._capture_physical_attempt_replay_views(
                        run.id,
                        deadline=deadline,
                    )
                    if (
                        self._capture_physical_attempt_replay_fence(
                            factory_run_id=run.id,
                            lease=held_lease,
                            deadline=deadline,
                        )
                        != fence
                        or final_role_evidence != role_evidence
                        or final_lifecycle != current_lifecycle
                    ):
                        continue
                    if not coordinator.drain_snapshot().settled:
                        raise FactoryPhysicalAttemptReplayError(
                            "factory_physical_attempt_replay_terminal_settlement_incomplete"
                        )
                    self._physical_attempt_coordinators[run.id] = coordinator
                    return coordinator
            except RuntimeError as exc:
                if "head_drift" in str(exc):
                    continue
                raise
        raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_head_unstable")

    @staticmethod
    def _attach_workspace_lease(run: FactoryRun, lease: FactoryWorkspaceRunLeaseV1) -> None:
        run.metadata[_WORKSPACE_LEASE_METADATA_KEY] = lease.to_dict()

    @staticmethod
    def _workspace_lease_fencing_token(run: FactoryRun) -> int:
        payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        lease_data = payload if isinstance(payload, Mapping) else {}
        try:
            fencing_token = int(lease_data.get("fencing_token") or 0)
        except (TypeError, ValueError) as exc:
            fencing_token = 0
            token_error: Exception | None = exc
        else:
            token_error = None
        if fencing_token > 0:
            return fencing_token

        from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

        raise FactoryPipelineError(
            "Factory run has no valid workspace fencing authority",
            code="factory_workspace_run_lease_authority_missing",
            details={"run_id": run.id, "error": str(token_error or "missing fencing_token")},
        )

    @classmethod
    def _expected_lifecycle_fencing_token(cls, run: FactoryRun) -> int | None:
        """Return authority proof only while the run projects an owned lease.

        A missing or released projection requests a fresh atomic acquisition.
        ACTIVE and DRAINING projections must carry their original fencing token;
        the admission ledger never supplies or upgrades that proof for callers.
        """

        payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        lease_data = payload if isinstance(payload, Mapping) else {}
        state = str(lease_data.get("state") or "").strip().lower()
        if not state or state == "released":
            return None
        if state in {"active", "draining"}:
            return cls._workspace_lease_fencing_token(run)

        from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

        raise FactoryPipelineError(
            "Factory run has an invalid workspace lease projection",
            code="factory_workspace_run_lease_projection_invalid",
            details={"run_id": run.id, "state": state},
        )

    def _acquire_workspace_lease(self, run: FactoryRun) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.acquire(run.id)
        self._attach_workspace_lease(run, lease)
        return lease

    def _claim_lifecycle_operation(
        self,
        run: FactoryRun,
        *,
        operation: str,
        nonce: str,
        acquire_if_available: bool,
        expected_fencing_token: int | None = None,
        allow_expired_owner: bool = False,
    ) -> FactoryWorkspaceRunLeaseV1:
        current = self._admission.current()
        if (
            operation
            not in {
                "recover_run",
                "recover_stale_workspace_owner",
                "resume_recovered_run",
            }
            and current is not None
            and current.run_id == run.id
            and (
                current.drain_reason == "factory_physical_attempt_restart_replay_fence"
                or (
                    current.release_evidence is not None
                    and current.release_evidence.details.get("physical_attempt_replay_fence") is True
                )
            )
        ):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_recovered_run_permanently_closed")
        expected_token = (
            self._expected_lifecycle_fencing_token(run) if expected_fencing_token is None else expected_fencing_token
        )
        replay_fence = run.id not in self._physical_attempt_coordinators and operation in {
            "recover_run",
            "recover_stale_workspace_owner",
        }
        lease = self._admission.claim_lifecycle_operation(
            run.id,
            operation=operation,
            nonce=nonce,
            acquire_if_available=acquire_if_available,
            expected_fencing_token=expected_token,
            allow_expired_owner=allow_expired_owner,
            replay_fence=replay_fence,
        )
        self._attach_workspace_lease(run, lease)
        if run.id not in self._physical_attempt_coordinators:
            try:
                self._recover_physical_attempt_coordinator(run=run, lease=lease)
            except (OSError, RuntimeError, TypeError, ValueError):
                try:
                    rolled_back = self._admission.rollback_lifecycle_operation(
                        run.id,
                        fencing_token=lease.fencing_token,
                        operation=operation,
                        nonce=nonce,
                        reason="factory_physical_attempt_replay_failed",
                    )
                    self._attach_workspace_lease(run, rolled_back)
                    if rolled_back.state.value == "draining" and replay_fence:
                        run.metadata["factory_physical_attempt_admission_dead"] = True
                except (OSError, RuntimeError, TypeError, ValueError) as rollback_exc:
                    logger.error(
                        "Factory physical-attempt replay rollback failed run_id=%s operation=%s: %s",
                        run.id,
                        operation,
                        rollback_exc,
                    )
                raise
        return lease

    async def _release_lifecycle_operation(
        self,
        run: FactoryRun,
        *,
        operation: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.release_lifecycle_operation(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            operation=operation,
            nonce=nonce,
        )
        self._attach_workspace_lease(run, lease)
        run.updated_at = self._now()
        await self.store.save_run(run)
        return lease

    async def _rollback_lifecycle_operation(
        self,
        run: FactoryRun,
        *,
        operation: str,
        nonce: str,
        reason: str,
        persist_run: bool = True,
    ) -> None:
        """Best-effort authority rollback while preserving the original error."""

        try:
            lease = self._admission.rollback_lifecycle_operation(
                run.id,
                fencing_token=self._workspace_lease_fencing_token(run),
                operation=operation,
                nonce=nonce,
                reason=reason,
            )
            # The lifecycle body may have durably persisted newer run metadata
            # before failing (for example, a terminal TaskRuntime snapshot
            # immediately before a destructive reset). Never save the stale
            # pre-operation object over those facts during rollback.
            latest = await self.store.get_run(run.id)
            target_run = latest or run
            self._attach_workspace_lease(target_run, lease)
            if lease.state.value == "draining" and operation in {
                "recover_run",
                "recover_stale_workspace_owner",
            }:
                target_run.metadata["factory_physical_attempt_admission_dead"] = True
            if persist_run:
                target_run.updated_at = self._now()
                await self.store.save_run(target_run)
            if target_run is not run:
                self._copy_run_state(run, target_run)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error(
                "Factory lifecycle rollback failed run_id=%s operation=%s: %s",
                run.id,
                operation,
                exc,
            )

    @staticmethod
    def _workspace_release_evidence(
        run_id: str,
        settlement: Mapping[str, object],
        *,
        source: str,
        observed_at: str,
        fenced_session_ids: tuple[str, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> FactoryWorkspaceReleaseEvidenceV1:
        from polaris.cells.factory.pipeline.public.contracts import (
            FactoryWorkspaceReleaseEvidenceV1,
        )

        return FactoryWorkspaceReleaseEvidenceV1(
            factory_run_id=run_id,
            source=source,
            observed_at=observed_at,
            active_session_count=int(str(settlement.get("active_session_count") or 0)),
            conflict_count=int(str(settlement.get("conflict_count") or 0)),
            fenced_session_ids=fenced_session_ids,
            details={"task_runtime_settlement": dict(settlement), **dict(details or {})},
        )

    def _renew_workspace_lease(
        self,
        run: FactoryRun,
        *,
        require_active: bool,
    ) -> FactoryWorkspaceRunLeaseV1:
        fencing_token = self._workspace_lease_fencing_token(run)
        lease = self._admission.renew(run.id, fencing_token=fencing_token)
        if require_active and lease.state.value != "active":
            from polaris.cells.factory.pipeline.public.contracts import (
                FactoryWorkspaceRunLeaseConflictError,
            )

            raise FactoryWorkspaceRunLeaseConflictError(
                "Factory stage execution requires an ACTIVE workspace lease",
                code="factory_workspace_run_not_active",
                requested_run_id=run.id,
                current_lease=lease,
            )
        self._attach_workspace_lease(run, lease)
        return lease

    def _assert_active_workspace_lease(self, run: FactoryRun) -> FactoryWorkspaceRunLeaseV1:
        fencing_token = self._workspace_lease_fencing_token(run)
        lease = self._admission.assert_active(run.id, fencing_token=fencing_token)
        self._attach_workspace_lease(run, lease)
        return lease

    def _claim_stage_execution(
        self,
        run: FactoryRun,
        *,
        stage: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.claim_stage(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            stage=stage,
            nonce=nonce,
        )
        self._attach_workspace_lease(run, lease)
        return lease

    def _build_factory_role_evidence_cutoff_port(
        self,
        *,
        run: FactoryRun,
        stage: str,
        lease: FactoryWorkspaceRunLeaseV1,
        run_lock: asyncio.Lock,
    ) -> FactoryRoleEvidenceAuthorityPort:
        """Capture one live stage claim into an A009B1-private authority port."""

        claim = lease.stage_execution_claim
        if claim is None or claim.run_id != run.id or claim.stage != stage:
            raise RuntimeError("factory_role_evidence_stage_claim_missing_after_claim")

        async def load_current_run() -> FactoryRun | None:
            return await self.store.get_run(run.id)

        return FactoryRoleEvidenceAuthorityPort(
            workspace=self.workspace,
            authority=FactoryRoleEvidenceStageAuthorityV1(
                factory_run_id=run.id,
                stage=stage,
                workspace_fencing_token=lease.fencing_token,
                stage_claim_attempt=claim.attempt,
                stage_claim_nonce=claim.nonce,
            ),
            run_lock=run_lock,
            run_loader=load_current_run,
            admission=self._admission,
            source_authority=CanonicalFactoryRoleEvidenceSourceAuthority(
                workspace=self.workspace,
                factory_store=self.store,
                factory_event_loader=self.store._read_authoritative_events_sync,
            ),
            physical_attempt_coordinator=self._physical_attempt_coordinator(run.id),
        )

    @staticmethod
    def _assert_no_factory_role_evidence_port_leak(
        result: StageResult,
        port: FactoryRoleEvidenceAuthorityPort,
    ) -> None:
        """Fail before persistence if StageResult projections capture private authority."""

        del port
        if contains_factory_role_evidence_runtime_authority(result):
            raise RuntimeError("factory_role_evidence_private_port_leaked_to_stage_result")

    async def _release_stage_execution(
        self,
        run: FactoryRun,
        *,
        stage: str,
        nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.release_stage(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            stage=stage,
            nonce=nonce,
        )
        self._attach_workspace_lease(run, lease)
        latest = await self.store.get_run(run.id)
        target_run = latest or run
        self._attach_workspace_lease(target_run, lease)
        target_run.updated_at = self._now()
        await self.store.save_run(target_run)
        return lease

    @staticmethod
    def _stage_result_releases_execution_claim(result: StageResult) -> bool:
        """Whether a finished stage may release the durable stage-execution claim.

        R187/M07: director_dispatch can finish ``status=success`` after timeout
        settle grace while metadata still carries ``inflight_run_continues=true``
        (child observation). The previous rule treated that flag as a hard hold
        and refused claim release, so quality_gate failed with
        ``factory_stage_execution_conflict`` / \"Another Factory stage execution
        already holds the durable claim\" even though delivery settle already
        committed tests (L1-01 r6). Success stages must release when child
        sessions are settled so the PM→CE→Director→QA chain can advance.
        """

        status = str(result.status or "").strip().lower()
        if status in {"failed", "cancelled"}:
            return False
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        if status == "success":
            return metadata.get("child_sessions_settled") is not False
        if metadata.get("inflight_run_continues") is True:
            return False
        return metadata.get("child_sessions_settled") is not False

    async def reconcile_stage_execution_for_reentry(
        self,
        run_id: str,
        *,
        operation: str,
    ) -> FactoryRun:
        """Explicitly release a failed-stage claim only after settlement proof."""

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            self._physical_attempt_coordinator(run.id)
            settlement = await self._require_child_session_settlement_for_reentry(
                run,
                operation=operation,
            )
            await self._reconcile_stage_execution_claim(run, settlement=settlement)
            return run

    async def _reconcile_stage_execution_claim(
        self,
        run: FactoryRun,
        *,
        settlement: Mapping[str, object] | None = None,
    ) -> FactoryWorkspaceRunLeaseV1 | None:
        current = self._admission.current()
        if current is None or current.run_id != run.id:
            return current
        claim = current.stage_execution_claim
        if claim is None or run.metadata.get(_STAGE_IN_FLIGHT_METADATA_KEY) is not False:
            self._attach_workspace_lease(run, current)
            return current

        evidence = dict(settlement or self._query_child_session_settlement(run.id))
        run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = evidence
        run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = evidence.get("settled") is True
        if evidence.get("settled") is not True:
            self._attach_workspace_lease(run, current)
            return current

        self._attach_workspace_lease(run, current)
        return await self._release_stage_execution(
            run,
            stage=claim.stage,
            nonce=claim.nonce,
        )

    async def _begin_terminal_drain(
        self,
        run: FactoryRun,
        *,
        reason: str,
        operation_nonce: str,
    ) -> FactoryWorkspaceRunLeaseV1 | None:
        payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        if not isinstance(payload, Mapping):
            if run.status == FactoryRunStatus.PENDING:
                return None
            current = self._admission.current()
            if current is None or current.state.value == "released":
                return None
            from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

            raise FactoryPipelineError(
                "Factory terminal transition lacks persisted fencing authority",
                code="factory_workspace_run_lease_authority_missing",
                details={"run_id": run.id, "current_lease": current.to_dict()},
            )
        lease = self._admission.begin_draining(
            run.id,
            fencing_token=self._workspace_lease_fencing_token(run),
            reason=reason,
            operation_nonce=operation_nonce,
        )
        self._attach_workspace_lease(run, lease)
        await self.store.save_run(run)
        return lease

    async def _record_drain_conflict(
        self,
        run: FactoryRun,
        lease: FactoryWorkspaceRunLeaseV1,
        *,
        code: str,
        message: str,
        details: Mapping[str, Any],
    ) -> FactoryRun:
        observed_at = self._now()
        conflict: dict[str, Any] = {
            "code": code,
            "message": message,
            "details": dict(details),
            "observed_at": observed_at,
        }
        run.metadata["factory_workspace_run_drain_conflict"] = conflict
        self._attach_workspace_lease(run, lease)
        run.updated_at = observed_at
        await self.store.save_run(run)
        return run

    def _query_child_session_settlement(self, run_id: str) -> dict[str, object]:
        return query_factory_run_settlement(
            str(self.workspace),
            factory_run_id=run_id,
        )

    async def _require_child_session_settlement_for_reentry(
        self,
        run: FactoryRun,
        *,
        operation: str,
    ) -> dict[str, object]:
        evidence = self._query_child_session_settlement(run.id)
        settled = evidence.get("settled") is True
        run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = settled
        run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = evidence
        if settled:
            run.metadata.pop("factory_child_session_conflict", None)
            return evidence

        observed_at = self._now()
        conflict: dict[str, Any] = {
            "code": "factory_workspace_run_child_session_inflight",
            "message": "Factory run cannot re-enter while a child execution session is active",
            "operation": operation,
            "settlement": evidence,
            "observed_at": observed_at,
        }
        run.metadata["factory_child_session_conflict"] = conflict
        run.updated_at = observed_at
        await self.store.save_run(run)
        from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

        raise FactoryPipelineError(
            conflict["message"],
            code=conflict["code"],
            details={
                "run_id": run.id,
                "operation": operation,
                "settlement": evidence,
            },
        )

    async def _finalize_terminal_drain(
        self,
        run: FactoryRun,
        lease: FactoryWorkspaceRunLeaseV1 | None,
        *,
        operation_nonce: str,
    ) -> FactoryRun:
        if lease is None or lease.state.value == "released":
            return run

        latest = await self.store.get_run(run.id)
        target_run = latest or run
        stage_in_flight = target_run.metadata.get(_STAGE_IN_FLIGHT_METADATA_KEY)
        if stage_in_flight is not False:
            # Terminal FAILED/CANCELLED closeout must not pin the workspace lease
            # forever when a stage crash left factory_stage_in_flight sticky
            # (L1-04 r79: drain_conflict factory_workspace_run_drain_unproven).
            # Success paths still require an explicit stage settlement proof.
            if target_run.status in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}:
                target_run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
                target_run.metadata["factory_stage_in_flight_force_cleared"] = {
                    "reason": f"factory_{target_run.status.value}",
                    "previous": stage_in_flight,
                    "observed_at": self._now(),
                }
                await self.store.save_run(target_run)
            else:
                return await self._record_drain_conflict(
                    target_run,
                    lease,
                    code="factory_workspace_run_drain_unproven",
                    message="Factory workspace drain cannot prove stage settlement",
                    details={
                        "stage_in_flight": stage_in_flight,
                    },
                )

        physical_drain = self._physical_attempt_coordinator(target_run.id).close()
        physical_drain_evidence = {
            "factory_run_id": physical_drain.factory_run_id,
            "settled": physical_drain.settled,
            "blocking_reservation_ids": list(physical_drain.blocking_reservation_ids),
            "terminal_failure_reservation_ids": list(physical_drain.terminal_failure_reservation_ids),
            "by_authority": [asdict(state) for state in physical_drain.by_authority],
        }
        target_run.metadata["factory_physical_attempt_drain"] = physical_drain_evidence
        if not physical_drain.settled:
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_physical_attempt_drain_open",
                message="Factory workspace drain found unsettled physical provider attempts",
                details={"physical_attempt_drain": physical_drain_evidence},
            )

        settlement = self._query_child_session_settlement(target_run.id)
        target_run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = settlement
        # R64: Director settlement-barrier timeouts leave owned active sessions that
        # pin child-session settlement forever. On FAILED/CANCELLED factory drain,
        # force-fail those owned active sessions (not foreign ones) so lease release
        # can complete. Live success paths never take this branch.
        if settlement.get("settled") is not True and target_run.status in {
            FactoryRunStatus.FAILED,
            FactoryRunStatus.CANCELLED,
        }:
            abort_summary = TaskRuntimeService(str(self.workspace)).terminalize_open_tasks_for_factory_abort(
                factory_run_id=target_run.id,
                reason=f"factory_{target_run.status.value}",
                source="factory_terminal_drain_force_active",
                force_active_sessions=True,
            )
            target_run.metadata["factory_task_runtime_abort"] = abort_summary
            await self.store.save_run(target_run)
            settlement = self._query_child_session_settlement(target_run.id)
            target_run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = settlement
        if settlement.get("settled") is not True:
            target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_workspace_run_child_session_inflight",
                message="Factory workspace drain found an active or foreign child session",
                details={"settlement": settlement},
            )

        reconciled_lease = await self._reconcile_stage_execution_claim(
            target_run,
            settlement=settlement,
        )
        if reconciled_lease is not None:
            lease = reconciled_lease

        from polaris.cells.factory.pipeline.public.contracts import (
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            FactoryTerminalTaskRuntimeProjectionV1,
        )

        terminal_snapshot_payload = target_run.metadata.get(
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
        )
        terminal_snapshot: FactoryTerminalTaskRuntimeProjectionV1 | None = None
        if terminal_snapshot_payload is not None:
            if not isinstance(terminal_snapshot_payload, Mapping):
                return await self._record_drain_conflict(
                    target_run,
                    lease,
                    code="factory_task_runtime_terminal_projection_invalid",
                    message="Factory workspace drain found an invalid frozen TaskRuntime projection",
                    details={
                        "error_type": "TypeError",
                        "error_message": "terminal projection payload must be a mapping",
                        "factory_run_id": target_run.id,
                    },
                )
            try:
                terminal_snapshot = FactoryTerminalTaskRuntimeProjectionV1.from_dict(
                    terminal_snapshot_payload,
                )
            except (OSError, TypeError, ValueError) as exc:
                return await self._record_drain_conflict(
                    target_run,
                    lease,
                    code="factory_task_runtime_terminal_projection_invalid",
                    message="Factory workspace drain found an invalid frozen TaskRuntime projection",
                    details={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:300],
                        "factory_run_id": target_run.id,
                    },
                )

        live_task_count = int(str(settlement.get("observable_row_count") or 0))
        frozen_task_count = (
            int(str(terminal_snapshot.projection.get("row_count") or 0)) if terminal_snapshot is not None else 0
        )
        task_count = max(live_task_count, frozen_task_count)
        barrier_evidence: dict[str, Any] = {
            "required": task_count > 0,
            "factory_run_id": target_run.id,
        }
        if task_count > 0:
            barrier = self._settlement_barrier_query(self.workspace, target_run.id)
            # R51: CE/provider early abort after PM materializes open task rows
            # leaves lifecycle_open on the Run Ledger barrier while child
            # sessions are already settled. Terminalize those open (never
            # dispatched) rows so release_allowed can become true; do not force
            # cancel active Director sessions or ignore open effect receipts.
            if (
                not barrier.release_allowed
                and target_run.status in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}
                and settlement.get("settled") is True
            ):
                blocking = {str(reason).strip() for reason in barrier.blocking_reasons if str(reason).strip()}
                # Residual tool/effect evidence gaps after a failed Director wave
                # must not prevent aborting open never-dispatched rows (R55/R57).
                abortable_blockers = {
                    "lifecycle_open",
                    "lifecycle_failed",
                    "tool_lifecycle_evidence_missing",
                    "effect_receipts_open",
                    "effect_receipt_missing",
                    # Residual after failed Director materialization/quality waves
                    # (L1-05 r79: task_boundary_failed blocked abort → lease stuck draining).
                    "task_boundary_failed",
                }
                non_abortable = blocking - abortable_blockers
                if not non_abortable and (
                    "lifecycle_open" in blocking
                    or "tool_lifecycle_evidence_missing" in blocking
                    or "effect_receipts_open" in blocking
                    or "task_boundary_failed" in blocking
                ):
                    abort_summary = TaskRuntimeService(str(self.workspace)).terminalize_open_tasks_for_factory_abort(
                        factory_run_id=target_run.id,
                        reason=f"factory_{target_run.status.value}",
                        source="factory_terminal_drain",
                    )
                    target_run.metadata["factory_task_runtime_abort"] = abort_summary
                    await self.store.save_run(target_run)
                    barrier = self._settlement_barrier_query(self.workspace, target_run.id)
            barrier_evidence.update(
                {
                    "schema_version": barrier.schema_version,
                    "barrier_hash": barrier.barrier_hash,
                    "closed": barrier.closed,
                    "passed": barrier.passed,
                    "release_allowed": barrier.release_allowed,
                    "blocking_reasons": list(barrier.blocking_reasons),
                    "evidence_refs": list(barrier.evidence_refs),
                    "consumed_run_ids": list(barrier.consumed_run_ids),
                }
            )
            target_run.metadata["factory_run_ledger_settlement_barrier"] = barrier_evidence
            if not barrier.release_allowed:
                return await self._record_drain_conflict(
                    target_run,
                    lease,
                    code="factory_run_ledger_settlement_barrier_open",
                    message="Factory workspace drain found open Run Ledger obligations",
                    details={"settlement_barrier": barrier_evidence},
                )
        else:
            target_run.metadata["factory_run_ledger_settlement_barrier"] = barrier_evidence

        if terminal_snapshot is None:
            try:
                task_runtime_projection = TaskRuntimeService(
                    str(self.workspace)
                ).query_observable_task_rows_projection()
                terminal_projection = task_runtime_projection.to_authority_dict(
                    factory_run_id=target_run.id,
                )
                if task_count > 0 and int(str(terminal_projection.get("row_count") or 0)) < 1:
                    raise ValueError("terminal TaskRuntime projection omitted factory-bound rows")
                terminal_snapshot = FactoryTerminalTaskRuntimeProjectionV1(
                    workspace=str(self.workspace),
                    factory_run_id=target_run.id,
                    captured_at=self._now(),
                    projection=terminal_projection,
                )
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
                return await self._record_drain_conflict(
                    target_run,
                    lease,
                    code="factory_task_runtime_terminal_projection_unavailable",
                    message=(
                        "Factory workspace drain could not freeze authoritative TaskRuntime evidence before reset"
                    ),
                    details={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:300],
                        "factory_run_id": target_run.id,
                        "expected_row_count": task_count,
                    },
                )

            # Persist before reset. A crash after this save can safely retry
            # drain from the exact frozen authority; a crash before it leaves
            # the live TaskRuntime rows intact.
            target_run.metadata[FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY] = terminal_snapshot.to_dict()
            await self.store.save_run(target_run)

        reset_summary = TaskRuntimeService(str(self.workspace)).reset_records(
            keep_plan=True,
            factory_run_id=target_run.id,
        )
        if reset_summary.get("ok") is not True or int(str(reset_summary.get("failed_count") or 0)) > 0:
            target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_workspace_run_task_runtime_drain_conflict",
                message="TaskRuntime records did not settle under the Factory run authority",
                details={"task_runtime_reset": reset_summary},
            )

        target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = True
        release_evidence = self._workspace_release_evidence(
            target_run.id,
            settlement,
            source="factory_terminal_drain",
            observed_at=self._now(),
            details={
                "task_runtime_reset": reset_summary,
                "settlement_barrier": barrier_evidence,
                "physical_attempt_drain": physical_drain_evidence,
            },
        )
        released = self._admission.release(
            target_run.id,
            fencing_token=lease.fencing_token,
            settlement_evidence=release_evidence,
            operation_nonce=operation_nonce,
        )
        self._attach_workspace_lease(target_run, released)
        target_run.metadata.pop("factory_workspace_run_drain_conflict", None)
        target_run.updated_at = self._now()
        await self.store.save_run(target_run)
        await self._append_event(
            target_run.id,
            {
                "type": "workspace_run_lease_released",
                "message": "Factory workspace run lease released after draining",
                "lease": released.to_dict(),
                "timestamp": target_run.updated_at,
            },
        )
        return target_run

    async def create_run(self, config: FactoryConfig) -> FactoryRun:
        """Create a new factory run with directory structure."""
        detached_config = FactoryConfig(
            name=config.name,
            description=config.description,
            stages=list(config.stages),
            auto_dispatch=config.auto_dispatch,
            checkpoint_interval=config.checkpoint_interval,
        )
        run = FactoryRun(
            id=f"factory_{uuid.uuid4().hex[:12]}",
            config=detached_config,
            status=FactoryRunStatus.PENDING,
            created_at=self._now(),
            metadata={
                "current_stage": None,
                "last_stage": None,
                "last_successful_stage": None,
                "last_failed_stage": None,
                _STAGE_IN_FLIGHT_METADATA_KEY: False,
                _CHILD_SESSIONS_SETTLED_METADATA_KEY: True,
            },
        )

        admitted = await self._append_event(
            run.id,
            build_factory_run_admitted_event(
                FactoryRunAdmissionV1(
                    factory_run_id=run.id,
                    created_at=run.created_at,
                    name=run.config.name,
                    description=run.config.description,
                )
            ),
            publish=False,
        )
        run_dir = self.store.get_run_dir(run.id)
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
        await self.store.save_run(run)
        self._ensure_physical_attempt_replay_ledgers(run.id)
        self._physical_attempt_coordinators[run.id] = FactoryPhysicalAttemptLiveControlPort(
            factory_run_id=run.id,
            revalidate_active_stage_claim=self._revalidate_active_physical_attempt_stage_claim,
        )
        # Realtime observers may only learn about the run after the mutable
        # snapshot exists.  The admission bytes remain authoritative if save
        # fails, but that half-run stays quarantined and unpublished.
        await self._publish_factory_event(run.id, admitted)
        logger.info("Created factory run %s", run.id)
        return run

    async def _revalidated_pm_stage_artifact_binding(
        self,
        run_id: str,
    ) -> RevalidatedPMStageArtifactBindingV1 | None:
        """Resolve the latest committed PM task contract from immutable facts."""

        try:
            events = await self.store.get_authoritative_events(run_id)
            persistence = reduce_factory_stage_persistence(events, factory_run_id=run_id)
            if persistence.is_quarantined:
                return None
            pm_commits = tuple(commit for commit in persistence.commits if commit.stage == "pm_planning")
            if not pm_commits:
                return None
            event_id = pm_commits[-1].stage_completed_event_id
            stage_event = next(
                (
                    event
                    for event in events
                    if event.get("type") == "stage_completed"
                    and event.get("event_id") == event_id
                    and event.get("stage") == "pm_planning"
                ),
                None,
            )
            if not isinstance(stage_event, Mapping):
                return None
            return revalidate_pm_stage_artifact_binding(
                factory_store=self.store,
                factory_run_id=run_id,
                stage_event=stage_event,
            )
        except (
            FactoryStageArtifactBindingError,
            FactoryStagePersistenceError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            return None

    async def execute_stage(
        self,
        run_id: str,
        stage: str,
        context: dict[str, Any] | None = None,
    ) -> StageResult:
        """Execute a single stage with durable lifecycle updates."""
        normalized_context = dict(context or {})
        if contains_factory_role_evidence_runtime_authority(normalized_context):
            raise RuntimeError("factory_role_evidence_private_authority_in_caller_context")
        normalized_context["_factory_abort_checker"] = self._build_abort_checker(run_id)
        cancel_event = _register_factory_cancel_event(self.workspace, run_id)
        normalized_context["_factory_cancel_event"] = cancel_event
        heartbeat_interval = self._resolve_heartbeat_interval_seconds(normalized_context)
        stage_claim_nonce = f"stage_{uuid.uuid4().hex}"

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            # Stage execution is not a recovery authority.  A restarted
            # service must first reconstruct the run under a lifecycle claim;
            # fail before lease renewal, stage claim, run save, or event append.
            self._require_physical_attempt_admission_open(run.id)
            if run.status not in {FactoryRunStatus.RUNNING, FactoryRunStatus.RECOVERING}:
                current_lease = self._admission.current()
                if (
                    current_lease is not None
                    and current_lease.run_id == run.id
                    and current_lease.stage_execution_claim is not None
                ):
                    self._attach_workspace_lease(run, current_lease)
                    self._claim_stage_execution(
                        run,
                        stage=stage,
                        nonce=stage_claim_nonce,
                    )
                raise ValueError(f"Run {run_id} is not executable in status {run.status.value}")
            self._renew_workspace_lease(run, require_active=True)
            claimed_lease = self._claim_stage_execution(
                run,
                stage=stage,
                nonce=stage_claim_nonce,
            )
            started_at = self._now()
            await self._mark_stage_started(run, stage, started_at)
            cutoff_port = self._build_factory_role_evidence_cutoff_port(
                run=run,
                stage=stage,
                lease=claimed_lease,
                run_lock=run_lock,
            )
            normalized_context[_FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY] = cutoff_port

        heartbeat_task: asyncio.Task[None] | None = None
        if heartbeat_interval > 0:
            heartbeat_task = asyncio.create_task(
                self._run_stage_heartbeat(
                    run_id,
                    stage,
                    heartbeat_interval,
                    fencing_token=claimed_lease.fencing_token,
                ),
                name=f"factory_stage_heartbeat:{run_id}:{stage}",
            )

        try:
            try:
                result = await self._execute_stage_logic(run, stage, normalized_context)
                self._assert_no_factory_role_evidence_port_leak(result, cutoff_port)
            finally:
                # The live capability ends with stage-logic execution on every
                # exit path, including failed results, wrapper exceptions, and
                # cancellation.  Claim settlement/release remains a separate
                # durable lifecycle decision below.
                # close_authority may block on a threading.Condition while
                # in-flight acquisitions drain.  Never run that wait on the
                # asyncio event loop thread — it freezes HTTP/WS/heartbeat and
                # is the R141 isolated-backend keepalive root cause when a
                # cutoff acquisition and stage teardown overlap.
                await asyncio.to_thread(cutoff_port.close_authority)
        except Exception as exc:
            # Fail-closed for provider/network errors (e.g. aiohttp.ClientResponseError
            # on HTTP 403 quota). A narrow typed list previously let those escape
            # without marking the stage finished, abandoning the stage claim and
            # freezing updated_at while the API still reported RUNNING.
            # asyncio.CancelledError is BaseException and is not caught here.
            result = StageResult(
                stage=stage,
                status="failed",
                output=f"{stage} failed: {exc}",
                artifacts=[],
                started_at=started_at,
                completed_at=self._now(),
                metadata={
                    "child_sessions_settled": True,
                    "inflight_run_continues": False,
                    "settlement_source": "factory_stage_wrapper_exception",
                    "exception_type": type(exc).__name__,
                },
            )
            async with run_lock:
                self._renew_workspace_lease(run, require_active=False)
                await self._mark_stage_finished(run, result, error=exc)
                # Failed stages normally keep the claim until reconcile. Wrapper
                # exceptions never started a continuing child session — release
                # here so settle_terminal_run can drain the workspace lease.
                try:
                    await self._release_stage_execution(
                        run,
                        stage=stage,
                        nonce=stage_claim_nonce,
                    )
                except Exception as release_exc:  # noqa: BLE001 — best-effort release
                    logger.warning(
                        "Factory stage claim release after wrapper exception failed for run %s stage %s: %s",
                        run_id,
                        stage,
                        release_exc,
                    )
            logger.error("Stage %s failed for run %s: %s", stage, run_id, exc)
            try:
                await self.settle_terminal_run(run_id)
            except Exception as settle_exc:  # noqa: BLE001 — best-effort; complete_run retries
                logger.warning(
                    "Factory post-exception settle failed for run %s stage %s: %s",
                    run_id,
                    stage,
                    settle_exc,
                )
            raise
        finally:
            _unregister_factory_cancel_event(self.workspace, run_id, cancel_event)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    try:
                        await heartbeat_task
                    except (
                        AttributeError,
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                        asyncio.TimeoutError,
                    ) as heartbeat_exc:
                        logger.warning(
                            "Factory heartbeat task failed for run %s stage %s: %s",
                            run_id,
                            stage,
                            heartbeat_exc,
                        )

        result.started_at = result.started_at or started_at
        result.completed_at = result.completed_at or self._now()
        result.metadata = dict(result.metadata) if isinstance(result.metadata, dict) else {}
        # Stage executors cannot self-issue terminal-drain deferral.  A legacy
        # TaskMarket receipt is not enough either: the current execution owner
        # is TaskRuntime and the durable completion cursor must commit the exact
        # owner action before this service may keep the run open.
        result.metadata.pop("factory_terminal_drain_deferred", None)
        local_rework_decision_pending = False
        terminal_after_stage = False
        async with run_lock:
            self._renew_workspace_lease(run, require_active=False)
            await self._mark_stage_finished(run, result)
            if self._stage_result_releases_execution_claim(result):
                await self._release_stage_execution(
                    run,
                    stage=stage,
                    nonce=stage_claim_nonce,
                )
            latest = await self.store.get_run(run_id)
            current_lease = self._admission.current()
            terminal_after_stage = (
                latest is not None
                and latest.status in TERMINAL_RUN_STATUSES
                and current_lease is not None
                and current_lease.run_id == run_id
                and current_lease.state.value in {"active", "draining"}
            )
        completion_facts_changed = (
            result.stage == "director_dispatch" and result.status != "success"
        ) or result.stage == "quality_gate"
        if completion_facts_changed:
            try:
                completion_result = await self._notify_project_completion_supervisor(run_id, result)
                completion_action_id = str(getattr(completion_result, "action_id", None) or "").strip()
                completion_reason_codes = tuple(getattr(completion_result, "reason_codes", ()) or ())
                completion_next_action = str(getattr(completion_result, "next_action", None) or "").strip()
                completion_projection = {
                    "schema_version": "factory.project-completion-advance.v1",
                    "status": str(getattr(completion_result, "status", None) or "unknown"),
                    "reason_codes": list(completion_reason_codes),
                    "action_id": completion_action_id,
                    "diagnostic_id": str(getattr(completion_result, "diagnostic_id", None) or "").strip(),
                    "next_action": completion_next_action,
                    "source_stage": result.stage,
                    "source_stage_status": result.status,
                }
                # The convergence store is authoritative for replay; this event is
                # the durable, machine-readable Factory projection explaining why
                # the exact downstream task will be resumed (or why it is parked).
                # It prevents operators and recovery code from inferring a PM/CE
                # restart merely from a failed Director/QA StageResult.
                result.metadata["project_completion_advance"] = completion_projection
                await self._append_event(
                    run_id,
                    {
                        "type": "project_completion_advance",
                        **completion_projection,
                        "terminal": False,
                        "timestamp": self._now(),
                    },
                )
                if (
                    result.status != "success"
                    and len(completion_action_id) == 64
                    and "owner_action_receipt_committed" in completion_reason_codes
                    and completion_next_action
                    in {"publish_owner_rework", "run_deterministic_repair", "run_required_verifier"}
                ):
                    local_rework_decision_pending = True
                    result.metadata["factory_terminal_drain_deferred"] = {
                        "schema_version": "factory.terminal-drain-deferred.v2",
                        "reason": (
                            "director_local_rework_decision_pending"
                            if result.stage == "director_dispatch"
                            else "quality_rework_decision_pending"
                        ),
                        "decision_owner": "orchestration.workflow_orchestration",
                        "action_id": completion_action_id,
                        "diagnostic_id": completion_projection["diagnostic_id"],
                    }
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.error(
                    "Project completion supervisor notification failed for run %s: %s",
                    run_id,
                    exc,
                    exc_info=True,
                )
                await self._append_event(
                    run_id,
                    {
                        "type": "project_completion_control_plane_blocked",
                        "message": str(exc)[:500],
                        "error_type": type(exc).__name__,
                        "timestamp": self._now(),
                        "terminal": False,
                    },
                )

        # Success StageResults that authorize claim release settle immediately.
        # Failed StageResults intentionally retain the stage claim for reconcile
        # (see test_failed_settled_stage_retains_exact_claim_*), but terminal
        # FAILED/CANCELLED must still drain the workspace lease here so a missed
        # or timed-out router complete_run cannot leave lease state=active forever
        # (L1-05 r82: director_dispatch failed, completed_at=null, lease stuck active).
        # Failed CE, Director, and quality stages can request one bounded
        # owner-local recovery wave. Preserve live TaskRuntime rows until the
        # synchronous orchestration caller records that decision. CE reuses the
        # authoritative PM contract; Director reopens only unfinished work; QA
        # returns to Director. If no rework is requested, the caller's normal
        # failure closeout drains immediately. Upstream roles are never replayed
        # merely because a downstream stage failed.
        if terminal_after_stage and not local_rework_decision_pending:
            try:
                if self._stage_result_releases_execution_claim(result):
                    await self.settle_terminal_run(run_id)
                else:
                    await self.reconcile_stage_execution_for_reentry(
                        run_id,
                        operation="factory_stage_failed_terminal_settle",
                    )
                    await self.complete_run(run_id, success=False)
            except Exception as settle_exc:  # noqa: BLE001 — best-effort settle
                logger.warning(
                    "Factory post-stage terminal settle failed for run %s stage %s: %s",
                    run_id,
                    stage,
                    settle_exc,
                )
        return result

    async def _notify_project_completion_supervisor(
        self,
        run_id: str,
        result: StageResult,
    ) -> object:
        """Emit the CE-owned completion identity after its stage commit.

        This is an event-driven call point, not a scan/poll loop.  The CE
        persisted portfolio remains authority; the Factory result only carries
        its logical artifact reference.
        """

        from polaris.cells.chief_engineer.blueprint.public import (
            QueryProjectCompletionContractV1,
            query_project_completion_contract,
        )
        from polaris.cells.factory.pipeline.public.project_completion_notification import (
            FactoryProjectCompletionIdentityV1,
            notify_factory_project_completion,
        )

        authority_result = result
        if result.stage != "chief_engineer_review":
            latest_run = await self.store.get_run(run_id)
            stage_results_raw = (
                latest_run.metadata.get("stage_results")
                if latest_run is not None and isinstance(latest_run.metadata, Mapping)
                else None
            )
            stage_results = stage_results_raw if isinstance(stage_results_raw, Mapping) else {}
            ce_result_raw = stage_results.get("chief_engineer_review")
            if not isinstance(ce_result_raw, Mapping):
                raise RuntimeError("chief_engineer_project_completion_result_missing")
            authority_result = StageResult(**dict(ce_result_raw))

        identities: list[FactoryProjectCompletionIdentityV1] = []
        for logical_ref in authority_result.artifacts:
            if not str(logical_ref).startswith("runtime/blueprints/ce_portfolio_"):
                continue
            physical_ref = Path(resolve_logical_path(str(self.workspace), str(logical_ref)))
            payload = json.loads(physical_ref.read_text(encoding="utf-8"))
            completion = payload.get("project_completion_contract")
            if not isinstance(completion, Mapping):
                continue
            project_id = str(completion.get("project_id") or "").strip()
            contract_hash = str(completion.get("contract_hash") or "").strip()
            if not project_id or not contract_hash:
                continue
            # Re-read via the CE owner API before scheduling; never trust the
            # transport artifact projection alone.
            contract = query_project_completion_contract(
                QueryProjectCompletionContractV1(
                    workspace=str(self.workspace),
                    project_id=project_id,
                    run_id=run_id,
                    contract_hash=contract_hash,
                )
            )
            identities.append(
                FactoryProjectCompletionIdentityV1(
                    workspace=str(self.workspace),
                    project_id=contract.project_id,
                    run_id=contract.run_id,
                    completion_contract_hash=contract.contract_hash,
                )
            )
        if len(identities) != 1:
            raise RuntimeError("chief_engineer_project_completion_identity_not_unique")
        return await notify_factory_project_completion(identities[0])

    async def _run_stage_heartbeat(
        self,
        run_id: str,
        stage: str,
        interval_seconds: float,
        *,
        fencing_token: int,
    ) -> None:
        # R189/M05: renew first, then sleep. Sleeping before the first renew left
        # a full interval with no heartbeat after stage claim — under loop
        # starvation the lease could expire before any renew ran.
        while True:
            # Workspace ownership is security-critical and must not depend on
            # the observability projection below. A transient Factory Run
            # Store/Event lock failure previously terminated this sole
            # coroutine, silently stopped lease renewal, and let a live
            # Director stage expire its workspace authority.
            #
            # Renew off the event loop: admission uses a process-wide file
            # lock that can be held by role-evidence cutoff.  A sync renew on
            # the asyncio thread starves HTTP/WS keepalive (R141 GET 35s +
            # websocket 1011).  Never let renew/projection exceptions kill
            # this coroutine — only CancelledError ends the heartbeat.
            try:
                await asyncio.to_thread(
                    self._admission.renew,
                    run_id,
                    fencing_token=fencing_token,
                )
            except asyncio.CancelledError:
                raise
            except Exception as renew_exc:  # noqa: BLE001 — heartbeat must survive transient renew failures
                logger.warning(
                    "Factory stage heartbeat lease renew failed for run %s stage %s: %s",
                    run_id,
                    stage,
                    renew_exc,
                )
            else:
                try:
                    await self._emit_stage_heartbeat(run_id, stage)
                except asyncio.CancelledError:
                    raise
                except Exception as projection_exc:  # noqa: BLE001 — projection is non-authoritative
                    logger.warning(
                        "Factory stage heartbeat projection failed after durable lease renewal for run %s stage %s: %s",
                        run_id,
                        stage,
                        projection_exc,
                    )
            await asyncio.sleep(interval_seconds)

    async def _emit_stage_heartbeat(self, run_id: str, stage: str) -> None:
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                return
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            current_stage = str(run.metadata.get("current_stage") or "").strip()
            if current_stage != stage:
                return

            timestamp = self._now()
            run.updated_at = timestamp
            run.metadata["last_stage_heartbeat_at"] = timestamp
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "stage_heartbeat",
                    "stage": stage,
                    "message": f"Stage {stage} is still running",
                    "timestamp": timestamp,
                },
            )

    def _build_abort_checker(self, run_id: str) -> Callable[[], Awaitable[str | None]]:
        async def _checker() -> str | None:
            current_run = await self.store.get_run(run_id)
            if current_run is None:
                return "run_not_found"
            if current_run.status == FactoryRunStatus.CANCELLED:
                return str(current_run.metadata.get("cancel_reason") or "run_cancelled")
            return None

        return _checker

    @staticmethod
    def _resolve_heartbeat_interval_seconds(context: dict[str, Any]) -> float:
        raw_value = context.get("heartbeat_interval_seconds")
        if raw_value is None:
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS
        if value <= 0:
            return 0.0
        return max(0.05, min(value, 300.0))

    @staticmethod
    def _require_failed_retry_terminal_release(run: FactoryRun) -> None:
        """Require durable proof that a failed run's old epoch fully drained."""

        lease_payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        lease = lease_payload if isinstance(lease_payload, Mapping) else {}
        release_payload = lease.get("release_evidence")
        release = release_payload if isinstance(release_payload, Mapping) else {}
        details_payload = release.get("details")
        details = details_payload if isinstance(details_payload, Mapping) else {}
        drain_payload = details.get("physical_attempt_drain")
        drain = drain_payload if isinstance(drain_payload, Mapping) else {}
        settlement_payload = details.get("task_runtime_settlement")
        settlement = settlement_payload if isinstance(settlement_payload, Mapping) else {}
        if (
            str(lease.get("run_id") or "").strip() != run.id
            or str(lease.get("state") or "").strip().lower() != "released"
            or str(release.get("factory_run_id") or "").strip() != run.id
            or drain.get("settled") is not True
            or settlement.get("settled") is not True
        ):
            raise FactoryPhysicalAttemptControlError("factory_physical_attempt_retry_terminal_settlement_missing")

    async def _replay_failed_retry_physical_attempt_epoch_locked(
        self,
        run: FactoryRun,
    ) -> FactoryPhysicalAttemptLiveControlPort:
        """Replay and permanently close the old epoch before failed-run retry."""

        operation = "recover_run"
        nonce = f"lifecycle_{uuid.uuid4().hex}"
        claimed = False
        try:
            lease = self._claim_lifecycle_operation(
                run,
                operation=operation,
                nonce=nonce,
                acquire_if_available=True,
            )
            claimed = True
            if lease.state.value != "draining":
                raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_retry_replay_fence_missing")
            settlement = await self._require_child_session_settlement_for_reentry(
                run,
                operation="retry_run_from_stage_restart_replay",
            )
            await self._reconcile_stage_execution_claim(run, settlement=settlement)
            replayed = self._physical_attempt_coordinator(run.id)
            physical_drain = replayed.close()
            if not physical_drain.settled:
                raise FactoryPhysicalAttemptReplayError(
                    "factory_physical_attempt_replay_terminal_settlement_incomplete"
                )
            run.metadata["factory_physical_attempt_admission_dead"] = True
            release_evidence = self._workspace_release_evidence(
                run.id,
                settlement,
                source="factory_failed_retry_physical_attempt_restart_replay",
                observed_at=self._now(),
                details={
                    "physical_attempt_replay_fence": True,
                    "physical_attempt_drain": {
                        "factory_run_id": physical_drain.factory_run_id,
                        "settled": physical_drain.settled,
                        "blocking_reservation_ids": list(physical_drain.blocking_reservation_ids),
                        "terminal_failure_reservation_ids": list(physical_drain.terminal_failure_reservation_ids),
                        "by_authority": [asdict(state) for state in physical_drain.by_authority],
                    },
                },
            )
            released = self._admission.release(
                run.id,
                fencing_token=lease.fencing_token,
                settlement_evidence=release_evidence,
                operation_nonce=nonce,
            )
            self._attach_workspace_lease(run, released)
            run.updated_at = self._now()
            await self.store.save_run(run)
            await self._append_event(
                run.id,
                {
                    "type": "physical_attempt_failed_retry_replayed",
                    "message": "Failed-run retry replayed and settled the prior physical-attempt epoch",
                    "timestamp": run.updated_at,
                },
            )
            claimed = False
            return replayed
        except Exception:
            if claimed:
                await self._rollback_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    reason="retry_run_from_stage_restart_replay_failed",
                )
            raise

    async def _open_fresh_physical_attempt_execution_epoch_locked(
        self,
        run: FactoryRun,
        *,
        replayed: FactoryPhysicalAttemptLiveControlPort,
        source: str,
    ) -> FactoryRun:
        """Open a new fenced epoch after the prior coordinator is settled."""

        replay_drain = replayed.close()
        if not replay_drain.settled:
            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_terminal_settlement_incomplete")
        operation = "resume_recovered_run"
        nonce = f"lifecycle_{uuid.uuid4().hex}"
        claimed = False
        try:
            lease = self._claim_lifecycle_operation(
                run,
                operation=operation,
                nonce=nonce,
                acquire_if_available=True,
            )
            claimed = True
            new_epoch = max(
                2,
                int(run.metadata.get("factory_physical_attempt_execution_epoch") or 1) + 1,
            )
            self._physical_attempt_coordinators[run.id] = FactoryPhysicalAttemptLiveControlPort(
                factory_run_id=run.id,
                revalidate_active_stage_claim=self._revalidate_active_physical_attempt_stage_claim,
            )
            run.metadata.pop("factory_physical_attempt_admission_dead", None)
            run.metadata["factory_physical_attempt_execution_epoch"] = new_epoch
            run.metadata["factory_physical_attempt_restart_replay"] = {
                "previous_epoch_closed": True,
                "previous_epoch_settled": True,
                "new_epoch": new_epoch,
                "new_workspace_fencing_token": lease.fencing_token,
                "source": source,
                "resumed_at": self._now(),
            }
            run.updated_at = self._now()
            await self.store.save_run(run)
            await self._append_event(
                run.id,
                {
                    "type": "physical_attempt_execution_epoch_reopened",
                    "execution_epoch": new_epoch,
                    "workspace_fencing_token": lease.fencing_token,
                    "source": source,
                    "message": "Prior attempt epoch is closed; a new fenced epoch is active",
                    "timestamp": run.updated_at,
                },
            )
            await self._release_lifecycle_operation(
                run,
                operation=operation,
                nonce=nonce,
            )
            claimed = False
            return run
        except Exception:
            self._physical_attempt_coordinators[run.id] = replayed
            if claimed:
                await self._rollback_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    reason="resume_recovered_run_failed",
                )
            raise

    async def _prepare_failed_retry_execution_epoch_locked(self, run: FactoryRun) -> FactoryRun:
        """Make one explicit FAILED retry safe without replaying PM or CE."""

        current = self._physical_attempt_coordinators.get(run.id)
        if current is not None and not current.admission_closed:
            return run
        replayed = (
            current if current is not None else await self._replay_failed_retry_physical_attempt_epoch_locked(run)
        )
        self._require_failed_retry_terminal_release(run)
        return await self._open_fresh_physical_attempt_execution_epoch_locked(
            run,
            replayed=replayed,
            source="failed_run_stage_retry",
        )

    async def recover_run(self, run_id: str) -> FactoryRun:
        """Recover a run from durable storage."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status in TERMINAL_RUN_STATUSES:
                return run

            operation = "recover_run"
            nonce = f"lifecycle_{uuid.uuid4().hex}"
            claimed = False
            try:
                lease = self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=True,
                )
                replay_fenced = lease.state.value == "draining"
                claimed = True
                settlement = await self._require_child_session_settlement_for_reentry(
                    run,
                    operation=operation,
                )
                run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._reconcile_stage_execution_claim(run, settlement=settlement)
                last_successful_stage = (
                    str(run.metadata.get("last_successful_stage") or "").strip()
                    or str(run.recovery_point or "").strip()
                    or str(await self._find_last_successful_stage(run_id) or "").strip()
                    or None
                )
                run.recovery_point = last_successful_stage
                run.status = FactoryRunStatus.RECOVERING
                run.updated_at = self._now()
                run.metadata["current_stage"] = last_successful_stage
                run.metadata["last_stage"] = last_successful_stage
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "recovered",
                        "stage": last_successful_stage,
                        "message": f"Recovered run at {last_successful_stage or 'start'}",
                        "timestamp": run.updated_at,
                    },
                )
                if replay_fenced:
                    physical_drain = self._physical_attempt_coordinator(run.id).close()
                    if not physical_drain.settled:
                        raise FactoryPhysicalAttemptReplayError(
                            "factory_physical_attempt_replay_terminal_settlement_incomplete"
                        )
                    run.metadata["factory_physical_attempt_admission_dead"] = True
                    release_evidence = self._workspace_release_evidence(
                        run.id,
                        settlement,
                        source="factory_physical_attempt_restart_replay",
                        observed_at=self._now(),
                        details={
                            "physical_attempt_replay_fence": True,
                            "physical_attempt_drain": {
                                "settled": physical_drain.settled,
                                "blocking_reservation_ids": list(physical_drain.blocking_reservation_ids),
                                "terminal_failure_reservation_ids": list(
                                    physical_drain.terminal_failure_reservation_ids
                                ),
                            },
                        },
                    )
                    released = self._admission.release(
                        run.id,
                        fencing_token=lease.fencing_token,
                        settlement_evidence=release_evidence,
                        operation_nonce=nonce,
                    )
                    self._attach_workspace_lease(run, released)
                    run.updated_at = self._now()
                    await self.store.save_run(run)
                else:
                    await self._release_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                    )
                claimed = False
                logger.info("Run %s recovered at stage %s", run_id, last_successful_stage)
                return run
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                        reason="recover_run_failed",
                    )
                elif run.metadata.get("factory_physical_attempt_admission_dead") is True:
                    run.updated_at = self._now()
                    await self.store.save_run(run)
                raise

    async def resume_recovered_run(self, run_id: str) -> FactoryRun:
        """Open one fresh execution epoch after strict restart replay.

        Restart replay permanently closes the *old* physical-attempt
        coordinator so an ambiguous provider request can never be repeated.
        That safety fence must not permanently kill the whole Factory run.
        After replay has drained and released the old workspace authority,
        this explicit lifecycle transition acquires a newer fencing token and
        installs an empty coordinator for future stage claims.
        """

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            if run.status in TERMINAL_RUN_STATUSES:
                return run
            if run.status is not FactoryRunStatus.RECOVERING:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_resume_requires_recovering_run")
            if run.metadata.get("factory_physical_attempt_admission_dead") is not True:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_resume_requires_replay_fence")

            replayed = self._physical_attempt_coordinator(run.id)
            replay_drain = replayed.close()
            if not replay_drain.settled:
                raise FactoryPhysicalAttemptReplayError(
                    "factory_physical_attempt_replay_terminal_settlement_incomplete"
                )
            current = self._admission.current()
            if (
                current is None
                or current.run_id != run.id
                or current.state.value != "released"
                or current.lifecycle_operation_claim is not None
                or current.release_evidence is None
                or current.release_evidence.details.get("physical_attempt_replay_fence") is not True
            ):
                raise FactoryPhysicalAttemptControlError(
                    "factory_physical_attempt_resume_replay_release_evidence_missing"
                )
            return await self._open_fresh_physical_attempt_execution_epoch_locked(
                run,
                replayed=replayed,
                source="restart_replay",
            )

    async def retry_run_from_stage(
        self,
        run_id: str,
        target_stage: str | None = None,
        reason: str | None = None,
    ) -> FactoryRun:
        """Move a run into recovery from a checkpoint or configured stage."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status in {FactoryRunStatus.COMPLETED, FactoryRunStatus.CANCELLED}:
                return run
            if run.status != FactoryRunStatus.FAILED:
                raise ValueError(f"Run {run_id} cannot be retried in status {run.status.value}")

            run = await self._prepare_failed_retry_execution_epoch_locked(run)
            self._require_physical_attempt_admission_open(run.id)
            self._acquire_workspace_lease(run)
            settlement = await self._require_child_session_settlement_for_reentry(
                run,
                operation="retry_run_from_stage",
            )

            configured_stages = [str(stage).strip() for stage in run.config.stages if str(stage).strip()]
            requested_stage = str(target_stage or "").strip()
            if requested_stage and requested_stage not in configured_stages:
                raise ValueError(f"Stage {requested_stage} is not configured for run {run_id}")

            retry_stage = (
                requested_stage
                or str(run.metadata.get("last_successful_stage") or "").strip()
                or str(run.recovery_point or "").strip()
                or str(await self._find_last_successful_stage(run_id) or "").strip()
                or None
            )
            retry_start_policy = "rerun_stage" if requested_stage else "after_checkpoint"
            retry_execution_stage = retry_stage
            if retry_stage and retry_stage in configured_stages:
                stage_index = configured_stages.index(retry_stage)
                rerun_start_index = stage_index if requested_stage else stage_index + 1
            else:
                rerun_start_index = 0
            stages_to_rerun = set(configured_stages[rerun_start_index:])
            if stages_to_rerun:
                run.stages_completed = [stage for stage in run.stages_completed if stage not in stages_to_rerun]
                run.stages_failed = [stage for stage in run.stages_failed if stage not in stages_to_rerun]
                retry_execution_stage = (
                    configured_stages[rerun_start_index] if rerun_start_index < len(configured_stages) else retry_stage
                )

            timestamp = self._now()
            previous_status = run.status.value
            previous_failure = run.metadata.get("failure")
            run.recovery_point = retry_stage
            run.status = FactoryRunStatus.RECOVERING
            run.completed_at = None
            run.updated_at = timestamp
            run.metadata["current_stage"] = retry_execution_stage
            run.metadata["last_stage"] = retry_stage
            run.metadata["retry_from_status"] = previous_status
            run.metadata["retry_start_policy"] = retry_start_policy
            run.metadata["retry_requested_stage"] = requested_stage or None
            run.metadata["retry_execution_stage"] = retry_execution_stage
            run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
            if previous_failure:
                run.metadata["retry_previous_failure"] = previous_failure
            run.metadata["failure"] = None
            run.metadata["last_failed_stage"] = None
            if reason:
                run.metadata["retry_reason"] = reason
            await self.store.save_run(run)
            await self._reconcile_stage_execution_claim(run, settlement=settlement)
            await self._append_event(
                run_id,
                {
                    "type": "retry_requested",
                    "stage": retry_stage,
                    "message": f"Retry requested from {retry_stage or 'start'}",
                    "reason": reason,
                    "previous_status": previous_status,
                    "timestamp": timestamp,
                },
            )
            logger.info("Run %s retry requested from stage %s", run_id, retry_stage)
            return run

    async def execute_pause(self, run_id: str) -> FactoryRun:
        """Pause a running factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status == FactoryRunStatus.RUNNING:
                self._physical_attempt_coordinator(run.id)
                self._renew_workspace_lease(run, require_active=True)
                run.status = FactoryRunStatus.PAUSED
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "paused",
                        "message": "Run paused",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s paused", run_id)
            return run

    async def execute_resume(self, run_id: str) -> FactoryRun:
        """Resume a paused factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status == FactoryRunStatus.PAUSED:
                self._require_physical_attempt_admission_open(run.id)
                self._acquire_workspace_lease(run)
                run.status = FactoryRunStatus.RUNNING
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "resumed",
                        "message": "Run resumed",
                        "timestamp": run.updated_at,
                    },
                )
                logger.info("Run %s resumed", run_id)
            return run

    async def update_run_metadata(self, run_id: str, metadata: dict[str, Any]) -> FactoryRun:
        """Persist metadata updates for an existing factory run."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            run = await self.assert_mutation_allowed(run_id, current_run=run)
            self._physical_attempt_coordinator(run.id)
            if "last_factory_stage_commit" in metadata:
                raise FactoryStagePersistenceError(
                    "factory_stage_commit_pointer_mutation_forbidden",
                    "Only the stage transaction may update its monotonic commit pointer",
                )

            run.metadata.update(dict(metadata))
            run.updated_at = self._now()
            await self.store.save_run(run)
            await self._append_event(
                run_id,
                {
                    "type": "metadata_updated",
                    "message": "Run metadata updated",
                    "metadata_keys": sorted(str(key) for key in metadata),
                    "timestamp": run.updated_at,
                },
            )
            logger.info("Run %s metadata updated: keys=%s", run_id, sorted(str(key) for key in metadata))
            return run

    async def start_run(self, run_id: str) -> FactoryRun:
        """Start a run only after durable workspace admission succeeds."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status in TERMINAL_RUN_STATUSES:
                return run

            operation = "start_run"
            nonce = f"lifecycle_{uuid.uuid4().hex}"
            claimed = False
            try:
                self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=True,
                )
                claimed = True
                self._require_physical_attempt_admission_open(run.id)
                started_now = run.status == FactoryRunStatus.PENDING
                if started_now:
                    started_at = self._now()
                    run.status = FactoryRunStatus.RUNNING
                    run.started_at = started_at
                    run.updated_at = started_at
                else:
                    run.updated_at = self._now()
                await self.store.save_run(run)
                if started_now:
                    await self._append_event(
                        run_id,
                        {
                            "type": "started",
                            "message": "Run started",
                            "timestamp": run.updated_at,
                        },
                    )
                    logger.info("Run %s started", run_id)
                await self._release_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                )
                claimed = False
                return run
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                        reason="start_run_failed",
                        persist_run=False,
                    )
                raise

    async def cancel_run(self, run_id: str, reason: str | None = None) -> FactoryRun:
        """Cancel a factory run and keep a distinct terminal status."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status not in TERMINAL_RUN_STATUSES:
                operation = "cancel_run"
                nonce = f"lifecycle_{uuid.uuid4().hex}"
                claimed = False
                try:
                    if run.status != FactoryRunStatus.PENDING:
                        self._claim_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                            acquire_if_available=False,
                        )
                        claimed = True
                        await self._begin_terminal_drain(
                            run,
                            reason=reason or "factory_run_cancelled",
                            operation_nonce=nonce,
                        )
                    timestamp = self._now()
                    run.status = FactoryRunStatus.CANCELLED
                    run.completed_at = timestamp
                    run.updated_at = timestamp
                    if reason:
                        run.metadata["cancel_reason"] = reason
                    await self.store.save_run(run)
                    await self._append_event(
                        run_id,
                        {
                            "type": "cancelled",
                            "message": reason or "Run cancelled",
                            "reason": reason,
                            "timestamp": timestamp,
                        },
                    )
                    if claimed:
                        await self._release_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                        )
                        claimed = False
                    logger.info("Run %s cancelled", run_id)
                    _signal_factory_cancel_event(self.workspace, run_id)
                except Exception:
                    if claimed:
                        await self._rollback_lifecycle_operation(
                            run,
                            operation=operation,
                            nonce=nonce,
                            reason="cancel_run_failed",
                        )
                    raise

        run = await self.settle_terminal_run(run_id)
        self._trigger_archive(run_id, "cancelled")
        return run

    async def complete_run(self, run_id: str, success: bool = True) -> FactoryRun:
        """Close the Factory orchestration session without granting verification.

        ``FactoryRun.status`` is an operational lifecycle projection used by
        the HTTP control surface. Verified delivery authority belongs to the
        canonical Run Ledger / QA projection and is intentionally not inferred
        from the caller-provided ``success`` flag.

        Stage failure paths may set ``status=FAILED`` before this method runs
        (via ``_apply_stage_result_to_run``). This close-out must still stamp
        ``completed_at`` / ``completion_authority`` and drive terminal drain
        settlement — skipping when already terminal left R50-class runs with
        ``completed_at=null`` and leases stuck in ``draining``.
        """
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            # Preserve CANCELLED: do not upgrade or emit a completion event.
            if run.status != FactoryRunStatus.CANCELLED:
                missing_completion_authority = not str(run.metadata.get("completion_authority") or "").strip()
                needs_lifecycle_closeout = (
                    run.status not in TERMINAL_RUN_STATUSES or run.completed_at is None or missing_completion_authority
                )
                if needs_lifecycle_closeout:
                    current_lease = self._admission.current()
                    lease_already_released = (
                        current_lease is None
                        or current_lease.run_id != run.id
                        or str(current_lease.state.value) == "released"
                    )
                    # Stage wrapper may have already settled/released the lease
                    # before re-raising. Still stamp completed_at / completion_authority.
                    if lease_already_released and run.status in TERMINAL_RUN_STATUSES:
                        timestamp = self._now()
                        if run.completed_at is None:
                            run.completed_at = timestamp
                        run.updated_at = timestamp
                        run.metadata["completion_authority"] = "orchestration_session_lifecycle"
                        run.metadata["verified"] = False
                        run.metadata["verification_authority"] = "execution_ledger_projection"
                        await self.store.save_run(run)
                        event_success = run.status == FactoryRunStatus.COMPLETED
                        await self._append_event(
                            run_id,
                            {
                                "type": "completed" if event_success else "failed",
                                "message": "Run completed" if event_success else "Run failed",
                                "timestamp": timestamp,
                                "success": event_success,
                                "authoritative": False,
                                "verified": False,
                                "authority_scope": "orchestration_session_lifecycle",
                            },
                        )
                        logger.info(
                            "Factory orchestration session %s soft-closed after prior lease release status=%s",
                            run_id,
                            run.status.value,
                        )
                    else:
                        operation = "complete_run"
                        nonce = f"lifecycle_{uuid.uuid4().hex}"
                        claimed = False
                        try:
                            if run.status != FactoryRunStatus.PENDING:
                                self._claim_lifecycle_operation(
                                    run,
                                    operation=operation,
                                    nonce=nonce,
                                    acquire_if_available=False,
                                )
                                claimed = True
                                await self._begin_terminal_drain(
                                    run,
                                    reason="factory_run_completed" if success else "factory_run_failed",
                                    operation_nonce=nonce,
                                )
                            timestamp = self._now()
                            if run.status not in TERMINAL_RUN_STATUSES:
                                run.status = FactoryRunStatus.COMPLETED if success else FactoryRunStatus.FAILED
                            elif run.status == FactoryRunStatus.FAILED and success:
                                # Stage already failed; do not upgrade to COMPLETED.
                                run.status = FactoryRunStatus.FAILED
                            if run.completed_at is None:
                                run.completed_at = timestamp
                            run.updated_at = timestamp
                            run.metadata["completion_authority"] = "orchestration_session_lifecycle"
                            run.metadata["verified"] = False
                            run.metadata["verification_authority"] = "execution_ledger_projection"
                            await self.store.save_run(run)
                            event_success = run.status == FactoryRunStatus.COMPLETED
                            await self._append_event(
                                run_id,
                                {
                                    "type": "completed" if event_success else "failed",
                                    "message": "Run completed" if event_success else "Run failed",
                                    "timestamp": timestamp,
                                    "success": event_success,
                                    "authoritative": False,
                                    "verified": False,
                                    "authority_scope": "orchestration_session_lifecycle",
                                },
                            )
                            if claimed:
                                await self._release_lifecycle_operation(
                                    run,
                                    operation=operation,
                                    nonce=nonce,
                                )
                                claimed = False
                            logger.info(
                                "Factory orchestration session %s closed with success=%s status=%s",
                                run_id,
                                success,
                                run.status.value,
                            )
                        except Exception:
                            if claimed:
                                await self._rollback_lifecycle_operation(
                                    run,
                                    operation=operation,
                                    nonce=nonce,
                                    reason="complete_run_failed",
                                )
                            raise

        run = await self.settle_terminal_run(run_id)
        archive_reason = "completed" if run.status == FactoryRunStatus.COMPLETED else "failed"
        if run.status == FactoryRunStatus.CANCELLED:
            archive_reason = "cancelled"
        self._trigger_archive(run_id, archive_reason)
        return run

    async def settle_terminal_run(
        self,
        run_id: str,
        *,
        expected_fencing_token: int | None = None,
    ) -> FactoryRun:
        """Explicitly settle a terminal run; observation APIs never call this.

        A supplied fencing token is verified by the admission claim while its
        exclusive lock is held.  The legacy run-id-only path keeps its existing
        behavior for direct callers.
        """

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            if run.status not in TERMINAL_RUN_STATUSES:
                return run
            current = self._admission.current()
            if current is None or current.run_id != run.id:
                if expected_fencing_token is None:
                    return run
            elif current.state.value == "released":
                if expected_fencing_token is None or current.fencing_token == expected_fencing_token:
                    return run
            elif expected_fencing_token is None:
                self._attach_workspace_lease(run, current)

            operation = "settle_terminal_run"
            nonce = f"lifecycle_{uuid.uuid4().hex}"
            claimed = False
            try:
                lease = self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=False,
                    expected_fencing_token=expected_fencing_token,
                )
                claimed = True
                if lease.state.value == "active":
                    draining_lease = await self._begin_terminal_drain(
                        run,
                        reason=f"terminal_{run.status.value}",
                        operation_nonce=nonce,
                    )
                    if draining_lease is None:
                        raise RuntimeError("factory_terminal_drain_lease_missing")
                    lease = draining_lease
                run = await self._finalize_terminal_drain(
                    run,
                    lease,
                    operation_nonce=nonce,
                )
                current = self._admission.current()
                if (
                    current is not None
                    and current.run_id == run.id
                    and current.lifecycle_operation_claim is not None
                    and current.lifecycle_operation_claim.nonce == nonce
                ):
                    await self._release_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                    )
                claimed = False
                return run
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=nonce,
                        reason="settle_terminal_run_failed",
                    )
                raise

    async def recover_stale_workspace_owner(
        self,
        run_id: str,
        *,
        expected_fencing_token: int,
        reason: str,
    ) -> FactoryWorkspaceRunLeaseV1:
        """Explicitly fence expired child sessions and release one stale owner."""

        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            operation = "recover_stale_workspace_owner"
            operation_nonce = uuid.uuid4().hex
            claimed = False
            try:
                stale = self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=operation_nonce,
                    acquire_if_available=False,
                    expected_fencing_token=expected_fencing_token,
                    allow_expired_owner=True,
                )
                claimed = True
                self._attach_workspace_lease(run, stale)

                physical_drain = self._physical_attempt_coordinator(run.id).close()
                physical_drain_evidence = {
                    "factory_run_id": physical_drain.factory_run_id,
                    "settled": physical_drain.settled,
                    "blocking_reservation_ids": list(physical_drain.blocking_reservation_ids),
                    "terminal_failure_reservation_ids": list(physical_drain.terminal_failure_reservation_ids),
                    "by_authority": [asdict(state) for state in physical_drain.by_authority],
                }
                run.metadata["factory_physical_attempt_drain"] = physical_drain_evidence
                if not physical_drain.settled:
                    from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                    raise FactoryPipelineError(
                        "Factory stale-owner recovery found unsettled physical provider attempts",
                        code="factory_physical_attempt_drain_open",
                        details={"physical_attempt_drain": physical_drain_evidence},
                    )

                fence_result = fence_expired_factory_run_sessions(
                    FenceExpiredFactoryRunSessionsCommandV1(
                        workspace=str(self.workspace),
                        factory_run_id=run_id,
                        reason=reason,
                    )
                )
                if not fence_result.ok:
                    from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                    raise FactoryPipelineError(
                        "Factory stale-owner recovery could not fence child sessions",
                        code="factory_workspace_stale_owner_fence_failed",
                        details=fence_result.to_record(),
                    )
                settlement = self._query_child_session_settlement(run_id)
                if settlement.get("settled") is not True:
                    from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                    raise FactoryPipelineError(
                        "Factory stale-owner recovery lacks child settlement proof",
                        code="factory_workspace_run_child_session_inflight",
                        details={"settlement": settlement, "fence_result": fence_result.to_record()},
                    )

                observed_at = self._now()
                run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
                run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = True
                run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = settlement
                run.updated_at = observed_at
                await self.store.save_run(run)
                release_evidence = self._workspace_release_evidence(
                    run_id,
                    settlement,
                    source="factory_stale_owner_recovery",
                    observed_at=observed_at,
                    fenced_session_ids=fence_result.fenced_session_ids,
                    details={
                        "reason": reason,
                        "session_fence": fence_result.to_record(),
                        "physical_attempt_drain": physical_drain_evidence,
                    },
                )
                released = self._admission.recover_stale_owner(
                    run_id,
                    fencing_token=stale.fencing_token,
                    operation_nonce=operation_nonce,
                    settlement_evidence=release_evidence,
                    reason=reason,
                )
                claimed = False
                self._attach_workspace_lease(run, released)
                run.updated_at = self._now()
                await self.store.save_run(run)
                await self._append_event(
                    run_id,
                    {
                        "type": "workspace_stale_owner_recovered",
                        "message": "Expired Factory workspace owner was explicitly fenced and released",
                        "lease": released.to_dict(),
                        "settlement": settlement,
                        "timestamp": run.updated_at,
                    },
                )
                return released
            except Exception:
                if claimed:
                    await self._rollback_lifecycle_operation(
                        run,
                        operation=operation,
                        nonce=operation_nonce,
                        reason="factory_stale_owner_recovery_failed",
                    )
                elif run.metadata.get("factory_physical_attempt_admission_dead") is True:
                    run.updated_at = self._now()
                    await self.store.save_run(run)
                raise

    async def list_runs(self) -> list[dict[str, Any]]:
        """List all factory runs with basic info."""
        run_ids = self.store.list_runs()
        runs: list[dict[str, Any]] = []
        for run_id in run_ids:
            run = await self.store.get_run(run_id)
            if run is None:
                continue
            runs.append(
                {
                    "id": run.id,
                    "name": run.config.name,
                    "status": run.status.value,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                    "current_stage": run.metadata.get("current_stage"),
                    "last_successful_stage": run.metadata.get("last_successful_stage"),
                    "stages_completed": len(run.stages_completed),
                    "stages_failed": len(run.stages_failed),
                }
            )
        return runs

    async def get_run(self, run_id: str) -> FactoryRun | None:
        """Return one persisted run without mutation, cleanup, or events."""

        return await self.store.get_run(run_id)

    async def get_run_events(self, run_id: str) -> list[dict[str, Any]]:
        """Get all events for a run."""
        return await self.store.get_events(run_id)

    async def assert_mutation_allowed(
        self,
        run_id: str,
        *,
        current_run: FactoryRun | None = None,
    ) -> FactoryRun:
        """Fail closed unless the latest stage transaction is fully committed."""

        run_snapshot = await self.store.read_strict_run_snapshot(run_id)
        try:
            persisted_run = FactoryRun.from_dict(run_snapshot)
        except (KeyError, TypeError, ValueError) as exc:
            raise FactoryStagePersistenceError(
                "factory_stage_current_run_invalid",
                "Current run snapshot does not satisfy the Factory run contract",
            ) from exc
        if persisted_run.id != run_id:
            raise FactoryStagePersistenceError(
                "factory_stage_current_run_identity_mismatch",
                "Current run snapshot belongs to another Factory run",
            )
        events = await self.store.get_authoritative_events(run_id)
        state = reduce_factory_stage_persistence(events, factory_run_id=run_id)
        if state.is_quarantined:
            # Terminal closeout (complete_run / settle) must still proceed after
            # quarantine terminalize; otherwise lease stays active forever (R56).
            if persisted_run.status in TERMINAL_RUN_STATUSES:
                if current_run is not None:
                    self._copy_run_state(current_run, persisted_run)
                return persisted_run
            raise FactoryStagePersistenceError(
                "factory_stage_persistence_quarantined",
                "Factory run has an explicit quarantine or unmatched stage event",
                details={
                    "pending_stage_event_id": state.pending_stage_event_id or "",
                    "quarantine_event_id": state.quarantine_event_id or "",
                },
            )
        latest_commit = state.latest_commit
        if latest_commit is not None:
            checkpoint = await self.store.read_strict_checkpoint_snapshot(run_id, latest_commit.checkpoint_ref)
            self._validate_checkpoint_ref_from_typed_run(run_id, latest_commit.checkpoint_ref, checkpoint)
            validate_committed_checkpoint_hashes(latest_commit, checkpoint)
            validate_current_stage_commit_pointer(
                persisted_run.metadata.get("last_factory_stage_commit"),
                latest_commit,
            )
            metadata = checkpoint.get("metadata")
            checkpoint_pointer = metadata.get("last_factory_stage_commit") if isinstance(metadata, Mapping) else None
            validate_current_stage_commit_pointer(checkpoint_pointer, latest_commit)
        else:
            validate_current_stage_commit_pointer(
                persisted_run.metadata.get("last_factory_stage_commit"),
                latest_commit,
            )
        if current_run is not None:
            self._copy_run_state(current_run, persisted_run)
        return persisted_run

    @staticmethod
    def automatic_router_mutation_guard_matrix() -> dict[str, tuple[str, ...]]:
        """Return a detached audit projection of the Service-owned matrix."""

        return dict(_AUTOMATIC_ROUTER_MUTATION_GUARD_MATRIX)

    @staticmethod
    def _automatic_router_mutation_families(operation: str) -> tuple[str, ...]:
        families = _AUTOMATIC_ROUTER_MUTATION_GUARD_MATRIX.get(operation)
        if families is None:
            raise RuntimeError(f"Unknown automatic Factory router mutation group: {operation}")
        return families

    async def assert_automatic_router_mutation_allowed(
        self,
        run_id: str,
        *,
        operation: str,
        current_run: FactoryRun | None = None,
    ) -> FactoryRun:
        """Service-owned read guard for service methods that do their own writes."""

        self._automatic_router_mutation_families(operation)
        async with self._get_run_lock(run_id):
            return await self.assert_mutation_allowed(run_id, current_run=current_run)

    async def apply_automatic_router_mutation(
        self,
        run_id: str,
        *,
        operation: str,
        mutation: Callable[[FactoryRun], bool | None],
        event: Mapping[str, Any] | None = None,
    ) -> FactoryRun:
        """Atomically guard, mutate, persist, and optionally append one router event.

        The strict stage-transaction reread and all mutable-run writes share the
        same per-run lock. A concurrent cancel/commit therefore cannot be
        overwritten by a stale router snapshot.
        """

        families = self._automatic_router_mutation_families(operation)
        if "store.save_run" not in families:
            raise RuntimeError(f"Automatic Factory router operation is not a direct persistence family: {operation}")
        if event is not None and "_append_event" not in families:
            raise RuntimeError(f"Automatic Factory router operation cannot append events: {operation}")
        async with self._get_run_lock(run_id):
            current = await self.assert_mutation_allowed(run_id)
            # Router projections are mutations too.  A restarted service may
            # not use this generic callback seam to write around the strict
            # physical-attempt replay fence.
            self._physical_attempt_coordinator(run_id)
            changed = mutation(current) is not False
            if changed:
                await self.store.save_run(current)
                if event is not None:
                    await self._append_event(run_id, dict(event))
            return FactoryRun.from_dict(current.to_dict())

    async def _execute_stage_logic(
        self,
        run: FactoryRun,
        stage: str,
        context: dict[str, Any],
    ) -> StageResult:
        if stage not in SUPPORTED_FACTORY_STAGES:
            return StageResult(stage=stage, status="skipped", output="No handler for this stage")
        stage_context = dict(context)
        if stage in {"chief_engineer_review", "director_dispatch", "quality_gate"}:
            proof = await self._revalidated_pm_stage_artifact_binding(run.id)
            if proof is not None:
                # Overwrite caller data.  Only Factory-owned strict revalidation
                # may issue this in-process evidence carrier.
                stage_context[PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY] = proof
            else:
                stage_context.pop(PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY, None)
        return await self._executor.execute(stage, run, stage_context)

    async def _find_last_successful_stage(self, run_id: str) -> str | None:
        """Find the last checkpoint-backed, commit-ACKed successful stage."""
        events = await self.store.get_authoritative_events(run_id)
        state = reduce_factory_stage_persistence(events, factory_run_id=run_id)
        if state.is_quarantined:
            raise FactoryStagePersistenceError(
                "factory_stage_persistence_quarantined",
                "Factory run has an explicit or unmatched stage transaction",
            )
        events_by_id = {str(event.get("event_id") or ""): event for event in events}
        for commit in reversed(state.commits):
            event = events_by_id.get(commit.stage_completed_event_id)
            result = event.get("result") if isinstance(event, Mapping) else None
            if not isinstance(result, Mapping) or result.get("status") != "success":
                continue
            checkpoint = await self.store.read_strict_checkpoint_snapshot(run_id, commit.checkpoint_ref)
            self._validate_checkpoint_ref_from_typed_run(run_id, commit.checkpoint_ref, checkpoint)
            validate_committed_checkpoint_hashes(commit, checkpoint)
            return commit.stage
        return None

    def _validate_checkpoint_ref_from_typed_run(
        self,
        run_id: str,
        checkpoint_ref: str,
        checkpoint: Mapping[str, Any],
    ) -> FactoryRun:
        """Reconstruct the sole checkpoint ref from a strict typed checkpoint."""

        try:
            checkpoint_run = FactoryRun.from_dict(dict(checkpoint))
            expected_ref = self.store.checkpoint_ref(checkpoint_run)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            raise FactoryStagePersistenceError(
                "factory_stage_checkpoint_invalid",
                "Committed checkpoint does not satisfy the typed Factory run contract",
            ) from exc
        if checkpoint_run.id != run_id or expected_ref != checkpoint_ref:
            raise FactoryStagePersistenceError(
                "factory_stage_checkpoint_ref_mismatch",
                "Committed checkpoint ref is not the exact Store reconstruction",
                details={"expected": expected_ref, "observed": checkpoint_ref},
            )
        return checkpoint_run

    @staticmethod
    def _copy_run_state(target: FactoryRun, source: FactoryRun) -> None:
        restored = FactoryRun.from_dict(source.to_dict())
        target.config = restored.config
        target.status = restored.status
        target.created_at = restored.created_at
        target.updated_at = restored.updated_at
        target.started_at = restored.started_at
        target.completed_at = restored.completed_at
        target.stages_completed = restored.stages_completed
        target.stages_failed = restored.stages_failed
        target.recovery_point = restored.recovery_point
        target.metadata = restored.metadata

    def _apply_stage_result_to_run(
        self,
        target_run: FactoryRun,
        result: StageResult,
        *,
        source_run: FactoryRun,
        error: Exception | None,
    ) -> None:
        completed_at = result.completed_at or self._now()
        result.completed_at = completed_at
        lease_payload = source_run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        if isinstance(lease_payload, Mapping):
            target_run.metadata[_WORKSPACE_LEASE_METADATA_KEY] = dict(lease_payload)
        target_run.metadata["last_stage"] = result.stage
        target_run.metadata["current_stage_completed_at"] = completed_at
        target_run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
        result_metadata = result.metadata if isinstance(result.metadata, dict) else {}
        inflight_run_continues = result_metadata.get("inflight_run_continues") is True
        child_sessions_settled = result_metadata.get("child_sessions_settled") is True and not inflight_run_continues
        target_run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = child_sessions_settled
        target_run.metadata[_CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY] = {
            "schema_version": "factory-stage.child-session-settlement/1",
            "stage": result.stage,
            "child_sessions_settled": child_sessions_settled,
            "inflight_run_continues": inflight_run_continues,
            "source": str(result_metadata.get("settlement_source") or "stage_result.metadata"),
        }
        stage_results = target_run.metadata.get("stage_results")
        if not isinstance(stage_results, dict):
            stage_results = {}
        stage_results[result.stage] = result.to_dict()
        target_run.metadata["stage_results"] = stage_results

        cancelled_externally = (
            target_run.status == FactoryRunStatus.CANCELLED or str(result.status or "").strip().lower() == "cancelled"
        )
        if cancelled_externally:
            result.status = "cancelled"
            if not str(result.output or "").strip():
                reason = str(target_run.metadata.get("cancel_reason") or "Run cancelled").strip()
                result.output = f"Stage {result.stage} cancelled: {reason}"
            target_run.status = FactoryRunStatus.CANCELLED
            target_run.metadata["last_cancelled_stage"] = result.stage
        elif result.status == "success":
            self._append_unique(target_run.stages_completed, result.stage)
            target_run.recovery_point = result.stage
            target_run.metadata["last_successful_stage"] = result.stage
        elif result.status == "failed":
            self._append_unique(target_run.stages_failed, result.stage)
            target_run.status = FactoryRunStatus.FAILED
            target_run.metadata["last_failed_stage"] = result.stage
            target_run.metadata["failure"] = {
                "stage": result.stage,
                "code": "FACTORY_STAGE_FAILED",
                "detail": result.output or str(error or "Stage failed"),
                "recoverable": True,
                "timestamp": completed_at,
            }
        target_run.updated_at = completed_at

    async def _build_stage_artifact_bindings(
        self,
        run_id: str,
        result: StageResult,
    ) -> FactoryStageArtifactBindingsV1 | None:
        if result.status != "success" or result.stage not in {"pm_planning", "chief_engineer_review"}:
            return None
        if self._stage_artifact_binding_builder is not None:
            return self._stage_artifact_binding_builder(run_id, result)
        source_root = Path(resolve_storage_roots(str(self.workspace)).runtime_root).resolve()
        if result.stage == "pm_planning":
            return await asyncio.to_thread(
                build_pm_stage_artifact_bindings,
                factory_store=self.store,
                source_root=source_root,
                factory_run_id=run_id,
            )
        events = await self.store.get_authoritative_events(run_id)
        state = reduce_factory_stage_persistence(events, factory_run_id=run_id)
        pm_commits = [commit for commit in state.commits if commit.stage == "pm_planning"]
        if not pm_commits:
            raise FactoryStagePersistenceError(
                "factory_stage_artifact_pm_commit_missing",
                "CE artifact binding requires a committed PM stage event",
            )
        pm_event_id = pm_commits[-1].stage_completed_event_id
        pm_event = next((event for event in events if event.get("event_id") == pm_event_id), None)
        if not isinstance(pm_event, Mapping):
            raise FactoryStagePersistenceError(
                "factory_stage_artifact_pm_event_missing",
                "Committed PM event is absent from the authoritative chain",
            )
        return await asyncio.to_thread(
            build_chief_engineer_stage_artifact_bindings,
            factory_store=self.store,
            source_root=source_root,
            factory_run_id=run_id,
            pm_stage_event=pm_event,
        )

    async def _strict_reread_stage_artifact_bindings(
        self,
        run_id: str,
        stage: str,
        bindings: FactoryStageArtifactBindingsV1,
    ) -> None:
        """Re-prove every immutable binding snapshot immediately before fact append."""

        try:
            parsed = FactoryStageArtifactBindingsV1.from_record(bindings.to_record())
            if parsed.factory_run_id != run_id or parsed.stage != stage:
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_binding_identity_mismatch",
                    "Artifact binding does not match the exact Factory run/stage identity",
                )

            async def reread(ref: str, raw_hash: str, byte_count: int) -> None:
                await asyncio.to_thread(
                    self.store.read_stage_artifact_snapshot,
                    run_id,
                    ref,
                    raw_hash,
                    byte_count,
                )

            if parsed.stage == "pm_planning":
                pm_item = parsed.items[0]
                if not isinstance(pm_item, PMContractArtifactBindingV1):
                    raise FactoryStageArtifactBindingError(
                        "factory_stage_artifact_pm_item_invalid",
                        "PM binding does not contain the exact PM contract item",
                    )
                await reread(pm_item.immutable_snapshot_ref, pm_item.raw_sha256, pm_item.utf8_byte_count)
                return

            pm_event_item = parsed.items[0]
            review_item = parsed.items[1]
            if not isinstance(pm_event_item, PMStageEventArtifactBindingV1) or not isinstance(
                review_item, CEReviewManifestArtifactBindingV1
            ):
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_ce_item_invalid",
                    "CE binding prefix items are not exact PM-event/review bindings",
                )
            events = await self.store.get_authoritative_events(run_id)
            pm_stage_event = next(
                (
                    event
                    for event in events
                    if event.get("event_id") == pm_event_item.event_id
                    and event.get("chain_sequence") == pm_event_item.chain_sequence
                    and event.get("chain_event_hash") == pm_event_item.chain_event_hash
                ),
                None,
            )
            if not isinstance(pm_stage_event, Mapping):
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_event_identity_mismatch",
                    "CE binding does not reference an exact authoritative PM stage event",
                )
            pm_bindings = FactoryStageArtifactBindingsV1.from_record(pm_stage_event.get("stage_artifact_bindings"))
            if pm_bindings.factory_run_id != run_id or pm_bindings.stage != "pm_planning":
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_event_binding_invalid",
                    "Referenced PM stage binding identity is invalid",
                )
            pm_item = pm_bindings.items[0]
            if not isinstance(pm_item, PMContractArtifactBindingV1) or (
                pm_event_item.pm_immutable_snapshot_ref,
                pm_event_item.pm_raw_sha256,
                pm_event_item.pm_canonical_json_sha256,
                pm_event_item.pm_task_id_vector_sha256,
                pm_event_item.pm_target_files_projection_sha256,
            ) != (
                pm_item.immutable_snapshot_ref,
                pm_item.raw_sha256,
                pm_item.canonical_json_sha256,
                pm_item.task_id_vector_sha256,
                pm_item.target_files_projection_sha256,
            ):
                raise FactoryStageArtifactBindingError(
                    "factory_stage_artifact_pm_event_binding_mismatch",
                    "CE PM-event binding does not match the committed PM artifact binding",
                )
            await reread(pm_item.immutable_snapshot_ref, pm_item.raw_sha256, pm_item.utf8_byte_count)
            await reread(review_item.immutable_snapshot_ref, review_item.raw_sha256, review_item.utf8_byte_count)
            for item in parsed.items[2:]:
                if not isinstance(item, CEBlueprintArtifactBindingV1):
                    raise FactoryStageArtifactBindingError(
                        "factory_stage_artifact_ce_blueprint_item_invalid",
                        "CE binding contains a non-blueprint tail item",
                    )
                await reread(item.immutable_snapshot_ref, item.raw_sha256, item.utf8_byte_count)
        except FactoryStagePersistenceError:
            raise
        except (FactoryStageArtifactBindingError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise FactoryStagePersistenceError(
                "factory_stage_artifact_snapshot_reread_failed",
                "Immutable stage artifact binding failed strict pre-append reread",
                details={"error_type": type(exc).__name__},
            ) from exc

    async def _append_stage_quarantine(
        self,
        *,
        run_id: str,
        stage: str,
        failed_step: str,
        stage_event: Mapping[str, Any],
        persistence_intent_sha256: str,
        error: BaseException,
    ) -> None:
        error_type = bounded_redacted_error(type(error).__name__, max_utf8_bytes=256) or "Error"
        error_message = bounded_redacted_error(error, max_utf8_bytes=2048) or error_type
        await self._append_event(
            run_id,
            {
                "type": "factory_run_quarantined",
                "schema_version": "factory.run_quarantined.v1",
                "factory_run_id": run_id,
                "stage": stage,
                "failed_step": failed_step,
                "stage_completed_event_id": str(stage_event["event_id"]),
                "stage_completed_chain_sequence": int(stage_event["chain_sequence"]),
                "stage_completed_chain_event_hash": str(stage_event["chain_event_hash"]),
                "persistence_intent_sha256": persistence_intent_sha256,
                "error_type": error_type,
                "error_message": error_message,
                "timestamp": self._now(),
            },
            publish=False,
        )
        # Quarantine must not leave orchestration forever RUNNING. Force a
        # terminal failed projection so complete_run/settle can release the lease
        # (assert_mutation_allowed allows closeout when status is already terminal).
        try:
            run = await self.store.get_run(run_id)
            if run is not None and run.status not in TERMINAL_RUN_STATUSES:
                timestamp = self._now()
                run.status = FactoryRunStatus.FAILED
                if run.completed_at is None:
                    run.completed_at = timestamp
                run.updated_at = timestamp
                run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = False
                run.metadata["completion_authority"] = "orchestration_session_lifecycle"
                run.metadata["verified"] = False
                run.metadata["verification_authority"] = "execution_ledger_projection"
                run.metadata["factory_quarantine_terminalized"] = True
                run.metadata["last_failed_stage"] = stage
                run.metadata["failure"] = {
                    "stage": stage,
                    "code": "FACTORY_STAGE_QUARANTINED",
                    "detail": f"Stage {stage} quarantined at {failed_step}: {error_type}: {error_message}",
                    "recoverable": True,
                    "timestamp": timestamp,
                }
                self._append_unique(run.stages_failed, stage)
                await self.store.save_run(run)
        except Exception as terminalize_exc:  # noqa: BLE001 — best-effort quarantine terminalize
            logger.warning(
                "Factory quarantine terminalize failed for run %s stage %s: %s",
                run_id,
                stage,
                terminalize_exc,
            )

    async def _preflight_stage_transaction(
        self,
        *,
        run_id: str,
        stage_event: dict[str, Any],
        checkpoint_ref: str,
        persistence_intent_sha256: str,
    ) -> None:
        """Prove 8 MiB capacity for both ordered transaction records."""

        (preview_stage,) = await self.store.preflight_authoritative_events(run_id, (stage_event,))
        marker_preview = {
            "type": "factory_stage_persistence_committed",
            "schema_version": "factory.stage_persistence_committed.v1",
            "run_id": run_id,
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "timestamp": self._now(),
            "factory_run_id": run_id,
            "stage": str(stage_event["stage"]),
            "stage_completed_event_id": str(preview_stage["event_id"]),
            "stage_completed_chain_sequence": int(preview_stage["chain_sequence"]),
            "stage_completed_chain_event_hash": str(preview_stage["chain_event_hash"]),
            "persistence_intent_sha256": persistence_intent_sha256,
            "run_snapshot_canonical_sha256": "0" * 64,
            "checkpoint_ref": checkpoint_ref,
            "checkpoint_canonical_sha256": "0" * 64,
        }
        await self.store.preflight_authoritative_events(run_id, (stage_event, marker_preview))

    async def _commit_stage_transaction(
        self,
        *,
        source_run: FactoryRun,
        candidate_run: FactoryRun,
        result: StageResult,
        event_payload: dict[str, Any],
        intent_sha256: str,
        checkpoint_ref: str,
        bindings: FactoryStageArtifactBindingsV1 | None,
        arbitration: _FactoryStageCommitArbitration,
        state: dict[str, object],
    ) -> FactoryRun:
        if bindings is not None:
            await self._strict_reread_stage_artifact_bindings(source_run.id, result.stage, bindings)
        stage_event = await self._append_event(source_run.id, event_payload, publish=False)
        state["stage_event"] = stage_event
        pointer = FactoryLastStageCommitV1(
            stage=result.stage,
            stage_completed_event_id=str(stage_event["event_id"]),
            stage_completed_chain_sequence=int(stage_event["chain_sequence"]),
            stage_completed_chain_event_hash=str(stage_event["chain_event_hash"]),
            persistence_intent_sha256=intent_sha256,
            checkpoint_ref=checkpoint_ref,
        )
        candidate_run.metadata["last_factory_stage_commit"] = pointer.to_record()
        failed_step = "save_run"
        try:
            await self.store.save_run(candidate_run)
            failed_step = "checkpoint"
            observed_checkpoint_ref = await self.store.checkpoint(candidate_run)
            if observed_checkpoint_ref != checkpoint_ref:
                raise FactoryStagePersistenceError(
                    "factory_stage_checkpoint_ref_mismatch",
                    "Checkpoint write returned a different logical ref",
                )
            run_snapshot = await self.store.read_strict_run_snapshot(source_run.id)
            checkpoint = await self.store.read_strict_checkpoint_snapshot(source_run.id, checkpoint_ref)
            self._validate_checkpoint_ref_from_typed_run(source_run.id, checkpoint_ref, checkpoint)
            if run_snapshot != candidate_run.to_dict() or checkpoint != candidate_run.to_dict():
                raise FactoryStagePersistenceError(
                    "factory_stage_snapshot_reread_mismatch",
                    "Strict run/checkpoint reread differs from the detached candidate",
                )
            failed_step = "commit_marker"
            marker = await self._append_event(
                source_run.id,
                {
                    "type": "factory_stage_persistence_committed",
                    "schema_version": "factory.stage_persistence_committed.v1",
                    "factory_run_id": source_run.id,
                    "stage": result.stage,
                    "stage_completed_event_id": str(stage_event["event_id"]),
                    "stage_completed_chain_sequence": int(stage_event["chain_sequence"]),
                    "stage_completed_chain_event_hash": str(stage_event["chain_event_hash"]),
                    "persistence_intent_sha256": intent_sha256,
                    "run_snapshot_canonical_sha256": canonical_run_snapshot_sha256(run_snapshot),
                    "checkpoint_ref": checkpoint_ref,
                    "checkpoint_canonical_sha256": canonical_checkpoint_sha256(checkpoint),
                    "timestamp": self._now(),
                },
                publish=False,
                commit_permit=arbitration.commit_permit,
            )
            commit = FactoryStagePersistenceCommittedV1.from_record(marker)
            validate_current_stage_commit_pointer(candidate_run.metadata.get("last_factory_stage_commit"), commit)
            state["marker_ack"] = True
        except _FactoryStageCancellationCutError:
            raise
        except BaseException as exc:
            try:
                await self._append_stage_quarantine(
                    run_id=source_run.id,
                    stage=result.stage,
                    failed_step=failed_step,
                    stage_event=stage_event,
                    persistence_intent_sha256=intent_sha256,
                    error=exc,
                )
            except BaseException as quarantine_exc:
                raise FactoryStagePersistenceError(
                    "factory_stage_quarantine_append_failed",
                    "Pending stage transaction could not append explicit quarantine",
                    details={"failed_step": failed_step},
                ) from quarantine_exc
            raise
        # Fanout is non-authoritative. A cancellation here cannot revoke the
        # already ACKed event+snapshot+checkpoint transaction.
        try:
            await self._publish_factory_event(source_run.id, stage_event)
        except asyncio.CancelledError:
            logger.debug("stage event fanout cancelled after durable commit ACK run=%s", source_run.id)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "stage event fanout failed after durable commit ACK run=%s: %s",
                source_run.id,
                exc,
            )
        return candidate_run

    async def _mark_stage_started(self, run: FactoryRun, stage: str, started_at: str) -> None:
        run.metadata["current_stage"] = stage
        run.metadata["current_stage_started_at"] = started_at
        run.metadata["last_stage"] = stage
        run.metadata[_STAGE_IN_FLIGHT_METADATA_KEY] = True
        run.metadata[_CHILD_SESSIONS_SETTLED_METADATA_KEY] = False
        run.updated_at = started_at
        await self.store.save_run(run)
        await self._append_event(
            run.id,
            {
                "type": "stage_started",
                "stage": stage,
                "message": f"Started stage {stage}",
                "timestamp": started_at,
            },
        )

    async def _mark_stage_finished(
        self,
        run: FactoryRun,
        result: StageResult,
        error: Exception | None = None,
    ) -> None:
        latest_run = await self.store.get_run(run.id)
        if latest_run is None:
            raise FactoryStagePersistenceError(
                "factory_stage_run_snapshot_missing",
                "Stage transaction requires the current run snapshot",
            )
        detached_result = StageResult(**result.to_dict())
        detached_result.completed_at = detached_result.completed_at or self._now()
        candidate_run = FactoryRun.from_dict(latest_run.to_dict())
        self._apply_stage_result_to_run(candidate_run, detached_result, source_run=run, error=error)
        checkpoint_ref = self.store.checkpoint_ref(candidate_run)
        preliminary_intent = build_stage_persistence_intent(
            factory_run_id=run.id,
            stage=detached_result.stage,
            stage_result=detached_result.to_dict(),
            checkpoint_ref=checkpoint_ref,
        )
        preliminary_event = {
            "type": "stage_completed",
            "run_id": run.id,
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "timestamp": detached_result.completed_at,
            "stage": detached_result.stage,
            "message": detached_result.output or f"Completed stage {detached_result.stage}",
            "result": detached_result.to_dict(),
            "persistence_intent": preliminary_intent.to_record(),
        }
        # Capacity is proven before any PM/CE source snapshot is frozen.
        await self._preflight_stage_transaction(
            run_id=run.id,
            stage_event=preliminary_event,
            checkpoint_ref=checkpoint_ref,
            persistence_intent_sha256=preliminary_intent.persistence_intent_sha256,
        )
        bindings: FactoryStageArtifactBindingsV1 | None = None
        try:
            bindings = await self._build_stage_artifact_bindings(run.id, detached_result)
        except (FactoryStageArtifactBindingError, FactoryStagePersistenceError, OSError, TypeError, ValueError) as exc:
            detached_result = StageResult(
                stage=result.stage,
                status="failed",
                output=f"factory_stage_artifact_binding_failed: {exc}",
                artifacts=[],
                started_at=result.started_at,
                completed_at=result.completed_at or self._now(),
                metadata={"error_code": "factory_stage_artifact_binding_failed"},
            )
            candidate_run = FactoryRun.from_dict(latest_run.to_dict())
            self._apply_stage_result_to_run(candidate_run, detached_result, source_run=run, error=exc)
            checkpoint_ref = self.store.checkpoint_ref(candidate_run)
        intent = build_stage_persistence_intent(
            factory_run_id=run.id,
            stage=detached_result.stage,
            stage_result=detached_result.to_dict(),
            checkpoint_ref=checkpoint_ref,
        )
        event_payload = {
            "type": "stage_completed",
            "run_id": run.id,
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "timestamp": detached_result.completed_at,
            "stage": detached_result.stage,
            "message": detached_result.output or f"Completed stage {detached_result.stage}",
            "result": detached_result.to_dict(),
            "persistence_intent": intent.to_record(),
        }
        if bindings is not None and detached_result.status == "success":
            event_payload["stage_artifact_bindings"] = bindings.to_record()
        # Re-prove exact payload capacity after bindings are frozen.
        await self._preflight_stage_transaction(
            run_id=run.id,
            stage_event=event_payload,
            checkpoint_ref=checkpoint_ref,
            persistence_intent_sha256=intent.persistence_intent_sha256,
        )
        transaction_state: dict[str, object] = {"marker_ack": False}
        arbitration = _FactoryStageCommitArbitration()
        worker = asyncio.create_task(
            self._commit_stage_transaction(
                source_run=run,
                candidate_run=candidate_run,
                result=detached_result,
                event_payload=event_payload,
                intent_sha256=intent.persistence_intent_sha256,
                checkpoint_ref=checkpoint_ref,
                bindings=bindings,
                arbitration=arbitration,
                state=transaction_state,
            )
        )
        try:
            committed_run = await asyncio.shield(worker)
        except asyncio.CancelledError as cancellation:
            cancellation_cut = asyncio.create_task(asyncio.to_thread(arbitration.mark_cancelled))
            while not cancellation_cut.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(cancellation_cut)
            cancellation_cut.result()
            while not worker.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(worker)
            marker_was_acked = transaction_state.get("marker_ack") is True
            worker_error: BaseException | None = None
            try:
                committed_run = worker.result()
            except (asyncio.CancelledError, OSError, RuntimeError, TypeError, ValueError) as exc:
                worker_error = exc
            if marker_was_acked and worker_error is None:
                pass
            else:
                stage_event = transaction_state.get("stage_event")
                if isinstance(worker_error, _FactoryStageCancellationCutError) and isinstance(stage_event, Mapping):
                    with contextlib.suppress(BaseException):
                        await self._append_stage_quarantine(
                            run_id=run.id,
                            stage=detached_result.stage,
                            failed_step="cancelled_before_commit_ack",
                            stage_event=stage_event,
                            persistence_intent_sha256=intent.persistence_intent_sha256,
                            error=cancellation,
                        )
                raise
        self._copy_run_state(run, committed_run)
        self._copy_run_state(latest_run, committed_run)
        result.stage = detached_result.stage
        result.status = detached_result.status
        result.output = detached_result.output
        result.artifacts = list(detached_result.artifacts)
        result.started_at = detached_result.started_at
        result.completed_at = detached_result.completed_at
        result.metadata = dict(detached_result.metadata)

    async def _append_event(
        self,
        run_id: str,
        event: dict[str, Any],
        *,
        publish: bool = True,
        commit_permit: Callable[[], contextlib.AbstractContextManager[None]] | None = None,
    ) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("run_id", run_id)
        payload.setdefault("event_id", f"evt_{uuid.uuid4().hex[:12]}")
        payload.setdefault("timestamp", self._now())
        if commit_permit is None:
            committed = await self.store.append_authoritative_event(run_id, payload)
        else:
            committed = await self.store.append_authoritative_event(
                run_id,
                payload,
                commit_permit=commit_permit,
            )
        if publish:
            await self._publish_factory_event(run_id, committed)
        return committed

    async def _publish_factory_event(self, run_id: str, event: Mapping[str, Any]) -> None:
        payload = dict(event)
        # Best-effort NAT JetStream fanout so the unified WebSocket pipeline
        # (``event.factory:<run_id>`` channel) can stream these events to
        # subscribers. The factory run stays the source of truth (durable on
        # disk); JetStream is the best-effort realtime fanout.
        try:
            from polaris.delivery.http.routers.jetstream_utils import (
                publish_to_jetstream,
            )

            workspace_key = ""
            if self.workspace:
                try:
                    roots = resolve_storage_roots(str(self.workspace))
                    workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.debug("factory workspace key resolution failed for %s: %s", self.workspace, exc)
                    workspace_key = self.workspace.name
            if not workspace_key:
                return
            subject = f"hp.runtime.{workspace_key}.event.factory.{run_id}"
            channel = f"event.factory:{run_id}"
            # Bound the realtime fanout payload to the JetStream 256KB max-message
            # limit and the WS 1MiB frame limit. A large director ``stage_completed``
            # payload (full ``task_results`` reaches ~2.7MB on big projects) is durable
            # on disk via ``store.append_event`` above; the fanout only carries control
            # fields plus a bounded preview. Without this bound the oversized frame is
            # dropped (WS close 1009), the bench loses the socket, times out after the
            # full deadline, and cancels the chain before QA runs (factory_bench L1-08).
            from polaris.delivery.ws.endpoints.json_utils import elide_oversized_frame

            envelope = {
                "schema_version": "runtime.v2",
                "event_id": payload.get("event_id"),
                "workspace_key": workspace_key,
                "run_id": run_id,
                "channel": channel,
                "kind": str(payload.get("type") or payload.get("kind") or "factory.event"),
                "ts": payload.get("timestamp"),
                "cursor": 0,
                "trace_id": None,
                "payload": elide_oversized_frame(payload, _FACTORY_FANOUT_MAX_PAYLOAD_BYTES),
                "meta": {"source": "factory_run_service"},
            }
            await asyncio.wait_for(
                publish_to_jetstream(
                    subject=subject,
                    payload=envelope,
                ),
                timeout=_factory_jetstream_fanout_timeout_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("factory JetStream fanout failed for run %s: %s", run_id, exc)

    def _trigger_archive(self, run_id: str, reason: str) -> None:
        """Trigger async archiving of factory run to history.

        This is non-blocking - archiving happens in background.
        """
        try:
            from polaris.cells.archive.factory_archive.public.service import trigger_factory_archive

            workspace = str(self.workspace) if hasattr(self, "workspace") else ""
            if workspace:
                trigger_factory_archive(
                    workspace=workspace,
                    factory_run_id=run_id,
                    reason=reason,
                )
                logger.debug("Triggered archive for factory run %s", run_id)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            # Log error but don't block the main flow
            logger.warning("Failed to trigger archive for factory run %s: %s", run_id, exc)

    @staticmethod
    def _append_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    @staticmethod
    def _now() -> str:
        return utc_now_iso()
