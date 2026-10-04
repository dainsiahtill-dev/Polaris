"""Director-stage materialization quality settle implementation.

Holds the materialization-settle method cluster using the impl-passing
pattern: each function takes ``executor`` (the original ``self``) as its first
parameter so it can reach back into the class for shared state and helper
methods. Behavior is preserved verbatim.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import subprocess
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import partial
from pathlib import PurePosixPath
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
from .factory_node_dependencies import node_dependency_readiness
from .factory_run_models import (
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    FactoryRun,
)
from .run_ledger import load_run_ledger_projection

logger = logging.getLogger(__name__)

_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3


def _deferred_repair_forward_target_paths(
    tool_results: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Return safe workspace-relative targets proven by typed repair plans.

    The deterministic repair runtime may derive concrete CE-owned files that
    are not present in the generic PM target row. Those paths become routing
    evidence after planning, not candidate-local JobToken grants. Never trust arbitrary result text:
    consume only typed plan effects (or the deferred request's own allowed
    paths), reject absolute/traversal/runtime paths, and preserve order.
    """

    paths: list[str] = []
    seen: set[str] = set()
    for item in tool_results:
        result = item.get("result") if isinstance(item, Mapping) else None
        if not isinstance(result, Mapping):
            continue
        deferred = result.get("deferred_request")
        plan = getattr(deferred, "plan", None) if deferred is not None else None
        effects = getattr(plan, "effects", None) if plan is not None else None
        candidates: list[object] = []
        if isinstance(effects, (list, tuple)):
            candidates.extend(
                getattr(effect, "target_path", "")
                for effect in effects
                if str(getattr(effect, "contingency_kind", "") or "").strip() == "forward"
            )
        if not candidates:
            candidates.extend(getattr(deferred, "allowed_paths", None) or result.get("allowed_paths") or ())
        for raw_path in candidates:
            token = str(raw_path or "").replace("\\", "/").strip()
            if not token or "\n" in token or "\r" in token:
                raise ValueError("repair_candidate_path_invalid")
            pure = PurePosixPath(token)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError("repair_candidate_path_invalid")
            normalized = pure.as_posix()
            while normalized.startswith("./"):
                normalized = normalized[2:]
            if not normalized or normalized == "." or normalized.startswith(".polaris/"):
                raise ValueError("repair_candidate_path_invalid")
            if normalized in seen:
                continue
            seen.add(normalized)
            paths.append(normalized)
    return paths


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


def _collect_director_stage_materialization_diagnostics(
    executor,
    *,
    run: FactoryRun | None = None,
    preparation_results: list[dict[str, Any]] | None = None,
) -> list[str]:
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
    try:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        diagnostics.extend(scan_workspace_artifact_quality(str(executor.workspace)))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Director stage materialization artifact scan skipped for %s: %s",
            executor.workspace,
            exc,
        )
    package_json = executor.workspace / "package.json"
    if not package_json.is_file():
        # Go/Rust/Python workspaces have no package.json. Live L1-10 returned
        # diagnostics=0 / tools=0 here while `go test -count=0` still showed
        # undefined selectors, so selector repair never entered settle.
        return diagnostics
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
        if not node_dependency_readiness(executor.workspace).ready:
            preparation = (
                executor._ensure_director_stage_materialization_typescript_toolchain(run=run)
                if run is not None
                else executor._ensure_director_stage_materialization_typescript_toolchain()
            )
            if preparation_results is not None and isinstance(preparation, Mapping):
                preparation_results.append(dict(preparation))
            if not isinstance(preparation, Mapping) or preparation.get("passed") is not True:
                diagnostics.append(
                    "artifact_quality_error: dependency_preparation_failed: "
                    + json.dumps(
                        dict(preparation)
                        if isinstance(preparation, Mapping)
                        else {"error": "physical_outcome_unproved"},
                        ensure_ascii=False,
                        default=str,
                    )[:_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS]
                )
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
    deferred_tool_results: Sequence[Mapping[str, Any]] = (),
    repair_task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the original CE authority; a repair plan cannot grant paths."""
    from polaris.cells.chief_engineer.blueprint.public import validate_director_handoff_from_payload
    from polaris.cells.control_plane.run_ledger.public import stable_hash
    from polaris.cells.director.tasking.public import (
        apply_task_execution_strategy_overrides,
        resolve_task_execution_profile,
        resolve_task_execution_strategy,
    )
    from polaris.kernelone.quality.scope_authority import path_matches_any_declared_scope_candidate

    del diagnostics  # Diagnostic discovery chooses an owner, never permissions.
    if not isinstance(repair_task, Mapping):
        raise ValueError("repair_owner_authority_required")
    owner = deepcopy(dict(repair_task))
    task_id = str(owner.get("task_id") or owner.get("external_task_id") or owner.get("id") or "").strip()
    handoff = validate_director_handoff_from_payload(str(executor.workspace), owner, require_strict=True)
    projection = handoff.get("task_completion_projection")
    token = handoff.get("capability_token")
    if (
        handoff.get("allowed") is not True
        or not isinstance(projection, Mapping)
        or projection.get("run_id") != run_id
        or projection.get("task_id") != task_id
        or not isinstance(token, Mapping)
        or token != handoff.get("job_token")
        or str(token.get("factory_run_id") or token.get("run_id") or "") != run_id
    ):
        raise ValueError("repair_owner_authority_invalid")
    metadata_raw = owner.get("metadata")
    metadata = deepcopy(dict(metadata_raw)) if isinstance(metadata_raw, Mapping) else {}
    target_files = list(owner.get("target_files") or metadata.get("target_files") or [])
    context: dict[str, Any] = {
        "workspace": str(executor.workspace),
        "run_id": run_id,
        "factory_run_id": run_id,
        "task_id": task_id,
        "target_files": target_files,
        "delivery_mode": "materialize_changes",
        "factory_stage": factory_stage,
        "task_completion_projection": deepcopy(dict(projection)),
        "capability_token_hash": stable_hash(token),
    }
    for key in ("job_token", "control_plane_job_token", "capability_token"):
        context[key] = deepcopy(dict(token))
        metadata[key] = deepcopy(dict(token))
    for key in ("director_execution_envelope", "task_execution_envelope", "execution_envelope"):
        if key in owner:
            context[key] = deepcopy(owner[key])
    root_constraints = {
        key: deepcopy(owner[key])
        for key in ("allowed_write_paths", "allowed_read_paths", "allowed_commands")
        if key in owner
    }
    if root_constraints:
        # A separate admitted ceiling prevents nested metadata overriding a
        # root-level explicit denial. This derives restrictions, never grants.
        context["metadata"] = {"execution_envelope": {"authorization": root_constraints}}
    metadata.update({"workspace": str(executor.workspace), "task_id": task_id, "run_id": run_id})
    profile = resolve_task_execution_profile(
        subject=str(owner.get("subject") or owner.get("title") or "Director repair"),
        description=str(owner.get("description") or ""),
        metadata=metadata,
        target_files=target_files,
        scope_paths=owner.get("scope_paths"),
        workspace=str(executor.workspace),
    )
    strategy = resolve_task_execution_strategy(profile, metadata=metadata)
    apply_task_execution_strategy_overrides(context=context, metadata=metadata, profile=profile, strategy=strategy)
    context.pop("metadata", None)
    envelope = context["director_execution_envelope"]
    authorization = envelope["authorization"]
    write_paths = list(authorization.get("allowed_write_paths") or [])
    if not write_paths or write_paths != list(token.get("allowed_write_paths") or []):
        raise ValueError("repair_owner_scope_binding_conflict")
    candidates = _deferred_repair_forward_target_paths(deferred_tool_results)
    if any(not path_matches_any_declared_scope_candidate(path, write_paths) for path in candidates):
        raise ValueError("repair_candidate_outside_owner_scope")
    context.update(
        {
            "allowed_paths": list(write_paths),
            "allowed_write_paths": list(write_paths),
            "allowed_read_paths": list(authorization.get("allowed_read_paths") or []),
            "execution_envelope": deepcopy(envelope),
            "execution_envelope_hash": envelope["envelope_hash"],
            "materialization_quality_settle": factory_stage == "director_dispatch",
            "workspace_quality_repair": factory_stage == "quality_gate",
        }
    )
    return context


def _director_stage_deferred_repair_owner_targets(
    executor: Any,
    *,
    run: FactoryRun,
    run_id: str,
    repair_task: Mapping[str, Any],
    candidate: Mapping[str, Any],
    owner_context: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Route foreign paths from immutable CE ownership, never from plan grants.

    A mixed plan is not split or rebound. Only its routing paths survive; the
    original owner must claim a new attempt and plan again from current bytes.
    """
    from .factory_workspace_quality_impl import _workspace_quality_frozen_ce_owner_task

    task_id = str(owner_context["task_id"])
    projection = owner_context["task_completion_projection"]
    owned_paths = {
        str(item.get("path") or "")
        for item in projection.get("owned_artifacts", ())
        if isinstance(item, Mapping) and item.get("owner_task_id") == task_id
    }
    paths = _deferred_repair_forward_target_paths([candidate])
    foreign_paths = [path for path in paths if path not in owned_paths]
    if not foreign_paths:
        return {}
    matches: dict[str, set[str]] = {path: set() for path in foreign_paths}
    load_tasks = getattr(executor, "_load_pm_plan_tasks", None)
    canonical_tasks = load_tasks("tasks/plan.json") if callable(load_tasks) else ()
    for index, canonical_task in enumerate(canonical_tasks, start=1):
        owner_id = executor._task_id(canonical_task, index)
        if not owner_id or owner_id.startswith("factory-"):
            continue
        owner = _workspace_quality_frozen_ce_owner_task(
            executor, run_id=run_id, task_id=owner_id, canonical_task=canonical_task
        )
        metadata_raw = owner.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        completion = owner.get("task_completion_projection") or metadata.get("task_completion_projection")
        if (
            not isinstance(completion, Mapping)
            or completion.get("task_id") != owner_id
            or completion.get("run_id") != run_id
        ):
            continue
        matched_paths = {
            str(item.get("path") or "")
            for item in completion.get("owned_artifacts", ())
            if isinstance(item, Mapping) and item.get("owner_task_id") == owner_id
        } & set(foreign_paths)
        if matched_paths:
            # Validate original token/envelope before even deferring a route.
            # Claim and commit validate again; this lookup confers no authority.
            executor._director_stage_materialization_settle_commit_context(
                run=run, run_id=run_id, diagnostics=[], repair_task=owner
            )
            for path in matched_paths:
                matches[path].add(owner_id)
    routes: dict[str, list[str]] = {}
    for path, owners in matches.items():
        if not owners:
            raise ValueError(f"repair_candidate_outside_owner_scope:repair_candidate_owner_unknown:{path}")
        if len(owners) != 1:
            raise ValueError(f"repair_candidate_owner_ambiguous:{path}")
        owner_id = next(iter(owners))
        if owner_id == task_id:
            # Projection/token disagreement is not a reason to widen scope.
            executor._director_stage_materialization_settle_commit_context(
                run=run, run_id=run_id, diagnostics=[], repair_task=repair_task, deferred_tool_results=[candidate]
            )
            raise ValueError(f"repair_candidate_owner_scope_conflict:{path}")
        routes.setdefault(owner_id, []).append(path)
    return routes


async def _run_director_stage_materialization_quality_settle(
    executor,
    *,
    run: FactoryRun,
    stage_status: str,
    error_code: str,
) -> dict[str, Any]:
    """Settle materialization through serial original-owner continuations (R165/M06).

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
    dependency_preparation_results: list[dict[str, Any]] = []
    collect_diagnostics = executor._collect_director_stage_materialization_diagnostics
    # Prebind legacy injected collectors without retrying an executed callback.
    # Legacy calls have no new authority; their preparation keeps unknown locks protected.
    collector_signature = inspect.signature(collect_diagnostics)
    if {"run", "preparation_results"}.issubset(collector_signature.parameters):
        collector_signature.bind(run=run, preparation_results=dependency_preparation_results)
        collect_diagnostics = partial(collect_diagnostics, run=run, preparation_results=dependency_preparation_results)
    diagnostics = await asyncio.to_thread(collect_diagnostics)
    run_id = str(run.id or "").strip() or "director-stage-settle"
    external_task_id = ""
    task_row_id: int | None = None
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None
    committed_receipts: list[dict[str, Any]] = []
    post_commit_diagnostics = list(diagnostics)
    deferred_candidates: list[Mapping[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    summary: Mapping[str, Any] = {}
    repair_task: Mapping[str, Any] | None = None
    artifact_receipts: tuple[dict[str, str], ...] = ()
    registered_artifacts: dict[tuple[str, str], dict[str, str]] = {}
    deferred_owner_targets: dict[str, list[str]] = {}
    owners_requiring_revalidation: dict[str, list[str]] = {}
    owner_routing_residuals: list[dict[str, Any]] = []
    seen_owner_frontiers: set[tuple[str, tuple[str, ...]]] = set()
    owner_revalidation = False
    attempt_receipt_start = 0
    repair_round_count = 0
    heartbeat_stop = asyncio.Event()
    heartbeat_failures: list[dict[str, Any]] = []
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        from polaris.cells.roles.adapters.public import (
            commit_materialization_deferred_repairs,
        )
        from polaris.cells.runtime.task_runtime.public import (
            create_task_runtime_execution_attempt_authority,
        )

        from .factory_workspace_quality_impl import (
            _claim_workspace_quality_repair_attempt,
            _record_workspace_quality_repair_artifact_receipts,
            _run_workspace_quality_repair_heartbeat,
            _stop_workspace_quality_repair_heartbeat,
            _task_completion_projection_from_repair_task,
            _workspace_quality_causal_repair_target_files,
        )

        owner_targets = _workspace_quality_causal_repair_target_files(
            executor,
            artifact_quality_errors=diagnostics,
        ) or executor._workspace_quality_repair_diagnostic_target_files(diagnostics)
        if not owner_targets:
            owner_targets = executor._workspace_quality_repair_target_files()
        external_task_id, task_row_id, execution_attempt, repair_task = (
            executor._claim_workspace_quality_repair_attempt(
                run=run,
                repair_attempt=1,
                target_files=owner_targets,
            )
        )
        # Validate original CE authority before the planner, not merely at commit.
        owner_context = executor._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=run_id,
            diagnostics=diagnostics,
            repair_task=repair_task,
        )
        authority = create_task_runtime_execution_attempt_authority(execution_attempt)
        heartbeat_task = asyncio.create_task(
            _run_workspace_quality_repair_heartbeat(
                authority,
                stop=heartbeat_stop,
                failures=heartbeat_failures,
                context_summary="director_stage_original_owner_repair",
            )
        )
        current_diagnostics = list(diagnostics)
        while True:
            seen_owner_frontiers.add((external_task_id, tuple(current_diagnostics)))
            seen_diagnostic_signatures = {tuple(current_diagnostics)}
            for _owner_round in range(_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS):
                repair_round_count += 1
                if owner_revalidation:
                    # A prior residual-close cannot become completed merely
                    # because another task edited the workspace. Revalidate and
                    # register this freshly claimed owner's original projection.
                    post_commit_diagnostics = await asyncio.to_thread(collect_diagnostics)
                    if heartbeat_failures:
                        raise RuntimeError(f"repair_owner_heartbeat_failed:{heartbeat_failures[0]['code']}")
                    if not post_commit_diagnostics:
                        receipts = _record_workspace_quality_repair_artifact_receipts(
                            {
                                "task_id": external_task_id,
                                "execution_attempt": execution_attempt,
                                "task_completion_projection": _task_completion_projection_from_repair_task(repair_task),
                            }
                        )
                        for receipt in receipts:
                            registered_artifacts[(external_task_id, receipt["path"])] = receipt
                        artifact_receipts = tuple(registered_artifacts.values())
                        owners_requiring_revalidation.pop(external_task_id, None)
                        break
                    current_diagnostics = list(post_commit_diagnostics)
                    owner_revalidation = False
                round_tool_results, summary = await asyncio.to_thread(
                    executor._apply_workspace_quality_repairs,
                    run_id=run_id,
                    artifact_quality_errors=current_diagnostics,
                    task_id=external_task_id,
                    execution_attempt=execution_attempt,
                    repair_task=repair_task,
                )
                tool_results.extend(round_tool_results)
                await asyncio.sleep(0)
                if heartbeat_failures:
                    raise RuntimeError(f"repair_owner_heartbeat_failed:{heartbeat_failures[0]['code']}")
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

                # Foreign candidates contribute routing paths only. Preserve
                # remaining owned effects; never transfer a plan/plan hash.
                round_committed = False
                for candidate_index, candidate in enumerate(round_candidates):
                    routes = _director_stage_deferred_repair_owner_targets(
                        executor,
                        run=run,
                        run_id=run_id,
                        repair_task=repair_task,
                        candidate=candidate,
                        owner_context=owner_context,
                    )
                    if routes:
                        for owner_id, paths in routes.items():
                            targets = deferred_owner_targets.setdefault(owner_id, [])
                            targets.extend(path for path in paths if path not in targets)
                            routing_residual = {
                                "requesting_task_id": external_task_id,
                                "owner_task_id": owner_id,
                                "target_files": list(paths),
                                "reason": "repair_candidate_deferred_to_original_owner",
                            }
                            if routing_residual not in owner_routing_residuals:
                                owner_routing_residuals.append(routing_residual)
                        continue
                    commit_context = executor._director_stage_materialization_settle_commit_context(
                        run=run,
                        run_id=run_id,
                        diagnostics=current_diagnostics,
                        deferred_tool_results=[candidate],
                        repair_task=repair_task,
                    )
                    if heartbeat_failures:
                        raise RuntimeError(f"repair_owner_heartbeat_failed:{heartbeat_failures[0]['code']}")
                    candidate_receipts = await commit_materialization_deferred_repairs(
                        workspace=str(execution_attempt.workspace),
                        tool_results=[candidate],
                        execution_attempt=execution_attempt,
                        execution_attempt_authority=authority,
                        turn_id=(
                            f"director-stage-mat-settle-{run_id}:round{repair_round_count - 1}:candidate{candidate_index}"
                        ),
                        context=commit_context,
                    )
                    committed_receipts.extend(candidate_receipts)
                    if not any(
                        isinstance(item, Mapping) and executor._director_stage_materialization_receipt_succeeded(item)
                        for item in candidate_receipts
                    ):
                        continue
                    round_committed = True
                    receipts = _record_workspace_quality_repair_artifact_receipts(
                        {
                            "task_id": external_task_id,
                            "execution_attempt": execution_attempt,
                            "task_completion_projection": _task_completion_projection_from_repair_task(repair_task),
                        }
                    )
                    for receipt in receipts:
                        registered_artifacts[(external_task_id, receipt["path"])] = receipt
                    artifact_receipts = tuple(registered_artifacts.values())
                    post_commit_diagnostics = await asyncio.to_thread(collect_diagnostics)
                    if not post_commit_diagnostics:
                        break
                if not post_commit_diagnostics or deferred_owner_targets:
                    break
                post_signature = tuple(post_commit_diagnostics)
                if not round_committed or post_signature in seen_diagnostic_signatures:
                    break
                seen_diagnostic_signatures.add(post_signature)
                current_diagnostics = list(post_commit_diagnostics)
            if heartbeat_task is not None:
                await _stop_workspace_quality_repair_heartbeat(heartbeat_task, heartbeat_stop)
            if heartbeat_failures:
                raise RuntimeError(f"repair_owner_heartbeat_failed:{heartbeat_failures[0]['code']}")
            next_owner_id = ""
            next_owner_targets: list[str] = []
            if post_commit_diagnostics and deferred_owner_targets:
                next_owner_id = next(iter(deferred_owner_targets))
                next_owner_targets = deferred_owner_targets.pop(next_owner_id)
            elif not post_commit_diagnostics:
                deferred_owner_targets.clear()
                owners_requiring_revalidation.pop(external_task_id, None)
                if owners_requiring_revalidation:
                    next_owner_id = next(iter(owners_requiring_revalidation))
                    next_owner_targets = owners_requiring_revalidation[next_owner_id]
            if not next_owner_id:
                break
            if (next_owner_id, tuple(post_commit_diagnostics)) in seen_owner_frontiers:
                break
            residual = bool(post_commit_diagnostics)
            owner_commit_failed = any(
                not executor._director_stage_materialization_receipt_succeeded(item)
                for item in committed_receipts[attempt_receipt_start:]
            )
            settlement = executor._settle_director_stage_materialization_attempt(
                task_row_id=task_row_id,
                execution_attempt=execution_attempt,
                stage_status="failed" if residual or owner_commit_failed else "success",
                summary="director_stage_original_owner_repair_continuation "
                + (
                    "effect_receipt_failed"
                    if owner_commit_failed
                    else "verifier_residual"
                    if residual
                    else "verifier_clean"
                ),
            )
            if settlement.get("success") is not True:
                raise RuntimeError(f"repair_owner_attempt_close_failed:{settlement.get('reason') or 'unknown'}")
            if residual:
                owners_requiring_revalidation[external_task_id] = [
                    str(item["path"])
                    for item in owner_context["task_completion_projection"].get("owned_artifacts", ())
                    if isinstance(item, Mapping) and item.get("owner_task_id") == external_task_id
                ]
            # Clear closed identity before a claim that may reject dependencies.
            task_row_id = None
            execution_attempt = None
            external_task_id, task_row_id, execution_attempt, repair_task = _claim_workspace_quality_repair_attempt(
                executor,
                run=run,
                repair_attempt=repair_round_count + 1,
                target_files=next_owner_targets,
                original_owner_task_id=next_owner_id,
            )
            if external_task_id != next_owner_id:
                raise RuntimeError("repair_original_owner_claim_mismatch")
            attempt_receipt_start = len(committed_receipts)
            owner_context = executor._director_stage_materialization_settle_commit_context(
                run=run, run_id=run_id, diagnostics=post_commit_diagnostics, repair_task=repair_task
            )
            current_diagnostics = list(post_commit_diagnostics)
            owner_revalidation = not current_diagnostics
            authority = create_task_runtime_execution_attempt_authority(execution_attempt)
            heartbeat_stop = asyncio.Event()
            heartbeat_task = asyncio.create_task(
                _run_workspace_quality_repair_heartbeat(
                    authority,
                    stop=heartbeat_stop,
                    failures=heartbeat_failures,
                    context_summary="director_stage_original_owner_repair",
                )
            )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        from .factory_workspace_quality_impl import _WorkspaceQualityArtifactClosureIncompleteError

        missing_required_artifacts: tuple[dict[str, str], ...] = ()
        partial_closure = isinstance(exc, _WorkspaceQualityArtifactClosureIncompleteError)
        if isinstance(exc, _WorkspaceQualityArtifactClosureIncompleteError):
            for receipt in exc.registered_receipts:
                registered_artifacts[(external_task_id, receipt["path"])] = receipt
            artifact_receipts = tuple(registered_artifacts.values())
            missing_required_artifacts = exc.missing_obligations
        if heartbeat_task is not None:
            heartbeat_stop.set()
            await heartbeat_task
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
                summary=str(exc) if partial_closure else f"settle_exception:{type(exc).__name__}",
            )
        return {
            "ok": False,
            "reason": "artifact_closure_incomplete" if partial_closure else "settle_exception",
            "dependency_preparation_results": dependency_preparation_results,
            "detail": f"{type(exc).__name__}: {exc}",
            "tool_result_count": len(tool_results),
            "committed_receipt_count": sum(
                executor._director_stage_materialization_receipt_succeeded(item) for item in committed_receipts
            ),
            "project_artifact_receipt_count": len(artifact_receipts),
            "project_artifact_receipts": [dict(receipt) for receipt in artifact_receipts],
            "missing_required_artifacts": [dict(item) for item in missing_required_artifacts],
            "diagnostic_count": len(diagnostics),
            "external_task_id": external_task_id,
            "owner_routing_residuals": owner_routing_residuals,
        }
    finally:
        # Cancellation must stop renewal without inventing a terminal receipt.
        # Physical drain/DEO recovery remains the execution owner's obligation.
        if heartbeat_task is not None:
            heartbeat_stop.set()
            await asyncio.shield(heartbeat_task)
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
    # Preserve earlier successful receipts, but a failed DEO receipt or an
    # unrevalidated earlier owner cannot be erased by a later clean verifier.
    missing_commit_receipt = deferred_expected and not successful_receipts
    verifier_residual = bool(post_commit_diagnostics)
    commit_failed = (
        missing_commit_receipt or verifier_residual or bool(failed_receipts) or bool(owners_requiring_revalidation)
    )
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
            if missing_commit_receipt or failed_receipts
            else "materialization_verifier_residual"
            if verifier_residual
            else "materialization_owner_revalidation_pending"
            if owners_requiring_revalidation
            else "settle_attempt_close_failed"
        )
        return {
            "ok": False,
            "reason": failure_reason,
            "dependency_preparation_results": dependency_preparation_results,
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
            "project_artifact_receipt_count": len(artifact_receipts),
            "owner_routing_residuals": owner_routing_residuals,
        }
    return {
        "ok": True,
        "reason": "director_stage_settle",
        "dependency_preparation_results": dependency_preparation_results,
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
        "project_artifact_receipt_count": len(artifact_receipts),
        "owner_routing_residuals": owner_routing_residuals,
    }
