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
import re
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
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
from polaris.kernelone.process import ProcessTreeCancelledError, ProcessTreeDrainError

from . import factory_stage_helpers as helpers
from .factory_node_dependencies import node_dependency_readiness
from .factory_run_models import (
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    _WORKSPACE_VALIDATION_TIMEOUT_SECONDS,
    FactoryRun,
)
from .native_validation_session import NativeValidationSession
from .run_ledger import load_run_ledger_projection

logger = logging.getLogger(__name__)

_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS = 3
_MATERIALIZATION_TYPESCRIPT_PARSER = """
const fs = require('fs');
const ts = require('./node_modules/typescript/lib/typescript.js');
let failed = false;
for (const path of process.argv.slice(1)) {
  const source = ts.createSourceFile(path, fs.readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true);
  for (const diagnostic of source.parseDiagnostics) {
    failed = true;
    console.error(path + ': error TS' + diagnostic.code + ': ' +
      ts.flattenDiagnosticMessageText(diagnostic.messageText, '\\n'));
  }
}
process.exitCode = failed ? 1 : 0;
"""


def _close_materialization_sessions(
    sessions: Sequence[NativeValidationSession], *, primary_error: BaseException | None
) -> None:
    """Keep the original lifecycle error while recording cleanup failures."""
    failures: list[Exception] = []
    for session in sessions:
        try:
            session.close()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            failures.append(exc)
            logger.warning(
                "validation_cleanup_failed %s",
                json.dumps(
                    {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "primary_error_type": type(primary_error).__name__ if primary_error is not None else None,
                        "drain_proven": False,
                        "authoritative_receipt": False,
                    },
                    ensure_ascii=False,
                ),
            )
    if failures and primary_error is None:
        raise failures[0]


class MaterializationDiagnostics(list[str]):
    """Diagnostics plus coverage/admission observations, never receipt authority."""

    def __init__(
        self,
        diagnostics: Sequence[str] = (),
        *,
        static_evidence: Mapping[str, Any],
        verifier_results: Sequence[Mapping[str, Any]] = (),
        blockers: Sequence[str] = (),
        preparation_results: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(dict.fromkeys(diagnostics))
        self.static_evidence = dict(static_evidence)
        self.verifier_results = [dict(result) for result in verifier_results]
        self.blockers = list(blockers)
        self.preparation_results = [dict(result) for result in preparation_results]

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostics": list(self),
            "static_evidence": self.static_evidence,
            "verifier_results": self.verifier_results,
            "blockers": self.blockers,
            "dependency_preparation_results": self.preparation_results,
            "missing_required_verifiers": [
                row["command"] for row in self.verifier_results if row.get("failure_kind") in {"startup", "admission"}
            ],
            "failed_required_verifiers": [
                row["command"]
                for row in self.verifier_results
                if row.get("failure_kind") in {"nonzero", "verifier_failure", "timeout"}
            ],
        }


def _materialization_verifier_diagnostics(result: Mapping[str, Any]) -> list[str]:
    """Project a controlled result without scanning or executing anything."""
    if result.get("passed") is True and result.get("exit_code") == 0 and not result.get("error"):
        return []
    output = "\n".join(str(result.get(key) or "") for key in ("diagnostic_excerpt", "stdout_tail", "stderr_tail"))
    coded = [line.strip() for line in output.splitlines() if "error TS" in line or ": error " in line.lower()]
    command = " ".join(result.get("command") or ())
    detail = " ".join(line.strip() for line in output.splitlines() if not line.lstrip().startswith(">"))
    failure = (
        f"artifact_quality_error: {command} failed (exit={result.get('exit_code')}): "
        f"{result.get('error') or detail[-_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS:] or 'no diagnostic output'}"
    )
    return list(dict.fromkeys([*coded, failure]))


async def _await_materialization_preparation(
    executor: Any, run: FactoryRun, *, preparation_results: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Wait for the existing preparation owner even when this caller cancels.

    Preparation has a separate receipt and physical process-tree owner. Its
    synchronous API has no cancellation control; never detach that owner.
    """
    owner = asyncio.create_task(
        asyncio.to_thread(executor._ensure_director_stage_materialization_typescript_toolchain, run=run)
    )
    try:
        return await asyncio.shield(owner)
    except asyncio.CancelledError as cancelled:
        # asyncio.wait observes completion without rethrowing the thread's
        # exception. Keep one barrier across repeated caller cancellations.
        completion = asyncio.create_task(asyncio.wait({owner}))
        while not completion.done():
            try:
                await asyncio.shield(completion)
            except asyncio.CancelledError:
                continue
        error = asyncio.CancelledError("preparation owner cancelled") if owner.cancelled() else owner.exception()
        result = owner.result() if error is None else None
        if error is None and not isinstance(result, dict):
            error = TypeError("materialization_preparation_result_not_mapping")
        if error is not None:
            failure = {
                "passed": False,
                "error_type": type(error).__name__,
                "error": str(error),
                "factory_run_id": run.id,
                "phase": "dependency_preparation",
                "caller_cancelled": True,
                "owner_returned": True,
                "drain_proven": False,
                "authoritative_receipt": False,
            }
            if preparation_results is not None:
                preparation_results.append(failure)
            logger.warning(
                "materialization_preparation_failed_after_cancel %s", json.dumps(failure, ensure_ascii=False)
            )
        else:
            if preparation_results is not None and isinstance(result, dict):
                preparation_results.append(result)
        raise cancelled


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
    # Refresh existing failed verdicts through their owning cell after delivery
    # settle. The predecessor's evidence requirements, capability and immutable
    # coordinates are preserved; missing verdicts cannot be synthesized here.
    try:
        projection = executor._canonical_factory_projection(run, context)
        latest_authority = helpers.evaluate_canonical_factory_authority(projection)
        runtime_raw = projection.get("task_runtime_projection")
        runtime = runtime_raw if isinstance(runtime_raw, Mapping) else {}
        rows_raw = runtime.get("rows")
        rows = rows_raw if isinstance(rows_raw, list) else []
        terminal_scope = (
            latest_authority.source_valid
            and latest_authority.task_runtime_projection_authoritative
            and latest_authority.contract_task_scope_valid
            and bool(rows)
            and all(
                isinstance(row, Mapping)
                and str(row.get("execution_state") or row.get("status") or "").strip().lower()
                in {"completed", "failed", "cancelled"}
                for row in rows
            )
        )
        if terminal_scope:
            from polaris.cells.control_plane.run_ledger.public import (
                ReprojectTaskBoundaryVerdictCommandV1,
                reproject_task_boundary_verdict,
            )

            boundaries = (projection.get("task_boundary") or {}).get("latest_by_task") or {}
            changed = False
            for raw_task_id, previous in boundaries.items():
                if (
                    helpers._canonical_task_id_token(raw_task_id) not in latest_authority.expected_task_ids
                    or not isinstance(previous, Mapping)
                    or bool(previous.get("ok"))
                ):
                    continue
                predecessor = str(previous.get("content_id") or "").strip().lower()
                child_run = str(previous.get("run_id") or "").strip()
                if not child_run or not re.fullmatch(r"[0-9a-f]{64}", predecessor):
                    continue
                result = reproject_task_boundary_verdict(
                    ReprojectTaskBoundaryVerdictCommandV1(
                        workspace=str(executor.workspace),
                        run_id=child_run,
                        task_id=str(raw_task_id),
                        expected_content_id=predecessor,
                        reason="director_post_settle_workspace_revalidation",
                    )
                )
                changed = changed or result.changed
                logger.info("Director post-settle TaskBoundary task=%s status=%s", raw_task_id, result.status)
            if changed:
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


async def _collect_director_stage_materialization_diagnostics(
    executor: Any,
    *,
    run: FactoryRun | None = None,
    context: Mapping[str, Any] | None = None,
    preparation_results: list[dict[str, Any]] | None = None,
    sessions: list[NativeValidationSession] | None = None,
) -> MaterializationDiagnostics:
    """Scan without effects and execute required intents only in a source Group.

    Preparation completes before the Session freezes source/dependency inputs.
    Unknown/incomplete coverage and admission failures never authorize repair.
    An owned Session survives candidate revalidation; a standalone call closes
    its Group after the physical execution owner has returned or drained.
    """
    from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
    from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

    owned_sessions = sessions is None
    active = sessions if sessions is not None else []
    preparations = preparation_results if preparation_results is not None else []
    results: list[dict[str, Any]] = []
    commands: list[list[str]] = []
    blockers: list[str] = []
    diagnostics: list[str] = []
    static: dict[str, Any] = {
        "scan_mode": "pure_static",
        "scan_complete": False,
        "checks_complete": False,
        "deferred_checks": [],
    }
    try:
        # Bind manifest identity before scanning. Consume its cached UTF-8
        # payload only; a later path reopen could follow a replacement link.
        reader = WorkspaceReadSnapshot(executor.workspace)
        package = executor.workspace / "package.json"
        manifest = reader.read(package)
        evidence = scan_workspace_artifact_quality_evidence(str(executor.workspace), scan_mode="pure_static")
        static = evidence.to_dict()
        static["materialization_manifest_snapshot"] = {
            "path": manifest.path,
            "status": manifest.status,
            "detail": manifest.detail,
            "content_sha256": manifest.content_sha256,
        }
        if manifest.status not in {"readable", "missing"}:
            blockers.append("materialization_manifest_snapshot_unknown:" + manifest.detail)
        if manifest.status == "missing":
            # Absence has no content witness; reject appearance during the scan
            # without reading the new content or treating it as the baseline.
            _, current = reader.confined_path(package)
            if current is not None:
                blockers.append("materialization_manifest_snapshot_drift:appeared")
        blockers.extend(reader.drift_reasons())
        diagnostics.extend(evidence.errors)
        if evidence.scan_mode != "pure_static" or evidence.scan_complete is not True:
            blockers.append("materialization_static_coverage_incomplete:" + str(evidence.incomplete_reasons))
        covered: list[str] = []
        for check in evidence.deferred_checks:
            project = check.project_relative_path or "."
            pure = PurePosixPath(project)
            if pure.is_absolute() or ".." in pure.parts:
                blockers.append("materialization_verifier_project_invalid:" + project)
                continue
            if check.check_id == "typescript_project_typecheck":
                config = (pure / "tsconfig.json").as_posix()
                config_read = reader.read(executor.workspace / config)
                if config_read.status == "unknown":
                    blockers.append("materialization_config_snapshot_unknown:" + config_read.detail)
                    continue
                if config_read.status != "readable":
                    blockers.append("materialization_typescript_config_missing:" + config)
                    continue
                commands.append(["node", "node_modules/typescript/bin/tsc", "-p", config, "--noEmit"])
            elif check.check_id == "typescript_parser":
                # Project include/exclude does not prove single-file parsing.
                commands.append(["node", "-e", _MATERIALIZATION_TYPESCRIPT_PARSER, "--", *check.relative_paths])
            elif check.check_id == "javascript_parser":
                commands.extend(["node", "--check", path] for path in check.relative_paths)
            elif check.check_id in {"go_project_compile", "go_project_test"}:
                command = ["go", "-C", project, "test"]
                if check.check_id == "go_project_compile":
                    command.extend(["-run", "^$"])
                commands.append([*command, "./..."])
            else:
                blockers.append("materialization_deferred_check_unsupported:" + check.check_id)
                continue
            covered.append(check.check_id)
        if manifest.status == "readable":
            payload = json.loads(manifest.text)
            if not isinstance(payload, Mapping):
                raise ValueError("materialization_package_not_object")
            scripts = payload.get("scripts")
            build_script = str(scripts.get("build") or "").strip() if isinstance(scripts, Mapping) else ""
            test_script = str(scripts.get("test") or "").strip() if isinstance(scripts, Mapping) else ""
            if build_script:
                # Typechecking alone does not materialize generated test or
                # entrypoint files. Build in the same owned Group before tests.
                commands.append(["npm", "run", "build"])
            if test_script:
                commands.append(["npm", "test"])
                has_tests = any(
                    PurePosixPath(path).suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
                    and ("test" in PurePosixPath(path).name.lower() or ".spec." in PurePosixPath(path).name)
                    for path in evidence.scanned_relative_paths
                )
                if not has_tests:
                    diagnostics.append(
                        "artifact_quality_error: missing test source files required by package.json "
                        + f"scripts.test ({test_script[:160]}); expected tests/verify.test.ts or equivalent"
                    )
        commands = [list(command) for command in dict.fromkeys(tuple(command) for command in commands)]
        if blockers:
            return MaterializationDiagnostics(
                diagnostics, static_evidence=static, blockers=blockers, preparation_results=preparations
            )
        if run is None:
            raise ValueError("materialization verification requires a committed Chief Engineer Factory run")
        bound_context = dict(context or {})
        for key in ("factory_run_id", "run_id"):
            if key in bound_context and bound_context[key] != run.id:
                raise ValueError("materialization_context_run_mismatch:" + key)
        if "workspace" in bound_context and Path(bound_context["workspace"]).resolve() != executor.workspace.resolve():
            raise ValueError("materialization_context_workspace_mismatch")
        # Authentication must precede any preparation effect. This is a query
        # of the existing committed CE contract, not a new grant or receipt.
        from .factory_node_dependencies import authenticated_preparation_contract

        contract = authenticated_preparation_contract(executor, run)
        project_id = executor._canonical_project_id(bound_context)
        if project_id and contract.project_id != project_id:
            raise ValueError("materialization_context_project_mismatch")
        # Stage-local contexts can omit presentation metadata. The committed CE
        # contract already supplies the verified project identity for admission.
        bound_context["project_id"] = contract.project_id
        needs_node = any(
            command[0] == "npm"
            or "node_modules/typescript/bin/tsc" in command
            or _MATERIALIZATION_TYPESCRIPT_PARSER in command
            for command in commands
        )
        if needs_node and not node_dependency_readiness(executor.workspace).ready:
            # A committed manifest repair invalidates the old dependency mount.
            # Finish that group before owned preparation, then freeze a new
            # source/dependency baseline rather than rejecting the repair itself.
            for session in active:
                session.close()
            active.clear()
            preparation = await _await_materialization_preparation(executor, run, preparation_results=preparations)
            preparations.append(dict(preparation))
            if preparation.get("passed") is not True:
                blockers.append("dependency_preparation_failed:" + str(preparation.get("error") or preparation))
                return MaterializationDiagnostics(
                    diagnostics, static_evidence=static, blockers=blockers, preparation_results=preparations
                )
        if not active:
            active.append(NativeValidationSession.from_factory(executor, run, bound_context, commands=commands))
        for command in commands:
            try:
                timeout = 90 if "node_modules/typescript/bin/tsc" in command else _WORKSPACE_VALIDATION_TIMEOUT_SECONDS
                result = dict(await active[0].run_command(command, timeout))
                result["configured_timeout_seconds"] = timeout
            except (ProcessTreeCancelledError, ProcessTreeDrainError):
                raise
            except subprocess.TimeoutExpired as exc:
                result = {
                    "command": command,
                    "exit_code": None,
                    "passed": False,
                    "error": str(exc),
                    "failure_kind": "timeout",
                    "stdout_tail": str(exc.stdout or ""),
                    "stderr_tail": str(exc.stderr or ""),
                }
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                result = {
                    "command": command,
                    "exit_code": None,
                    "passed": False,
                    "error": f"{type(exc).__name__}:{exc}",
                    "failure_kind": "startup",
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
            if result.get("command") != command:
                result["passed"] = False
                result["error"] = "materialization_verifier_command_identity_mismatch"
                result["observed_command"] = result.get("command")
                result["command"] = command
                result["failure_kind"] = "admission"
            if (
                command == ["npm", "test"]
                and result.get("passed") is True
                and result.get("exit_code") == 0
                and re.fullmatch(
                    r"\s*(?:npm\s+run\s+(?:build|compile|lint|typecheck)|(?:npx\s+)?tsc\b[^;&|]*)\s*", test_script
                )
            ):
                from polaris.cells.control_plane.verifier_policy.public.service import evaluate_verifier_proof

                # Script names alone do not establish what ran. A build can
                # legitimately include tests; reuse its physical test proof.
                output = "\n".join(str(result.get(key) or "") for key in ("stdout_tail", "stderr_tail"))
                if not evaluate_verifier_proof(
                    profile_id="node.script_test",
                    modality="test",
                    exit_code=0,
                    timed_out=False,
                    output_bytes=output.encode("utf-8"),
                ):
                    result["passed"] = False
                    result["error"] = (
                        "npm manifest-only test script in package.json: scripts.test produced no test execution proof"
                    )
            if (
                command == ["npm", "test"]
                and result.get("passed") is True
                and result.get("exit_code") == 0
                and re.search(r"(?m)^# tests\s+0\s*$", str(result.get("stdout_tail") or ""))
            ):
                # Node can exit zero for an unmatched test glob. Preserve the
                # physical exit code while rejecting the empty test verdict.
                result["passed"] = False
                result["error"] = "node_test_zero_tests: package.json scripts.test discovered no executable tests"
                result["native_test_count"] = 0
            if result.get("passed") is not True or result.get("exit_code") != 0 or result.get("error"):
                error = str(result.get("error") or "")
                result.setdefault(
                    "failure_kind",
                    "nonzero"
                    if result.get("exit_code") not in {None, 0}
                    else "verifier_failure"
                    if result.get("exit_code") == 0
                    else "timeout"
                    if error.startswith("timeout after")
                    else "startup",
                )
                diagnostics.extend(_materialization_verifier_diagnostics(result))
                if result["failure_kind"] not in {"nonzero", "verifier_failure"}:
                    blockers.append("materialization_verifier_" + result["failure_kind"] + ":" + error)
            results.append(result)
            if blockers:
                break
        static["controlled_check_ids"] = covered
        static["controlled_checks_complete"] = not blockers and len(results) == len(commands)
        return MaterializationDiagnostics(
            diagnostics,
            static_evidence=static,
            verifier_results=results,
            blockers=blockers,
            preparation_results=preparations,
        )
    except (ProcessTreeCancelledError, ProcessTreeDrainError):
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        blockers.append(f"materialization_verification_admission_failed:{type(exc).__name__}:{exc}")
        if not results:
            # Admission produced no verifier execution. Preserve every planned
            # command as missing evidence, never as a failed executed receipt.
            results.extend(
                {
                    "command": command,
                    "exit_code": None,
                    "passed": False,
                    "error": f"{type(exc).__name__}:{exc}",
                    "failure_kind": "admission",
                    "phase": "admission",
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
                for command in commands
            )
        return MaterializationDiagnostics(
            diagnostics,
            static_evidence=static,
            verifier_results=results,
            blockers=blockers,
            preparation_results=preparations,
        )
    finally:
        if owned_sessions:
            _close_materialization_sessions(active, primary_error=sys.exception())


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
    executor: Any,
    *,
    run: FactoryRun,
    stage_status: str,
    error_code: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep each validation Group owned until controlled execution terminates."""
    sessions: list[NativeValidationSession] = []
    observations: list[dict[str, Any]] = []
    try:
        result = await _run_materialization_settle_with_session(
            executor,
            run=run,
            stage_status=stage_status,
            error_code=error_code,
            context=dict(context or {}),
            sessions=sessions,
            observations=observations,
        )
        if result.get("reason") == "materialization_verifier_residual":
            result = await _continue_materialization_verifier_repair(
                executor,
                run=run,
                context=dict(context or {}),
                result=result,
                sessions=sessions,
                observations=observations,
            )
    except _MaterializationVerificationBlockedError as exc:
        result = {
            "ok": False,
            "reason": "materialization_verification_blocked",
            "detail": str(exc),
            "tool_result_count": 0,
            "diagnostic_count": len(exc.observation),
            "dependency_preparation_results": exc.observation.preparation_results,
        }
    finally:
        _close_materialization_sessions(sessions, primary_error=sys.exception())
    result["validation_observations"] = observations
    return result


class _MaterializationVerificationBlockedError(RuntimeError):
    def __init__(self, observation: MaterializationDiagnostics) -> None:
        self.observation = observation
        super().__init__("; ".join(observation.blockers))


def _materialization_observation_with_coverage(observation: Any) -> MaterializationDiagnostics:
    if not isinstance(observation, MaterializationDiagnostics):
        observation = MaterializationDiagnostics(
            [],
            static_evidence={"scan_mode": "unknown", "scan_complete": False},
            blockers=["materialization_collector_coverage_required"],
        )
    static = observation.static_evidence
    if (
        static.get("scan_mode") != "pure_static"
        or static.get("scan_complete") is not True
        or (static.get("deferred_checks") and static.get("controlled_checks_complete") is not True)
    ):
        observation.blockers.append("materialization_collector_coverage_incomplete")
    return observation


async def _continue_materialization_verifier_repair(
    executor: Any,
    *,
    run: FactoryRun,
    context: dict[str, Any],
    result: dict[str, Any],
    sessions: list[NativeValidationSession],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Use the existing Director repair owner after deterministic settlement.

    The prior attempt is closed before this bridge claims an original CE owner.
    Provider success never settles the new attempt: its receipt-bound candidate
    must pass controlled verification or demonstrate the existing progress rule.
    """
    from .factory_workspace_quality_impl import (
        _apply_workspace_quality_llm_repairs,
        _record_workspace_quality_repair_artifact_receipts,
        _settle_pending_workspace_quality_repair_attempt,
        _stop_workspace_quality_repair_heartbeat,
    )

    errors = list(result.get("post_commit_diagnostics") or ())
    pending: Mapping[str, Any] | None = None
    preparations: list[dict[str, Any]] = []
    authority_binding = None
    try:
        for round_number in range(_WORKSPACE_QUALITY_REPAIR_MAX_ROUNDS):
            if not errors:
                break
            before_results = observations[-1].get("verifier_results", []) if observations else []
            for session in sessions:
                session.before_repair()
            authority_port = executor._factory_role_evidence_cutoff_port(context)
            if authority_binding is None:
                authority_binding = authority_port.mint_authority_binding("director")
            tools, summary = await executor._call_with_factory_role_evidence_authority(
                authority_port,
                "director",
                lambda _errors=list(errors), _attempt=int(result.get("repair_round_count") or 0) + round_number + 1: (
                    _apply_workspace_quality_llm_repairs(
                        executor,
                        run=run,
                        context=context,
                        artifact_quality_errors=_errors,
                        repair_attempt=_attempt,
                    )
                ),
                authority_binding=authority_binding,
            )
            summary = dict(summary)
            pending = summary.pop("_pending_task_runtime_repair_attempt", None)
            result["llm_repair"] = {
                key: value
                for key, value in summary.items()
                if key in {"attempted", "success", "error", "error_code", "repair_mode", "task_id"}
                and (value is None or type(value) in {str, bool, int, float})
            }
            result["tool_result_count"] = int(result.get("tool_result_count") or 0) + len(tools)
            if not isinstance(pending, Mapping):
                pending = None
                break
            effects = [
                item
                for item in tools
                if isinstance(item, Mapping)
                and (
                    str(item.get("tool_name") or item.get("tool") or "") in {"write_file", "edit_file", "delete_file"}
                    or (
                        isinstance(item.get("effect_receipt"), Mapping)
                        and item["effect_receipt"].get("normalized_tool_name")
                        in {"write_file", "edit_file", "delete_file"}
                    )
                )
            ]
            for session in sessions:
                session.candidate(pending=pending, results=effects)
            # Artifact receipts attest landed bytes independently of quality.
            # Refresh them before a dependency change may require a new group.
            _record_workspace_quality_repair_artifact_receipts(pending)
            observation = _materialization_observation_with_coverage(
                await executor._collect_director_stage_materialization_diagnostics(
                    run=run,
                    context=context,
                    sessions=sessions,
                    preparation_results=preparations,
                )
            )
            observations.append(observation.to_dict())
            if observation.blockers:
                raise _MaterializationVerificationBlockedError(observation)
            after_errors = list(observation)
            effect = executor._workspace_quality_repair_effect(
                before_signature=executor._workspace_quality_diagnostic_signature(errors),
                after_signature=executor._workspace_quality_diagnostic_signature(after_errors),
                verifier_passed=not after_errors,
                write_tool_evidence=bool(effects),
                before_results=before_results,
                after_results=observation.verifier_results,
                workspace=Path(executor.workspace),
            )
            accepted = effect in {"resolved", "progress"}
            settlement = await _settle_pending_workspace_quality_repair_attempt(
                executor,
                pending,
                accepted=accepted,
                reason=f"director_stage_verifier_repair_{effect}",
            )
            pending = None
            result["llm_repair"]["verifier_effect"] = effect
            result["llm_repair"]["task_runtime_repair_attempt"] = settlement
            if not accepted:
                for session in sessions:
                    session.restored()
                if not settlement or settlement.get("settled") is not True or settlement.get("artifact_receipt_error"):
                    break
                # Feed the rejected candidate's real diagnostic to the same
                # owner, while the restored baseline remains authoritative.
                repair_context = dict(context.get("director_quality_repair") or {})
                repair_context["candidate_rejection_errors"] = after_errors[:4]
                repair_context["candidate_rejection_target_files"] = list(summary.get("repair_target_files") or ())[:12]
                context["director_quality_repair"] = repair_context
                continue
            if not settlement or settlement.get("outcome") != "completed" or not settlement.get("settled"):
                break
            errors = after_errors
            result["post_commit_diagnostics"] = errors
            result["post_commit_diagnostic_count"] = len(errors)
            result["project_artifact_receipt_count"] = int(result.get("project_artifact_receipt_count") or 0) + int(
                settlement.get("project_artifact_receipt_count") or 0
            )
            result["mutated"] = True
            if not errors:
                result.update(
                    ok=True, reason="director_stage_settle", detail="Director repair passed controlled verification"
                )
                break
    except ProcessTreeDrainError:
        # Keep the physical attempt unresolved until its process owner drains.
        # Stopping renewal is independent of authorizing rollback/settlement.
        if isinstance(pending, Mapping):
            task, stop = pending.get("heartbeat_task"), pending.get("heartbeat_stop")
            if isinstance(task, asyncio.Task) and isinstance(stop, asyncio.Event):
                await _stop_workspace_quality_repair_heartbeat(task, stop)
        pending = None
        raise
    finally:
        if pending is not None:
            await _settle_pending_workspace_quality_repair_attempt(
                executor,
                pending,
                accepted=False,
                reason="director_stage_verifier_repair_interrupted",
            )
    return result


async def _run_materialization_settle_with_session(
    executor: Any,
    *,
    run: FactoryRun,
    stage_status: str,
    error_code: str,
    context: Mapping[str, Any],
    sessions: list[NativeValidationSession],
    observations: list[dict[str, Any]],
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
    dependency_preparation_results: list[dict[str, Any]] = []

    async def collect_diagnostics() -> MaterializationDiagnostics:
        try:
            observation = await executor._collect_director_stage_materialization_diagnostics(
                run=run,
                context=context,
                preparation_results=dependency_preparation_results,
                sessions=sessions,
            )
        except (asyncio.CancelledError, ProcessTreeCancelledError, ProcessTreeDrainError) as exc:
            lifecycle = {
                "kind": "validation_lifecycle",
                "run_id": str(run.id),
                "workspace": str(executor.workspace),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "session_count": len(sessions),
                "drain_proven": False,
                "authoritative_receipt": False,
            }
            observations.append(lifecycle)
            logger.warning("validation_lifecycle %s", json.dumps(lifecycle, ensure_ascii=False))
            raise
        observation = _materialization_observation_with_coverage(observation)
        observations.append(observation.to_dict())
        if observation.blockers:
            raise _MaterializationVerificationBlockedError(observation)
        return observation

    diagnostics = await collect_diagnostics()
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
        for session in sessions:
            session.before_repair()
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
                    post_commit_diagnostics = await collect_diagnostics()
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
                    for session in sessions:
                        session.before_repair()
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
                        for session in sessions:
                            session.restored()
                        # A failed commit can still leave source drift. Revalidate
                        # the restored baseline before any further candidate.
                        post_commit_diagnostics = await collect_diagnostics()
                        continue
                    round_committed = True
                    for session in sessions:
                        effect_rows = [
                            dict(row)
                            for receipt in candidate_receipts
                            for row in receipt.get("results", ())
                            if isinstance(row, Mapping) and row.get("effect_receipt") is not None
                        ]
                        session.candidate(
                            pending={
                                "task_id": external_task_id,
                                "task_row_id": task_row_id,
                                "execution_attempt": execution_attempt,
                                "task_completion_projection": owner_context["task_completion_projection"],
                            },
                            results=effect_rows,
                        )
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
                    post_commit_diagnostics = await collect_diagnostics()
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
            for session in sessions:
                session.before_repair()
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
    except (ProcessTreeCancelledError, ProcessTreeDrainError):
        # Physical lifecycle failure belongs to the execution owner, including
        # after a commit. Never convert it to an ordinary settle result.
        raise
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
            "reason": (
                "artifact_closure_incomplete"
                if partial_closure
                else "materialization_verification_blocked"
                if isinstance(exc, _MaterializationVerificationBlockedError)
                else "settle_exception"
            ),
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
