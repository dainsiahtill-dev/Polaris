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
import math
import re
import stat
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

from polaris.cells.chief_engineer.blueprint.public import (
    ProjectCompletionContractV1,
    VerificationCommandAuthorityV1,
    VerificationObligationV1,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1, ReprojectTaskBoundaryVerdictCommandV1
from polaris.cells.control_plane.verifier_policy.public import (
    EvaluateVerifierCommandPolicyQueryV1,
    evaluate_verifier_command_policy,
)
from polaris.cells.director.runtime.public import RepairDiagnosticV1
from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectCompletionOwnerObservationV1Error,
    ProjectVerificationAdmissionObservationV1,
)
from polaris.cells.runtime.execution_broker.public.project_verification import ProjectVerificationReceiptV1
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import (
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
)
from polaris.kernelone.process import ProcessTreeCancelledError, ProcessTreeDrainError, ProcessTreeRunControl
from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

from . import factory_stage_helpers as helpers
from .factory_node_dependencies import node_dependency_readiness
from .factory_run_models import (
    _WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS,
    FactoryRun,
)
from .native_validation_session import NativeValidationSession
from .run_ledger import load_run_ledger_projection

logger = logging.getLogger(__name__)

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
        self._owner_receipts: tuple[ProjectVerificationReceiptV1, ...] = ()
        self._admission_repair_diagnostics: tuple[RepairDiagnosticV1, ...] = ()

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


def _materialization_verifier_diagnostics(
    result: Mapping[str, Any], *, receipt: ProjectVerificationReceiptV1 | None = None
) -> list[str]:
    """Project a controlled result without scanning or executing anything."""
    if receipt is not None and type(receipt) is ProjectVerificationReceiptV1 and receipt.succeeded:
        return []
    if receipt is None and result.get("passed") is True and result.get("exit_code") == 0 and not result.get("error"):
        return []
    output = "\n".join(str(result.get(key) or "") for key in ("diagnostic_excerpt", "stdout_tail", "stderr_tail"))
    coded = [line.strip() for line in output.splitlines() if "error TS" in line or ": error " in line.lower()]
    command = " ".join(result.get("command") or ())
    detail = " ".join(line.strip() for line in output.splitlines() if not line.lstrip().startswith(">"))
    failure = (
        f"artifact_quality_error: {command} failed (exit={result.get('exit_code')}): "
        f"{': '.join(part for part in (str(result.get('error') or ''), detail[-_WORKSPACE_VALIDATION_OUTPUT_MAX_CHARS:]) if part) or 'no diagnostic output'}"
    )
    return list(dict.fromkeys([*coded, failure]))


def _missing_required_entrypoint_diagnostic(
    *,
    contract: ProjectCompletionContractV1,
    command: Any,
    command_text: str,
    error: FileNotFoundError,
    receipts: Sequence[ProjectVerificationReceiptV1],
) -> RepairDiagnosticV1 | None:
    """Project an absent declared build output; never relax broker admission.

    Only a required entrypoint whose source inputs still match a successful
    same-owner build is recoverable. Source loss, drift, links and authority
    failures remain blockers. Candidate paths are CE-owned discovery hints;
    the subsequent Director claim still enforces the original JobToken scope.
    """
    from polaris.cells.runtime.execution_broker.public import project_verification as broker
    from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

    if type(command) is not broker.RunProjectVerificationCommandV1 or command.modality != "entrypoint":
        return None
    if not error.filename:
        return None
    build = next(
        (
            receipt
            for receipt in reversed(receipts)
            if type(receipt) is ProjectVerificationReceiptV1
            and receipt.modality == "build"
            and receipt.owner_task_id == command.owner_task_id
            and receipt.succeeded
        ),
        None,
    )
    if build is None:
        return None
    reader = WorkspaceReadSnapshot(command.workspace)
    failed_path = Path(error.filename)
    for entry in contract.obligations.entrypoints:
        if (
            entry.applicability != "required"
            or entry.owner_task_id != command.owner_task_id
            or entry.command != command_text
            or not entry.runtime_path
            or not entry.source_path
        ):
            continue
        expected = reader.root / entry.runtime_path
        if failed_path != expected and failed_path not in expected.parents:
            continue
        try:
            failed_path.relative_to(reader.root)
            _, runtime_witness = reader.confined_path(expected)
            if runtime_witness is not None:
                continue
            runtime_inputs = [item for item in command.input_artifacts if item.path == entry.runtime_path]
            if len(runtime_inputs) != 1:
                continue
            build_inputs = {item.path: item.artifact_hash for item in build.input_artifacts}
            for item in command.input_artifacts:
                if item.path == entry.runtime_path:
                    continue
                snapshot = reader.read(reader.root / item.path)
                if snapshot.status != "readable" or snapshot.content_sha256 != build_inputs.get(item.path):
                    return None
            scope = [
                item.path
                for item in contract.obligations.artifacts
                if item.owner_task_id == entry.owner_task_id
                and item.applicability == "required"
                and (item.path == entry.source_path or item.semantic_role in {"config", "manifest"})
                and reader.read(reader.root / item.path).status == "readable"
            ]
            if entry.source_path not in scope or reader.drift_reasons():
                return None
        except (OSError, RuntimeError, ValueError):
            return None
        scope = list(dict.fromkeys(scope))
        message = (
            f"artifact_quality_error: required entrypoint artifact missing: {entry.runtime_path}; "
            f"expected command: {command_text}; successful build did not produce the required runtime path; "
            + "; ".join(f"repair source ({path})" for path in scope)
        )
        return RepairDiagnosticV1(
            source="verifier_admission",
            code="failed_required_entrypoint",
            message=message,
            path=entry.source_path,
            metadata={
                "runtime_path": entry.runtime_path,
                "source_path": entry.source_path,
                "expected_command": list(command.argv),
                "repair_possible_scope": scope,
                "scope_is_authority": False,
                "build_receipt_ref": build.receipt_ref,
                "obligation_id": command.obligation_id,
                "execution_started": False,
                "command_receipt_missing": True,
            },
        )
    return None


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
    executor: Any,
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


def _workspace_has_delivery_surface(executor: Any) -> bool:
    """True when package + source surface exists (real-run-capable scaffold)."""

    if not (executor.workspace / "package.json").is_file():
        return False
    return (
        any(executor.workspace.rglob("*.ts"))
        or any(executor.workspace.rglob("*.tsx"))
        or any(executor.workspace.rglob("*.py"))
    )


def _revalidated_deferred_boundary_command(
    executor: Any, *, run: FactoryRun, task_id: str, child_run: str, predecessor: str
) -> ReprojectTaskBoundaryVerdictCommandV1:
    """Rebuild normal requirements from immutable CE ownership and current owner receipts."""
    from polaris.cells.control_plane.run_ledger.public import evaluate_task_boundary_verdict
    from polaris.cells.runtime.execution_broker.public import project_verification as broker
    from polaris.cells.runtime.task_runtime.public import TaskRuntimeService
    from polaris.cells.runtime.task_runtime.public.execution_claim_evidence import (
        ExecutionClaimEvidenceError,
        ExecutionClaimEvidenceV1,
        QueryExecutionClaimEvidenceV1,
        query_execution_claim_evidence,
    )

    from .factory_node_dependencies import authenticated_preparation_contract
    from .native_validation_inputs import _validate_projection

    workspace = str(Path(executor.workspace).resolve())
    contract = authenticated_preparation_contract(executor, run)
    if type(contract) is not ProjectCompletionContractV1 or contract.run_id != run.id:
        raise ValueError("task_boundary_revalidation_ce_identity_invalid")

    owner_row = TaskRuntimeService(workspace, read_only=True).resolve_factory_task_row(
        external_task_id=task_id, factory_run_id=run.id
    )
    owner_metadata = owner_row.get("metadata") if isinstance(owner_row, Mapping) else None
    runtime = owner_metadata.get("runtime_execution") if isinstance(owner_metadata, Mapping) else None
    if not isinstance(runtime, Mapping):
        raise ValueError("task_boundary_revalidation_claim_missing")
    try:
        identity = TaskRuntimeExecutionAttemptIdentityV1(
            workspace=workspace,
            task_id=runtime["task_id"],
            external_task_id=runtime["external_task_id"],
            session_id=runtime["session_id"],
            attempt=runtime["attempt"],
            role_id=runtime["role_id"],
            worker_id=runtime["worker_id"],
            run_id=runtime["run_id"],
            lease_expires_at=runtime["lease_expires_at"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("task_boundary_revalidation_claim_identity_invalid") from exc
    if (identity.external_task_id, identity.run_id, identity.role_id) != (task_id, child_run, "director"):
        raise ValueError("task_boundary_revalidation_claim_identity_invalid")
    claim_query = QueryExecutionClaimEvidenceV1(workspace, identity, purpose="receipt_revalidation")

    def capture_claim_evidence() -> ExecutionClaimEvidenceV1:
        try:
            return query_execution_claim_evidence(claim_query)
        except ExecutionClaimEvidenceError as exc:
            raise ValueError(f"task_boundary_revalidation_claim_unavailable:{exc.code}") from exc

    claim_evidence = capture_claim_evidence()
    if claim_evidence.factory_run_id != run.id or claim_evidence.project_id not in {"", contract.project_id}:
        raise ValueError("task_boundary_revalidation_claim_scope_invalid")
    claimed_metadata = claim_evidence.claim_metadata
    source_token = claimed_metadata.get("control_plane_job_token", claimed_metadata.get("job_token", {}))
    current_metadata = claim_evidence.current_metadata
    if (
        not isinstance(source_token, Mapping)
        or current_metadata.get("control_plane_job_token", current_metadata.get("job_token", {})) != source_token
    ):
        raise ValueError("task_boundary_revalidation_claim_token_changed")
    for metadata in (claim_evidence.claim_metadata, claim_evidence.current_metadata):
        runtime_scope = metadata.get("runtime_execution")
        if not isinstance(runtime_scope, Mapping):
            raise ValueError("task_boundary_revalidation_claim_identity_invalid")
        for scope in (metadata, runtime_scope):
            if (
                (scope.get("factory_run_id") and scope["factory_run_id"] != run.id)
                or (scope.get("workspace") and Path(str(scope["workspace"])).resolve() != Path(workspace))
                or any(
                    scope.get(key) and scope[key] != contract.project_id
                    for key in ("project_id", "factory_bench_project_id")
                )
            ):
                raise ValueError("task_boundary_revalidation_claim_scope_invalid")
        projection = metadata.get("task_completion_projection")
        if not isinstance(projection, Mapping):
            raise ValueError("task_boundary_revalidation_claim_projection_missing")
        _validate_projection(contract, projection, task_id)
    reader = WorkspaceReadSnapshot(Path(workspace))
    artifacts: list[tuple[broker.QueryProjectArtifactReceiptV1, broker.ProjectArtifactReceiptV1]] = []
    for artifact in contract.obligations.artifacts:
        if artifact.owner_task_id != task_id or artifact.applicability != "required":
            continue
        artifact_query = broker.QueryProjectArtifactReceiptV1(
            workspace=workspace,
            project_id=contract.project_id,
            run_id=run.id,
            completion_contract_hash=contract.contract_hash,
            obligation_id=artifact.obligation_id,
            owner_task_id=task_id,
            path=artifact.path,
        )
        artifact_receipt = broker.query_project_artifact_receipt(artifact_query)
        source = reader.read(Path(workspace) / artifact.path)
        if (
            type(artifact_receipt) is not broker.ProjectArtifactReceiptV1
            or source.status != "readable"
            or artifact_receipt.artifact_hash != source.content_sha256
            or any(
                getattr(artifact_receipt, name) != getattr(artifact_query, name)
                for name in artifact_query.__dataclass_fields__
            )
            or (not artifact_receipt.receipt_ref)
            or (not artifact_receipt.receipt_hash)
        ):
            raise ValueError("task_boundary_revalidation_artifact_receipt_not_current")
        artifacts.append((artifact_query, artifact_receipt))
    verification = [
        item
        for item in contract.obligations.verification
        if item.owner_task_id == task_id and item.applicability == "required"
    ]
    if not artifacts or not verification:
        raise ValueError("task_boundary_revalidation_requirements_empty")
    authorities = {item.authority_hash: item for item in contract.verification_command_authority}
    receipts: list[tuple[broker.QueryProjectVerificationReceiptV1, broker.ProjectVerificationReceiptV1]] = []
    required_commands: list[str] = []
    for obligation in verification:
        authority_hash = obligation.command_authority_hash
        command_text = obligation.command
        if authority_hash is None or command_text is None:
            raise ValueError("task_boundary_revalidation_verifier_authority_invalid")
        required_commands.append(command_text)
        command = broker.authorize_project_verification_command(
            broker.ResolveProjectVerificationAuthorityQueryV1(
                workspace=workspace,
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=obligation.obligation_id,
            )
        )
        authority = authorities.get(authority_hash)
        if (
            type(command) is not broker.RunProjectVerificationCommandV1
            or authority is None
            or (command.owner_task_id, command.modality, command.argv, command.cwd, command.command_authority_hash)
            != (task_id, obligation.modality, authority.argv, authority.cwd, authority.authority_hash)
        ):
            raise ValueError("task_boundary_revalidation_verifier_authority_invalid")
        verification_query = broker.QueryProjectVerificationReceiptV1(
            **{name: getattr(command, name) for name in broker.QueryProjectVerificationReceiptV1.__dataclass_fields__}
        )
        verification_receipt = broker.query_project_verification_receipt(verification_query)
        if (
            type(verification_receipt) is not ProjectVerificationReceiptV1
            or not verification_receipt.succeeded
            or verification_receipt.exit_code != 0
            or (not verification_receipt.receipt_ref)
            or (not verification_receipt.receipt_hash)
            or any(
                getattr(verification_receipt, name) != getattr(verification_query, name)
                for name in verification_query.__dataclass_fields__
                if name != "input_artifacts"
            )
            or (
                tuple(
                    (obligation.obligation_id, obligation.path) for obligation in verification_receipt.input_artifacts
                )
                != tuple(
                    (obligation.obligation_id, obligation.path) for obligation in verification_query.input_artifacts
                )
            )
        ):
            raise ValueError("task_boundary_revalidation_verifier_receipt_not_current")
        receipts.append((verification_query, verification_receipt))
    # Re-read existing owners at the CAS frontier; a cached result or changed
    # input cannot be upgraded by replaying the old deferred placeholder.
    if (
        claim_evidence != capture_claim_evidence()
        or reader.drift_reasons()
        or any(broker.query_project_artifact_receipt(query) != receipt for query, receipt in artifacts)
        or any(broker.query_project_verification_receipt(query) != receipt for query, receipt in receipts)
    ):
        raise ValueError("task_boundary_revalidation_current_basis_drift")
    source_policy = source_token.get("gate_policy", {})
    declared_modalities = source_policy.get(
        "required_evidence_modalities", source_policy.get("required_modalities", [])
    )
    normal = evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=task_id,
        run_id=child_run,
        target_files=[receipt.path for _, receipt in artifacts],
        completed_artifacts=[receipt.path for _, receipt in artifacts],
        required_verifiers=required_commands,
        completed_verifiers=required_commands,
        required_evidence_modalities=[name for name in declared_modalities if name in {"code", "command"}],
        present_evidence_modalities=[
            name for name, present in (("code", bool(artifacts)), ("command", bool(receipts))) if present
        ],
        evidence_refs=[
            claim_evidence.claim_event_id,
            *[receipt.receipt_ref for _, receipt in artifacts],
            *[receipt.receipt_ref for _, receipt in receipts],
        ],
    )
    if not normal.ok:
        raise ValueError("task_boundary_revalidation_normal_verdict_failed")
    return ReprojectTaskBoundaryVerdictCommandV1(
        workspace=workspace,
        run_id=child_run,
        task_id=task_id,
        expected_content_id=predecessor,
        reason="director_post_settle_workspace_revalidation",
        revalidated_verdict=normal,
        source_claim_fact_event_id=claim_evidence.claim_event_id,
        current_receipt_bindings=tuple(
            (receipt.obligation_id, receipt.receipt_ref, receipt.receipt_hash, receipt.input_artifact_hash)
            for _, receipt in receipts
        ),
    )


def _recover_director_stage_authority_after_delivery_settle(
    executor: Any,
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
    # Refresh failed verdicts through their owning cell after delivery settle.
    # Verifier fields describe the earlier execution cut, not later Broker
    # observations. Rebuild those fields from the same immutable CE/claim and
    # current per-obligation proof; artifact-only legacy replay remains local.
    # Missing verdicts cannot be synthesized here.
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
                requires_current_proof = previous.get("status") in {
                    "deferred_followup_required",
                    "needs_workflow",
                } or any(
                    previous.get(field)
                    for field in ("required_verifiers", "missing_required_verifiers", "failed_required_verifiers")
                )
                if requires_current_proof:
                    command = _revalidated_deferred_boundary_command(
                        executor, run=run, task_id=str(raw_task_id), child_run=child_run, predecessor=predecessor
                    )
                else:
                    command = ReprojectTaskBoundaryVerdictCommandV1(
                        workspace=str(executor.workspace),
                        run_id=child_run,
                        task_id=str(raw_task_id),
                        expected_content_id=predecessor,
                        reason="director_post_settle_workspace_revalidation",
                    )
                result = reproject_task_boundary_verdict(command)
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
    executor: Any,
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


def _materialization_source_scope(contract: ProjectCompletionContractV1, *, reader: Any) -> tuple[str, ...]:
    """Use the entire committed project artifact closure for source discovery."""
    if type(contract) is not ProjectCompletionContractV1:
        raise TypeError("materialization_completion_contract_invalid")
    artifacts = tuple(item for item in contract.obligations.artifacts if item.applicability != "not_applicable")
    paths = tuple(item.path for item in artifacts)
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("materialization_authored_source_scope_missing_or_ambiguous")
    if any(
        item.owner_task_id not in contract.covered_task_ids
        for item in artifacts
        if item.applicability == "required" or item.owner_task_id is not None
    ):
        raise ValueError("materialization_authored_source_owner_not_covered")
    if any(
        item.source_path not in paths
        for item in contract.obligations.entrypoints
        if item.applicability != "not_applicable" and item.source_path
    ):
        raise ValueError("materialization_authored_entrypoint_source_not_covered")
    selected: list[str] = []
    for item in artifacts:
        if item.semantic_role == "assets":
            _, witness = reader.confined_path(reader.root / item.path)
            if witness is None and item.applicability == "required":
                raise ValueError("materialization_required_asset_missing:" + item.path)
            if witness is not None and not stat.S_ISREG(witness.st_mode):
                raise ValueError("materialization_asset_not_regular_file:" + item.path)
            continue
        if item.applicability == "optional" and reader.read(reader.root / item.path).status == "missing":
            continue
        selected.append(item.path)
    return tuple(selected)


def _materialization_admission_repair_diagnostic(
    *,
    error: ProjectCompletionOwnerObservationV1Error,
    contract: ProjectCompletionContractV1,
    verifier: VerificationObligationV1,
    authority: VerificationCommandAuthorityV1,
    reader: WorkspaceReadSnapshot,
) -> RepairDiagnosticV1 | None:
    """Project current artifact feedback without granting command or write scope."""
    failure = error.admission_failure
    if (
        type(error) is not ProjectCompletionOwnerObservationV1Error
        or error.error_code != "project_verification_command_policy_rejected"
        or type(failure) is not ProjectVerificationAdmissionObservationV1
    ):
        return None
    expected = (
        str(reader.root),
        contract.project_id,
        contract.run_id,
        contract.contract_hash,
        verifier.obligation_id,
        verifier.owner_task_id,
        verifier.modality,
        authority.argv,
        authority.cwd,
        authority.authority_hash,
    )
    observed = (
        failure.workspace,
        failure.project_id,
        failure.run_id,
        failure.completion_contract_hash,
        failure.obligation_id,
        failure.owner_task_id,
        failure.modality,
        failure.argv,
        failure.cwd,
        failure.command_authority_hash,
    )
    if (
        observed != expected
        or authority.task_id != verifier.owner_task_id
        or verifier.owner_task_id not in contract.covered_task_ids
    ):
        return None
    if (
        failure.authorized is not False
        or failure.execution_started is not False
        or failure.execution_authority is not False
        or failure.authoritative_receipt is not False
        or failure.receipt_ref is not None
        or (
            failure.modality,
            failure.policy_error_code,
            failure.failure_domain,
            failure.cause,
            failure.source_snapshot_kind,
        )
        not in {
            (
                "test",
                "non_proving_package_script",
                "artifact_profile",
                "package_test_script_not_proving",
                "confined_readable_regular_utf8",
            ),
            (
                "entrypoint",
                "untrusted_package_script",
                "artifact_profile",
                "package_entrypoint_target_missing",
                "confined_readable_regular_utf8",
            ),
        }
        or failure.source_path != (PurePosixPath(authority.cwd) / "package.json").as_posix()
    ):
        return None
    artifacts = [
        item
        for item in contract.obligations.artifacts
        if item.path == failure.source_path and item.applicability != "not_applicable"
    ]
    if (
        len(artifacts) != 1
        or artifacts[0].owner_task_id != verifier.owner_task_id
        or artifacts[0].semantic_role not in {"manifest", "source", "config"}
    ):
        return None
    source = reader.read(reader.root / failure.source_path)
    if source.status != "readable" or source.content_sha256 != failure.source_sha256 or reader.drift_reasons():
        return None
    if failure.cause == "package_entrypoint_target_missing":
        entrypoints = [
            item
            for item in contract.obligations.entrypoints
            if item.obligation_id in verifier.covers_obligation_ids
            and item.applicability != "not_applicable"
            and item.owner_task_id == verifier.owner_task_id
            and item.runtime_path == failure.target_path
            and item.command == authority.command
            and any(
                artifact.path == item.source_path
                and artifact.owner_task_id == verifier.owner_task_id
                and artifact.applicability != "not_applicable"
                for artifact in contract.obligations.artifacts
            )
        ]
        if len(entrypoints) != 1 or failure.target_path is None:
            return None
        try:
            _, target_witness = reader.confined_path(reader.root / failure.target_path)
            current = evaluate_verifier_command_policy(
                EvaluateVerifierCommandPolicyQueryV1(
                    workspace=str(reader.root),
                    project_id=contract.project_id,
                    run_id=contract.run_id,
                    task_id=verifier.owner_task_id,
                    completion_contract_hash=contract.contract_hash,
                    verifier_obligation_id=verifier.obligation_id,
                    command_authority_hash=authority.authority_hash,
                    modality="entrypoint",
                    argv=authority.argv,
                    cwd=authority.cwd,
                    input_obligation_ids=tuple(
                        item.obligation_id
                        for item in contract.obligations.artifacts
                        if item.applicability != "not_applicable"
                    ),
                )
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        expected_metadata = {
            "schema_version": "verifier.artifact_admission_failure.v1",
            "failure_domain": failure.failure_domain,
            "cause": failure.cause,
            "source_path": failure.source_path,
            "source_sha256": failure.source_sha256,
            "source_snapshot_kind": failure.source_snapshot_kind,
            "target_path": failure.target_path,
        }
        if (
            target_witness is not None
            or current.authorized is not False
            or current.error_code != failure.policy_error_code
            or dict(current.artifact_failure_metadata) != expected_metadata
            or reader.drift_reasons()
        ):
            return None
    diagnostic_code = (
        "package_entrypoint_target_missing"
        if failure.cause == "package_entrypoint_target_missing"
        else "non_proving_package_script"
    )
    target_detail = f"; declared runtime target={failure.target_path}" if failure.target_path is not None else ""
    return RepairDiagnosticV1(
        source="verifier_admission",
        code=diagnostic_code,
        message=f"artifact_quality_error: verifier admission {diagnostic_code}; repair source ({failure.source_path}){target_detail}",
        path=failure.source_path,
        severity="error",
        metadata={"execution_started": False, "command_receipt_missing": True, "admission_failure": asdict(failure)},
    )


def _materialization_repair_blockers(observation: MaterializationDiagnostics) -> list[str]:
    """Physical coverage still blocks delivery, but does not invent a repair deadlock."""
    if observation._admission_repair_diagnostics:
        return [reason for reason in observation.blockers if reason != "materialization_collector_coverage_incomplete"]
    return observation.blockers


async def _collect_director_stage_materialization_diagnostics(
    executor: Any,
    *,
    run: FactoryRun | None = None,
    context: Mapping[str, Any] | None = None,
    preparation_results: list[dict[str, Any]] | None = None,
    sessions: list[NativeValidationSession] | None = None,
) -> MaterializationDiagnostics:
    """Discover statically, then consume exact CE commands through the receipt owner."""
    from polaris.cells.runtime.execution_broker.public import project_verification as broker
    from polaris.kernelone.quality import scan_workspace_artifact_quality_evidence
    from polaris.kernelone.quality.interface_ledger import WorkspaceReadSnapshot

    preparations = preparation_results if preparation_results is not None else []
    diagnostics: list[str] = []
    results: list[dict[str, Any]] = []
    blockers: list[str] = []
    receipts: list[broker.ProjectVerificationReceiptV1] = []
    admission_diagnostics: list[RepairDiagnosticV1] = []
    planned_verifier_results: list[dict[str, Any]] = []
    static: dict[str, Any] = {"scan_mode": "pure_static", "scan_complete": False, "deferred_checks": []}

    def append_unexecuted_verifiers(*, error: str, phase: str) -> None:
        observed = {row.get("obligation_id") for row in results}
        for planned in planned_verifier_results:
            if planned["obligation_id"] not in observed:
                results.append(
                    {
                        **planned,
                        "exit_code": None,
                        "passed": False,
                        "error": error,
                        "failure_kind": "admission",
                        "phase": phase,
                        "receipt_ref": None,
                        "execution_started": False,
                    }
                )

    try:
        if run is None:
            raise ValueError("materialization verification requires a committed Chief Engineer Factory run")
        bound_context = dict(context or {})
        for key in ("factory_run_id", "run_id"):
            if key in bound_context and bound_context[key] != run.id:
                raise ValueError("materialization_context_run_mismatch:" + key)
        if "workspace" in bound_context and Path(bound_context["workspace"]).resolve() != executor.workspace.resolve():
            raise ValueError("materialization_context_workspace_mismatch")
        from .factory_node_dependencies import authenticated_preparation_contract

        contract = authenticated_preparation_contract(executor, run)
        reader = WorkspaceReadSnapshot(executor.workspace)
        source_scope = _materialization_source_scope(contract, reader=reader)
        project_id = executor._canonical_project_id(bound_context)
        if project_id and contract.project_id != project_id:
            raise ValueError("materialization_context_project_mismatch")
        manifest = reader.read(executor.workspace / "package.json")
        evidence = scan_workspace_artifact_quality_evidence(
            str(executor.workspace), relative_paths=source_scope, scan_mode="pure_static"
        )
        static = evidence.to_dict()
        static["materialization_source_scope"] = {
            "source": "chief_engineer_project_completion_contract",
            "completion_contract_hash": contract.contract_hash,
            "covered_task_ids": list(contract.covered_task_ids),
            "relative_paths": list(source_scope),
            "required_paths": [
                item.path for item in contract.obligations.artifacts if item.applicability == "required"
            ],
            "observed_optional_paths": [
                item.path
                for item in contract.obligations.artifacts
                if item.applicability == "optional" and item.path in source_scope
            ],
            "absent_optional_paths": [
                item.path
                for item in contract.obligations.artifacts
                if item.applicability == "optional" and item.semantic_role != "assets" and item.path not in source_scope
            ],
            "asset_paths": [
                item.path
                for item in contract.obligations.artifacts
                if item.applicability != "not_applicable" and item.semantic_role == "assets"
            ],
            "execution_qualified": False,
        }
        diagnostics.extend(evidence.errors)
        static["materialization_manifest_snapshot"] = {
            "path": manifest.path,
            "status": manifest.status,
            "detail": manifest.detail,
            "content_sha256": manifest.content_sha256,
        }
        if manifest.status not in {"readable", "missing"}:
            blockers.append("materialization_manifest_snapshot_unknown:" + manifest.detail)
        if manifest.status == "missing":
            _, current = reader.confined_path(executor.workspace / "package.json")
            if current is not None:
                blockers.append("materialization_manifest_snapshot_drift:appeared")
        blockers.extend(reader.drift_reasons())
        if evidence.scan_mode != "pure_static":
            blockers.append("materialization_static_scan_mode_invalid")
        # These findings describe a static discovery limit, never execution permission.
        hard_unknown = [
            reason
            for reason in evidence.incomplete_reasons
            if not reason.startswith(("script_invocation_unknown:", "import_target_missing:"))
        ]
        if hard_unknown:
            blockers.append("materialization_static_coverage_incomplete:" + str(tuple(hard_unknown)))
        for check in evidence.deferred_checks:
            project = PurePosixPath(check.project_relative_path or ".")
            if project.is_absolute() or ".." in project.parts:
                blockers.append("materialization_verifier_project_invalid:" + str(project))
        if blockers:
            return MaterializationDiagnostics(diagnostics, static_evidence=static, blockers=blockers)
        deadlines = []
        for key in ("factory_run_deadline_epoch_seconds", "factory_director_execution_deadline_epoch_seconds"):
            value = bound_context.get(key)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("materialization_verifier_deadline_invalid:" + key)
            deadlines.append(float(value))
        if deadlines and min(deadlines) <= time.time():
            raise ValueError("materialization_verifier_deadline_expired")
        from .native_validation_execution import await_verification_worker

        priorities = {"environment_prep": 0, "build": 1, "lint": 2, "test": 3, "entrypoint": 4}
        verifiers = sorted(
            (row for row in contract.obligations.verification if row.applicability == "required"),
            key=lambda row: (priorities[row.modality], row.obligation_id),
        )
        if not verifiers:
            raise ValueError("materialization_required_verifier_authority_missing")
        authorities = {row.authority_hash: row for row in contract.verification_command_authority}
        bound_authorities = {}
        for row in verifiers:
            authority = authorities.get(row.command_authority_hash or "")
            if authority is None:
                raise ValueError("materialization_verifier_command_authority_missing:" + row.obligation_id)
            bound_authorities[row.obligation_id] = authority
        planned_verifier_results = [
            {
                "command": list(bound_authorities[row.obligation_id].argv),
                "cwd": bound_authorities[row.obligation_id].cwd,
                "obligation_id": row.obligation_id,
                "command_authority_hash": bound_authorities[row.obligation_id].authority_hash,
            }
            for row in verifiers
        ]
        needs_node = any(bound_authorities[row.obligation_id].argv[0] in {"node", "npm", "npx"} for row in verifiers)
        if needs_node and not node_dependency_readiness(executor.workspace).ready:
            for session in sessions or ():
                session.close()
            if sessions is not None:
                sessions.clear()
            preparation = await _await_materialization_preparation(executor, run, preparation_results=preparations)
            preparations.append(dict(preparation))
            if preparation.get("passed") is not True:
                raise ValueError("dependency_preparation_failed:" + str(preparation.get("error") or preparation))

        def execute(command: broker.RunProjectVerificationCommandV1, control: ProcessTreeRunControl) -> dict[str, Any]:
            execution = broker.run_project_verification(command, control=control)
            if type(execution) is not broker.ProjectVerificationExecutionResultV1 or execution.receipt is None:
                raise ValueError("materialization_verifier_owner_receipt_missing")
            query = broker.QueryProjectVerificationReceiptV1(
                **{
                    name: getattr(command, name)
                    for name in broker.QueryProjectVerificationReceiptV1.__dataclass_fields__
                }
            )
            receipt = broker.query_project_verification_receipt(query)
            if type(receipt) is not broker.ProjectVerificationReceiptV1 or receipt != execution.receipt:
                raise ValueError("materialization_verifier_owner_receipt_not_current")
            receipts.append(receipt)
            passed = receipt.succeeded
            return {
                "command": list(receipt.argv),
                "cwd": receipt.cwd,
                "obligation_id": receipt.obligation_id,
                "command_authority_hash": receipt.command_authority_hash,
                "exit_code": receipt.exit_code,
                "passed": passed,
                "receipt_ref": receipt.receipt_ref,
                "receipt_hash": receipt.receipt_hash,
                "input_artifact_hash": receipt.input_artifact_hash,
                "output_hash": receipt.output_hash,
                "diagnostic_excerpt": receipt.output_excerpt,
                "error": "" if passed else "project_verification_proof_failed",
                "failure_kind": ""
                if passed
                else "timeout"
                if receipt.timed_out
                else "nonzero"
                if receipt.exit_code not in {None, 0}
                else "verifier_failure",
            }

        for verifier in verifiers:
            authority = bound_authorities[verifier.obligation_id]
            try:
                command = broker.authorize_project_verification_command(
                    broker.ResolveProjectVerificationAuthorityQueryV1(
                        workspace=str(executor.workspace),
                        project_id=contract.project_id,
                        run_id=run.id,
                        completion_contract_hash=contract.contract_hash,
                        obligation_id=verifier.obligation_id,
                    )
                )
            except ProjectCompletionOwnerObservationV1Error as exc:
                diagnostic = _materialization_admission_repair_diagnostic(
                    error=exc, contract=contract, verifier=verifier, authority=authority, reader=reader
                )
                if diagnostic is None:
                    raise
                admission_diagnostics.append(diagnostic)
                # Preserve full static evidence, but keep mutable placeholder text
                # out of the diagnostic signature used to measure repair progress.
                placeholder_errors = {
                    (issue.metadata or {}).get("raw")
                    for issue in evidence.issues
                    if issue.path == diagnostic.path
                    and issue.code == "npm_manifest_invalid"
                    and (issue.metadata or {}).get("script_name") == "test"
                    and (issue.metadata or {}).get("package_script_issue_code") == "npm_placeholder_script"
                }
                diagnostics = [message for message in diagnostics if message not in placeholder_errors]
                diagnostics.append(diagnostic.message)
                results.append(
                    {
                        **next(
                            row for row in planned_verifier_results if row["obligation_id"] == verifier.obligation_id
                        ),
                        "exit_code": None,
                        "passed": False,
                        "error": exc.error_code,
                        "failure_kind": "admission",
                        "phase": "artifact_profile",
                        "receipt_ref": None,
                        "execution_started": False,
                        "repair_diagnostic": {
                            "source": diagnostic.source,
                            "code": diagnostic.code,
                            "message": diagnostic.message,
                            "path": diagnostic.path,
                            "severity": diagnostic.severity,
                            "metadata": dict(diagnostic.metadata),
                        },
                    }
                )
                append_unexecuted_verifiers(error="materialization_verifier_dependency_failed", phase="dependency")
                break
            expected = (
                verifier.owner_task_id,
                verifier.modality,
                authority.argv,
                authority.cwd,
                authority.authority_hash,
            )
            observed = (
                command.owner_task_id,
                command.modality,
                command.argv,
                command.cwd,
                command.command_authority_hash,
            )
            if observed != expected:
                raise ValueError("materialization_verifier_command_identity_mismatch")
            remaining = min(deadlines) - time.time() if deadlines else None
            if remaining is not None and remaining <= 0:
                raise ValueError("materialization_verifier_deadline_expired")
            try:
                async with asyncio.timeout(remaining):
                    result = await await_verification_worker(partial(execute, command))
            except FileNotFoundError as exc:
                diagnostic = _missing_required_entrypoint_diagnostic(
                    contract=contract,
                    command=command,
                    command_text=authority.command,
                    error=exc,
                    receipts=receipts,
                )
                if diagnostic is None:
                    raise
                diagnostics.append(diagnostic.message)
                results.append(
                    {
                        **next(
                            row for row in planned_verifier_results if row["obligation_id"] == verifier.obligation_id
                        ),
                        "exit_code": None,
                        "passed": False,
                        "error": f"{type(exc).__name__}:{exc}",
                        "failure_kind": "admission",
                        "phase": "entrypoint_artifact_missing",
                        "receipt_ref": None,
                        "repair_diagnostic": {
                            "source": diagnostic.source,
                            "code": diagnostic.code,
                            "message": diagnostic.message,
                            "path": diagnostic.path,
                            "severity": diagnostic.severity,
                            "metadata": dict(diagnostic.metadata),
                        },
                    }
                )
                append_unexecuted_verifiers(error="materialization_verifier_dependency_failed", phase="dependency")
                break
            results.append(result)
            diagnostics.extend(_materialization_verifier_diagnostics(result, receipt=receipts[-1]))
            if result["passed"] is not True:
                # Failed build output cannot be used to qualify dependent tests.
                append_unexecuted_verifiers(error="materialization_verifier_dependency_failed", phase="dependency")
                break
        static["controlled_check_ids"] = [row["obligation_id"] for row in results]
        static["controlled_checks_complete"] = len(receipts) == len(verifiers)
    except (ProcessTreeCancelledError, ProcessTreeDrainError):
        raise
    except (OSError, RuntimeError, TypeError, ValueError, KeyError) as exc:
        blockers.append(f"materialization_verification_admission_failed:{type(exc).__name__}:{exc}")
        append_unexecuted_verifiers(error=f"{type(exc).__name__}:{exc}", phase="admission")
    observation = MaterializationDiagnostics(
        diagnostics,
        static_evidence=static,
        verifier_results=results,
        blockers=blockers,
        preparation_results=preparations,
    )
    observation._owner_receipts = tuple(receipts)
    observation._admission_repair_diagnostics = tuple(admission_diagnostics)
    return observation


def _claim_director_stage_materialization_settle_attempt(
    executor: Any,
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
    executor: Any,
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
    executor: Any,
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
    assert isinstance(observation, MaterializationDiagnostics)
    static = observation.static_evidence
    if observation._owner_receipts and all(
        type(receipt) is ProjectVerificationReceiptV1 for receipt in observation._owner_receipts
    ):
        # Owner-sealed physical success/failure replaces static confidence as
        # verification feedback. Admission blockers and failed verdicts remain.
        return observation
    if (
        static.get("scan_mode") != "pure_static"
        or static.get("scan_complete") is not True
        or (static.get("deferred_checks") and static.get("controlled_checks_complete") is not True)
    ):
        observation.blockers.append("materialization_collector_coverage_incomplete")
    return observation


def _current_materialization_receipts(
    executor: Any, run: FactoryRun, results: Sequence[Mapping[str, Any]]
) -> tuple[ProjectVerificationReceiptV1, ...]:
    """Capture the current owner-sealed baseline before a candidate changes it."""
    from polaris.cells.runtime.execution_broker.public import project_verification as broker

    from .factory_node_dependencies import authenticated_preparation_contract

    if not any(row.get("receipt_hash") for row in results):
        return ()
    contract = authenticated_preparation_contract(executor, run)
    receipts = []
    for row in results:
        if not row.get("receipt_ref"):
            continue
        command = broker.authorize_project_verification_command(
            broker.ResolveProjectVerificationAuthorityQueryV1(
                workspace=str(executor.workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=str(row["obligation_id"]),
            )
        )
        receipt = broker.query_project_verification_receipt(
            broker.QueryProjectVerificationReceiptV1(
                **{
                    name: getattr(command, name)
                    for name in broker.QueryProjectVerificationReceiptV1.__dataclass_fields__
                }
            )
        )
        if (
            type(receipt) is not ProjectVerificationReceiptV1
            or receipt.receipt_ref != row.get("receipt_ref")
            or receipt.receipt_hash != row.get("receipt_hash")
            or receipt.argv != tuple(row.get("command") or ())
            or receipt.command_authority_hash != row.get("command_authority_hash")
            or receipt.exit_code != row.get("exit_code")
            or receipt.succeeded is not row.get("passed")
        ):
            raise ValueError("materialization_baseline_verifier_receipt_not_current")
        receipts.append(receipt)
    return tuple(receipts)


def _entrypoint_missing_after_verified_frontier_advance(
    before: Sequence[ProjectVerificationReceiptV1], after: MaterializationDiagnostics
) -> bool:
    """Preserve a proven test repair that exposes only a declared missing entry."""
    if not before or after.blockers:
        return False
    missing: list[Mapping[str, Any]] = []
    for row in after.verifier_results:
        if row.get("passed") is True:
            continue
        item = row.get("repair_diagnostic")
        if not isinstance(item, Mapping):
            return False
        metadata = item.get("metadata")
        if (
            item.get("code") != "failed_required_entrypoint"
            or not isinstance(metadata, Mapping)
            or metadata.get("execution_started") is not False
            or metadata.get("command_receipt_missing") is not True
        ):
            return False
        missing.append(item)
    if not missing:
        return False
    if set(after) != {item["message"] for item in missing}:
        return False
    current = {receipt.obligation_id: receipt for receipt in after._owner_receipts}
    improved = False
    for previous in before:
        receipt = current.get(previous.obligation_id)
        if (
            type(previous) is not ProjectVerificationReceiptV1
            or receipt is None
            or type(receipt) is not ProjectVerificationReceiptV1
        ):
            return False
        identity = (
            "workspace",
            "project_id",
            "run_id",
            "completion_contract_hash",
            "owner_task_id",
            "modality",
            "argv",
            "cwd",
            "command_authority_hash",
            "job_token_id",
            "job_token_set_hash",
            "execution_policy_hash",
            "authority_revision",
            "policy_profile_id",
            "policy_decision_hash",
            "executable_path",
            "executable_realpath",
            "executable_hash",
        )
        if any(getattr(previous, name) != getattr(receipt, name) for name in identity) or not receipt.succeeded:
            return False
        if {(item.obligation_id, item.path) for item in previous.input_artifacts} != {
            (item.obligation_id, item.path) for item in receipt.input_artifacts
        }:
            return False
        if not previous.succeeded:
            if previous.modality != "test" or previous.exit_code in {None, 0}:
                return False
            improved = True
    return improved


async def _continue_materialization_verifier_repair(
    executor: Any,
    *,
    run: FactoryRun,
    context: dict[str, Any],
    result: dict[str, Any],
    sessions: list[NativeValidationSession],
    observations: list[dict[str, Any]],
    collect_diagnostics: Any = None,
    repair_candidate: Any = None,
) -> dict[str, Any]:
    """Use the existing Director repair owner after deterministic settlement.

    The prior attempt is closed before this bridge claims an original CE owner.
    Provider success never settles the new attempt: its receipt-bound candidate
    must pass controlled verification or demonstrate the existing progress rule.
    """
    from .factory_workspace_quality_impl import (
        _apply_workspace_quality_llm_repairs,
        _finish_workspace_quality_repair_candidate,
        _record_workspace_quality_repair_artifact_receipts,
        _settle_pending_workspace_quality_repair_attempt,
        _stop_workspace_quality_repair_heartbeat,
        _workspace_quality_candidate_advances,
        _workspace_quality_test_frontier_reduced,
        _WorkspaceRepairContinuation,
    )

    errors = list(result.get("post_commit_diagnostics") or ())
    pending: Mapping[str, Any] | None = None
    preparations: list[dict[str, Any]] = []
    authority_binding = None
    baseline_restored = False
    continuation = _WorkspaceRepairContinuation(executor, context)
    seen_codes: set[str] = set()
    try:
        for round_number in continuation.rounds():
            if not errors:
                break
            abort = await executor._quality_gate_abort_reason(executor._resolve_abort_checker(context))
            if abort:
                result["convergence_stop_reason"] = "quality_repair_cancelled"
                break
            before_results = observations[-1].get("verifier_results", []) if observations else []
            baseline_receipts = (
                () if baseline_restored else _current_materialization_receipts(executor, run, before_results)
            )
            for session in sessions:
                session.before_repair()
            authority_port = executor._factory_role_evidence_cutoff_port(context)
            if authority_binding is None:
                authority_binding = authority_port.mint_authority_binding("director")

            async def repair_operation(_errors=tuple(errors), _round=round_number):
                if repair_candidate is not None:
                    return await repair_candidate(errors=list(_errors), attempt=_round + 1)
                return await _apply_workspace_quality_llm_repairs(
                    executor,
                    run=run,
                    context=context,
                    artifact_quality_errors=list(_errors),
                    repair_attempt=int(result.get("repair_round_count") or 0) + _round + 1,
                )

            tools, summary = await executor._call_with_factory_role_evidence_authority(
                authority_port,
                "director",
                repair_operation,
                authority_binding=authority_binding,
            )
            summary = dict(summary)
            pending = summary.pop("_pending_task_runtime_repair_attempt", None)
            result["llm_repair"] = {
                key: value
                for key, value in summary.items()
                if key
                in {
                    "attempted",
                    "success",
                    "error",
                    "error_code",
                    "repair_mode",
                    "task_id",
                    "stage",
                    "success_reason",
                    "llm_fallback_blocked",
                }
                and (value is None or type(value) in {str, bool, int, float})
            }
            scope_filter = summary.get("task_boundary_scope_filter")
            if isinstance(scope_filter, Mapping):
                projected_scope = {
                    key: value
                    for key, value in scope_filter.items()
                    if key in {"schema_version", "reason", "deferred"}
                    and (value is None or type(value) in {str, bool, int, float})
                }
                for key in ("task_declared_write_targets", "out_of_scope_repair_target_files"):
                    value = scope_filter.get(key)
                    if isinstance(value, (list, tuple)):
                        projected_scope[key] = [item for item in value[:12] if isinstance(item, str)]
                requests = scope_filter.get("ownership_handoff_requests")
                if isinstance(requests, (list, tuple)):
                    projected_scope["ownership_handoff_requests"] = [
                        {
                            key: value
                            for key, value in item.items()
                            if key in {"target_file", "owner_found", "owner_task_id", "recommended_route", "reason"}
                            and (value is None or type(value) in {str, bool, int, float})
                        }
                        for item in requests[:12]
                        if isinstance(item, Mapping)
                    ]
                result["llm_repair"]["task_boundary_scope_filter"] = projected_scope
            result["tool_result_count"] = int(result.get("tool_result_count") or 0) + len(tools)
            if not isinstance(pending, Mapping):
                pending = None
                break
            effects = [
                item
                for item in tools
                if isinstance(item, Mapping)
                and (
                    str(item.get("tool_name") or item.get("tool") or "")
                    in {"write_file", "edit_file", "edit_blocks", "delete_file"}
                    or (
                        isinstance(item.get("effect_receipt"), Mapping)
                        and item["effect_receipt"].get("normalized_tool_name")
                        in {"write_file", "edit_file", "edit_blocks", "delete_file"}
                    )
                )
            ]
            for session in sessions:
                session.candidate(pending=pending, results=effects)
            # Artifact receipts attest landed bytes independently of quality.
            # Refresh them before a dependency change may require a new group.
            _record_workspace_quality_repair_artifact_receipts(pending)
            observation = _materialization_observation_with_coverage(
                await collect_diagnostics()
                if collect_diagnostics is not None
                else await executor._collect_director_stage_materialization_diagnostics(
                    run=run,
                    context=context,
                    sessions=sessions,
                    preparation_results=preparations,
                )
            )
            observations.append(observation.to_dict())
            if _materialization_repair_blockers(observation):
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
            if effect not in {"resolved", "regression"} and _workspace_quality_test_frontier_reduced(
                before_results,
                observation.verifier_results,
                before_receipts=baseline_receipts,
                after_receipts=observation._owner_receipts,
            ):
                effect = "progress"
            if (
                collect_diagnostics is not None
                and before_results
                and all(row.get("passed") is True and row.get("exit_code") == 0 for row in before_results)
                and after_errors
            ):
                # A semantic QA finding is not a failed physical baseline.
                effect = "regression"
            if effect == "equal_count_swap" and _entrypoint_missing_after_verified_frontier_advance(
                baseline_receipts, observation
            ):
                # A successful test repair can expose an entry prerequisite
                # with the same diagnostic count. Receipts prove the frontier;
                # the missing entry still prevents final completion.
                effect = "progress"
            after_codes = executor._workspace_quality_diagnostic_error_codes(
                executor._workspace_quality_diagnostic_signature(after_errors)
            )
            accepted = _workspace_quality_candidate_advances(effect, after_codes=after_codes, seen_codes=seen_codes)
            seen_codes.update(after_codes)
            settlement, post = await _finish_workspace_quality_repair_candidate(
                executor,
                pending,
                run=run,
                context=context,
                sessions=sessions,
                observations=observations,
                accepted=accepted,
                reason=f"director_stage_verifier_repair_{effect}",
                verifier_resolved=not after_errors,
                revalidate=collect_diagnostics,
            )
            pending = None
            baseline_restored = not accepted
            result["llm_repair"]["verifier_effect"] = effect
            result["llm_repair"]["task_runtime_repair_attempt"] = settlement
            continuation.observe(progress=accepted)
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
                if collect_diagnostics is not None:
                    restored = _materialization_observation_with_coverage(await collect_diagnostics())
                    observations.append(restored.to_dict())
                    if _materialization_repair_blockers(restored):
                        raise _MaterializationVerificationBlockedError(restored)
                continue
            result["post_commit_diagnostics"] = after_errors
            result["post_commit_diagnostic_count"] = len(after_errors)
            if post is not None:
                settlement = post
                result["llm_repair"]["post_verification_attempt"] = post
                after_errors = list(observations[-1].get("diagnostics") or ())
                result["post_commit_diagnostics"] = after_errors
                result["post_commit_diagnostic_count"] = len(after_errors)
            expected_outcome = "completed" if effect == "resolved" else "failed"
            if not settlement or settlement.get("outcome") != expected_outcome or not settlement.get("settled"):
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
    result["convergence_stop_reason"] = continuation.stop_reason or result.get("convergence_stop_reason", "")
    return result


async def _revalidate_materialization_original_owner(
    executor: Any,
    *,
    run: FactoryRun,
    context: dict[str, Any],
    original_owner_task_id: str,
    original_task_row_id: str,
    expected_completion_contract_hash: str,
    original_owned_paths: list[str],
    sessions: list[NativeValidationSession],
    observations: list[dict[str, Any]],
    collect_diagnostics: Any = None,
) -> dict[str, Any]:
    """Reuse the existing CE claim and controlled verifier paths without new effects."""
    from polaris.cells.runtime.task_runtime.public import create_task_runtime_execution_attempt_authority

    from .factory_workspace_quality_impl import (
        _claim_workspace_quality_repair_attempt,
        _record_workspace_quality_repair_artifact_receipts,
        _run_workspace_quality_repair_heartbeat,
        _stop_workspace_quality_repair_heartbeat,
        _task_completion_projection_from_repair_task,
    )

    for session in sessions:
        session.before_repair()
    owner_id, task_row_id, attempt, repair_task = _claim_workspace_quality_repair_attempt(
        executor,
        run=run,
        repair_attempt=1,
        target_files=original_owned_paths,
        original_owner_task_id=original_owner_task_id,
    )
    observation = None
    artifact_receipts: tuple[dict[str, str], ...] = ()
    projection = None
    heartbeat_stop = asyncio.Event()
    heartbeat_failures: list[dict[str, Any]] = []
    heartbeat_task = None
    try:
        if owner_id != original_owner_task_id or str(task_row_id) != original_task_row_id:
            raise ValueError("materialization_post_verification_owner_mismatch")
        projection = _task_completion_projection_from_repair_task(repair_task)
        heartbeat_task = asyncio.create_task(
            _run_workspace_quality_repair_heartbeat(
                create_task_runtime_execution_attempt_authority(attempt),
                stop=heartbeat_stop,
                failures=heartbeat_failures,
                context_summary="director_stage_post_verification",
            )
        )
        commit_context = executor._director_stage_materialization_settle_commit_context(
            run=run,
            run_id=run.id,
            diagnostics=[],
            repair_task=repair_task,
        )
        if (
            not expected_completion_contract_hash
            or commit_context["task_completion_projection"].get("project_contract_hash")
            != expected_completion_contract_hash
            or not projection
            or projection.get("project_contract_hash") != expected_completion_contract_hash
        ):
            raise ValueError("materialization_post_verification_contract_mismatch")
        artifact_receipts = _record_workspace_quality_repair_artifact_receipts(
            {
                "task_id": owner_id,
                "execution_attempt": attempt,
                "task_completion_projection": projection,
            }
        )
        observation = _materialization_observation_with_coverage(
            await collect_diagnostics()
            if collect_diagnostics is not None
            else await executor._collect_director_stage_materialization_diagnostics(
                run=run, context=context, sessions=sessions
            )
        )
        observations.append(observation.to_dict())
        passed = (
            not observation
            and not observation.blockers
            and observation.static_evidence.get("controlled_checks_complete") is True
        )
        await _stop_workspace_quality_repair_heartbeat(heartbeat_task, heartbeat_stop)
        if heartbeat_failures:
            raise RuntimeError("materialization_post_verification_heartbeat_failed")
    except (ProcessTreeCancelledError, ProcessTreeDrainError):
        # A live physical verifier remains owned; no terminal close is inferred.
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        executor._settle_director_stage_materialization_attempt(
            task_row_id=task_row_id,
            execution_attempt=attempt,
            stage_status="failed",
            summary=f"director_stage_post_verification_admission_failed:{type(exc).__name__}:{exc}",
        )
        raise
    finally:
        if heartbeat_task is not None:
            await _stop_workspace_quality_repair_heartbeat(heartbeat_task, heartbeat_stop)
    closed = executor._settle_director_stage_materialization_attempt(
        task_row_id=task_row_id,
        execution_attempt=attempt,
        stage_status="success" if passed else "failed",
        summary="director_stage_post_verification_passed" if passed else "director_stage_post_verification_failed",
    )
    return {
        "task_id": owner_id,
        "task_row_id": str(task_row_id),
        "session_id": attempt.session_id,
        "settled": closed.get("success") is True,
        "outcome": "completed" if passed else "failed",
        "project_artifact_receipt_count": len(artifact_receipts),
        "settlement_code": closed.get("code") or closed.get("reason"),
        "settlement_evidence": dict(closed),
        "completion_contract_hash": projection.get("project_contract_hash") if projection else None,
        "verifier_diagnostics": list(observation),
        "verifier_blockers": list(observation.blockers),
        "verifier_results": observation.verifier_results,
    }


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
        if _materialization_repair_blockers(observation):
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
            from .factory_workspace_quality_impl import _WorkspaceRepairContinuation

            deterministic_continuation = _WorkspaceRepairContinuation(executor, context)
            for _owner_round in deterministic_continuation.rounds():
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
                deterministic_effect = executor._workspace_quality_repair_effect(
                    before_signature=executor._workspace_quality_diagnostic_signature(current_diagnostics),
                    after_signature=executor._workspace_quality_diagnostic_signature(post_commit_diagnostics),
                    verifier_passed=False,
                    write_tool_evidence=round_committed,
                    workspace=Path(executor.workspace),
                )
                deterministic_continuation.observe(progress=deterministic_effect in {"progress", "forward_unmask"})
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
