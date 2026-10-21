"""_FactoryRunServiceLifecycleMixin methods for FactoryRunService composition.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import asdict  # re-exported for lossless surface
from typing import TYPE_CHECKING, Any  # Protocol re-exported for lossless surface

from polaris.cells.runtime.task_runtime.public.contracts import (
    FenceExpiredFactoryRunSessionsCommandV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    fence_expired_factory_run_sessions,
)

from ..factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptLiveControlPort,
)
from ..factory_physical_attempt_replay import (
    FactoryPhysicalAttemptReplayError,
    build_factory_physical_attempt_replay_candidate,
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
        require_quiescent_children: bool = False,
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

        failed_evidence_incomplete = bool(
            target_run.status in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}
            and self._settlement_barrier_query(self.workspace, target_run.id).missing_required_modalities
        )
        terminal_claim = lease.lifecycle_operation_claim
        resume_terminal_claim = bool(
            target_run.status in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}
            and terminal_claim is not None
            and terminal_claim.operation == "settle_terminal_run"
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
            if failed_evidence_incomplete or resume_terminal_claim:
                try:
                    claim = lease.lifecycle_operation_claim
                    if claim is None or existing_map.get("factory_run_id") != target_run.id:
                        raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_required")
                    hold = (
                        self._admission.hold_terminal_settlement_claim(
                            target_run.id,
                            fencing_token=lease.fencing_token,
                            sequence=claim.sequence,
                            nonce=claim.nonce,
                        )
                        if resume_terminal_claim
                        else self._admission.hold_active_lifecycle_operation_claim(
                            target_run.id,
                            fencing_token=lease.fencing_token,
                            operation=claim.operation,
                            sequence=claim.sequence,
                            nonce=claim.nonce,
                            allow_expired_owner=claim.operation == "recover_stale_workspace_owner",
                        )
                    )
                    with hold as revalidate:
                        held = revalidate()
                        fence = self._capture_physical_attempt_replay_fence(factory_run_id=target_run.id, lease=held)
                        role_evidence, lifecycle = self._capture_physical_attempt_replay_views(target_run.id)
                        candidate = build_factory_physical_attempt_replay_candidate(
                            fence=fence,
                            role_evidence=role_evidence,
                            lifecycle=lifecycle,
                        )
                        states = existing_map.get("by_authority")
                        if any(record.terminal is None for record in candidate.records):
                            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_required")
                        if (
                            not isinstance(states, list)
                            or not all(isinstance(state, Mapping) for state in states)
                            or existing_map.get("settled") is not True
                            or existing_map.get("blocking_reservation_ids")
                        ):
                            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_required")
                        replayed, recovery_work = FactoryPhysicalAttemptLiveControlPort.from_replay_candidate(candidate)
                        canonical_drain = replayed.drain_snapshot()
                        if recovery_work or not canonical_drain.settled:
                            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_required")
                        for state in states:
                            authority_records = [
                                record
                                for record in candidate.records
                                if record.start.execution_authority_hash == state.get("execution_authority_hash")
                            ]
                            if (
                                state.get("factory_run_id") != target_run.id
                                or any(
                                    state.get(key) is not True for key in ("registered", "revoked", "closed", "settled")
                                )
                                or any(
                                    state.get(key) != 0
                                    for key in (
                                        "reserved_count",
                                        "start_persisting_count",
                                        "ambiguous_count",
                                        "inflight_count",
                                    )
                                )
                                or state.get("committed_count") != len(authority_records)
                            ):
                                raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_required")
                        verified_role, verified_lifecycle = self._capture_physical_attempt_replay_views(target_run.id)
                        if (
                            verified_role != role_evidence
                            or verified_lifecycle != lifecycle
                            or self._capture_physical_attempt_replay_fence(
                                factory_run_id=target_run.id, lease=revalidate()
                            )
                            != fence
                        ):
                            raise FactoryPhysicalAttemptReplayError("factory_physical_attempt_replay_required")
                        # The persisted latest epoch may have zero calls while
                        # append-only history contains earlier epochs. Both are
                        # independently closed; their authority sets need not be equal.
                        existing_map = dict(existing_map)
                        existing_map["canonical_history_drain"] = {
                            "factory_run_id": canonical_drain.factory_run_id,
                            "settled": canonical_drain.settled,
                            "blocking_reservation_ids": list(canonical_drain.blocking_reservation_ids),
                            "terminal_failure_reservation_ids": list(canonical_drain.terminal_failure_reservation_ids),
                            "by_authority": [asdict(state) for state in canonical_drain.by_authority],
                        }
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    return await self._record_drain_conflict(
                        target_run,
                        lease,
                        code="factory_physical_attempt_replay_required",
                        message="Factory failed closeout requires stable canonical physical attempt closure",
                        details={"error_type": type(exc).__name__, "error_message": str(exc)[:300]},
                    )
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
                if "canonical_history_drain" in existing_map:
                    physical_drain_evidence["canonical_history_drain"] = existing_map["canonical_history_drain"]
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
        if require_quiescent_children and settlement.get("settled") is not True:
            return await self._record_drain_conflict(
                target_run,
                lease,
                code="factory_workspace_run_child_session_inflight",
                message="Orphan terminal settlement observed child authority reopen during closure",
                details={"settlement": settlement},
            )
        # R64: Director settlement-barrier timeouts leave owned active sessions that
        # pin child-session settlement forever. On FAILED/CANCELLED factory drain,
        # force-fail those owned active sessions (not foreign ones) so lease release
        # can complete. Live success paths never take this branch.
        if (
            not failed_evidence_incomplete
            and settlement.get("settled") is not True
            and target_run.status
            in {
                FactoryRunStatus.FAILED,
                FactoryRunStatus.CANCELLED,
            }
        ):
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
                    # Failed acceptance stays failed; it must not prevent
                    # aborting never-dispatched owners during failure closeout.
                    "run_ledger_gate_failed",
                    "required_evidence_missing",
                    "required_evidence_failed",
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
        released, release_committed = self._admission.release_with_transition(
            target_run.id,
            fencing_token=lease.fencing_token,
            settlement_evidence=release_evidence,
            operation_nonce=operation_nonce,
        )
        if not release_committed:
            # Another service won the durable release CAS for this same orphan
            # nonce. Its evidence owns the transition and the release event;
            # this caller must not append a second authoritative closeout.
            return await self.store.get_run(target_run.id) or target_run
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
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

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
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

    async def resume_recovered_run(self: Any, run_id: str) -> FactoryRun:
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

    async def retry_run_from_stage(
        self: Any,
        run_id: str,
        target_stage: str | None = None,
        reason: str | None = None,
    ) -> FactoryRun:
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

    async def execute_pause(self: Any, run_id: str) -> FactoryRun:
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

    async def execute_resume(self: Any, run_id: str) -> FactoryRun:
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

    async def update_run_metadata(self: Any, run_id: str, metadata: dict[str, Any]) -> FactoryRun:
        """Persist metadata updates for an existing factory run."""
        self._require_writable()
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")

            run = await self.assert_mutation_allowed(run_id, current_run=run)
            # Updating a terminal run's metadata does not start provider work.
            # Durable mutation checks still apply; a process-local transport
            # coordinator is needed when execution is open, not for this edit.
            if run.status not in TERMINAL_RUN_STATUSES:
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
        """Retired execution belongs only to its frozen historical process."""
        raise RuntimeError("legacy_engine_retired: use roles.runtime.public.engineering_service")

    async def cancel_run(self: Any, run_id: str, reason: str | None = None) -> FactoryRun:
        """Cancel a factory run and keep a distinct terminal status."""
        self._require_writable()
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
        self._require_writable()
        run_lock = self._get_run_lock(run_id)
        completion_event_appended = False
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
                        completion_event_appended = True
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
                            completion_event_appended = True
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

        # The terminal Factory event is part of ProjectOutcome authority.  Wake
        # convergence only after it is durable, while the exact workspace lease
        # still exists for physical-evidence closure.  Notification failure is
        # projected but never rolls back the already-closed Factory lifecycle.
        stage_results_raw = run.metadata.get("stage_results") if isinstance(run.metadata, Mapping) else None
        stage_results = stage_results_raw if isinstance(stage_results_raw, Mapping) else {}
        quality_result = stage_results.get("quality_gate")
        has_successful_quality_result = (
            isinstance(quality_result, Mapping) and str(quality_result.get("status") or "").strip() == "success"
        )
        if completion_event_appended and run.status == FactoryRunStatus.COMPLETED and has_successful_quality_result:
            try:
                await self._notify_project_completion_after_terminal(run_id)
            except Exception as exc:  # noqa: BLE001 -- owner boundary failure is projected fail-closed
                logger.error(
                    "Post-terminal project completion notification failed for run %s: %s",
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
                        "source_stage": "quality_gate",
                        "source_stage_status": "success",
                        "timestamp": self._now(),
                        "terminal": False,
                    },
                )

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

        self._require_writable()
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
        resumed_terminal_claim = False
        try:
            if (
                current is not None
                and current.run_id == run.id
                and current.state.value == "draining"
                and current_claim is not None
                and current_claim.operation == operation
                and run.status in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}
            ):
                with self._admission.hold_terminal_settlement_claim(
                    run.id,
                    fencing_token=(current.fencing_token if expected_fencing_token is None else expected_fencing_token),
                    sequence=current_claim.sequence,
                    nonce=current_claim.nonce,
                ) as revalidate:
                    lease = revalidate()
                settlement = self._query_child_session_settlement(run.id)
                if settlement.get("settled") is not True:
                    from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                    raise FactoryPipelineError(
                        "Orphan terminal settlement requires already settled child sessions",
                        code="factory_workspace_run_child_session_inflight",
                        details={"settlement": settlement},
                    )
                self._attach_workspace_lease(run, lease)
                resumed_terminal_claim = True
            else:
                lease = self._claim_lifecycle_operation(
                    run,
                    operation=operation,
                    nonce=nonce,
                    acquire_if_available=False,
                    expected_fencing_token=expected_fencing_token,
                )
            claimed = not resumed_terminal_claim
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
                require_quiescent_children=resumed_terminal_claim,
            )
            current = self._admission.current()
            if resumed_terminal_claim and (
                current is None or current.run_id != run.id or current.state.value != "released"
            ):
                from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                raise FactoryPipelineError(
                    "Orphan terminal settlement lacks complete release proof",
                    code="factory_workspace_run_drain_unproven",
                    details={"current_lease": current.to_dict() if current is not None else None},
                )
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

        self._require_writable()
        run_lock = self._get_run_lock(run_id)
        async with run_lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise ValueError(f"Run {run_id} not found")
            run = await self.assert_mutation_allowed(run_id, current_run=run)
            current = self._admission.current()
            if (
                current is not None
                and current.run_id == run_id
                and current.state.value == "draining"
                and current.lifecycle_operation_claim is not None
                and current.lifecycle_operation_claim.operation == "settle_terminal_run"
                and run.status in {FactoryRunStatus.FAILED, FactoryRunStatus.CANCELLED}
            ):
                self._admission.assert_stale_owner(run_id, fencing_token=expected_fencing_token)
                await self._settle_terminal_run_locked(run, expected_fencing_token=expected_fencing_token)
                released = self._admission.current()
                if released is None or released.run_id != run_id or released.state.value != "released":
                    from polaris.cells.factory.pipeline.public.contracts import FactoryPipelineError

                    raise FactoryPipelineError(
                        "Orphan terminal settlement lacks complete release proof",
                        code="factory_workspace_run_drain_unproven",
                        details={"current_lease": released.to_dict() if released is not None else None},
                    )
                return released
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
