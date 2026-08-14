"""Workspace quality checks implementation extracted from ``OrchestrationStageExecutor``.

Holds the workspace-quality-checks method cluster using the impl-passing
pattern: each function takes ``executor`` (the original ``self``) as its first
parameter so it can reach back into the class for shared state and helper
methods. Behavior is preserved verbatim.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
)
from polaris.kernelone.llm.budget_policy import FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS

from .factory_run_models import _WORKSPACE_VALIDATION_TIMEOUT_SECONDS, FactoryRun
from .factory_workspace_quality_evidence import _dedupe_workspace_repair_paths

# Module-local constants (mirrors of the ones in ``factory_stage_executor`` so
# the impl is self-contained without importing the executor module).
_QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS = 5.0
_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3
_WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS = FACTORY_LLM_STAGE_MIN_START_BUDGET_SECONDS
_WORKSPACE_QUALITY_REPAIR_LEASE_TTL_SECONDS = 300
_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS = 30.0


async def _run_workspace_quality_repair_heartbeat(
    authority: Any,
    *,
    stop: asyncio.Event,
    failures: list[dict[str, Any]],
    context_summary: str,
) -> None:
    """Keep a claimed repair task alive while planning/provider work runs.

    Ordinary Director execution owns a background TaskRuntime heartbeat.  The
    Factory quality-repair continuation used the same 300-second claim but did
    not start that heartbeat, so long deterministic/LLM repair work could only
    reach DEO after its lease had expired.  The physical write then failed
    closed with ``deo_execution_attempt_heartbeat_failed`` and settlement
    failed again with ``session_lease_expired``.

    ``authority_operation_in_progress`` is a transient overlap with DEO's own
    atomic heartbeat/settlement operation.  DEO remains authoritative and
    fail-closed; the keeper simply retries on the next interval.
    """

    while True:
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=_WORKSPACE_QUALITY_REPAIR_HEARTBEAT_INTERVAL_SECONDS,
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            verdict = await asyncio.to_thread(
                authority.heartbeat,
                lease_ttl_seconds=_WORKSPACE_QUALITY_REPAIR_LEASE_TTL_SECONDS,
                lock_timeout_seconds=5.0,
                context_summary=context_summary,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            failures.append(
                {
                    "code": "heartbeat_exception",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            return
        if bool(getattr(verdict, "success", False)):
            continue
        code = str(getattr(verdict, "code", "") or "heartbeat_rejected")
        if code == "authority_operation_in_progress":
            continue
        failures.append({"code": code})
        return


async def _stop_workspace_quality_repair_heartbeat(
    heartbeat_task: asyncio.Task[None],
    stop: asyncio.Event,
) -> None:
    stop.set()
    await heartbeat_task


def _is_deferred_declared_test_entrypoint_issue(
    issue: Any,
    *,
    declared_targets: set[str],
) -> bool:
    """Ignore only test paths that a later PM task is contracted to create.

    Workspace quality repair runs after Director materialization but before all
    downstream tasks necessarily settle.  A manifest task may therefore create
    ``"test": "node --test tests/"`` before the test-owner task creates
    ``tests/product.test.js``.  Treating that discovery path as a missing
    *entrypoint* sends repair planning down an unrelated deterministic rule and
    hides the real verifier failure.

    This is not a final-quality waiver: the real test command still runs and
    remains authoritative.  Unowned/mistyped paths remain errors.
    """

    if str(getattr(issue, "code", "") or "").strip() != "npm_script_missing_local_entrypoint":
        return False
    metadata = getattr(issue, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    script_name = str(metadata.get("script_name") or "").strip().lower()
    if script_name != "test" and not script_name.startswith("test:"):
        return False
    entrypoint = str(metadata.get("entrypoint") or "").strip().replace("\\", "/")
    while entrypoint.startswith("./"):
        entrypoint = entrypoint[2:]
    if not entrypoint:
        return False
    prefix = entrypoint.rstrip("/") + "/"
    return any(target == entrypoint or target.startswith(prefix) for target in declared_targets)


def _workspace_quality_repair_errors(executor, results: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for result in results:
        if bool(result.get("passed")):
            continue
        error_text = str(result.get("error") or "").strip()
        diagnostic_excerpt = str(result.get("diagnostic_excerpt") or "").strip()
        stream_output = "\n".join(
            str(result.get(key) or "").strip()
            for key in ("stdout_tail", "stderr_tail")
            if str(result.get(key) or "").strip()
        )
        # ``diagnostic_excerpt`` is already the bounded, marker-aware projection
        # of stdout+stderr. Feeding it together with both tails duplicates the
        # same failure block up to three times and multiplies repair coverage.
        # Prefer it as the sole diagnostic input; command/error provenance stays
        # in the durable workspace-validation command row.
        diagnostic_input = diagnostic_excerpt or stream_output or error_text
        if not diagnostic_input:
            continue
        command = result.get("command")
        command_text = " ".join(str(part) for part in command) if isinstance(command, list) else str(command or "")
        output = executor._trim_command_output(diagnostic_input)
        # The command row is durable verifier evidence, but its wrapper is
        # not itself a repair diagnostic.  Feeding the entire wrapper into
        # Director Runtime makes the actionable nested compiler/runtime
        # diagnostic compete with generic ``workspace_validation_failed``
        # rows.  Coverage then fails closed even when an executable repair
        # binding exists.  Project through the public Director diagnostic
        # normalizer and transport only actionable raw diagnostics.  Keep
        # the wrapper as a fail-closed fallback when no actionable signal
        # can be extracted; command/phase/stdout/stderr provenance remains
        # authoritative in ``workspace-validation.json.commands``.
        try:
            from polaris.cells.director.runtime.public import normalize_director_repair_diagnostics

            diagnostics = normalize_director_repair_diagnostics((output,))
        except (ImportError, RuntimeError, TypeError, ValueError):
            diagnostics = ()
        actionable = [
            diagnostic
            for diagnostic in diagnostics
            if str(diagnostic.code or "").strip() not in {"artifact_quality_error", "workspace_validation_failed"}
        ]
        if actionable:
            errors.extend(
                str(diagnostic.metadata.get("raw") or diagnostic.message or "").strip()
                for diagnostic in actionable
                if str(diagnostic.metadata.get("raw") or diagnostic.message or "").strip()
            )
        else:
            fallback_output = executor._trim_command_output(
                "\n".join(part for part in (error_text, output) if part)
            )
            errors.append(
                "Artifact quality scan failed: workspace validation command failed"
                f" ({command_text or 'unknown command'}): {fallback_output}"
            )

    try:
        from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence

        evidence = scan_workspace_artifact_quality_evidence(str(executor.workspace))
        declared_targets = {
            str(path or "").strip().replace("\\", "/")
            for path in executor._workspace_quality_repair_target_files()
            if str(path or "").strip()
        }
        deferred_error_messages = {
            str((getattr(issue, "metadata", None) or {}).get("raw") or "").strip()
            for issue in evidence.issues
            if _is_deferred_declared_test_entrypoint_issue(
                issue,
                declared_targets=declared_targets,
            )
        }
        errors.extend(error for error in evidence.errors if str(error or "").strip() not in deferred_error_messages)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        errors.append(f"Artifact quality scan failed: workspace quality repair scan failed: {exc}")

    deduped: list[str] = []
    seen: set[str] = set()
    for error in errors:
        normalized = str(error or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _claim_workspace_quality_repair_attempt(
    executor,
    *,
    run: FactoryRun,
    repair_attempt: int,
    target_files: list[str],
) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1, dict[str, Any]]:
    """Claim the Director attempt that owns one post-verifier repair round.

    Reopen the exact owning task when one exists.  If no canonical PM/CE
    owner can be resolved, fail closed instead of minting a helper task:
    verifier repair must remain a continuation of real Director work, not a
    fresh authority that QA invents after the fact. Workspace verification used to invoke
    the guarded Director role without
    a TaskRuntime execution attempt.  Directed-effect validation therefore
    rejected the turn before the model could edit the failed artifacts.  A
    repair round is real Director work: give it a fresh, run-bound task row,
    propagate its exact session identity to roles.runtime, and terminally
    settle it after the repair result is known.  PM and CE are intentionally
    not restarted for this local verifier failure.
    """

    run_id = run.id
    task_runtime = TaskRuntimeService(str(executor.workspace))
    normalized_targets = {
        str(path or "").strip().replace("\\", "/") for path in target_files if str(path or "").strip()
    }

    def row_owner_score(candidate: Mapping[str, Any]) -> tuple[int, int]:
        return executor._workspace_quality_repair_owner_score(
            candidate,
            run_id=run_id,
            normalized_targets=normalized_targets,
        )

    owner_rows = [
        candidate
        for candidate in task_runtime.list_task_rows(include_terminal=True)
        if row_owner_score(candidate)[0] > 0
    ]
    owner_row = max(owner_rows, key=row_owner_score) if owner_rows else None
    if owner_row is None:
        # A terminal Factory drain deliberately removes live TaskRuntime rows
        # after freezing their authority.  A QA-only retry preserves that
        # frozen epoch, so restore the exact PM task contract named by it
        # before claiming a local Director repair.  Without this bridge the QA
        # boundary can validate the frozen projection, but the repair claimant
        # sees no live owner and fails before the Provider/tool layer runs.
        from polaris.cells.factory.pipeline.public.contracts import (
            FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
            FactoryTerminalTaskRuntimeProjectionV1,
        )

        frozen_payload = run.metadata.get(FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY)
        frozen_task_statuses: dict[str, str] = {}
        if isinstance(frozen_payload, Mapping):
            frozen = FactoryTerminalTaskRuntimeProjectionV1.from_dict(frozen_payload)
            if frozen.factory_run_id != run_id:
                raise RuntimeError("workspace_quality_repair_frozen_authority_run_mismatch")
            rows = frozen.projection.get("rows")
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, Mapping):
                    continue
                external_task_id = str(row.get("external_task_id") or "").strip()
                row_factory_run_id = str(row.get("factory_run_id") or "").strip()
                if (
                    external_task_id
                    and not external_task_id.startswith("factory-")
                    and row_factory_run_id == run_id
                ):
                    frozen_task_statuses[external_task_id] = str(
                        row.get("execution_state") or row.get("status") or ""
                    ).strip()

        canonical_tasks: dict[str, dict[str, Any]] = {}
        frozen_candidates: list[dict[str, Any]] = []
        for index, task in enumerate(executor._load_pm_plan_tasks("tasks/plan.json"), start=1):
            task_id = executor._task_id(task, index)
            if not task_id or task_id not in frozen_task_statuses:
                continue
            canonical_task = dict(task)
            canonical_tasks[task_id] = canonical_task
            metadata_raw = canonical_task.get("metadata")
            metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
            metadata.update(
                {
                    "external_task_id": task_id,
                    "pm_task_id": task_id,
                    "source_task_id": task_id,
                    "factory_run_id": run_id,
                    "factory_stage": "quality_gate",
                    "source_artifact": "tasks/plan.json",
                    "task_contract": canonical_task,
                }
            )
            for key in ("scope", "scope_paths", "target_files", "acceptance", "acceptance_criteria", "steps"):
                if key in canonical_task:
                    metadata[key] = canonical_task[key]
            frozen_candidate = {
                **canonical_task,
                "external_task_id": task_id,
                "status": frozen_task_statuses[task_id],
                "metadata": metadata,
            }
            if row_owner_score(frozen_candidate)[0] > 0:
                frozen_candidates.append(frozen_candidate)

        if frozen_candidates:
            frozen_owner = max(frozen_candidates, key=row_owner_score)
            frozen_owner_id = str(frozen_owner.get("external_task_id") or "").strip()
            materialized = executor._materialize_pm_plan_taskboard(
                [canonical_tasks[frozen_owner_id]],
                run_id=run_id,
                source_stage="quality_gate",
                run_metadata=run.metadata,
            )
            binding_failures = materialized.get("binding_failures")
            if binding_failures:
                raise RuntimeError("workspace_quality_repair_frozen_owner_binding_failed")
            # ``_materialize_pm_plan_taskboard`` owns a separate service
            # instance. Reopen TaskRuntime here so this claimant cannot retain
            # the pre-restore empty board cache and report ``task_not_found``.
            task_runtime = TaskRuntimeService(str(executor.workspace))
            restored_row = task_runtime.get_task(frozen_owner_id)
            if not isinstance(restored_row, Mapping):
                raise RuntimeError("workspace_quality_repair_frozen_owner_restore_failed")
            owner_row = restored_row
    if owner_row is not None:
        owner_metadata = owner_row.get("metadata")
        owner_metadata = owner_metadata if isinstance(owner_metadata, Mapping) else {}
        external_task_id = str(
            owner_metadata.get("external_task_id") or owner_row.get("external_task_id") or ""
        ).strip()
        task_row_id = task_runtime.normalize_task_id(owner_row.get("id"))
        if task_row_id is None:
            raise RuntimeError("workspace_quality_repair_owner_task_id_invalid")
        owner_status = str(owner_row.get("status") or owner_row.get("raw_status") or "").strip().lower()
        if owner_status in {"completed", "failed", "cancelled"}:
            reopened = task_runtime.reopen_task_row(
                task_row_id,
                reason="workspace_quality_gate_failed",
                metadata={
                    "factory_run_id": run_id,
                    "workspace_quality_repair": True,
                    "repair_attempt": repair_attempt,
                },
            )
            if not isinstance(reopened, Mapping) or str(reopened.get("status") or "").lower() not in {
                "pending",
                "ready",
                "blocked",
            }:
                raise RuntimeError("workspace_quality_repair_owner_reopen_failed")
        repair_task = dict(owner_row)
        repair_task_metadata = dict(owner_metadata)
    else:
        raise RuntimeError("workspace_quality_repair_canonical_owner_missing")
    # The quality-repair adapter must receive the original Director task
    # contract, not a synthetic ``target_files`` shell.  Final-request
    # qualification reconstructs authoritative PM/CE evidence from this
    # row (including blueprint_id/runtime_blueprint_path).  Dropping the
    # owner metadata made a valid local retry fail closed with
    # missing_required_refs=pm_contract,ce_blueprint after QA had already
    # reopened the task.
    repair_task["id"] = external_task_id
    repair_task["task_id"] = external_task_id
    repair_task["external_task_id"] = external_task_id
    for key in (
        "goal",
        "description",
        "scope",
        "scope_paths",
        "target_files",
        "acceptance",
        "acceptance_criteria",
        "verification_commands",
        "blueprint_id",
        "runtime_blueprint_path",
        "blueprint_path",
    ):
        if not repair_task.get(key) and repair_task_metadata.get(key) is not None:
            repair_task[key] = repair_task_metadata[key]
    if not repair_task.get("target_files"):
        repair_task["target_files"] = sorted(normalized_targets)
    repair_task_metadata.update(
        {
            "external_task_id": external_task_id,
            "factory_run_id": run_id,
            "workspace_quality_repair": True,
            "repair_attempt": repair_attempt,
        }
    )
    repair_task["metadata"] = repair_task_metadata
    binding = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(executor.workspace),
            task_id=external_task_id,
            factory_run_id=run_id,
        )
    )
    if not binding.ok:
        raise RuntimeError(f"workspace_quality_repair_binding_failed:{binding.code}")
    claim = task_runtime.claim_execution(
        task_row_id,
        worker_id="director",
        role_id="director",
        run_id=run_id,
        lease_ttl_seconds=_WORKSPACE_QUALITY_REPAIR_LEASE_TTL_SECONDS,
        selection_source="factory_stage_executor.workspace_quality_repair",
        external_task_id=external_task_id,
        context_summary="director_workspace_quality_repair",
        metadata={
            "factory_run_id": run_id,
            "factory_stage": "quality_gate",
            "workspace_quality_repair": True,
            "repair_attempt": repair_attempt,
            "execution_identity_required": True,
        },
    )
    session = claim.get("session") if isinstance(claim, dict) else None
    attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
    if not isinstance(session, Mapping) or not isinstance(attempt_record, Mapping) or not bool(claim.get("success")):
        reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
        raise RuntimeError(f"workspace_quality_repair_claim_failed:{reason}")
    execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
    return external_task_id, task_row_id, execution_attempt, repair_task


def _apply_workspace_quality_repairs(
    executor,
    *,
    run_id: str,
    artifact_quality_errors: list[str],
    task_id: str | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
    repair_task: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule,
    )

    class _QualityRepairAdapter:
        def __init__(self, workspace: Path) -> None:
            self.workspace = str(workspace)
            self._execution = SimpleNamespace(_message_bus=None)

        def _update_task_progress(
            self,
            task_id: str,
            phase: str,
            current_file: str | None = None,
            event_code: str | None = None,
            event_status: str | None = None,
            event_reason: str | None = None,
            event_detail: str | None = None,
            event_refs: dict[str, Any] | None = None,
        ) -> None:
            del task_id, phase, current_file, event_code, event_status, event_reason, event_detail, event_refs

    task_payload = dict(repair_task) if isinstance(repair_task, Mapping) else {}
    task_metadata = task_payload.get("metadata")
    task_metadata = dict(task_metadata) if isinstance(task_metadata, Mapping) else {}
    raw_owned_targets = task_payload.get("target_files") or task_metadata.get("target_files") or ()
    owned_targets = _dedupe_workspace_repair_paths(
        [raw_owned_targets] if isinstance(raw_owned_targets, str) else list(raw_owned_targets)
    )
    target_files = owned_targets or executor._workspace_quality_repair_target_files()
    if not target_files:
        target_files = executor._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors)
    if not target_files:
        target_files = executor._workspace_quality_repair_changed_files()
    if "package.json" not in target_files and (executor.workspace / "package.json").is_file():
        target_files = [*target_files, "package.json"]
    metadata: dict[str, Any] = {
        **task_metadata,
        "target_files": target_files,
        "delivery_mode": "materialize_changes",
    }
    blueprint_artifact, blueprint_text = executor._workspace_quality_repair_blueprint_evidence(run_id=run_id)
    if not task_payload:
        # Compatibility-only workspace invocation. Canonical Factory retries
        # pass ``repair_task`` and remain constrained to that exact PM/CE owner.
        metadata["factory_workspace_quality_repair"] = {
            "ce_blueprint_artifact": blueprint_artifact,
            "target_files": target_files,
            "run_id": run_id,
        }
    if blueprint_text:
        blueprint_payload = {
            "schema_version": "factory.workspace_quality_repair.ce_blueprint_context.v1",
            "artifact": blueprint_artifact,
            "evidence": blueprint_text,
        }
        metadata["ce_blueprint"] = blueprint_payload
        metadata["chief_engineer_blueprint"] = blueprint_payload
        metadata["chief_engineer_blueprint_evidence"] = blueprint_text
    resolved_task_id = str(task_id or "").strip() or f"factory-quality-gate:{run_id}"
    if task_payload:
        task_payload["target_files"] = target_files
        task_payload["metadata"] = metadata
    else:
        task_payload = {"target_files": target_files, "metadata": metadata}
    return run_director_materialization_quality_repair_schedule(
        _QualityRepairAdapter(executor.workspace),
        task=task_payload,
        task_id=resolved_task_id,
        artifact_quality_errors=artifact_quality_errors,
        execution_attempt=execution_attempt,
    )


async def _apply_workspace_quality_deterministic_repairs(
    executor,
    *,
    run: FactoryRun,
    artifact_quality_errors: list[str],
    repair_attempt: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute one deterministic repair round on its owning Director task.

    Runtime repair planners intentionally emit deferred DEO effects; they do
    not write merely because a plan is plannable.  Workspace QA previously
    called the planner without a canonical TaskRuntime attempt and without
    committing the deferred effects.  Every executable rule therefore
    collapsed to ``deo_deferred_repair_attempt_required`` and the quality
    gate needlessly fell through to another LLM turn.

    Keep ordinary verifier failures on Director: claim/reopen the best owning
    task, defer through the repair kernel, commit through DEO, settle that
    exact attempt, then let the caller re-run only the failed verifier set.
    PM and Chief Engineer are not restarted.
    """

    from polaris.cells.roles.adapters.public import commit_materialization_deferred_repairs
    from polaris.cells.runtime.task_runtime.public import (
        create_task_runtime_execution_attempt_authority,
    )

    run_id = str(run.id or "").strip() or "workspace-quality-repair"
    target_files = executor._director_stage_materialization_settle_target_files(diagnostics=artifact_quality_errors)
    try:
        task_id, task_row_id, execution_attempt, repair_task = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=repair_attempt,
            target_files=target_files,
        )
        authority = create_task_runtime_execution_attempt_authority(execution_attempt)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "success": False,
            "repair_mode": "director_deterministic",
            "error": f"workspace_quality_deterministic_attempt_claim_failed:{exc}",
            "source_tools": ["director_runtime_repair_attempt_error"],
            "tool_results": 0,
            "write_tool_evidence": False,
        }

    results: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    receipts: list[dict[str, Any]] = []
    heartbeat_stop = asyncio.Event()
    heartbeat_failures: list[dict[str, Any]] = []
    heartbeat_task = asyncio.create_task(
        _run_workspace_quality_repair_heartbeat(
            authority,
            stop=heartbeat_stop,
            failures=heartbeat_failures,
            context_summary="director_workspace_quality_deterministic_repair",
        )
    )
    try:
        results, raw_summary = await asyncio.to_thread(
            executor._apply_workspace_quality_repairs,
            run_id=run_id,
            artifact_quality_errors=artifact_quality_errors,
            task_id=task_id,
            execution_attempt=execution_attempt,
            repair_task=repair_task,
        )
        summary = dict(raw_summary)
        deferred_candidates = [
            item
            for item in results
            if isinstance(item, Mapping)
            and isinstance(item.get("result"), Mapping)
            and (
                item["result"].get("deferred_request") is not None
                or str(item["result"].get("status") or "").strip()
                in {"deferred_repair_effects_pending", "deferred_command_effect_pending"}
            )
        ]
        commit_context = executor._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=run_id,
            diagnostics=artifact_quality_errors,
            factory_stage="quality_gate",
        )
        for candidate_index, candidate in enumerate(deferred_candidates):
            candidate_receipts = await commit_materialization_deferred_repairs(
                workspace=str(execution_attempt.workspace),
                tool_results=[candidate],
                execution_attempt=execution_attempt,
                execution_attempt_authority=authority,
                turn_id=(f"workspace-quality-repair-{run_id}-round{repair_attempt}-candidate{candidate_index}"),
                context=commit_context,
            )
            receipts.extend(dict(item) for item in candidate_receipts if isinstance(item, Mapping))
    except Exception as exc:  # noqa: BLE001 - fail closed at DEO commit boundary.
        summary = {
            **summary,
            "attempted": True,
            "success": False,
            "repair_mode": "director_deterministic",
            "error": f"workspace_quality_deterministic_commit_failed:{type(exc).__name__}:{exc}",
        }
    finally:
        await _stop_workspace_quality_repair_heartbeat(heartbeat_task, heartbeat_stop)

    if heartbeat_failures:
        summary["execution_attempt_heartbeat_failures"] = heartbeat_failures
        summary.setdefault(
            "error",
            f"workspace_quality_repair_lease_heartbeat_failed:{heartbeat_failures[0]['code']}",
        )

    successful_receipts = [
        item for item in receipts if executor._director_stage_materialization_receipt_succeeded(item)
    ]
    failed_receipts = [
        item for item in receipts if not executor._director_stage_materialization_receipt_succeeded(item)
    ]
    # Lease liveness is part of the write authority.  A physical receipt that
    # lands after heartbeat rejection/expiry cannot complete the task because
    # this Director no longer proves exclusive ownership of the attempt.
    mutation_committed = bool(successful_receipts) and not heartbeat_failures
    settle_result = executor._settle_director_stage_materialization_attempt(
        task_row_id=task_row_id,
        execution_attempt=execution_attempt,
        stage_status="success" if mutation_committed else "failed",
        summary=(
            "workspace_quality_deterministic_repair_committed"
            if mutation_committed
            else str(summary.get("error") or "workspace_quality_deterministic_repair_no_commit")
        ),
    )
    evidence = (
        [f"deferred_commit:successful={len(successful_receipts)};failed={len(failed_receipts)}"]
        if mutation_committed
        else []
    )
    summary.update(
        {
            "attempted": True,
            "success": mutation_committed,
            "repair_mode": "director_deterministic",
            "tool_results": len(results),
            "committed_receipt_count": len(successful_receipts),
            "failed_receipt_count": len(failed_receipts),
            "write_tool_evidence": mutation_committed,
            "evidence": evidence,
            "task_runtime_repair_attempt": {
                "task_id": task_id,
                "session_id": execution_attempt.session_id,
                "settled": bool(settle_result.get("success")),
                "outcome": "completed" if mutation_committed else "failed",
            },
        }
    )
    return results, summary


async def _apply_workspace_quality_llm_repairs(
    executor,
    *,
    run: FactoryRun,
    context: dict[str, Any],
    artifact_quality_errors: list[str],
    repair_attempt: int,
    interface_discrepancy_evidence: dict[str, Any] | None = None,
    owner_target_files: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_id = run.id
    changed_files = executor._workspace_quality_repair_changed_files()
    if not changed_files:
        return [], {
            "attempted": False,
            "repair_mode": "director_llm",
            "reason": "no_workspace_source_files_for_repair",
            "source_tools": [],
            "tool_results": 0,
        }
    declared_target_files = executor._workspace_quality_repair_target_files()
    diagnostic_target_files = executor._workspace_quality_repair_diagnostic_target_files(artifact_quality_errors)
    target_files = (
        _dedupe_workspace_repair_paths(owner_target_files)
        if owner_target_files
        else diagnostic_target_files or declared_target_files
    )
    repair_context: dict[str, Any] = {
        "delivery_mode": "materialize_changes",
        "target_files": (target_files or changed_files)[:80],
        "changed_files": changed_files[:80],
        "factory_workspace_quality_repair": {
            "changed_files": changed_files[:80],
            "target_files": target_files[:80],
        },
    }
    catalog = executor._read_catalog_contract()
    primary_language = str(catalog.get("primary_language") or "").strip()
    project_type = str(catalog.get("project_type") or "").strip()
    if primary_language:
        repair_context.setdefault("language", primary_language)
        repair_context.setdefault("programming_language", primary_language)
        repair_context.setdefault("tech_stack", {"language": primary_language})
    if project_type:
        repair_context.setdefault("project_type", project_type)
        repair_context.setdefault("project_kind", project_type)
    blueprint_artifact, blueprint_text = executor._workspace_quality_repair_blueprint_evidence(run_id=run_id)
    if blueprint_text:
        blueprint_payload = {
            "schema_version": "factory.workspace_quality_repair.ce_blueprint_context.v1",
            "artifact": blueprint_artifact,
            "evidence": blueprint_text,
        }
        repair_context["ce_blueprint"] = blueprint_payload
        repair_context["chief_engineer_blueprint"] = blueprint_payload
        repair_context["chief_engineer_blueprint_evidence"] = blueprint_text
        repair_context["factory_workspace_quality_repair"]["ce_blueprint_artifact"] = blueprint_artifact
    if interface_discrepancy_evidence:
        repair_context["director_interface_discrepancy_retry"] = {
            "authorized": executor._workspace_quality_interface_discrepancy_allows_director_retry(
                interface_discrepancy_evidence
            ),
            "recommended_owner": interface_discrepancy_evidence.get("recommended_owner"),
            "recommended_route": interface_discrepancy_evidence.get("recommended_route"),
            "reason": interface_discrepancy_evidence.get("reason"),
            "interface_discrepancy_evidence": interface_discrepancy_evidence,
        }
        repair_context["factory_task_boundary_interface_discrepancy"] = interface_discrepancy_evidence
        repair_context["factory_workspace_quality_repair"]["interface_discrepancy_evidence"] = (
            interface_discrepancy_evidence
        )
    for key in (
        "language",
        "prompt_language",
        "programming_language",
        "artifact",
        "artifact_type",
        "project_kind",
        "prompt_profile_ids",
        "prompt_profiles",
        "prompt_profile",
        "prompt_profile_id",
    ):
        if key in context:
            repair_context[key] = context[key]
    # Quality repair is a child execution of the same Factory run, not an
    # unbounded ad-hoc role call.  Preserve the parent deadline and TaskRuntime
    # wall-clock budget so roles.adapters can keep the provider timeout narrow
    # while allowing the already-started tool/DEO transaction to settle.  L1-01
    # r42 dropped these fields, reported ``no_factory_deadline``, then marked a
    # task failed even though its write receipt committed moments later.
    for key in (
        "factory_run_deadline_epoch_seconds",
        "factory_run_deadline_source",
        "factory_run_timeout_seconds",
        "factory_director_execution_deadline_epoch_seconds",
        "request_timeout_seconds",
    ):
        if key in context:
            repair_context[key] = context[key]
    try:
        (
            repair_task_id,
            repair_task_row_id,
            execution_attempt,
            repair_task,
        ) = executor._claim_workspace_quality_repair_attempt(
            run=run,
            repair_attempt=repair_attempt,
            target_files=target_files or changed_files,
        )
        from polaris.cells.runtime.task_runtime.public import (
            create_task_runtime_execution_attempt_authority,
        )

        execution_attempt_authority = create_task_runtime_execution_attempt_authority(execution_attempt)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "repair_mode": "director_llm",
            "success": False,
            "error": f"workspace_quality_repair_attempt_claim_failed:{exc}",
            "source_tools": ["director_materialization_quality_repair_error"],
            "tool_results": 0,
        }

    repair_context["task_id"] = repair_task_id
    repair_context["session_id"] = execution_attempt.session_id
    repair_context["task_runtime_execution_attempt"] = execution_attempt
    repair_context["task_runtime_execution_attempt_authority"] = execution_attempt_authority
    repair_metadata = repair_context.get("metadata")
    if not isinstance(repair_metadata, dict):
        repair_metadata = {}
        repair_context["metadata"] = repair_metadata
    repair_metadata["task_id"] = repair_task_id
    repair_metadata["task_runtime_session_id"] = execution_attempt.session_id
    repair_metadata["workspace_quality_repair"] = True
    heartbeat_stop = asyncio.Event()
    heartbeat_failures: list[dict[str, Any]] = []
    heartbeat_task = asyncio.create_task(
        _run_workspace_quality_repair_heartbeat(
            execution_attempt_authority,
            stop=heartbeat_stop,
            failures=heartbeat_failures,
            context_summary="director_workspace_quality_llm_repair",
        )
    )
    try:
        from polaris.cells.roles.adapters.public.service import run_director_materialization_quality_repair

        results, summary = await run_director_materialization_quality_repair(
            str(executor.workspace),
            task=repair_task,
            target_task_id=repair_task_id,
            run_id=run_id,
            context=repair_context,
            original_message=executor._workspace_quality_repair_original_message(
                run_id=run_id,
                target_files=target_files,
            ),
            llm_call_timeout=executor._workspace_quality_llm_repair_timeout_seconds(context),
            artifact_quality_errors=artifact_quality_errors,
            changed_files=changed_files,
            repair_attempt=repair_attempt,
        )
    except Exception as exc:  # noqa: BLE001 - fail closed around external LLM repair boundary.
        results = []
        summary = {
            "attempted": True,
            "repair_mode": "director_llm",
            "success": False,
            "error": str(exc),
            "source_tools": ["director_materialization_quality_repair_error"],
            "tool_results": 0,
        }
    finally:
        await _stop_workspace_quality_repair_heartbeat(heartbeat_task, heartbeat_stop)
    normalized_summary = dict(summary)
    if heartbeat_failures:
        normalized_summary["execution_attempt_heartbeat_failures"] = heartbeat_failures
        normalized_summary.setdefault(
            "error",
            f"workspace_quality_repair_lease_heartbeat_failed:{heartbeat_failures[0]['code']}",
        )
    normalized_summary["repair_mode"] = "director_llm"
    raw_source_tools = normalized_summary.get("source_tools")
    source_tool_items = raw_source_tools if isinstance(raw_source_tools, list | tuple | set) else []
    source_tools = [str(item) for item in source_tool_items if str(item or "").strip()]
    if results and "director_materialization_quality_repair" not in source_tools:
        source_tools.append("director_materialization_quality_repair")
    normalized_summary["source_tools"] = source_tools
    normalized_summary.setdefault("tool_results", len(results))
    normalized_summary.setdefault("attempted", True)
    mutation_committed = not heartbeat_failures and any(
        executor._workspace_quality_repair_result_has_mutation(dict(item))
        for item in results
        if isinstance(item, Mapping)
    )
    settle_result = executor._settle_director_stage_materialization_attempt(
        task_row_id=repair_task_row_id,
        execution_attempt=execution_attempt,
        stage_status="success" if mutation_committed else "failed",
        summary=(
            "workspace_quality_repair_mutation_committed"
            if mutation_committed
            else str(normalized_summary.get("error") or "workspace_quality_repair_no_mutation")
        ),
    )
    normalized_summary["task_runtime_repair_attempt"] = {
        "task_id": repair_task_id,
        "session_id": execution_attempt.session_id,
        "settled": bool(settle_result.get("success")),
        "outcome": "completed" if mutation_committed else "failed",
    }
    return [dict(item) for item in results], normalized_summary


async def _run_workspace_quality_checks(executor, run: FactoryRun, context: dict[str, Any]) -> tuple[bool, str]:
    commands = executor._workspace_quality_commands(context)
    task_boundary_blocker = executor._workspace_quality_task_boundary_blocker(run, context)
    depth_result = (
        None if task_boundary_blocker else executor._workspace_quality.delivery_depth_contract_result(context)
    )
    if not task_boundary_blocker and not commands and depth_result is None:
        return True, ""

    configured_timeout_seconds = float(
        context.get("workspace_validation_timeout_seconds") or _WORKSPACE_VALIDATION_TIMEOUT_SECONDS
    )
    results: list[dict[str, Any]] = []
    repair_summary: dict[str, Any] = {
        "attempted": False,
        "success": False,
        "source_tools": [],
        "tool_results": 0,
        "rounds": [],
    }

    def write_workspace_validation_failure(
        reason_code: str,
        detail: str,
        *,
        repair_override: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        payload = {
            "schema_version": "factory.workspace_quality_checks.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "factory_stage_executor",
            "factory_run_id": run.id,
            "workspace": str(executor.workspace),
            "passed": False,
            "commands": results,
            "repair": repair_override if repair_override is not None else repair_summary,
            "warnings": [reason_code],
            "error": detail,
            "deadline": {
                "remaining_seconds": executor._factory_deadline_remaining_seconds(context),
                "deadline_epoch_seconds": context.get("factory_run_deadline_epoch_seconds"),
                "timeout_seconds": context.get("factory_run_timeout_seconds"),
                "source": context.get("factory_run_deadline_source"),
            },
        }
        if extra_payload:
            payload.update(dict(extra_payload))
        artifact = executor._write_workspace_validation_artifact(run, context, payload)
        return False, artifact

    if task_boundary_blocker:
        reason_code = str(
            task_boundary_blocker.get("reason_code") or "factory_quality_gate_task_boundary_incomplete_materialization"
        )
        detail = str(task_boundary_blocker.get("detail") or reason_code)
        repair_override = {
            "attempted": False,
            "success": False,
            "source_tools": [],
            "tool_results": 0,
            "reason": "task_boundary_not_ready",
            "task_boundary_blocker": task_boundary_blocker,
        }
        return write_workspace_validation_failure(
            reason_code,
            detail,
            repair_override=repair_override,
            extra_payload={
                "failure_class": task_boundary_blocker.get("failure_class"),
                "responsible_layer": task_boundary_blocker.get("responsible_layer"),
                "task_boundary_blocker": task_boundary_blocker,
                "commands_skipped": True,
                "skip_reason": reason_code,
            },
        )

    def workspace_checks_deadline_blocker(phase: str) -> str:
        remaining_seconds = executor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return ""
        minimum_remaining = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
        if remaining_seconds >= minimum_remaining:
            return ""
        return (
            f"Workspace quality checks stopped at {phase} because the factory run deadline has only "
            f"{remaining_seconds:.1f}s remaining and QA requires at least {minimum_remaining:.1f}s"
        )

    def workspace_quality_command_timeout_seconds() -> float:
        remaining_seconds = executor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return configured_timeout_seconds
        reserved_for_qa = _QUALITY_GATE_MIN_QA_START_BUDGET_SECONDS + _QUALITY_GATE_QA_DEADLINE_SAFETY_SECONDS
        available_for_command = max(1.0, remaining_seconds - reserved_for_qa)
        return max(1.0, min(configured_timeout_seconds, available_for_command))

    async def run_workspace_quality_command_with_deadline(
        command: list[str],
        phase: str,
    ) -> tuple[dict[str, Any], str]:
        deadline_detail = workspace_checks_deadline_blocker(f"before_{phase}")
        if deadline_detail:
            return {}, deadline_detail
        command_timeout = workspace_quality_command_timeout_seconds()
        result = await asyncio.to_thread(executor._run_workspace_quality_command, command, command_timeout)
        result["phase"] = phase
        if command_timeout < configured_timeout_seconds:
            result["deadline_capped_timeout_seconds"] = command_timeout
            result["configured_timeout_seconds"] = configured_timeout_seconds
        return result, ""

    def workspace_repair_deadline_blocker(phase: str) -> str:
        remaining_seconds = executor._factory_deadline_remaining_seconds(context)
        if remaining_seconds is None:
            return ""
        if remaining_seconds >= _WORKSPACE_QUALITY_REPAIR_MIN_LLM_START_BUDGET_SECONDS:
            return ""
        return (
            f"Workspace quality repair skipped at {phase} because the factory run deadline has only "
            f"{remaining_seconds:.1f}s remaining"
        )

    initial_deadline_detail = workspace_checks_deadline_blocker("before_prepare")
    if initial_deadline_detail:
        return write_workspace_validation_failure(
            "factory_quality_gate_workspace_checks_deadline_insufficient",
            initial_deadline_detail,
        )

    prepare_commands = executor._workspace_quality_prepare_commands(commands, context)
    prepare_failed = False
    for command in prepare_commands:
        result, deadline_detail = await run_workspace_quality_command_with_deadline(command, "prepare")
        if deadline_detail:
            return write_workspace_validation_failure(
                "factory_quality_gate_workspace_checks_deadline_insufficient",
                deadline_detail,
            )
        results.append(result)
        if not bool(result.get("passed")):
            prepare_failed = True

    run_commands = [] if prepare_failed else commands
    for command in run_commands:
        result, deadline_detail = await run_workspace_quality_command_with_deadline(command, "check")
        if deadline_detail:
            return write_workspace_validation_failure(
                "factory_quality_gate_workspace_checks_deadline_insufficient",
                deadline_detail,
            )
        results.append(result)
    if not prepare_failed and depth_result is not None:
        depth_result["phase"] = "check"
        results.append(depth_result)
    if prepare_failed:
        for command in commands:
            results.append(
                {
                    "command": command,
                    "phase": "check",
                    "exit_code": None,
                    "passed": False,
                    "error": "skipped because workspace validation preparation failed",
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
            )

    repair_errors: list[str] = []
    repair_results: list[dict[str, Any]] = []

    rerun_prepare_results: list[dict[str, Any]] = []
    rerun_results: list[dict[str, Any]] = []
    if run_commands and not prepare_failed and not all(bool(item.get("passed")) for item in results):
        max_rounds = int(context.get("workspace_quality_repair_max_rounds") or _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS)
        max_rounds = max(1, min(max_rounds, _WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS))
        latest_check_results = [item for item in results if str(item.get("phase") or "") == "check"]
        repair_rounds: list[dict[str, Any]] = []
        source_tools: list[str] = []
        evidence: list[str] = []
        write_tool_evidence = False
        task_boundary_triage_required = False
        task_boundary_triage_summary: dict[str, Any] = {}
        consecutive_stagnant_rounds = 0
        convergence_stop_reason = ""

        def current_workspace_repair_summary(
            *,
            residual_errors: list[str] | None = None,
            deadline_detail: str = "",
        ) -> dict[str, Any]:
            partial_summary = {
                "attempted": bool(repair_rounds),
                "success": False,
                "revalidated": bool(rerun_results),
                "residual_error_count": len(residual_errors or []),
                "residual_errors": (residual_errors or [])[:10],
                "director_runtime_repair_coverage": executor._workspace_quality_repair_coverage_report(
                    residual_errors or []
                ),
                "plan_probe_preaudit": executor._workspace_quality_repair_plan_probe_report(residual_errors or []),
                "source_tools": list(dict.fromkeys(source_tools)),
                "tool_results": len(repair_results),
                "write_tool_evidence": write_tool_evidence,
                "artifact_quality_errors": repair_errors[:10],
                "evidence": evidence[:12],
                "max_rounds": max_rounds,
                "rounds": repair_rounds,
                "consecutive_stagnant_rounds": consecutive_stagnant_rounds,
                "convergence_stop_reason": convergence_stop_reason,
            }
            if deadline_detail:
                partial_summary["deadline_blocker"] = deadline_detail
            if task_boundary_triage_required:
                partial_summary.update(
                    {
                        "task_boundary_triage_required": True,
                        "success_reason": "task_boundary_interface_discrepancy_required",
                        "plan_probe_preaudit": task_boundary_triage_summary.get("plan_probe_preaudit"),
                        "interface_discrepancy_evidence": task_boundary_triage_summary.get(
                            "interface_discrepancy_evidence"
                        ),
                    }
                )
            return partial_summary

        for round_index in range(max_rounds):
            if latest_check_results and all(bool(item.get("passed")) for item in latest_check_results):
                break
            repair_errors = executor._workspace_quality_repair_errors(latest_check_results or results)
            if not repair_errors:
                break
            before_signature = executor._workspace_quality_diagnostic_signature(repair_errors)
            round_repair_results, round_summary = await executor._apply_workspace_quality_deterministic_repairs(
                run=run,
                artifact_quality_errors=repair_errors,
                repair_attempt=round_index + 1,
            )
            round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                dict(round_summary)
            )
            round_repair_evidence = executor._workspace_quality_repair_evidence(round_repair_results)
            round_write_tool_evidence = any(
                executor._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
            ) or bool(round_summary.get("write_tool_evidence"))
            raw_round_summary_evidence = round_summary.get("evidence")
            if isinstance(raw_round_summary_evidence, list | tuple):
                round_repair_evidence.extend(
                    str(item) for item in raw_round_summary_evidence if str(item or "").strip()
                )
            if round_requires_task_boundary_triage:
                interface_discrepancy_evidence = executor._workspace_quality_interface_discrepancy_evidence(
                    dict(round_summary),
                    repair_errors,
                )
                if executor._workspace_quality_interface_discrepancy_allows_director_retry(
                    interface_discrepancy_evidence
                ):
                    deterministic_noop_summary = dict(round_summary)
                    deadline_detail = workspace_repair_deadline_blocker(
                        f"before_interface_discrepancy_llm_repair_round_{round_index + 1}"
                    )
                    if deadline_detail:
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_repair_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(
                                residual_errors=repair_errors,
                                deadline_detail=deadline_detail,
                            ),
                        )
                    round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                        run=run,
                        context=context,
                        artifact_quality_errors=repair_errors,
                        repair_attempt=round_index + 1,
                        interface_discrepancy_evidence=interface_discrepancy_evidence,
                    )
                    if not round_repair_results:
                        round_summary = dict(round_summary)
                        round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                    round_requires_task_boundary_triage = (
                        executor._workspace_quality_summary_requires_task_boundary_triage(dict(round_summary))
                    )
                    round_repair_evidence = executor._workspace_quality_repair_evidence(round_repair_results)
                    round_write_tool_evidence = any(
                        executor._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
                    )
            if (
                not round_requires_task_boundary_triage
                and round_repair_results
                and not round_write_tool_evidence
            ):
                # Logs, coverage notes, or failed/no-op tool rows are evidence
                # of an attempt, not evidence of repair progress. Only an
                # authoritative workspace mutation may suppress the same-task
                # LLM edit fallback. r46 returned a deterministic source_tool
                # plus evidence but mutated nothing; this old condition skipped
                # the LLM twice and tripped stagnation with the failing source
                # untouched.
                deterministic_noop_summary = dict(round_summary)
                deadline_detail = workspace_repair_deadline_blocker(f"before_llm_repair_round_{round_index + 1}")
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=context,
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                )
                if not round_repair_results:
                    round_summary = dict(round_summary)
                    round_summary["deterministic_no_materialized_evidence"] = deterministic_noop_summary
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            elif not round_requires_task_boundary_triage and not round_repair_results:
                deadline_detail = workspace_repair_deadline_blocker(f"before_llm_repair_round_{round_index + 1}")
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=context,
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                )
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            deferred_owner_targets = executor._workspace_quality_deferred_owner_targets(dict(round_summary))
            if deferred_owner_targets:
                # Target inference happens inside the Director adapter after the
                # first TaskRuntime owner has been claimed. If the precise
                # verifier targets belong to a different PM task, the adapter
                # correctly refuses the write and returns structured ownership
                # evidence. Rebind once to that exact owner instead of failing
                # the chain or restarting PM/CE.
                deferred_summary = executor._workspace_quality_repair_summary_projection(
                    dict(round_summary),
                    repair_errors,
                )
                deadline_detail = workspace_repair_deadline_blocker(
                    f"before_deferred_owner_rebind_round_{round_index + 1}"
                )
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_repair_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(
                            residual_errors=repair_errors,
                            deadline_detail=deadline_detail,
                        ),
                    )
                round_repair_results, round_summary = await executor._apply_workspace_quality_llm_repairs(
                    run=run,
                    context=context,
                    artifact_quality_errors=repair_errors,
                    repair_attempt=round_index + 1,
                    owner_target_files=deferred_owner_targets,
                )
                round_summary = dict(round_summary)
                round_summary["deferred_owner_rebind"] = {
                    "attempted": True,
                    "target_files": deferred_owner_targets,
                    "previous_repair": deferred_summary,
                }
                round_requires_task_boundary_triage = executor._workspace_quality_summary_requires_task_boundary_triage(
                    dict(round_summary)
                )
            cpp_post_repair_results: list[dict[str, Any]] = []
            if not round_requires_task_boundary_triage:
                cpp_post_repair_results = await asyncio.to_thread(executor._apply_workspace_quality_cpp_post_repairs)
            if cpp_post_repair_results:
                round_repair_results.extend(cpp_post_repair_results)
                round_summary = dict(round_summary)
                round_summary_tools = [
                    str(item) for item in round_summary.get("source_tools", []) if str(item or "").strip()
                ]
                if "deterministic_cpp_post_repair" not in round_summary_tools:
                    round_summary_tools.append("deterministic_cpp_post_repair")
                round_summary["source_tools"] = round_summary_tools
            repair_results.extend(round_repair_results)
            normalized_round_summary = dict(round_summary)
            round_source_tools = [
                str(item) for item in normalized_round_summary.get("source_tools", []) if str(item or "").strip()
            ]
            round_evidence = executor._workspace_quality_repair_evidence(round_repair_results)
            round_write_tool_evidence = any(
                executor._workspace_quality_repair_result_has_mutation(item) for item in round_repair_results
            ) or bool(normalized_round_summary.get("write_tool_evidence"))
            raw_round_summary_evidence = normalized_round_summary.get("evidence")
            if isinstance(raw_round_summary_evidence, list | tuple):
                round_evidence.extend(str(item) for item in raw_round_summary_evidence if str(item or "").strip())
            source_tools.extend(round_source_tools)
            evidence.extend(round_evidence)
            write_tool_evidence = write_tool_evidence or round_write_tool_evidence
            summary_projection = executor._workspace_quality_repair_summary_projection(
                normalized_round_summary,
                repair_errors,
            )
            round_payload: dict[str, Any] = {
                "round": round_index + 1,
                "attempted": True,
                "artifact_quality_errors": repair_errors[:10],
                "director_runtime_repair_coverage": executor._workspace_quality_repair_coverage_report(repair_errors),
                "plan_probe_preaudit": executor._workspace_quality_repair_plan_probe_report(repair_errors),
                "tool_results": len(round_repair_results),
                "source_tools": round_source_tools,
                "write_tool_evidence": round_write_tool_evidence,
                "evidence": round_evidence,
            }
            if summary_projection:
                round_payload["repair_summary"] = summary_projection
                if round_requires_task_boundary_triage:
                    task_boundary_triage_required = True
                    task_boundary_triage_summary = summary_projection
                    round_payload["task_boundary_triage_required"] = True
            repair_rounds.append(round_payload)
            if round_requires_task_boundary_triage:
                convergence_stop_reason = "task_boundary_triage_required"
                break
            if not round_write_tool_evidence:
                # A provider/tool result is attempt evidence, not delivery
                # progress.  In r51 an ``edit_file`` turn produced no physical
                # mutation, but the non-empty result list still caused
                # ``go test`` and ``go run`` to execute again.  The failed
                # attempt was then reopened for the next round, projecting an
                # active TaskRuntime row with an older failed settlement.
                #
                # Do not spend verifier budget until an authoritative effect
                # receipt proves a write.  Give the same Director task one
                # more edit opportunity, then stop after two consecutive
                # no-mutation rounds.  PM/CE are never restarted here.
                round_payload["verifier_effect"] = "no_op"
                round_payload["verifier_authoritative_success"] = False
                round_payload["diagnostic_count_before"] = len(before_signature)
                round_payload["diagnostic_count_after"] = len(before_signature)
                round_payload["residual_errors_after"] = repair_errors[:10]
                projected_summary_raw = round_payload.get("repair_summary")
                if isinstance(projected_summary_raw, dict):
                    projected_summary: dict[str, Any] = projected_summary_raw
                    projected_summary["claimed_success_before_revalidation"] = bool(projected_summary.get("success"))
                    projected_summary["success"] = False
                    projected_summary["success_authority"] = "post_repair_verifier"
                    projected_summary["verifier_effect"] = "no_op"
                consecutive_stagnant_rounds += 1
                if consecutive_stagnant_rounds >= 2:
                    convergence_stop_reason = "two_consecutive_no_mutation_repairs"
                    break
                convergence_stop_reason = "repair_produced_no_effect_retry_same_director_task"
                continue
            latest_check_results = []
            rerun_prepare_results = []
            rerun_results = []
            round_prepare_failed = False
            prepare_phase = "prepare_after_repair" if round_index == 0 else f"prepare_after_repair_{round_index + 1}"
            for command in prepare_commands:
                result, deadline_detail = await run_workspace_quality_command_with_deadline(command, prepare_phase)
                if deadline_detail:
                    return write_workspace_validation_failure(
                        "factory_quality_gate_workspace_checks_deadline_insufficient",
                        deadline_detail,
                        repair_override=current_workspace_repair_summary(residual_errors=repair_errors),
                    )
                results.append(result)
                rerun_prepare_results.append(result)
                if not bool(result.get("passed")):
                    round_prepare_failed = True
            phase = "check_after_repair" if round_index == 0 else f"check_after_repair_{round_index + 1}"
            if round_prepare_failed:
                for command in run_commands:
                    result = {
                        "command": command,
                        "phase": phase,
                        "exit_code": None,
                        "passed": False,
                        "error": "skipped because workspace validation preparation failed after repair",
                        "stdout_tail": "",
                        "stderr_tail": "",
                    }
                    results.append(result)
                    latest_check_results.append(result)
                    rerun_results.append(result)
            else:
                for command in run_commands:
                    result, deadline_detail = await run_workspace_quality_command_with_deadline(command, phase)
                    if deadline_detail:
                        return write_workspace_validation_failure(
                            "factory_quality_gate_workspace_checks_deadline_insufficient",
                            deadline_detail,
                            repair_override=current_workspace_repair_summary(residual_errors=repair_errors),
                        )
                    results.append(result)
                    latest_check_results.append(result)
                    rerun_results.append(result)
                round_depth_result = executor._workspace_quality.delivery_depth_contract_result(context)
                if round_depth_result is not None:
                    round_depth_result["phase"] = phase
                    results.append(round_depth_result)
                    latest_check_results.append(round_depth_result)
                    rerun_results.append(round_depth_result)
            round_residual_failures = [item for item in latest_check_results if not bool(item.get("passed"))]
            after_errors = (
                executor._workspace_quality_repair_errors(round_residual_failures) if round_residual_failures else []
            )
            after_signature = executor._workspace_quality_diagnostic_signature(after_errors)
            verifier_passed = not round_residual_failures
            repair_effect = executor._workspace_quality_repair_effect(
                before_signature=before_signature,
                after_signature=after_signature,
                verifier_passed=verifier_passed,
                write_tool_evidence=round_write_tool_evidence,
            )
            round_payload.update(
                {
                    "verifier_effect": repair_effect,
                    "verifier_authoritative_success": verifier_passed,
                    "diagnostic_count_before": len(before_signature),
                    "diagnostic_count_after": len(after_signature),
                    "residual_errors_after": after_errors[:10],
                }
            )
            projected_summary_raw = round_payload.get("repair_summary")
            if isinstance(projected_summary_raw, dict):
                projected_summary = projected_summary_raw
                projected_summary["claimed_success_before_revalidation"] = bool(projected_summary.get("success"))
                projected_summary["success"] = verifier_passed
                projected_summary["success_authority"] = "post_repair_verifier"
                projected_summary["verifier_effect"] = repair_effect
            if repair_effect in {"resolved", "progress"}:
                consecutive_stagnant_rounds = 0
            else:
                consecutive_stagnant_rounds += 1
            if verifier_passed:
                convergence_stop_reason = "verifier_passed"
                break
            if round_prepare_failed:
                convergence_stop_reason = "prepare_after_repair_failed"
                break
            if consecutive_stagnant_rounds >= 2:
                convergence_stop_reason = "two_consecutive_stagnant_repairs"
                break
        residual_failures = [item for item in latest_check_results if not bool(item.get("passed"))]
        residual_errors = executor._workspace_quality_repair_errors(residual_failures) if residual_failures else []
        residual_coverage_report = executor._workspace_quality_repair_coverage_report(residual_errors)
        repair_revalidated = bool(rerun_results)
        repair_summary = {
            "attempted": bool(repair_rounds),
            "success": repair_revalidated and not residual_failures,
            "revalidated": repair_revalidated,
            "residual_error_count": len(residual_failures),
            "residual_errors": residual_errors[:10],
            "director_runtime_repair_coverage": residual_coverage_report,
            "plan_probe_preaudit": executor._workspace_quality_repair_plan_probe_report(residual_errors),
            "source_tools": list(dict.fromkeys(source_tools)),
            "tool_results": len(repair_results),
            "write_tool_evidence": write_tool_evidence,
            "artifact_quality_errors": repair_errors[:10],
            "evidence": evidence[:12],
            "max_rounds": max_rounds,
            "rounds": repair_rounds,
            "consecutive_stagnant_rounds": consecutive_stagnant_rounds,
            "convergence_stop_reason": convergence_stop_reason,
        }
        if task_boundary_triage_required:
            repair_summary.update(
                {
                    "task_boundary_triage_required": True,
                    "success_reason": "task_boundary_interface_discrepancy_required",
                    "plan_probe_preaudit": task_boundary_triage_summary.get("plan_probe_preaudit"),
                    "interface_discrepancy_evidence": task_boundary_triage_summary.get(
                        "interface_discrepancy_evidence"
                    ),
                }
            )

    effective_results = rerun_results if rerun_results else results
    if rerun_results:
        effective_results = rerun_prepare_results + rerun_results

    payload_warnings = []
    if bool(repair_summary.get("task_boundary_triage_required")):
        payload_warnings.append("task_boundary_interface_discrepancy_required")

    payload = {
        "schema_version": "factory.workspace_quality_checks.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "factory_stage_executor",
        "factory_run_id": run.id,
        "workspace": str(executor.workspace),
        "passed": all(bool(item.get("passed")) for item in effective_results),
        "commands": results,
        # Preserve every physical attempt above for audit, but project only the
        # terminal verifier epoch into Run Ledger outcome authority.  A failed
        # pre-repair command is immutable history; once the same verifier is
        # rerun successfully it must not keep the repaired delivery red.
        "effective_commands": effective_results,
        "repair": repair_summary,
    }
    if payload_warnings:
        payload["warnings"] = payload_warnings
    artifact = executor._write_workspace_validation_artifact(run, context, payload)
    return bool(payload["passed"]), artifact
