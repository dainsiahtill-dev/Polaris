"""Director-stage materialization quality settle implementation.

Holds the materialization-settle method cluster using the impl-passing
pattern: each function takes ``executor`` (the original ``self``) as its first
parameter so it can reach back into the class for shared state and helper
methods. Behavior is preserved verbatim.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
)

from . import factory_stage_helpers as helpers
from .factory_run_models import (
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    FactoryRun,
)
from .run_ledger import load_run_ledger_projection

logger = logging.getLogger(__name__)

_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3


def _director_stage_should_run_materialization_quality_settle(
    executor,
    *,
    stage_status: str,
    error_code: str,
) -> bool:
    """Whether director_dispatch should run a final materialization settle pass.

    Always run when the workspace already has delivery scaffolding (package /
    sources), including failed/timeout multi-task stages. Cancelled stages skip.
    """

    if str(stage_status or "").strip().lower() == "cancelled":
        return False
    if (executor.workspace / "package.json").is_file():
        return True
    if any(executor.workspace.rglob("*.ts")) or any(executor.workspace.rglob("*.tsx")):
        return True
    if any(executor.workspace.rglob("*.py")) or any(executor.workspace.rglob("*.go")):
        return True
    # Still settle on explicit multi-task incompleteness even if scan is empty
    # (defensive: path may be mid-write).
    code = str(error_code or "").strip().lower()
    return code in {
        "director.canonical_task_boundary_missing",
        "director.dispatch_timeout",
        "director.taskboard_not_converged",
        "director.execution_barrier_timeout",
    }


def _workspace_has_delivery_surface(executor) -> bool:
    """True when package + source surface exists (real-run-capable scaffold)."""

    if not (executor.workspace / "package.json").is_file():
        return False
    return (
        any(executor.workspace.rglob("*.ts"))
        or any(executor.workspace.rglob("*.tsx"))
        or any(executor.workspace.rglob("*.py"))
    )


def _recover_director_stage_authority_after_delivery_settle(
    executor,
    *,
    run: FactoryRun,
    context: dict[str, Any],
    prior_authority: helpers.CanonicalFactoryAuthority,
) -> helpers.CanonicalFactoryAuthority | None:
    """Re-evaluate Director authority from canonical post-settle facts.

    TaskRuntime history remains immutable. Recovery is allowed only when
    every non-completed PM-contract task is terminal and canonical
    TaskBoundary evidence independently proves ``completed_verified``.
    Active, blocked, disk-only, or synthetic evidence remains fail-closed.
    """

    if prior_authority.director_stage_authorized:
        return prior_authority
    # Re-read only canonical owner facts after settle. TaskRuntime history
    # remains immutable; recovery is allowed solely when every contract task
    # has a canonical completed_verified boundary with ledger coordinates and
    # evidence, while every non-completed runtime row is terminal. No disk
    # scan or synthetic verdict may authorize this transition.
    try:
        projection = executor._canonical_factory_projection(run, context)
        latest_authority = helpers.evaluate_canonical_factory_authority(projection)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("Director stage authority re-eval after settle failed: %s", exc)
        return None
    return helpers.recover_terminal_runtime_delivery_authority(
        projection,
        latest_authority,
    )


def _seal_director_stage_missing_tool_lifecycles(
    executor,
    *,
    run: FactoryRun,
    incomplete_task_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """R177/M06: seal blocked lifecycle for claimed materialization without tools.

    Multi-task timeout leaves TASK-N claimed in TaskRuntime (tool-lifecycle
    requirement via director_materialization_claimed) but never reaches
    execute_method's no_materialized_changes seal. Projection then reports
    TOOL_LIFECYCLE_MISSING even though claim/fail facts exist. Append one
    blocked incomplete receipt per missing required task so integrity can
    distinguish incomplete work from true missing evidence.

    Complexity:
        O(t + o) over tool-lifecycle events and requirement obligations.
    """

    from polaris.cells.control_plane.run_ledger.public import (
        AppendToolCallLifecycleEventCommandV1,
        append_tool_call_lifecycle_event,
        build_claimed_materialization_without_tool_lifecycle_receipt,
    )

    try:
        projection = load_run_ledger_projection(
            executor.workspace,
            run_id=str(run.id or "").strip(),
            factory_run_id=str(run.id or "").strip(),
            project_id="",
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Director stage lifecycle seal skipped: projection unavailable for run %s: %s",
            run.id,
            exc,
        )
        return {
            "ok": False,
            "reason": "projection_unavailable",
            "detail": f"{type(exc).__name__}: {exc}",
            "sealed_count": 0,
            "missing_before": [],
        }

    tool_lifecycle = projection.get("tool_lifecycle")
    lifecycle_map = tool_lifecycle if isinstance(tool_lifecycle, Mapping) else {}
    requirement_projection = lifecycle_map.get("requirement_projection")
    requirement_map = requirement_projection if isinstance(requirement_projection, Mapping) else {}
    missing_raw = lifecycle_map.get("missing_required_task_keys")
    if not isinstance(missing_raw, list) or not missing_raw:
        missing_raw = requirement_map.get("missing_required_task_keys")
    missing_keys = [
        str(item or "").strip()
        for item in (missing_raw if isinstance(missing_raw, list) else [])
        if str(item or "").strip()
    ]
    if not missing_keys:
        return {
            "ok": True,
            "reason": "no_missing_required_task_keys",
            "detail": "all claimed materialization tasks already have lifecycle evidence",
            "sealed_count": 0,
            "missing_before": [],
        }

    obligations_raw = requirement_map.get("obligations")
    obligations = (
        [dict(item) for item in obligations_raw if isinstance(item, Mapping)]
        if isinstance(obligations_raw, list)
        else []
    )
    obligation_by_key: dict[str, dict[str, Any]] = {}
    for obligation in obligations:
        task_key = str(obligation.get("task_key") or obligation.get("task_id") or "").strip()
        if task_key:
            obligation_by_key[task_key] = obligation

    incomplete_tokens = {
        str(item or "").strip().lower() for item in (incomplete_task_ids or ()) if str(item or "").strip()
    }
    sealed: list[dict[str, str]] = []
    for task_key in missing_keys:
        obligation = obligation_by_key.get(task_key) or {}
        task_id = str(obligation.get("task_id") or task_key or "").strip()
        run_id = str(obligation.get("run_id") or "").strip() or f"director-stage-{run.id}"
        if not task_id:
            continue
        # Prefer sealing incomplete multi-task claims; still seal any missing
        # required key so TOOL_LIFECYCLE_MISSING cannot stick after stage exit.
        task_token = task_id.lower().removeprefix("task-").removeprefix("task_")
        if incomplete_tokens and task_token not in incomplete_tokens and task_id.lower() not in incomplete_tokens:
            # Still seal: missing required is itself the defect to close.
            pass
        lifecycle = build_claimed_materialization_without_tool_lifecycle_receipt(
            run_id=run_id,
            task_id=task_id,
            turn_id="",
            role="director",
            reason="director_stage_incomplete_without_tools",
            failure_class=FailureClassV1.INCOMPLETE_MATERIALIZATION.value,
        )
        try:
            append_tool_call_lifecycle_event(
                AppendToolCallLifecycleEventCommandV1(
                    workspace=str(executor.workspace),
                    run_id=run_id,
                    task_id=task_id,
                    turn_id="",
                    role="director",
                    lifecycle_receipt=lifecycle,
                    stage="director_dispatch",
                    project_id=task_id,
                    ok=False,
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "Director stage failed to seal missing tool lifecycle task=%s run=%s: %s",
                task_id,
                run_id,
                exc,
            )
            continue
        sealed.append({"task_id": task_id, "run_id": run_id, "task_key": task_key})

    return {
        "ok": bool(sealed),
        "reason": "director_stage_incomplete_without_tools",
        "detail": (
            f"sealed {len(sealed)} missing tool lifecycle receipt(s) for claimed "
            f"materialization without tools (missing_before={missing_keys})"
        ),
        "sealed_count": len(sealed),
        "missing_before": missing_keys,
        "sealed": sealed,
    }


def _collect_director_stage_materialization_diagnostics(executor) -> list[str]:
    """Collect physical settle-time diagnostics from source and real verifiers.

    Compiler-only revalidation is not convergence.  A Director candidate may
    make ``tsc`` green while leaving the declared package test or static HTML
    entrypoint physically broken.  Collect all three surfaces up front so the
    existing repair schedule can admit the corresponding deterministic
    candidates in one same-task settle attempt.

    R167/M10: when package.json declares typescript but ``node_modules/.bin/tsc``
    is absent (quality_gate never ran after director fail), best-effort
    ``npm install`` so settle can feed real TS diagnostics into the schedule.

    R184/M06: also surface missing package.json test entrypoints so the
    materialization schedule can plan smoke tests even when tsc is clean
    (L1-01 residual: real_run green, test_files=0).
    """

    diagnostics: list[str] = []
    package_json = executor.workspace / "package.json"
    if not package_json.is_file():
        return []
    try:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        diagnostics.extend(scan_workspace_artifact_quality(str(executor.workspace)))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Director stage materialization artifact scan skipped for %s: %s",
            executor.workspace,
            exc,
        )
    # Missing on-disk tests referenced by package.json scripts.test is a
    # first-class settle diagnostic (not a compiler error).
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        payload = None
    test_script = ""
    if isinstance(payload, Mapping):
        scripts = payload.get("scripts")
        if isinstance(scripts, Mapping):
            test_script = str(scripts.get("test") or "").strip()
        has_test_files = False
        tests_root = executor.workspace / "tests"
        if tests_root.is_dir():
            has_test_files = any(
                path.is_file()
                and path.suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
                and "test" in path.name.lower()
                for path in tests_root.rglob("*")
                if "node_modules" not in path.parts
            )
        if not has_test_files:
            has_test_files = any(
                path.is_file()
                and path.suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
                and path.name.endswith((".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js"))
                for path in executor.workspace.rglob("*")
                if "node_modules" not in path.parts and path.parts[:1] != (".git",)
            )
        if test_script and not has_test_files:
            diagnostics.append(
                "artifact_quality_error: missing test source files required by package.json "
                f"scripts.test ({test_script[:160]}); expected tests/verify.test.ts or equivalent"
            )
    node_modules = executor.workspace / "node_modules"
    tsc_bin = node_modules / ".bin" / "tsc"
    tsconfig = executor.workspace / "tsconfig.json"
    if tsconfig.is_file():
        if not tsc_bin.is_file():
            executor._ensure_director_stage_materialization_typescript_toolchain()
            tsc_bin = node_modules / ".bin" / "tsc"
        if tsc_bin.is_file():
            try:
                completed = subprocess.run(
                    [str(tsc_bin), "-p", "tsconfig.json", "--noEmit"],
                    cwd=str(executor.workspace),
                    capture_output=True,
                    text=True,
                    timeout=90,
                    check=False,
                )
            except (OSError, TimeoutError, ValueError):
                completed = None
            if completed is not None:
                combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
                diagnostics.extend(
                    line.strip() for line in combined.splitlines() if "error TS" in line or ": error " in line.lower()
                )

    if test_script:
        try:
            test_result = subprocess.run(
                ["npm", "test"],
                cwd=str(executor.workspace),
                capture_output=True,
                text=True,
                timeout=_WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
                check=False,
                env={**os.environ, "CI": "1"},
            )
        except subprocess.TimeoutExpired:
            diagnostics.append(
                f"artifact_quality_error: npm test timed out after {_WORKSPACE_VALIDATION_TIMEOUT_SECONDS}s"
            )
        except (OSError, TimeoutError, ValueError) as exc:
            diagnostics.append(f"artifact_quality_error: npm test could not execute: {type(exc).__name__}: {exc}")
        else:
            if int(test_result.returncode or 0) != 0:
                combined = f"{test_result.stdout or ''}\n{test_result.stderr or ''}".strip()
                # Coverage normalisation treats each line as a separate
                # diagnostic. Keep the command identity (``npm test``) and
                # terminal error (for example ``Could not find ...``) in
                # one record so an existing executable rule can match the
                # complete verifier fact instead of seeing unrelated lines.
                signal_lines = [
                    line.strip() for line in combined.splitlines() if line.strip() and not line.lstrip().startswith(">")
                ]
                compact = " ".join(signal_lines[-40:]) or " ".join(combined.split())
                bounded = compact[-_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS:]
                diagnostics.append(
                    f"artifact_quality_error: npm test failed (exit={test_result.returncode}): {bounded}"
                )

    return list(dict.fromkeys(item.strip() for item in diagnostics if str(item or "").strip()))[:200]


def _claim_director_stage_materialization_settle_attempt(
    executor,
    *,
    run_id: str,
) -> tuple[str, int, TaskRuntimeExecutionAttemptIdentityV1]:
    """Claim a short director TaskRuntime attempt so settle repairs can write.

    Repair execution is DEO-gated: without a canonical attempt identity the
    schedule only projects ``deo_deferred_repair_attempt_required`` and never
    materializes smoke/tsc patches (R165/r166 residual).
    """

    external_task_id = f"factory-director-mat-settle:{run_id}:{uuid.uuid4().hex[:12]}"
    # R190/M06: each settle attempt needs a fresh TaskRuntime row. A fixed
    # external_task_id was terminal-closed (completed/failed) after the first
    # director wave; QA rework → second director_dispatch then failed claim with
    # ``task_terminal`` and skipped deferred DEO commits (L1-01 r10:
    # diagnostics=5, tools=0, committed=0, settle_exception task_terminal).
    task_runtime = TaskRuntimeService(str(executor.workspace))
    row = task_runtime.ensure_task_row(
        external_task_id=external_task_id,
        subject="Director stage materialization quality settle",
        description=(
            "End-of-director_dispatch materialization quality settle for partial multi-task completion / stage timeout"
        ),
        metadata={
            "factory_run_id": run_id,
            "factory_stage": "director_dispatch",
            "role": "director",
            "execution_identity_required": True,
            "materialization_quality_settle": True,
            "settle_attempt_id": external_task_id,
        },
    )
    task_row_id = task_runtime.normalize_task_id(row.get("id"))
    if task_row_id is None:
        raise RuntimeError("director_stage_materialization_settle_task_id_invalid")
    binding = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(executor.workspace),
            task_id=external_task_id,
            factory_run_id=run_id,
        )
    )
    if not binding.ok:
        raise RuntimeError(f"director_stage_materialization_settle_binding_failed:{binding.code}")
    claim = task_runtime.claim_execution(
        task_row_id,
        worker_id="director",
        role_id="director",
        run_id=run_id,
        lease_ttl_seconds=300,
        selection_source="factory_stage_executor.director_stage_materialization_settle",
        external_task_id=external_task_id,
        context_summary="director_stage_materialization_quality_settle",
        metadata={
            "factory_run_id": run_id,
            "factory_stage": "director_dispatch",
            "materialization_quality_settle": True,
            "execution_identity_required": True,
        },
    )
    session = claim.get("session") if isinstance(claim, dict) else None
    attempt_record = claim.get("execution_attempt") if isinstance(claim, dict) else None
    if not isinstance(session, Mapping) or not isinstance(attempt_record, Mapping) or not bool(claim.get("success")):
        reason = str(claim.get("reason") or "unknown") if isinstance(claim, dict) else "invalid_claim_result"
        raise RuntimeError(f"director_stage_materialization_settle_claim_failed:{reason}")
    execution_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
    return external_task_id, task_row_id, execution_attempt


@staticmethod
def _settle_director_stage_materialization_attempt(
    executor,
    *,
    task_row_id: int,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
    stage_status: str,
    summary: str,
) -> dict[str, Any]:
    """Terminal-close settle claim and return authoritative TaskRuntime result.

    R184/M06: when settle finished without file mutations the previous path
    mapped non-success ``stage_status`` to outcome=``suspended``. That left
    the helper TaskRuntime row pending (L1-01 incomplete_task_ids=['5']) and
    blocked ``task_runtime_not_completed`` even after solid delivery + boundary
    recovery. Factory-owned settle claims must terminal-close:
    success → completed, failure → failed. Never suspend.
    """

    del task_row_id  # identity carries the private row id
    try:
        normalized = str(stage_status or "").strip().lower()
        outcome = executor._materialization_settle_attempt_outcome(stage_status)
        result = TaskRuntimeService(str(executor.workspace)).settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=execution_attempt.workspace,
                identity=execution_attempt,
                outcome=outcome,
                summary=str(summary or "director_stage_materialization_quality_settle")[:500],
                lock_timeout_seconds=5.0,
                metadata={
                    "factory_stage": "director_dispatch",
                    "materialization_quality_settle": True,
                    "settle_stage_status": normalized or "unknown",
                },
            )
        )
        if not bool(result.get("success")):
            logger.warning(
                "Director stage materialization settle attempt close failed: %s",
                result.get("reason") or "unknown",
            )
        return dict(result)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Director stage materialization settle attempt close failed: %s",
            exc,
        )
        return {
            "success": False,
            "reason": f"settle_exception:{type(exc).__name__}",
            "detail": str(exc)[:500],
        }


def _director_stage_materialization_settle_commit_context(
    executor,
    *,
    run: FactoryRun,
    run_id: str,
    diagnostics: list[str],
    factory_stage: str = "director_dispatch",
) -> dict[str, Any]:
    """Build DEO commit context with control-plane JobToken evidence (M06).

    Deferred materialization commit refuses synthetic attempt-only tokens.
    Mint a stage-scoped JobToken from factory run + CE blueprint surface so
    capability_audit.ok is true and execution_envelope_hash is bound.
    """

    from polaris.cells.control_plane.run_ledger.public import stable_hash
    from polaris.cells.factory.pipeline.internal.run_ledger import build_job_token_from_record

    normalized_stage = str(factory_stage or "").strip() or "director_dispatch"
    target_files = executor._director_stage_materialization_settle_target_files(diagnostics=diagnostics)
    blueprint_artifact, blueprint_text = executor._workspace_quality_repair_blueprint_evidence(run_id=run_id)
    project_id = str(getattr(run.config, "name", "") or "").strip() or run_id
    token_record: dict[str, Any] = {
        "target_files": target_files,
        "allowed_paths": target_files,
        "code_files": [path for path in target_files if path not in {"tests/verify.test.ts", "tests/smoke.test.ts"}],
        "contract_goal": f"{normalized_stage}_workspace_quality_repair:{run_id}",
        "brief": f"Factory {normalized_stage} workspace quality repair",
        "factory_run_id": run_id,
        "run_id": run_id,
        "project_id": project_id,
        "factory_workspace_quality_repair": {
            "run_id": run_id,
            "target_files": target_files,
            "ce_blueprint_artifact": blueprint_artifact,
        },
    }
    if blueprint_text:
        token_record["blueprint_id"] = blueprint_artifact or f"factory-blueprint:{run_id}"
        token_record["blueprints"] = [
            {
                "id": token_record["blueprint_id"],
                "artifact": blueprint_artifact,
                "evidence_chars": len(blueprint_text),
            }
        ]
        token_record["chief_engineer"] = {
            "blueprint_id": token_record["blueprint_id"],
            "artifact": blueprint_artifact,
        }
    else:
        # Still satisfy capability_audit CE source when live blueprint artifact
        # is unavailable at settle (multi-task timeout residual path).
        token_record["blueprint_id"] = f"factory-director-mat-settle:{run_id}"
        token_record["blueprints"] = [{"id": token_record["blueprint_id"], "source": "settle_stage"}]
        token_record["chief_engineer"] = {
            "blueprint_id": token_record["blueprint_id"],
            "source": "director_stage_materialization_settle",
        }

    job_token = build_job_token_from_record(
        token_record,
        run_id=run_id,
        project_id=project_id,
        stage=normalized_stage,
    ).to_dict()
    envelope_hash = stable_hash(
        {
            "schema_version": "factory.director_stage_materialization_settle_envelope.v1",
            "run_id": run_id,
            "stage": normalized_stage,
            "target_files": target_files,
            "token_id": str(job_token.get("token_id") or ""),
        }
    )
    job_token["execution_envelope_hash"] = envelope_hash
    token_hash = stable_hash(job_token)
    # Deferred DEO commit (_capability_token_from_context) requires root
    # capability_token_hash + envelope.authorization.capability_token_hash
    # matching stable_hash(token). Missing hash caused committed=0 with
    # silent skip ("authoritative write capability missing") on L1-01 R184.
    capability_audit = job_token.get("capability_audit")
    if not (isinstance(capability_audit, Mapping) and capability_audit.get("ok") is True):
        logger.warning(
            "Director stage materialization settle JobToken capability_audit not ok run=%s audit=%s",
            run_id,
            capability_audit,
        )

    write_paths = list(job_token.get("allowed_write_paths") or target_files)
    read_paths = list(job_token.get("allowed_read_paths") or write_paths)
    if not write_paths:
        write_paths = list(target_files)
    if not read_paths:
        read_paths = list(write_paths)
    # Keep token path lists authoritative for DEO capability equality checks.
    job_token["allowed_write_paths"] = write_paths
    job_token["allowed_read_paths"] = read_paths
    # Re-hash after path normalization so root hash matches the final token body.
    token_hash = stable_hash(job_token)
    authorization = {
        "capability_token_ref": str(job_token.get("token_id") or ""),
        "capability_token_hash": token_hash,
        "allowed_write_paths": list(write_paths),
        "allowed_read_paths": list(read_paths),
    }
    execution_envelope = {
        "envelope_hash": envelope_hash,
        "authorization": authorization,
        "stage": normalized_stage,
        "run_id": run_id,
    }
    return {
        "target_files": target_files,
        "allowed_paths": list(write_paths),
        "allowed_write_paths": list(write_paths),
        "allowed_read_paths": list(read_paths),
        "delivery_mode": "materialize_changes",
        "factory_stage": normalized_stage,
        "materialization_quality_settle": normalized_stage == "director_dispatch",
        "workspace_quality_repair": normalized_stage == "quality_gate",
        "capability_token_hash": token_hash,
        "job_token": job_token,
        "control_plane_job_token": job_token,
        "capability_token": job_token,
        "execution_envelope_hash": envelope_hash,
        "execution_envelope": execution_envelope,
        "director_execution_envelope": dict(execution_envelope),
        "task_execution_envelope": dict(execution_envelope),
    }


@staticmethod
async def _run_director_stage_materialization_quality_settle(
    executor,
    *,
    run: FactoryRun,
    stage_status: str,
    error_code: str,
) -> dict[str, Any]:
    """Run materialization quality once at the end of director_dispatch (R165/M06).

    Live residual: Director multi-task timeout left package.json + src on disk
    but skipped quality_gate, so smoke/tests and covered tsc repairs never ran.
    Writes require a claimed TaskRuntime execution attempt + DEO commit of
    deferred repair effects, plus control-plane JobToken evidence.
    """

    if not executor._director_stage_should_run_materialization_quality_settle(
        stage_status=stage_status,
        error_code=error_code,
    ):
        return {
            "ok": False,
            "reason": "settle_not_applicable",
            "detail": "workspace has no materializable surface or stage cancelled",
            "tool_result_count": 0,
            "diagnostic_count": 0,
        }
    # Compiler/verifier scans and deterministic repair planning are
    # synchronous and may take minutes on a freshly materialized project.
    # Keep them off the ASGI event loop so /health, runtime WebSocket, NATS
    # keepalives, and the runner's status reads remain live while Director
    # settles the owning task.
    diagnostics = await asyncio.to_thread(executor._collect_director_stage_materialization_diagnostics)
    run_id = str(run.id or "").strip() or "director-stage-settle"
    external_task_id = ""
    task_row_id: int | None = None
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None
    committed_receipts: list[dict[str, Any]] = []
    post_commit_diagnostics = list(diagnostics)
    deferred_candidates: list[Mapping[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    summary: Mapping[str, Any] = {}
    repair_round_count = 0
    try:
        from polaris.cells.roles.adapters.public import (
            commit_materialization_deferred_repairs,
        )
        from polaris.cells.runtime.task_runtime.public import (
            create_task_runtime_execution_attempt_authority,
        )

        external_task_id, task_row_id, execution_attempt = (
            executor._claim_director_stage_materialization_settle_attempt(run_id=run_id)
        )
        authority = create_task_runtime_execution_attempt_authority(execution_attempt)
        current_diagnostics = list(diagnostics)
        seen_diagnostic_signatures = {tuple(current_diagnostics)}
        for round_index in range(_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS):
            repair_round_count += 1
            round_tool_results, summary = await asyncio.to_thread(
                executor._apply_workspace_quality_repairs,
                run_id=run_id,
                artifact_quality_errors=current_diagnostics,
                task_id=external_task_id,
                execution_attempt=execution_attempt,
            )
            tool_results.extend(round_tool_results)
            commit_context = executor._director_stage_materialization_settle_commit_context(
                run=run,
                run_id=run_id,
                diagnostics=current_diagnostics,
            )
            round_candidates = [
                item
                for item in round_tool_results
                if isinstance(item, Mapping)
                and isinstance(item.get("result"), Mapping)
                and (
                    item["result"].get("deferred_request") is not None
                    or str(item["result"].get("status") or "").strip()
                    in {"deferred_repair_effects_pending", "deferred_command_effect_pending"}
                )
            ]
            deferred_candidates.extend(round_candidates)
            if not round_candidates:
                post_commit_diagnostics = current_diagnostics
                break

            # Revalidate after each effect. A newly exposed verifier layer
            # is replanned inside this same TaskRuntime attempt; PM/CE do not
            # restart for ordinary code/test defects. Repeated diagnostic
            # signatures and the shared round cap stop no-progress loops.
            round_committed = False
            for candidate_index, candidate in enumerate(round_candidates):
                candidate_receipts = await commit_materialization_deferred_repairs(
                    workspace=str(execution_attempt.workspace),
                    tool_results=[candidate],
                    execution_attempt=execution_attempt,
                    execution_attempt_authority=authority,
                    turn_id=(f"director-stage-mat-settle-{run_id}:round{round_index}:candidate{candidate_index}"),
                    context=commit_context,
                )
                committed_receipts.extend(candidate_receipts)
                if not any(
                    isinstance(item, Mapping) and executor._director_stage_materialization_receipt_succeeded(item)
                    for item in candidate_receipts
                ):
                    continue
                round_committed = True
                await asyncio.to_thread(executor._ensure_director_stage_materialization_typescript_toolchain)
                post_commit_diagnostics = await asyncio.to_thread(
                    executor._collect_director_stage_materialization_diagnostics
                )
                if not post_commit_diagnostics:
                    break
            if not post_commit_diagnostics:
                break
            post_signature = tuple(post_commit_diagnostics)
            if not round_committed or post_signature in seen_diagnostic_signatures:
                break
            seen_diagnostic_signatures.add(post_signature)
            current_diagnostics = list(post_commit_diagnostics)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Director stage materialization quality settle failed for run %s: %s",
            run.id,
            exc,
        )
        if task_row_id is not None and execution_attempt is not None:
            executor._settle_director_stage_materialization_attempt(
                task_row_id=task_row_id,
                execution_attempt=execution_attempt,
                stage_status="failed",
                summary=f"settle_exception:{type(exc).__name__}",
            )
        return {
            "ok": False,
            "reason": "settle_exception",
            "detail": f"{type(exc).__name__}: {exc}",
            "tool_result_count": 0,
            "diagnostic_count": len(diagnostics),
        }
    summary_dict = dict(summary) if isinstance(summary, Mapping) else {}
    deferred_expected = bool(deferred_candidates)
    successful_receipts = [
        dict(item)
        for item in committed_receipts
        if isinstance(item, Mapping) and executor._director_stage_materialization_receipt_succeeded(item)
    ]
    failed_receipts = [
        dict(item)
        for item in committed_receipts
        if not isinstance(item, Mapping) or not executor._director_stage_materialization_receipt_succeeded(item)
    ]
    # Partial DEO failures remain evidence, but must not erase a verified
    # successful repair candidate.  The post-commit verifier is the
    # authority for whether the same Director task still needs local rework.
    missing_commit_receipt = deferred_expected and not successful_receipts
    verifier_residual = bool(post_commit_diagnostics)
    commit_failed = missing_commit_receipt or verifier_residual
    mutated = bool(successful_receipts) or any(
        executor._workspace_quality_repair_result_has_mutation(dict(item))
        for item in tool_results
        if isinstance(item, Mapping)
    )
    settlement_result: dict[str, Any] = {"success": True}
    if task_row_id is not None and execution_attempt is not None:
        settlement_result = executor._settle_director_stage_materialization_attempt(
            task_row_id=task_row_id,
            execution_attempt=execution_attempt,
            stage_status="failed" if commit_failed else "success",
            summary=(
                "director_stage_materialization_quality_settle "
                f"mutated={mutated} committed={len(successful_receipts)} "
                f"failed={len(failed_receipts)} tools={len(tool_results)}"
            ),
        )
    settlement_failed = settlement_result.get("success") is not True
    if commit_failed or settlement_failed:
        failure_reason = (
            "deferred_repair_commit_failed"
            if missing_commit_receipt
            else "materialization_verifier_residual"
            if verifier_residual
            else "settle_attempt_close_failed"
        )
        return {
            "ok": False,
            "reason": failure_reason,
            "detail": (
                "materialization settle did not reach a verifier-clean terminal state "
                f"(expected={deferred_expected}, receipts={len(committed_receipts)}, "
                f"failed={len(failed_receipts)}, residual={len(post_commit_diagnostics)}, settle_reason="
                f"{settlement_result.get('reason') or 'unknown'!s})"
            ),
            "tool_result_count": len(tool_results),
            "committed_receipt_count": len(successful_receipts),
            "failed_receipt_count": len(failed_receipts),
            "diagnostic_count": len(diagnostics),
            "post_commit_diagnostic_count": len(post_commit_diagnostics),
            "post_commit_diagnostics": post_commit_diagnostics[:20],
            "repair_round_count": repair_round_count,
            "mutated": mutated,
            "external_task_id": external_task_id,
        }
    return {
        "ok": True,
        "reason": "director_stage_settle",
        "detail": (
            "materialization quality schedule + deferred DEO commit at end of director_dispatch "
            f"(diagnostics={len(diagnostics)}, tools={len(tool_results)}, "
            f"committed={len(committed_receipts)}, mutated={mutated})"
        ),
        "tool_result_count": len(tool_results),
        "committed_receipt_count": len(successful_receipts),
        "failed_receipt_count": len(failed_receipts),
        "diagnostic_count": len(diagnostics),
        "post_commit_diagnostic_count": len(post_commit_diagnostics),
        "repair_round_count": repair_round_count,
        "mutated": mutated,
        "external_task_id": external_task_id,
        "summary_keys": sorted(str(key) for key in summary_dict)[:24],
    }
