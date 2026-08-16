"""_FactoryRunServiceLifecycleMixin methods for FactoryRunService composition.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import asdict  # re-exported for lossless surface
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any  # Protocol re-exported for lossless surface

from polaris.cells.runtime.task_runtime.public.contracts import (
    FenceExpiredFactoryRunSessionsCommandV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    fence_expired_factory_run_sessions,
)

from ..factory_deadline_calculations import extend_factory_run_deadline_for_same_run_retry
from ..factory_event_chain import (
    FactoryRunAdmissionV1,
    build_factory_run_admitted_event,
)
from ..factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptLiveControlPort,
)
from ..factory_physical_attempt_replay import (
    FactoryPhysicalAttemptReplayError,
)
from ..factory_run_models import (
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
    _signal_factory_cancel_event,
)
from ..factory_stage_artifact_bindings import (
    FactoryStageArtifactBindingError,
    RevalidatedPMStageArtifactBindingV1,
    revalidate_pm_stage_artifact_binding,
)
from ..factory_stage_persistence import (
    FactoryStagePersistenceError,
    reduce_factory_stage_persistence,
)

if TYPE_CHECKING:
    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceRunLeaseV1,
    )

from ._helpers import (
    _CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY,
    _CHILD_SESSIONS_SETTLED_METADATA_KEY,
    _STAGE_IN_FLIGHT_METADATA_KEY,
    logger,
)


class _FactoryRunServiceLifecycleMixin:
    async def _finalize_terminal_drain(
        self: Any,
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

        coordinator = self._physical_attempt_coordinators.get(target_run.id)
        if coordinator is None:
            # After Launcher-restart the process-local coordinator is gone.
            # FAILED/CANCELLED drain may reuse the last persisted settled
            # snapshot or treat the empty new process as already drained.
            existing_drain = target_run.metadata.get("factory_physical_attempt_drain")
            existing_map = existing_drain if isinstance(existing_drain, Mapping) else {}
            if target_run.status not in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}:
                raise FactoryPhysicalAttemptControlError("factory_physical_attempt_replay_required")
            if existing_map.get("settled") is True:
                physical_drain_evidence = {
                    "factory_run_id": target_run.id,
                    "settled": True,
                    "blocking_reservation_ids": list(existing_map.get("blocking_reservation_ids") or []),
                    "terminal_failure_reservation_ids": list(
                        existing_map.get("terminal_failure_reservation_ids") or []
                    ),
                    "by_authority": list(existing_map.get("by_authority") or []),
                }
            else:
                physical_drain_evidence = {
                    "factory_run_id": target_run.id,
                    "settled": True,
                    "blocking_reservation_ids": [],
                    "terminal_failure_reservation_ids": [],
                    "by_authority": [],
                    "reconstructed_after_restart": True,
                }
        else:
            physical_drain = coordinator.close()
            physical_drain_evidence = {
                "factory_run_id": physical_drain.factory_run_id,
                "settled": physical_drain.settled,
                "blocking_reservation_ids": list(physical_drain.blocking_reservation_ids),
                "terminal_failure_reservation_ids": list(physical_drain.terminal_failure_reservation_ids),
                "by_authority": [asdict(state) for state in physical_drain.by_authority],
            }
        target_run.metadata["factory_physical_attempt_drain"] = physical_drain_evidence
        if physical_drain_evidence.get("settled") is not True:
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

    async def create_run(self: Any, config: FactoryConfig) -> FactoryRun:
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
        self: Any,
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

    async def recover_run(self: Any, run_id: str) -> FactoryRun:
        """Recover a run from durable storage."""
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            await self._recover_stage_commit_if_proven(run_id)
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found after cancelled stage recovery")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            if run.status in TERMINAL_RUN_STATUSES:
                return run

            operation = "recover_run"
            # A backend crash can occur after the durable restart-replay claim
            # is committed but before it is released.  Under the platform's
            # single-backend-per-workspace invariant, the next lifespan owner
            # must resume that exact idempotent operation instead of creating a
            # fresh nonce that conflicts with its own orphaned claim for the
            # full workspace TTL (live L1-04: 30 minutes).
            current_lease = self._admission.current()
            current_claim = (
                current_lease.lifecycle_operation_claim
                if current_lease is not None and current_lease.run_id == run.id
                else None
            )
            resumable_restart_claim = (
                current_lease is not None
                and current_lease.state.value == "draining"
                and current_lease.drain_reason == "factory_physical_attempt_restart_replay_fence"
                and current_claim is not None
                and current_claim.operation == operation
            )
            nonce = (
                current_claim.nonce
                if resumable_restart_claim and current_claim is not None
                else f"lifecycle_{uuid.uuid4().hex}"
            )
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
                # Backend restart recovery is allowed to close only child
                # execution leases that are already expired and belong to
                # this exact Factory run.  The TaskRuntime fence remains
                # fail-closed for live/foreign sessions and for any pending
                # directed effect.  Without this step an expired Director
                # session with no ambiguous write operation stays ``active``
                # forever: DEO startup recovery has nothing to reconcile,
                # while the Factory settlement projection correctly refuses
                # re-entry on that stale session.
                child_fence = fence_expired_factory_run_sessions(
                    FenceExpiredFactoryRunSessionsCommandV1(
                        workspace=str(self.workspace),
                        factory_run_id=run.id,
                        reason="factory_restart_recovery_expired_child_session",
                    )
                )
                run.metadata["factory_expired_child_session_fence"] = child_fence.to_record()
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

    async def resume_recovered_run(self: Any, run_id: str) -> FactoryRun:
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
        self: Any,
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
            await self._recover_stage_commit_if_proven(run_id)
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found after cancelled stage recovery")
            run = await self.assert_mutation_allowed(run_id, current_run=run)

            requested_stage = str(target_stage or "").strip()
            # Completed is terminal for PM/CE/Director replay. Live L2-14
            # quality_gate passed after skipping rust (lowercase cargo.toml).
            # Same-run owner repair must be allowed to re-run only quality_gate
            # without opening a new Factory run or rolling back PM/CE.
            if run.status == FactoryRunStatus.CANCELLED:
                return run
            if run.status == FactoryRunStatus.COMPLETED and requested_stage != "quality_gate":
                return run
            if run.status != FactoryRunStatus.FAILED and not (
                run.status == FactoryRunStatus.COMPLETED and requested_stage == "quality_gate"
            ):
                raise ValueError(f"Run {run_id} cannot be retried in status {run.status.value}")

            # Launcher-restart can leave an orphaned settle_terminal_run claim.
            # Finish that exact nonce before recover_run, otherwise retry_phase
            # conflicts for the workspace TTL (live L2-12 factory_a1b49b0460f2).
            run = await self._settle_terminal_run_locked(run)
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
            # Frozen TaskRuntime authority belongs to one Director execution
            # epoch. Re-executing Director (or any earlier stage) will create
            # newer TaskRuntime facts, so retaining the old snapshot would let
            # terminal drain reuse stale rows forever. QA-only retry preserves
            # the snapshot because no execution authority is regenerated.
            if "director_dispatch" in stages_to_rerun:
                from polaris.cells.factory.pipeline.public.contracts import (
                    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
                )

                invalidated_snapshot = run.metadata.pop(
                    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
                    None,
                )
                if isinstance(invalidated_snapshot, Mapping):
                    run.metadata["factory_terminal_task_runtime_projection_invalidation"] = {
                        "schema_version": "factory.terminal-task-runtime-projection-invalidation.v1",
                        "invalidated_at": timestamp,
                        "requested_stage": requested_stage or None,
                        "retry_execution_stage": retry_execution_stage,
                        "prior_captured_at": str(invalidated_snapshot.get("captured_at") or "").strip() or None,
                        "reason": "director_execution_epoch_reopened",
                    }
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
            deadline_extension = extend_factory_run_deadline_for_same_run_retry(
                now_epoch=datetime.now(timezone.utc).timestamp(),
                metadata=run.metadata,
                retry_stage=str(retry_execution_stage or retry_stage or ""),
            )
            if deadline_extension:
                run.metadata.update(deadline_extension)
                start_request = run.metadata.get("factory_start_request")
                if isinstance(start_request, Mapping):
                    start_payload = dict(start_request)
                    start_metadata_raw = start_payload.get("metadata")
                    start_metadata = dict(start_metadata_raw) if isinstance(start_metadata_raw, Mapping) else {}
                    start_metadata.update(deadline_extension)
                    start_payload["metadata"] = start_metadata
                    run.metadata["factory_start_request"] = start_payload
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

    async def execute_pause(self: Any, run_id: str) -> FactoryRun:
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

    async def execute_resume(self: Any, run_id: str) -> FactoryRun:
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

    async def update_run_metadata(self: Any, run_id: str, metadata: dict[str, Any]) -> FactoryRun:
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

    async def start_run(self: Any, run_id: str) -> FactoryRun:
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

    async def cancel_run(self: Any, run_id: str, reason: str | None = None) -> FactoryRun:
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

    async def complete_run(self: Any, run_id: str, success: bool = True) -> FactoryRun:
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
        self: Any,
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
            return await self._settle_terminal_run_locked(
                run,
                expected_fencing_token=expected_fencing_token,
            )

    async def _settle_terminal_run_locked(
        self: Any,
        run: FactoryRun,
        *,
        expected_fencing_token: int | None = None,
    ) -> FactoryRun:
        """Settle one terminal run while the caller already holds ``run_lock``."""

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
        current_claim = current.lifecycle_operation_claim if current is not None and current.run_id == run.id else None
        # Resume the exact orphaned settle nonce after Launcher-restart instead
        # of minting a conflicting claim for the workspace TTL.
        nonce = (
            current_claim.nonce
            if current_claim is not None and current_claim.operation == operation
            else f"lifecycle_{uuid.uuid4().hex}"
        )
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
        self: Any,
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

                # Live L2-12: expired Director session + OPEN DEO parent
                # made fence_expired_factory_run_sessions fail-closed
                # (settlement_parent_close_required).  Factory stale-owner
                # recovery already owns the workspace; force-fail that
                # orphan first, then fence any residual expired session.
                abort_summary = TaskRuntimeService(str(self.workspace)).terminalize_open_tasks_for_factory_abort(
                    factory_run_id=run_id,
                    reason=reason,
                    source="factory_stale_owner_force_expired_child",
                    force_active_sessions=True,
                )
                run.metadata["factory_task_runtime_abort"] = abort_summary
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
