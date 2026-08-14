"""_FactoryRunServicePhysicalMixin methods for FactoryRunService composition.

Private implementation module of the factory_run_service package.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict  # re-exported for lossless surface
from pathlib import Path
from typing import TYPE_CHECKING, Any  # Protocol re-exported for lossless surface

from polaris.cells.roles.kernel.public.physical_attempt_control import (
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    SettleFactoryPhysicalAttemptV1,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    APPEND_FACTORY_PROVIDER_ATTEMPT_RECOVERY_TERMINAL_SCHEMA,
    AppendFactoryProviderAttemptRecoveryTerminalV1,
    append_factory_provider_attempt_recovery_terminal,
)
from polaris.kernelone.storage import resolve_logical_path

from ..factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptLiveControlPort,
)
from ..factory_physical_attempt_replay import (
    FactoryPhysicalAttemptReplayError,
    FactoryPhysicalAttemptReplayFenceV1,
    FactoryPhysicalAttemptReplayPolicyV1,
    build_factory_physical_attempt_replay_candidate,
)
from ..factory_role_evidence_authority import (
    FactoryRoleEvidenceReplaySnapshotV1,
)
from ..factory_run_models import (
    DEFAULT_STAGE_HEARTBEAT_INTERVAL_SECONDS,
    FactoryRun,
    FactoryRunStatus,
    StageResult,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from polaris.cells.factory.pipeline.public.contracts import (
        FactoryWorkspaceRunLeaseV1,
    )

from ._helpers import (
    _WORKSPACE_LEASE_METADATA_KEY,
    _FactoryProviderAttemptRecoveryFence,
    logger,
)


class _FactoryRunServicePhysicalMixin:
    async def _close_project_completion_physical_evidence(
        self: Any,
        *,
        contract: Any,
        result: StageResult,
    ) -> tuple[dict[str, Any], ...]:
        """Materialize exact CE obligations after a successful physical QA gate.

        QA command receipts and on-disk files are process evidence, not the
        project-completion SSoT.  The VerificationGuard/ExecutionBroker path
        must record each artifact and verifier receipt before the completion
        supervisor evaluates the project.  Failed stages never use this bridge.
        """

        if result.stage != "quality_gate" or result.status != "success":
            return ()
        from polaris.cells.factory.verification_guard.public import (
            RunProjectCompletionEvidenceBatchCommandV1,
            run_project_completion_evidence_batch,
        )

        obligations = (
            *contract.obligations.artifacts,
            *contract.obligations.verification,
            *contract.obligations.entrypoints,
        )
        obligation_ids = tuple(
            obligation.obligation_id
            for obligation in obligations
            if str(getattr(obligation, "applicability", "") or "").strip() != "not_applicable"
        )
        if not obligation_ids:
            return ()
        batch = await asyncio.to_thread(
            run_project_completion_evidence_batch,
            RunProjectCompletionEvidenceBatchCommandV1(
                workspace=str(self.workspace),
                project_id=contract.project_id,
                run_id=contract.run_id,
                completion_contract_hash=contract.contract_hash,
                obligation_ids=obligation_ids,
            ),
        )
        effects = [
            {
                "obligation_id": effect.obligation_id,
                "code": effect.code,
                "spawned": effect.spawned,
                "receipt_ref": effect.receipt_ref,
            }
            for effect in batch.effects
        ]
        result.metadata["project_completion_physical_evidence_closure"] = {
            "schema_version": "factory.project-completion-physical-evidence-closure.v1",
            "effect_count": len(effects),
            "effects": effects,
        }
        return tuple(effects)

    def _recover_physical_attempt_coordinator(
        self: Any,
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

    async def _notify_project_completion_supervisor(
        self: Any,
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
            await self._close_project_completion_physical_evidence(
                contract=contract,
                result=result,
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
        self: Any,
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

    async def _emit_stage_heartbeat(self: Any, run_id: str, stage: str) -> None:
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

    def _build_abort_checker(self: Any, run_id: str) -> Callable[[], Awaitable[str | None]]:
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
        self: Any,
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
        self: Any,
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

    async def _prepare_failed_retry_execution_epoch_locked(self: Any, run: FactoryRun) -> FactoryRun:
        """Make one explicit FAILED retry safe without replaying PM or CE."""

        current = self._physical_attempt_coordinators.get(run.id)
        if current is not None and not current.admission_closed:
            return run
        lease_payload = run.metadata.get(_WORKSPACE_LEASE_METADATA_KEY)
        lease = lease_payload if isinstance(lease_payload, Mapping) else {}
        lease_released = str(lease.get("state") or "").strip().lower() == "released"
        # Closed in-process coordinator + already-released lease can reuse the
        # coordinator (quality-only retry). Closed coordinator + draining lease
        # must replay/release first — live L1-08 second retry_phase skipped
        # replay and raised retry_terminal_settlement_missing.
        if current is not None and current.admission_closed and lease_released:
            replayed = current
        else:
            replayed = await self._replay_failed_retry_physical_attempt_epoch_locked(run)
        self._require_failed_retry_terminal_release(run)
        return await self._open_fresh_physical_attempt_execution_epoch_locked(
            run,
            replayed=replayed,
            source="failed_run_stage_retry",
        )
