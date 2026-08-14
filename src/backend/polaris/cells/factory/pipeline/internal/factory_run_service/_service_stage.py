"""_FactoryRunServiceStageMixin methods for FactoryRunService composition.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any  # Protocol re-exported for lossless surface

from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import (
    contains_factory_role_evidence_runtime_authority,
)
from polaris.kernelone.storage import resolve_storage_roots

from ..factory_run_models import (
    TERMINAL_RUN_STATUSES,
    FactoryRun,
    FactoryRunStatus,
    StageResult,
    _register_factory_cancel_event,
    _unregister_factory_cancel_event,
)
from ..factory_stage_artifact_bindings import (
    CEBlueprintArtifactBindingV1,
    CEReviewManifestArtifactBindingV1,
    FactoryStageArtifactBindingError,
    FactoryStageArtifactBindingsV1,
    PMContractArtifactBindingV1,
    PMStageEventArtifactBindingV1,
    build_chief_engineer_stage_artifact_bindings,
    build_pm_stage_artifact_bindings,
)
from ..factory_stage_persistence import (
    FactoryLastStageCommitV1,
    FactoryStagePersistenceCommittedV1,
    FactoryStagePersistenceError,
    FactoryStagePersistenceIntentV1,
    bounded_redacted_error,
    build_stage_persistence_intent,
    canonical_checkpoint_sha256,
    canonical_run_snapshot_sha256,
    reduce_factory_stage_persistence,
    validate_current_stage_commit_pointer,
)

if TYPE_CHECKING:
    pass

from ._helpers import (
    _CHILD_SESSION_SETTLEMENT_EVIDENCE_METADATA_KEY,
    _CHILD_SESSIONS_SETTLED_METADATA_KEY,
    _FACTORY_ROLE_EVIDENCE_CUTOFF_PORT_CONTEXT_KEY,
    _STAGE_IN_FLIGHT_METADATA_KEY,
    _WORKSPACE_LEASE_METADATA_KEY,
    _FactoryStageCancellationCutError,
    _FactoryStageCommitArbitration,
    logger,
)


class _FactoryRunServiceStageMixin:
    async def _recover_stage_commit_if_proven(self: Any, run_id: str) -> bool:
        """Complete one exact, fully materialized stage commit missing its ACK.

        This is not a generic quarantine bypass.  It is available only when the
        quarantine names the current pending stage event and both the mutable
        run plus immutable checkpoint carry its exact last-stage pointer.  This
        covers cancellation and transient strict-reread/commit-marker failures
        after durable writes.  Missing or mismatched proof remains quarantined.
        """

        events = await self.store.get_authoritative_events(run_id)
        state = reduce_factory_stage_persistence(events, factory_run_id=run_id)
        pending_event_id = state.recoverable_stage_event_id
        if not pending_event_id:
            return False
        stage_event = next(
            (
                event
                for event in events
                if event.get("type") == "stage_completed" and event.get("event_id") == pending_event_id
            ),
            None,
        )
        if not isinstance(stage_event, Mapping):
            return False
        intent = FactoryStagePersistenceIntentV1.from_record(stage_event.get("persistence_intent"))
        checkpoint = await self.store.read_strict_checkpoint_snapshot(run_id, intent.checkpoint_ref)
        self._validate_checkpoint_ref_from_typed_run(run_id, intent.checkpoint_ref, checkpoint)
        expected_pointer = FactoryLastStageCommitV1(
            stage=intent.stage,
            stage_completed_event_id=str(stage_event["event_id"]),
            stage_completed_chain_sequence=int(stage_event["chain_sequence"]),
            stage_completed_chain_event_hash=str(stage_event["chain_event_hash"]),
            persistence_intent_sha256=intent.persistence_intent_sha256,
            checkpoint_ref=intent.checkpoint_ref,
        )
        checkpoint_metadata = checkpoint.get("metadata")
        checkpoint_pointer = (
            checkpoint_metadata.get("last_factory_stage_commit") if isinstance(checkpoint_metadata, Mapping) else None
        )
        if FactoryLastStageCommitV1.from_record(checkpoint_pointer, factory_run_id=run_id) != expected_pointer:
            raise FactoryStagePersistenceError(
                "factory_stage_recovery_pointer_mismatch",
                "Stage checkpoint does not carry the exact pending commit pointer",
            )

        checkpoint_run = FactoryRun.from_dict(checkpoint)
        if checkpoint_run.id != run_id:
            raise FactoryStagePersistenceError(
                "factory_stage_recovery_run_mismatch",
                "Stage checkpoint belongs to another run",
            )

        # The checkpoint is immutable proof of the interrupted stage
        # transaction; it is not authority to roll mutable runtime state back.
        # A later retry may already have reopened a physical epoch or advanced
        # the workspace fencing token before startup closes the missing ACK.
        # Replacing the current run with this older checkpoint would split
        # authority (durable admission lease at the new token, run snapshot at
        # the old token) and permanently fence same-stage recovery.
        current_run_snapshot = await self.store.read_strict_run_snapshot(run_id)
        current_run = FactoryRun.from_dict(current_run_snapshot)
        if current_run.id != run_id:
            raise FactoryStagePersistenceError(
                "factory_stage_recovery_current_run_mismatch",
                "Current mutable run snapshot belongs to another run",
            )
        current_metadata = current_run_snapshot.get("metadata")
        current_pointer = (
            current_metadata.get("last_factory_stage_commit") if isinstance(current_metadata, Mapping) else None
        )
        if FactoryLastStageCommitV1.from_record(current_pointer, factory_run_id=run_id) != expected_pointer:
            raise FactoryStagePersistenceError(
                "factory_stage_recovery_current_pointer_mismatch",
                "Current mutable run no longer names the exact pending stage commit",
            )
        marker = await self._append_event(
            run_id,
            {
                "type": "factory_stage_persistence_committed",
                "schema_version": "factory.stage_persistence_committed.v1",
                "factory_run_id": run_id,
                "stage": intent.stage,
                "stage_completed_event_id": str(stage_event["event_id"]),
                "stage_completed_chain_sequence": int(stage_event["chain_sequence"]),
                "stage_completed_chain_event_hash": str(stage_event["chain_event_hash"]),
                "persistence_intent_sha256": intent.persistence_intent_sha256,
                "run_snapshot_canonical_sha256": canonical_run_snapshot_sha256(checkpoint),
                "checkpoint_ref": intent.checkpoint_ref,
                "checkpoint_canonical_sha256": canonical_checkpoint_sha256(checkpoint),
                "timestamp": self._now(),
            },
            publish=False,
        )
        commit = FactoryStagePersistenceCommittedV1.from_record(marker)
        validate_current_stage_commit_pointer(checkpoint_pointer, commit)
        recovered_state = reduce_factory_stage_persistence(
            await self.store.get_authoritative_events(run_id),
            factory_run_id=run_id,
        )
        if recovered_state.is_quarantined:
            raise FactoryStagePersistenceError(
                "factory_stage_recovery_not_converged",
                "Exact cancelled stage commit did not clear persistence quarantine",
            )
        logger.info(
            "Recovered exact stage ACK without runtime rollback: run=%s stage=%s event=%s",
            run_id,
            intent.stage,
            pending_event_id,
        )
        return True

    async def _recover_cancelled_stage_commit_if_proven(self: Any, run_id: str) -> bool:
        """Compatibility alias for callers/tests predating generic exact recovery."""

        return await self._recover_stage_commit_if_proven(run_id)

    async def execute_stage(
        self: Any,
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

    def _apply_stage_result_to_run(
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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
        self: Any,
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

    async def _mark_stage_started(self: Any, run: FactoryRun, stage: str, started_at: str) -> None:
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
        self: Any,
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
