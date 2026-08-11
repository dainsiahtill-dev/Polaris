"""Director execution consumer for TaskMarket PENDING_EXEC claims."""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    QueryTaskMarketStatusV1,
    TaskMarketError,
)
from polaris.kernelone.fs.materialization import materialized_file_paths
from polaris.kernelone.quality import resolve_owner_handoff_routing, task_record_routing_key

from ._helpers import (
    _OWNER_HANDOFF_TASK_RECORD_LIMIT,
    DirectorTaskExecutor,
    InterfaceContractAmendmentRequiredError,
    InterfaceContractRepairRequiredError,
    UnrecoverableExecutionError,
    _adapter_failure_message,
    _allows_no_execution_evidence,
    _attach_handoff_validation_payload,
    _build_director_adapter_input,
    _changed_files_cover_target,
    _compact_director_adapter_summary,
    _contract_authority_blocker,
    _director_evidence_status,
    _director_execution_timeout_seconds,
    _extract_director_changed_files,
    _extract_director_side_effects,
    _fill_assembly_baseline,
    _fill_assembly_drift_error,
    _final_convergence_failure,
    _has_verified_existing_scope_evidence,
    _interface_contract_amendment_from_adapter_failure,
    _interface_contract_repair_from_adapter_failure,
    _job_token_from_payload,
    _mapping_copy,
    _normalize_handoff_validation_result,
    _normalize_string_list,
    _normalize_task_market_route,
    _owner_handoff_failure_from_adapter_failure,
    _owner_handoff_failure_projection,
    _OwnerHandoffFailure,
    _OwnerHandoffRoutingRequiredError,
    _pre_state_punch_list,
    _read_consumed_interfaces,
    _record_task_owned_artifact_receipts,
    _repair_prior_target_size,
    _repair_shrink_error,
    _resolve_qa_local_repair_authority,
    _revalidate_qa_exact_verifier,
    _run_coroutine_sync,
    _step_target_file,
    _task_projection_artifact_state,
    _verified_existing_scope_covers_target,
)
from ._scope_lease import ScopeConflictDetector, _LeaseHeartbeat


def _package() -> Any:
    """Return the package module for monkeypatch-visible late binding."""
    return sys.modules[__package__]


class DirectorExecutionConsumer:
    """Canonical TaskMarket consumer for ``pending_exec`` Director work.

    This class owns the synchronous claim -> execute -> boundary-verdict path
    between ``runtime.task_market`` and the Director execution adapter. CE-side
    Director pools may assign or observe work, but they do not replace this
    stage consumer.
    """

    def __init__(
        self,
        workspace: str,
        worker_id: str = "director_worker",
        visibility_timeout_seconds: int = 1800,
        poll_interval: float = 5.0,
        enable_safe_parallel: bool = False,
        lease_renew_interval_seconds: float | None = None,
        task_executor: DirectorTaskExecutor | None = None,
        wake_event: threading.Event | None = None,
    ) -> None:
        self._workspace = workspace
        self._worker_id = worker_id
        self._visibility_timeout = visibility_timeout_seconds
        self._enable_safe_parallel = enable_safe_parallel
        self._lease_renew_interval_seconds = (
            float(lease_renew_interval_seconds)
            if lease_renew_interval_seconds is not None
            else max(1.0, min(60.0, float(self._visibility_timeout) / 3.0))
        )
        self._stop_event = threading.Event()
        self._work_event = wake_event or threading.Event()
        self._svc = _package().get_task_market_service()
        self._conflict_detector = ScopeConflictDetector()
        self._task_executor = task_executor
        self._active_claim_lock = threading.Lock()
        self._active_claim_task_id = ""
        self._active_claim_started_monotonic: float | None = None
        self._execution_timeout_seconds = _director_execution_timeout_seconds(self._visibility_timeout)
        self._active_claim_timeout_seconds = self._execution_timeout_seconds

    def active_claim_watchdog_snapshot(self) -> dict[str, Any]:
        """Return the currently executing claim, if any, for outer pool watchdogs."""
        with self._active_claim_lock:
            return {
                "task_id": self._active_claim_task_id,
                "started_monotonic": self._active_claim_started_monotonic,
                "timeout_seconds": self._active_claim_timeout_seconds,
            }

    def _mark_active_claim(self, task_id: str) -> None:
        with self._active_claim_lock:
            self._active_claim_task_id = str(task_id or "").strip()
            self._active_claim_started_monotonic = time.monotonic()

    def _clear_active_claim(self, task_id: str) -> None:
        with self._active_claim_lock:
            if self._active_claim_task_id == str(task_id or "").strip():
                self._active_claim_task_id = ""
                self._active_claim_started_monotonic = None

    def poll_once(self) -> list[dict[str, Any]]:
        """Poll once for PENDING_EXEC tasks."""
        results: list[dict[str, Any]] = []
        while not self._stop_event.is_set():
            claim = self._svc.claim_work_item(
                ClaimTaskWorkItemCommandV1(
                    workspace=self._workspace,
                    stage="pending_exec",
                    worker_id=self._worker_id,
                    worker_role="director",
                    visibility_timeout_seconds=self._visibility_timeout,
                )
            )
            if not claim.ok:
                break

            self._mark_active_claim(str(claim.task_id or ""))
            try:
                processed = self._process_claim(claim)
            finally:
                self._clear_active_claim(str(claim.task_id or ""))
            results.append(processed)
        return results

    def _process_claim(self, claim: Any) -> dict[str, Any]:
        """Process a single claimed execution task."""
        task_id = claim.task_id
        lease_token = claim.lease_token
        payload = dict(claim.payload) if claim.payload else {}
        route = _normalize_task_market_route(payload)

        # All Director execution must carry ChiefEngineer evidence. Legacy
        # direct PM task routes are parsed for compatibility, but never grant
        # execution authority without a blueprint handoff.
        handoff_allowed, blueprint_id, handoff_error, handoff_validation = _normalize_handoff_validation_result(
            _package()._validated_blueprint_handoff(self._workspace, task_id, payload)
        )
        if not handoff_allowed:
            handoff_error_code = "INVALID_BLUEPRINT_HANDOFF" if blueprint_id else "MISSING_BLUEPRINT"
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code=handoff_error_code,
                    error_message=handoff_error,
                    failure_disposition="isolated_contract_blocker",
                    metadata={
                        "reason": "invalid_blueprint_handoff" if blueprint_id else "missing_blueprint",
                        "structured_blocker": _contract_authority_blocker(
                            task_id=task_id,
                            error_code=handoff_error_code,
                            evidence={"handoff_validation": handoff_validation, "reason": handoff_error},
                            payload=payload,
                        ),
                    },
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": "invalid_blueprint_handoff" if blueprint_id else "missing_blueprint",
            }
        _attach_handoff_validation_payload(payload, handoff_validation)

        try:
            qa_repair_authority = _resolve_qa_local_repair_authority(
                workspace=self._workspace,
                task_id=task_id,
                payload=payload,
            )
            qa_repair_before_state = (
                _task_projection_artifact_state(workspace=self._workspace, payload=payload)
                if qa_repair_authority is not None and qa_repair_authority.kind == "diagnostic_effect"
                else {}
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="QA_LOCAL_REPAIR_AUTHORITY_REJECTED",
                    error_message=str(exc),
                    failure_disposition="isolated_contract_blocker",
                    metadata={
                        "reason": "qa_local_repair_authority_rejected",
                        "qa_local_repair_context": _mapping_copy(payload.get("qa_local_repair_context")),
                        "automatic_upstream_replan": False,
                        "automatic_escalation": False,
                    },
                )
            )
            return {"task_id": task_id, "ok": False, "reason": "qa_local_repair_authority_rejected"}

        # Safe parallel conflict check
        if self._enable_safe_parallel:
            scope_paths = sorted(self._conflict_detector.extract_conflict_paths(payload))
            if self._conflict_detector.check_conflict(self._workspace, task_id, scope_paths):
                # Requeue instead of dead-letter — it's a transient conflict
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="SCOPE_CONFLICT",
                        error_message="Scope conflict with other in-progress task",
                        requeue_stage="pending_exec",
                        failure_disposition="same_task_local_retry",
                    )
                )
                return {"task_id": task_id, "ok": False, "reason": "scope_conflict"}

        heartbeat: _LeaseHeartbeat | None = None
        try:
            heartbeat = _LeaseHeartbeat(
                svc=self._svc,
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                visibility_timeout_seconds=self._visibility_timeout,
                interval_seconds=self._lease_renew_interval_seconds,
            )
            heartbeat.start()
            # R7-C (I3-r28): snapshot the repair target's prior size BEFORE exec, so a
            # degenerate "rewrite smaller" repair is caught deterministically below.
            repair_prior_size = _repair_prior_target_size(self._workspace, payload)
            # P3 (deterministic file-assembly): snapshot the skeleton/prior-fill baseline
            # BEFORE an anchored fill exec, so the merger gate below can reject a fill that
            # drifts the interface or touches an unassigned function.
            fill_assembly_baseline = _fill_assembly_baseline(self._workspace, payload)
            # Execute (placeholder — actual execution delegated to DirectorAgent)
            exec_result = self._execute_task(task_id, payload, lease_token)
            changed_files = _normalize_string_list(exec_result.get("changed_files"))
            has_verified_existing_scope = _has_verified_existing_scope_evidence(exec_result)
            if not changed_files and not has_verified_existing_scope and not _allows_no_execution_evidence(payload):
                return self._missing_execution_evidence_result(
                    task_id=task_id,
                    lease_token=lease_token,
                    blueprint_id=blueprint_id,
                    payload=payload,
                )
            # Step contract: a fission step declares exactly one target_file.
            # "Any change" is not evidence the STEP was done — a weak model
            # can write a different file entirely and sail through (live
            # I3-r9: the readme.md step wrote index.html, acked clean, and
            # QA passed it). Requeue with a teaching error so the retry
            # ladder can correct course.
            step_target = _step_target_file(payload)
            if step_target and not (
                _changed_files_cover_target(step_target, changed_files)
                or _verified_existing_scope_covers_target(exec_result, step_target)
            ):
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="EXEC_TARGET_MISSING",
                        error_message=(
                            f"step requires changes to '{step_target}' but changed_files={changed_files}. "
                            f"Write ONLY the declared target_file for this step."
                        ),
                        requeue_stage="pending_exec",
                        failure_disposition="same_task_local_retry",
                    )
                )
                return {"task_id": task_id, "ok": False, "reason": "step_target_missing"}
            # R7-C: a repair turn that shrank the target below the preservation floor
            # deleted working content — requeue with a teaching error (which becomes the
            # next attempt's last_failure) instead of acking the degraded file to QA.
            if repair_prior_size is not None and step_target:
                shrink_error = _repair_shrink_error(self._workspace, step_target, repair_prior_size)
                if shrink_error is not None:
                    self._svc.fail_task_stage(
                        FailTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=task_id,
                            lease_token=lease_token,
                            error_code="REPAIR_SHRANK_FILE",
                            error_message=shrink_error,
                            requeue_stage="pending_exec",
                            failure_disposition="same_task_local_retry",
                        )
                    )
                    return {"task_id": task_id, "ok": False, "reason": "repair_shrank_file"}
            # P3 deterministic merger gate (codex 2026-06-15): an anchored fill must keep
            # the skeleton's interface (imports/exports/signatures), preserve every
            # @anchor, and touch ONLY its owned function bodies. On drift, REQUEUE with a
            # teaching error (becomes the next attempt's last_failure) — never dead-letter,
            # so the weak model is corrected instead of the cluster silently failing.
            assembly_drift = _fill_assembly_drift_error(self._workspace, payload, fill_assembly_baseline)
            if assembly_drift is not None:
                drift_code, drift_message = assembly_drift
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code=drift_code,
                        error_message=drift_message,
                        requeue_stage="pending_exec",
                        failure_disposition="same_task_local_retry",
                    )
                )
                return {"task_id": task_id, "ok": False, "reason": drift_code}
            if qa_repair_authority is not None and qa_repair_authority.kind == "diagnostic_effect":
                qa_repair_after_state = _task_projection_artifact_state(workspace=self._workspace, payload=payload)
                if not qa_repair_before_state or qa_repair_after_state == qa_repair_before_state:
                    self._svc.fail_task_stage(
                        FailTaskStageCommandV1(
                            workspace=self._workspace,
                            task_id=task_id,
                            lease_token=lease_token,
                            error_code="QA_LOCAL_REPAIR_MATERIAL_EFFECT_MISSING",
                            error_message="Director local repair produced no task-owned artifact byte change",
                            requeue_stage="pending_exec",
                            failure_disposition="same_task_local_retry",
                            metadata={
                                "reason": "qa_local_repair_material_effect_missing",
                                "task_completion_projection_hash": qa_repair_authority.projection_hash,
                            },
                        )
                    )
                    return {"task_id": task_id, "ok": False, "reason": "qa_local_repair_material_effect_missing"}
            final_convergence = _final_convergence_failure(
                workspace_path=Path(self._workspace).expanduser().resolve(),
                task_id=task_id,
                payload=payload,
                changed_files=changed_files,
                exec_result=exec_result,
            )
            if final_convergence is not None:
                error_code, error_message, requeue_stage, evidence = final_convergence
                self._append_final_convergence_event(
                    task_id=task_id,
                    payload=payload,
                    ok=False,
                    error_code=error_code,
                    summary=error_message,
                    evidence=evidence,
                )
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code=error_code,
                        error_message=error_message,
                        requeue_stage=requeue_stage,
                        failure_disposition=(
                            "same_task_local_retry" if requeue_stage == "pending_exec" else "isolated_contract_blocker"
                        ),
                        metadata={
                            "reason": "director_final_convergence_failed",
                            "final_convergence": evidence,
                            **(
                                {"structured_blocker": evidence["structured_blocker"]}
                                if isinstance(evidence.get("structured_blocker"), dict)
                                else {}
                            ),
                        },
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "reason": "director_final_convergence_failed",
                    "requeue_stage": requeue_stage,
                }
            try:
                artifact_receipts = _record_task_owned_artifact_receipts(
                    workspace=self._workspace,
                    task_id=task_id,
                    payload=payload,
                )
            except OSError as exc:
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="PROJECT_ARTIFACT_RECEIPT_WRITE_FAILED",
                        error_message=str(exc),
                        requeue_stage="pending_exec",
                        failure_disposition="same_task_local_retry",
                        metadata={
                            "reason": "project_artifact_receipt_write_failed",
                            "automatic_upstream_replan": False,
                            "automatic_escalation": False,
                        },
                    )
                )
                return {"task_id": task_id, "ok": False, "reason": "project_artifact_receipt_write_failed"}
            except (RuntimeError, TypeError, ValueError) as exc:
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="PROJECT_ARTIFACT_RECEIPT_AUTHORITY_FAILED",
                        error_message=str(exc),
                        failure_disposition="isolated_contract_blocker",
                        metadata={
                            "reason": "project_artifact_receipt_authority_failed",
                            "automatic_upstream_replan": False,
                            "automatic_escalation": False,
                        },
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "reason": "project_artifact_receipt_authority_failed",
                }
            try:
                qa_exact_revalidation = _revalidate_qa_exact_verifier(
                    workspace=self._workspace,
                    task_id=task_id,
                    payload=payload,
                    authority=qa_repair_authority,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="QA_EXACT_VERIFIER_REVALIDATION_AUTHORITY_FAILED",
                        error_message=str(exc),
                        failure_disposition="isolated_contract_blocker",
                        metadata={
                            "reason": "qa_exact_verifier_revalidation_authority_failed",
                            "qa_local_repair_context": _mapping_copy(payload.get("qa_local_repair_context")),
                        },
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "reason": "qa_exact_verifier_revalidation_authority_failed",
                }
            if qa_exact_revalidation is not None and not qa_exact_revalidation["succeeded"]:
                self._svc.fail_task_stage(
                    FailTaskStageCommandV1(
                        workspace=self._workspace,
                        task_id=task_id,
                        lease_token=lease_token,
                        error_code="QA_EXACT_VERIFIER_REVALIDATION_FAILED",
                        error_message="same-task repair did not satisfy the exact failed verifier",
                        requeue_stage="pending_exec",
                        failure_disposition="same_task_local_retry",
                        metadata={
                            "reason": "qa_exact_verifier_revalidation_failed",
                            "qa_exact_verifier_revalidation": qa_exact_revalidation,
                        },
                    )
                )
                return {
                    "task_id": task_id,
                    "ok": False,
                    "reason": "qa_exact_verifier_revalidation_failed",
                }
            registered_actions = self._register_compensation_actions(
                task_id=task_id,
                lease_token=lease_token,
                exec_result=exec_result,
            )
            adapter_summary_raw = exec_result.get("director_adapter_result")
            adapter_summary = adapter_summary_raw if isinstance(adapter_summary_raw, dict) else {}
            job_token = _job_token_from_payload(payload)

            # Acknowledge → PENDING_QA
            ack = self._svc.acknowledge_task_stage(
                AcknowledgeTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    next_stage="pending_qa",
                    summary=f"Execution complete for {task_id}",
                    metadata={
                        "blueprint_id": blueprint_id,
                        "blueprint_hash": str(payload.get("blueprint_hash") or job_token.get("blueprint_hash") or ""),
                        "contract_hash": str(payload.get("contract_hash") or job_token.get("contract_hash") or ""),
                        "job_token_id": str(payload.get("job_token_id") or job_token.get("token_id") or ""),
                        "job_token": job_token,
                        "control_plane_job_token": job_token,
                        "capability_token": job_token,
                        "control_plane_lineage": _mapping_copy(payload.get("control_plane_lineage")),
                        "route": route,
                        "task_market_route": route,
                        "blueprint_required": True,
                        "director_execution_authority": "chief_engineer_blueprint",
                        "changed_files": changed_files,
                        "director_evidence_status": _director_evidence_status(changed_files, exec_result),
                        "director_files_changed_count": len(changed_files),
                        "project_artifact_receipts": list(artifact_receipts),
                        "exec_duration_seconds": exec_result.get("duration", 0),
                        "director_adapter": adapter_summary,
                        **(
                            {"qa_exact_verifier_revalidation": qa_exact_revalidation}
                            if qa_exact_revalidation is not None
                            else {}
                        ),
                    },
                )
            )
            if ack.ok and registered_actions > 0:
                self._svc.commit_compensation_actions(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                )
            return {
                "task_id": task_id,
                "ok": ack.ok,
                "status": ack.status,
                "saga_actions": registered_actions,
            }

        except UnrecoverableExecutionError as exc:
            _package().logger.exception("Unrecoverable execution failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="EXEC_UNRECOVERABLE",
                    error_message=str(exc),
                    requeue_stage="pending_exec",
                    failure_disposition="same_task_local_retry",
                    metadata={"reason": "director_unrecoverable_requires_local_repair"},
                )
            )
            return {"task_id": task_id, "ok": False, "reason": str(exc)}

        except TimeoutError as exc:
            _package().logger.warning("Execution timed out for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="EXEC_TIMEOUT",
                    error_message=str(exc),
                    requeue_stage="pending_exec",
                    failure_disposition="same_task_local_retry",
                )
            )
            return {"task_id": task_id, "ok": False, "reason": "exec_timeout"}

        except InterfaceContractAmendmentRequiredError as exc:
            _package().logger.warning("Interface contract amendment required for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="INTERFACE_CONTRACT_AMENDMENT_REQUIRED",
                    error_message=str(exc),
                    failure_disposition="isolated_contract_blocker",
                    metadata={
                        "reason": "interface_contract_amendment_required",
                        "amendment_request": exc.amendment_request,
                        "structured_blocker": _contract_authority_blocker(
                            task_id=task_id,
                            error_code="INTERFACE_CONTRACT_AMENDMENT_REQUIRED",
                            evidence=exc.amendment_request,
                            payload=payload,
                        ),
                    },
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": "interface_contract_amendment_required",
            }

        except InterfaceContractRepairRequiredError as exc:
            _package().logger.warning("Interface contract repair required for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="INTERFACE_CONTRACT_REPAIR_REQUIRED",
                    error_message=str(exc),
                    requeue_stage="pending_exec",
                    failure_disposition="same_task_local_retry",
                    metadata={
                        "reason": "interface_contract_repair_required",
                        "repair_evidence": exc.repair_evidence,
                    },
                )
            )
            return {
                "task_id": task_id,
                "ok": False,
                "reason": "interface_contract_repair_required",
            }

        except _OwnerHandoffRoutingRequiredError as exc:
            return self._handle_owner_handoff_routing(
                task_id=task_id,
                lease_token=lease_token,
                failure=exc.failure,
                adapter_failure_message=str(exc),
                source_payload=payload,
            )

        except Exception as exc:  # noqa: BLE001
            _package().logger.exception("Execution failed for task %s: %s", task_id, exc)
            self._svc.fail_task_stage(
                FailTaskStageCommandV1(
                    workspace=self._workspace,
                    task_id=task_id,
                    lease_token=lease_token,
                    error_code="EXEC_FAILED",
                    error_message=str(exc),
                    requeue_stage="pending_exec",
                    failure_disposition="same_task_local_retry",
                )
            )
            return {"task_id": task_id, "ok": False, "reason": str(exc)}
        finally:
            if heartbeat is not None:
                heartbeat.stop()

    def _handle_owner_handoff_routing(
        self,
        *,
        task_id: str,
        lease_token: str,
        failure: _OwnerHandoffFailure,
        adapter_failure_message: str,
        source_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Route one structured owner handoff while the requester lease is held.

        The KernelOne resolver only reads scope and Task Market projections. The
        Task Market public route command remains the sole state-transition
        authority for the requester lease, dependency, and owner reopening.
        """

        source_identity = {
            key: source_payload.get(key)
            for key in (
                "blueprint_id",
                "completion_contract_hash",
                "contract_hash",
                "run_id",
                "factory_run_id",
                "trace_id",
                "job_token",
            )
            if source_payload.get(key) is not None
        }
        routing_summary: dict[str, Any] = {"source_payload_identity": source_identity}
        handoff_request: Mapping[str, Any] | None = None
        try:
            status = self._svc.query_status(
                QueryTaskMarketStatusV1(
                    workspace=self._workspace,
                    include_payload=False,
                    limit=_OWNER_HANDOFF_TASK_RECORD_LIMIT,
                )
            )
            raw_task_records = status.items
            if not isinstance(raw_task_records, Sequence) or isinstance(raw_task_records, (str, bytes, bytearray)):
                raise TypeError("Task Market owner-handoff status rows must be a sequence")
            task_records = tuple(dict(row) for row in raw_task_records if isinstance(row, Mapping))
            routing = resolve_owner_handoff_routing(failure.scope_payload, task_records)
            routing_summary = {
                **dict(routing.summary),
                "source_payload_identity": source_identity,
            }
            routing_summary["task_record_count"] = len(task_records)
        except (OSError, TaskMarketError, TypeError, ValueError) as exc:
            _package().logger.exception(
                "Owner-handoff projection failed: task_id=%s failure_class=%s error=%s",
                task_id,
                failure.failure_class,
                exc,
            )
            return self._fail_owner_handoff(
                task_id=task_id,
                lease_token=lease_token,
                failure=failure,
                adapter_failure_message=adapter_failure_message,
                error_code="OWNER_HANDOFF_PROJECTION_FAILED",
                error_message="Owner-handoff projection could not be resolved",
                reason="owner_handoff_projection_failed",
                requeue_stage="pending_exec",
                routing_summary=routing_summary,
                routing_error=exc,
            )

        unresolved_requests = (
            routing.index.unmatched_owner_handoff_requests or routing.index.unknown_owner_handoff_requests
        )
        if routing.has_unresolved_handoffs or unresolved_requests:
            handoff_request = unresolved_requests[0] if unresolved_requests else None
            return self._fail_owner_handoff(
                task_id=task_id,
                lease_token=lease_token,
                failure=failure,
                adapter_failure_message=adapter_failure_message,
                error_code="OWNER_HANDOFF_UNRESOLVED",
                error_message="Owner-handoff contract scope authority is unresolved",
                reason="owner_handoff_unresolved",
                requeue_stage=None,
                routing_summary=routing_summary,
                handoff_request=handoff_request,
            )

        if len(routing.owner_routing_keys) != 1:
            error_code = "OWNER_HANDOFF_AMBIGUOUS" if routing.owner_routing_keys else "OWNER_HANDOFF_UNRESOLVED"
            if routing.owner_routing_keys:
                handoff_request = routing.index.matched_owner_handoff_by_task_key.get(routing.owner_routing_keys[0])
            return self._fail_owner_handoff(
                task_id=task_id,
                lease_token=lease_token,
                failure=failure,
                adapter_failure_message=adapter_failure_message,
                error_code=error_code,
                error_message="Owner-handoff projection did not resolve exactly one owner task",
                reason="owner_handoff_ambiguous" if routing.owner_routing_keys else "owner_handoff_unresolved",
                requeue_stage=None,
                routing_summary=routing_summary,
                handoff_request=handoff_request,
            )

        owner_task_key = routing.owner_routing_keys[0]
        owner_record = next(
            (record for record in task_records if task_record_routing_key(record) == owner_task_key),
            None,
        )
        handoff_request = routing.index.matched_owner_handoff_by_task_key.get(owner_task_key)
        owner_task_id = str(owner_record.get("task_id") or "").strip() if owner_record is not None else ""
        if not owner_task_id or not isinstance(handoff_request, Mapping):
            return self._fail_owner_handoff(
                task_id=task_id,
                lease_token=lease_token,
                failure=failure,
                adapter_failure_message=adapter_failure_message,
                error_code="OWNER_HANDOFF_OWNER_RECORD_INVALID",
                error_message="Owner-handoff projection matched an invalid Task Market owner record",
                reason="owner_handoff_owner_record_invalid",
                requeue_stage=None,
                routing_summary=routing_summary,
                handoff_request=handoff_request,
            )

        routing_summary = {
            **routing_summary,
            "selected_owner_task_key": owner_task_key,
            "selected_owner_task_id": owner_task_id,
        }
        # An exact cross-task owner match is still outside the claimed task's
        # immutable completion contract.  Director must not reopen the owner,
        # add dependencies, or hand work back upstream.  Stop this task with a
        # structured authority blocker; an explicit operator-authored contract
        # revision is the only legal way to change ownership.
        return self._fail_owner_handoff(
            task_id=task_id,
            lease_token=lease_token,
            failure=failure,
            adapter_failure_message=adapter_failure_message,
            error_code="OWNER_HANDOFF_CROSS_TASK_REPAIR_FORBIDDEN",
            error_message="Current task cannot mutate an artifact owned by another task contract",
            reason="owner_handoff_cross_task_repair_forbidden",
            requeue_stage=None,
            routing_summary=routing_summary,
            handoff_request=handoff_request,
        )

    def _fail_owner_handoff(
        self,
        *,
        task_id: str,
        lease_token: str,
        failure: _OwnerHandoffFailure,
        adapter_failure_message: str,
        error_code: str,
        error_message: str,
        reason: str,
        requeue_stage: str | None,
        routing_summary: Mapping[str, Any],
        handoff_request: Mapping[str, Any] | None = None,
        routing_error: BaseException | None = None,
    ) -> dict[str, Any]:
        """Settle one lease-held owner-handoff failure with typed evidence."""

        metadata = _owner_handoff_failure_projection(
            failure,
            adapter_failure_message=adapter_failure_message,
            handoff_request=handoff_request,
            routing_summary=routing_summary,
            routing_error=routing_error,
        )
        if requeue_stage is None:
            owner_handoff_evidence = metadata.get("owner_handoff_evidence")
            routing_evidence = (
                owner_handoff_evidence.get("owner_handoff_routing")
                if isinstance(owner_handoff_evidence, Mapping)
                else None
            )
            source_identity = (
                routing_evidence.get("source_payload_identity") if isinstance(routing_evidence, Mapping) else None
            )
            metadata["structured_blocker"] = _contract_authority_blocker(
                task_id=task_id,
                error_code=error_code,
                evidence=metadata,
                payload=source_identity if isinstance(source_identity, Mapping) else None,
            )
        self._svc.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                error_code=error_code,
                error_message=error_message,
                requeue_stage=requeue_stage,
                failure_disposition=(
                    "same_task_local_retry" if requeue_stage is not None else "isolated_contract_blocker"
                ),
                metadata=metadata,
            )
        )
        result: dict[str, Any] = {
            "task_id": task_id,
            "ok": False,
            "reason": reason,
            "error_code": error_code,
            "failure_class": failure.failure_class,
            "responsible_layer": failure.responsible_layer,
        }
        return result

    def _append_final_convergence_event(
        self,
        *,
        task_id: str,
        payload: dict[str, Any],
        ok: bool,
        error_code: str,
        summary: str,
        evidence: dict[str, Any],
    ) -> None:
        job_token = _job_token_from_payload(payload)
        run_id = str(job_token.get("run_id") or payload.get("run_id") or "").strip()
        if not run_id:
            return
        try:
            from polaris.cells.control_plane.run_ledger.public import (
                AppendRunLedgerEventCommandV1,
                append_run_ledger_event,
            )

            append_run_ledger_event(
                AppendRunLedgerEventCommandV1(
                    workspace=self._workspace,
                    run_id=run_id,
                    event={
                        "event_type": "gate_evaluated",
                        "stage": "director_final_convergence",
                        "gate": {
                            "name": "director_final_convergence",
                            "ok": bool(ok),
                            "summary": summary,
                        },
                        "job_token": job_token,
                        "physical_evidence": {
                            "modalities": {
                                "code": {
                                    "present": True,
                                    "ok": bool(ok),
                                    "detail": summary,
                                }
                            },
                            "error_code": error_code,
                            "final_convergence": evidence,
                        },
                    },
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            _package().logger.warning("Could not append Director final convergence event for %s: %s", task_id, exc)

    def _register_compensation_actions(
        self,
        *,
        task_id: str,
        lease_token: str,
        exec_result: dict[str, Any],
    ) -> int:
        actions = self._normalize_compensation_actions(exec_result)
        for action in actions:
            self._svc.register_compensation_action(
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                action=action,
            )
        return len(actions)

    def _normalize_compensation_actions(self, exec_result: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        raw_effects = exec_result.get("side_effects")
        if not isinstance(raw_effects, list):
            return ()

        actions: list[dict[str, Any]] = []
        for row in raw_effects:
            if not isinstance(row, dict):
                continue
            action_type = str(row.get("action_type") or row.get("type") or "").strip()
            target = str(row.get("target") or "").strip()
            if not action_type or not target:
                continue
            reverse_payload_raw = row.get("reverse_payload")
            if not isinstance(reverse_payload_raw, dict):
                reverse_payload_raw = row.get("reverse_data")
            reverse_payload = dict(reverse_payload_raw) if isinstance(reverse_payload_raw, dict) else {}
            actions.append(
                {
                    "action_type": action_type,
                    "target": target,
                    "reverse_payload": reverse_payload,
                }
            )
        return tuple(actions)

    def _missing_execution_evidence_result(
        self,
        *,
        task_id: str,
        lease_token: str,
        blueprint_id: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._svc.fail_task_stage(
            FailTaskStageCommandV1(
                workspace=self._workspace,
                task_id=task_id,
                lease_token=lease_token,
                error_code="EXEC_NO_EVIDENCE",
                error_message="Director execution produced no changed_files evidence",
                requeue_stage="pending_exec",
                failure_disposition="same_task_local_retry",
                metadata={
                    "blueprint_id": str(blueprint_id or ""),
                    "target_files": _normalize_string_list(payload.get("target_files")),
                    "scope_paths": _normalize_string_list(payload.get("scope_paths")),
                    "reason": "director_no_changed_files_evidence",
                },
            )
        )
        return {"task_id": task_id, "ok": False, "reason": "missing_execution_evidence"}

    def run(self) -> None:
        """Continuously process PENDING_EXEC tasks until stop() is called."""
        _package().logger.info(
            "Director consumer started: worker_id=%s workspace=%s idle_mode=event_wakeup",
            self._worker_id,
            self._workspace,
        )
        while not self._stop_event.is_set():
            try:
                self._work_event.clear()
                processed = self.poll_once()
                if not processed:
                    retry_delay = self._svc.next_local_retry_delay(self._workspace, "pending_exec")
                    self._work_event.wait(timeout=retry_delay)
            except Exception as exc:  # noqa: BLE001
                _package().logger.exception(
                    "Director consumer cycle failed, waiting for next wake signal: %s",
                    exc,
                )
                self._work_event.wait()
        _package().logger.info("Director consumer stopped: worker_id=%s", self._worker_id)

    def stop(self) -> None:
        """Signal the consumer to stop after the current poll cycle."""
        self._stop_event.set()
        self._work_event.set()

    def _execute_task(self, task_id: str, payload: dict[str, Any], lease_token: str) -> dict[str, Any]:
        """Execute task through the real Director adapter and normalize evidence."""

        if self._task_executor is not None:
            return self._task_executor(task_id, payload, lease_token)

        workspace_path = Path(self._workspace)
        if not workspace_path.exists():
            _package().logger.warning(
                "Director consumer workspace does not exist; returning no-evidence result: workspace=%s task_id=%s",
                self._workspace,
                task_id,
            )
            return {"changed_files": [], "duration": 0, "side_effects": []}

        from polaris.cells.roles.adapters.public.service import create_role_adapter

        started_at = time.monotonic()
        adapter = create_role_adapter("director", str(workspace_path))
        adapter_input = _build_director_adapter_input(task_id, payload, lease_token)
        pm_task_id = str(adapter_input.get("pm_task_id") or task_id).strip() or task_id
        job_token = _job_token_from_payload(payload)
        context: dict[str, Any] = {
            "run_id": str(payload.get("run_id") or f"task-market-director-{task_id}"),
            "task_id": task_id,
            "pm_task_id": pm_task_id,
            "target_task_id": task_id,
            "metadata": {
                "task_id": task_id,
                "pm_task_id": pm_task_id,
                "target_task_id": task_id,
                "task_market_task_id": task_id,
                "task_market_stage": "pending_exec",
                "task_market_worker_id": self._worker_id,
                "blueprint_id": str(payload.get("blueprint_id") or ""),
                "blueprint_hash": str(payload.get("blueprint_hash") or job_token.get("blueprint_hash") or ""),
                "contract_hash": str(payload.get("contract_hash") or job_token.get("contract_hash") or ""),
                "job_token": job_token,
                "control_plane_job_token": job_token,
                "capability_token": job_token,
                "control_plane_lineage": _mapping_copy(payload.get("control_plane_lineage")),
                "route": _normalize_task_market_route(payload),
            },
        }
        for key in (
            "target_files",
            "scope_paths",
            "acceptance",
            "acceptance_criteria",
            "execution_checklist",
            "verification_commands",
            "quality_commands",
            "workspace_quality_commands",
            "task_completion_projection",
            "qa_local_repair_context",
            "completion_contract_hash",
            "completion_contract_ref",
        ):
            value = payload.get(key)
            if value:
                context[key] = value
                context["metadata"][key] = value
        # Three-tier fission (I2): a CE-fissioned leaf step carries its
        # construction_step blueprint card; the context gateway injects it as
        # the Director's bounded "local god view" (BlueprintStepsSignal).
        construction_step = payload.get("construction_step")
        if isinstance(construction_step, dict) and construction_step:
            context["construction_step"] = construction_step
            # Fix-13 缺陷清单: 改建式步骤必须携带现状勘察, 否则弱执行者
            # 读到看似完整的目标文件会拒绝动笔 (live I3-r13 编辑模式 0/5)。
            punch_list = _pre_state_punch_list(construction_step, cwd=str(workspace_path))
            if punch_list is not None:
                context["pre_state_verify"] = punch_list
            # Interface coherence (I3-r28): surface the frozen identifiers of OTHER
            # files so the weak Director REUSES cross-file names instead of inventing
            # mismatched ones (live: main.js getElementById('game') vs index.html
            # 'gameCanvas'). The cross-file ledger is the shared blackboard trace.
            consumed_interfaces = _read_consumed_interfaces(str(workspace_path), payload, construction_step)
            if consumed_interfaces:
                context["consumed_interfaces"] = consumed_interfaces
        # Bounce teaching: a requeued step carries the previous failure
        # (QA verify output, target-miss directive). Without it the retry
        # is blind — the file looks complete, the model makes no changes,
        # and the step dies no_materialized_changes (live I3-r10).
        last_failure = payload.get("last_failure")
        if isinstance(last_failure, dict) and str(last_failure.get("error_message") or "").strip():
            context["last_failure"] = last_failure
        adapter_result = _run_coroutine_sync(
            adapter.execute(task_id=task_id, input_data=adapter_input, context=context),
            timeout_seconds=self._execution_timeout_seconds,
        )
        duration = time.monotonic() - started_at

        if adapter_result.get("success") is not True:
            amendment_evidence = _interface_contract_amendment_from_adapter_failure(
                workspace_path=workspace_path,
                task_id=task_id,
                payload=payload,
                adapter_result=adapter_result,
            )
            if amendment_evidence is not None:
                raise InterfaceContractAmendmentRequiredError(
                    _adapter_failure_message(adapter_result),
                    amendment_request=amendment_evidence,
                )
            repair_evidence = _interface_contract_repair_from_adapter_failure(
                workspace_path=workspace_path,
                task_id=task_id,
                payload=payload,
                adapter_result=adapter_result,
            )
            if repair_evidence is not None:
                raise InterfaceContractRepairRequiredError(
                    _adapter_failure_message(adapter_result),
                    repair_evidence=repair_evidence,
                )
            owner_handoff_failure = _owner_handoff_failure_from_adapter_failure(adapter_result)
            if owner_handoff_failure is not None:
                raise _OwnerHandoffRoutingRequiredError(
                    _adapter_failure_message(adapter_result),
                    failure=owner_handoff_failure,
                )
            raise RuntimeError(_adapter_failure_message(adapter_result))

        reported_changed_files = _extract_director_changed_files(adapter_result)
        changed_files, unmaterialized_changed_files = materialized_file_paths(
            workspace_path,
            reported_changed_files,
        )
        adapter_summary = _compact_director_adapter_summary(adapter_result)
        if unmaterialized_changed_files:
            adapter_summary["reported_changed_files"] = reported_changed_files
            adapter_summary["unmaterialized_reported_changed_files"] = unmaterialized_changed_files
        return {
            "changed_files": changed_files,
            "duration": duration,
            "side_effects": _extract_director_side_effects(adapter_result),
            "director_adapter_result": adapter_summary,
        }
