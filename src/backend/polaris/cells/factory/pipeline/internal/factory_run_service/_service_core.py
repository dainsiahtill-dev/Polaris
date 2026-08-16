"""_FactoryRunServiceCore methods for FactoryRunService composition.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any  # Protocol re-exported for lossless surface

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
from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    contains_factory_role_evidence_runtime_authority,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    FactoryProviderAttemptLifecycleReplaySnapshotV1,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    factory_provider_attempt_lifecycle_stream,
    query_factory_provider_attempt_lifecycle_replay,
)
from polaris.cells.runtime.task_runtime.public.service import (
    query_factory_run_settlement,
)
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.utils import utc_now_iso

from ..factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptLiveControlPort,
)
from ..factory_physical_attempt_replay import (
    FACTORY_PHYSICAL_ATTEMPT_REPLAY_FENCE_SCHEMA,
    FactoryPhysicalAttemptReplayError,
    FactoryPhysicalAttemptReplayFenceV1,
)
from ..factory_role_evidence_authority import (
    FactoryRoleEvidenceAuthorityPort,
    FactoryRoleEvidenceReplaySnapshotV1,
    FactoryRoleEvidenceStageAuthorityV1,
    factory_role_evidence_authority_stream,
    query_factory_role_evidence_replay_snapshot,
)
from ..factory_role_evidence_source_resolver import CanonicalFactoryRoleEvidenceSourceAuthority
from ..factory_run_admission import FactoryWorkspaceRunAdmission
from ..factory_run_models import (
    SUPPORTED_FACTORY_STAGES,
    TERMINAL_RUN_STATUSES,
    FactoryRun,
    FactoryRunStatus,
    FactoryStageExecutor,
    StageResult,
)
from ..factory_stage_artifact_bindings import (
    PM_STAGE_ARTIFACT_BINDING_CONTEXT_KEY,
    FactoryStageArtifactBindingsV1,
)
from ..factory_stage_executor import OrchestrationStageExecutor
from ..factory_stage_persistence import (
    FactoryStagePersistenceError,
    reduce_factory_stage_persistence,
    validate_committed_checkpoint_hashes,
    validate_current_stage_commit_pointer,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceReleaseEvidenceV1,
        FactoryWorkspaceRunLeaseV1,
    )

from ._helpers import (
    _AUTOMATIC_ROUTER_MUTATION_GUARD_MATRIX,
    _CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY,
    _CHILD_SESSIONS_SETTLED_METADATA_KEY,
    _FACTORY_FANOUT_MAX_PAYLOAD_BYTES,
    _STAGE_IN_FLIGHT_METADATA_KEY,
    _WORKSPACE_LEASE_METADATA_KEY,
    logger,
)


class _FactoryRunServiceCore:
    """Formal service for Factory runs with persistence and recovery."""

    _LOCK_BUCKETS = 64

    def __init__(
        self: Any,
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
        from ..factory_store import FactoryStore

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

    def _get_run_lock(self: Any, run_id: str) -> asyncio.Lock:
        """获取 run_id 对应的细粒度锁。

        使用哈希分片确保同一 run 的操作串行化，不同 run 可并行。
        """
        bucket = hash(run_id) % self._LOCK_BUCKETS
        return self._run_locks[bucket]

    def _physical_attempt_coordinator(self: Any, factory_run_id: str) -> FactoryPhysicalAttemptLiveControlPort:
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
        self: Any,
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
        self: Any,
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

    def _ensure_physical_attempt_replay_ledgers(self: Any, factory_run_id: str) -> None:
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
        self: Any,
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
        self: Any,
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

    def _acquire_workspace_lease(self: Any, run: FactoryRun) -> FactoryWorkspaceRunLeaseV1:
        lease = self._admission.acquire(run.id)
        self._attach_workspace_lease(run, lease)
        return lease

    def _claim_lifecycle_operation(
        self: Any,
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
        # settle_terminal_run may resume an orphaned FAILED drain after restart.
        # Reconstructing the physical-attempt coordinator requires ACTIVE or
        # replay-fenced authority; a factory_run_failed drain is neither.
        # Finalize creates a fresh coordinator instead (live L2-12 retry).
        if operation != "settle_terminal_run" and run.id not in self._physical_attempt_coordinators:
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
        self: Any,
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
        self: Any,
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
        self: Any,
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

    def _assert_active_workspace_lease(self: Any, run: FactoryRun) -> FactoryWorkspaceRunLeaseV1:
        fencing_token = self._workspace_lease_fencing_token(run)
        lease = self._admission.assert_active(run.id, fencing_token=fencing_token)
        self._attach_workspace_lease(run, lease)
        return lease

    def _claim_stage_execution(
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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

    def _query_child_session_settlement(self: Any, run_id: str) -> dict[str, object]:
        return query_factory_run_settlement(
            str(self.workspace),
            factory_run_id=run_id,
        )

    @staticmethod
    def _only_expired_owned_factory_children(evidence: Mapping[str, object]) -> bool:
        """True when leftover children are expired and owned by this Factory run.

        Live L2-12 retry: task 60 stayed ``active_expired_session`` after
        Director heartbeat died.  A still-live child must keep blocking
        reentry (``test_retry_run_rejects_active_factory_child``).
        """

        sessions = evidence.get("active_sessions")
        if not isinstance(sessions, (list, tuple)) or not sessions:
            return False
        for raw in sessions:
            if not isinstance(raw, Mapping):
                return False
            if raw.get("lease_expired") is not True:
                return False
            ownership = str(raw.get("ownership") or "").strip()
            if ownership != "requested_factory_run":
                return False
        return True

    async def _require_child_session_settlement_for_reentry(
        self: Any,
        run: FactoryRun,
        *,
        operation: str,
    ) -> dict[str, object]:
        evidence = self._query_child_session_settlement(run.id)
        if evidence.get("settled") is not True and self._only_expired_owned_factory_children(evidence):
            from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

            abort_summary = TaskRuntimeService(str(self.workspace)).terminalize_open_tasks_for_factory_abort(
                factory_run_id=run.id,
                reason=f"factory_retry_{operation}",
                source="factory_retry_force_expired_child",
                force_active_sessions=True,
            )
            run.metadata["factory_task_runtime_abort"] = abort_summary
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

    async def list_runs(self: Any) -> list[dict[str, Any]]:
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

    async def get_run(self: Any, run_id: str) -> FactoryRun | None:
        """Return one persisted run without mutation, cleanup, or events."""

        return await self.store.get_run(run_id)

    async def get_run_events(self: Any, run_id: str) -> list[dict[str, Any]]:
        """Get all events for a run."""
        return await self.store.get_events(run_id)

    async def assert_mutation_allowed(
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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

    async def _find_last_successful_stage(self: Any, run_id: str) -> str | None:
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
        self: Any,
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

    async def _append_event(
        self: Any,
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

    async def _publish_factory_event(self: Any, run_id: str, event: Mapping[str, Any]) -> None:
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
                timeout=__import__(
                    "polaris.cells.factory.pipeline.internal.factory_run_service",
                    fromlist=["_factory_jetstream_fanout_timeout_seconds"],
                )._factory_jetstream_fanout_timeout_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("factory JetStream fanout failed for run %s: %s", run_id, exc)

    def _trigger_archive(self: Any, run_id: str, reason: str) -> None:
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
