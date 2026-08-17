"""Claim/finalize/task-runtime settlement helpers for Director execute."""

from __future__ import annotations

import fnmatch as fnmatch
import hashlib as hashlib
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_verified_existing_artifact_lifecycle_receipt,
    evaluate_task_boundary_verdict,
)
from polaris.cells.runtime.execution_broker.public import (
    RecordProjectArtifactCommandV1,
    record_project_artifact,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
)
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)

from ..helpers import (
    _DEFAULT_TASK_LEASE_TTL_SECONDS,
    taskboard_snapshot_brief,
)
from ._helpers import (
    _adapter_materialized_file_paths,
    _deterministic_repair_profile_summary_from_tool_results,
)

logger = logging.getLogger(__name__)


def _job_token_from_director_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the CE/control-plane JobToken already bound to this Director claim."""

    if not isinstance(context, Mapping):
        return {}
    candidates: list[Any] = [
        context.get("job_token"),
        context.get("capability_token"),
        context.get("control_plane_job_token"),
    ]
    metadata = context.get("metadata")
    if isinstance(metadata, Mapping):
        candidates.extend(
            (
                metadata.get("job_token"),
                metadata.get("capability_token"),
                metadata.get("control_plane_job_token"),
            )
        )
    for raw in candidates:
        if isinstance(raw, Mapping) and str(raw.get("token_id") or "").strip():
            return dict(raw)
    return {}


def _project_preflight_execution_capability(
    adapter: Any,
    *,
    context: Mapping[str, Any] | None,
    target_task_id: str,
    contract_task_id: str,
    run_id: str,
) -> None:
    """Project a pending_exec JobToken for preflight-only artifact receipt.

    Live L2-19 remint-1: TASK-3-foundation accepted existing
    ``requirements.txt`` with zero tools, then
    ``record_project_artifact`` fail-closed because
    ``execution_capability_by_task`` only indexes ``pending_exec``
    ``tool_receipt`` gates.  CE already minted
    ``job-f98bbeae7ce542f388691e3f`` on the blueprint; preflight must
    append that token before sealing ProjectArtifactReceiptV1.
    """

    token = _job_token_from_director_context(context)
    token_id = str(token.get("token_id") or "").strip()
    owner = str(contract_task_id or "").strip() or str(target_task_id or "").strip()
    workspace = str(getattr(adapter, "workspace", "") or "").strip()
    ledger_run_id = str(token.get("factory_run_id") or token.get("run_id") or run_id or "").strip()
    if not token_id or not owner or not workspace or not ledger_run_id:
        return
    projected_token = dict(token)
    projected_token["task_id"] = owner
    projected_token["stage"] = "pending_exec"
    # Capability grants must not inherit CE verifier obligations.  Live
    # L2-19 remint-9: copying required_evidence_modalities made every
    # preflight gate look like missing qa/code/command after QA PASS.
    gate_policy = dict(projected_token.get("gate_policy") or {})
    if gate_policy:
        gate_policy["required_evidence_modalities"] = []
        gate_policy["required_modalities"] = []
        projected_token["gate_policy"] = gate_policy
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=workspace,
            run_id=ledger_run_id,
            event={
                "event_type": "gate_evaluated",
                "stage": "pending_exec",
                "gate": {
                    "name": "existing_scope_preflight",
                    "ok": True,
                    "summary": "existing declared scope accepted under task-local JobToken",
                },
                "job_token": projected_token,
                "physical_evidence": {
                    "command_count": 0,
                    "sampled_command_count": 0,
                    "commands_truncated": False,
                    "metadata": {
                        "role": "director",
                        "task_id": owner,
                        "runtime_task_id": str(target_task_id or "").strip(),
                        "source": "director.existing_scope_preflight",
                    },
                },
            },
        )
    )


def _append_receipt_bound_preflight_task_boundary(
    adapter: Any,
    *,
    context: Mapping[str, Any],
    target_task_id: str,
    run_id: str,
    finalize_result: Mapping[str, Any],
    receipt_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Commit the successful boundary fact for a no-write, receipt-bound retry.

    Provider turns append their own TaskBoundary verdict.  A Director retry
    that completes entirely in existing-scope preflight has no provider turn,
    so without this projection an older ``mutation_bypass_blocked`` verdict
    remains latest even after TaskRuntime settles completed.  Only exact,
    byte-current ProjectArtifactReceiptV1 evidence may close that gap.
    """

    if (
        receipt_evidence.get("ok") is not True
        or receipt_evidence.get("schema_version") != "polaris.current_task_project_artifact_receipt_evidence.v1"
        or receipt_evidence.get("authority") != "runtime.execution_broker.project_artifact_receipt.v1"
    ):
        raise ValueError("receipt-bound preflight lacks current project artifact evidence")
    identity = finalize_result.get("identity")
    if not isinstance(identity, Mapping):
        raise ValueError("receipt-bound preflight lacks settled TaskRuntime identity")
    external_task_id = str(identity.get("external_task_id") or "").strip()
    if not external_task_id:
        raise ValueError("receipt-bound preflight lacks external task identity")
    projection = _task_completion_projection_from_context(
        context,
        target_task_id=target_task_id,
    )
    if not isinstance(projection, Mapping):
        raise ValueError("receipt-bound preflight lacks task completion projection")
    if _canonical_task_owner_identity(projection.get("task_id")) != _canonical_task_owner_identity(external_task_id):
        raise ValueError("receipt-bound preflight projection owner does not match settled task")
    target_files = [
        str(artifact.get("path") or "").strip()
        for artifact in projection.get("owned_artifacts", ())
        if isinstance(artifact, Mapping)
        and str(artifact.get("applicability") or "required").strip().lower() == "required"
        and str(artifact.get("path") or "").strip()
    ]
    receipt_paths = [str(path or "").strip() for path in receipt_evidence.get("receipt_paths", ())]
    receipt_refs = [str(ref or "").strip() for ref in receipt_evidence.get("receipt_refs", ())]
    if (
        not target_files
        or sorted(set(target_files)) != sorted(set(receipt_paths))
        or len(set(receipt_refs)) != len(set(target_files))
        or int(receipt_evidence.get("receipt_count") or 0) != len(set(target_files))
        or int(receipt_evidence.get("required_artifact_count") or 0) != len(set(target_files))
    ):
        raise ValueError("receipt-bound preflight evidence does not cover exact owned artifacts")
    verdict = evaluate_task_boundary_verdict(
        workspace=str(getattr(adapter, "workspace", "") or ""),
        task_id=external_task_id,
        run_id=run_id,
        target_files=target_files,
        completed_artifacts=target_files,
        evidence_refs=receipt_refs,
    )
    if verdict.ok is not True or verdict.status != "completed_verified":
        raise RuntimeError(f"receipt-bound task boundary remained incomplete: {verdict.status}")
    project_id = str(projection.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("receipt-bound preflight lacks project identity")
    lifecycle_receipt = build_verified_existing_artifact_lifecycle_receipt(
        run_id=run_id,
        task_id=external_task_id,
        artifact_receipt_refs=receipt_refs,
    )
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(getattr(adapter, "workspace", "") or ""),
            run_id=run_id,
            task_id=external_task_id,
            turn_id="",
            role="director",
            lifecycle_receipt=lifecycle_receipt.to_dict(),
            stage="director_receipt_bound_preflight",
            project_id=project_id,
            ok=True,
        )
    )
    payload = verdict.to_dict()
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(getattr(adapter, "workspace", "") or ""),
            run_id=run_id,
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": external_task_id,
                "run_id": run_id,
                "task_boundary_verdict": payload,
                "job_token": {
                    "run_id": run_id,
                    "task_id": external_task_id,
                    "project_id": project_id,
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )
    return payload


def _declared_scope_existing_file_evidence_refs(
    *,
    workspace: str,
    existing_paths: Sequence[str],
) -> list[str]:
    """Bind declared existing files to content-addressed TaskBoundary refs.

    CE ``owned_artifacts`` can be empty for split foundation rows. Receipt-bound
    preflight then has nothing to record, but the files still exist. Hash the
    current UTF-8/binary bytes so ``completed_verified`` cannot be sealed with
    empty ``evidence_refs``.
    """

    root = Path(workspace).expanduser().resolve()
    refs: list[str] = []
    for raw in existing_paths:
        relative = str(raw or "").strip().replace("\\", "/")
        if not relative:
            continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        refs.append(f"workspace-file-sha256:{digest}:{relative}")
    return refs


def _append_declared_scope_preflight_task_boundary(
    adapter: Any,
    *,
    context: Mapping[str, Any],
    target_task_id: str,
    run_id: str,
    finalize_result: Mapping[str, Any],
    existing_paths: Sequence[str],
) -> dict[str, Any]:
    """Seal TaskBoundary from declared existing paths when CE owned_artifacts is empty.

    Live L2-12 TASK-3-foundation: PM split owns ``requirements.txt`` while the
    CE completion projection assigned that file to TASK-1 and left the
    foundation owner with zero ``owned_artifacts``. Receipt-bound preflight
    then skipped the ledger append (``required_artifact_count=0``) and
    Factory fail-closed ``task_boundary_verdict_missing`` after TaskRuntime
    already completed. Declared on-disk scope is the owner write-set.
    """

    identity = finalize_result.get("identity")
    if not isinstance(identity, Mapping):
        raise ValueError("declared-scope preflight lacks settled TaskRuntime identity")
    external_task_id = str(identity.get("external_task_id") or "").strip()
    if not external_task_id:
        raise ValueError("declared-scope preflight lacks external task identity")
    projection = _task_completion_projection_from_context(
        context,
        target_task_id=target_task_id,
    )
    if not isinstance(projection, Mapping):
        raise ValueError("declared-scope preflight lacks task completion projection")
    if _canonical_task_owner_identity(projection.get("task_id")) != _canonical_task_owner_identity(external_task_id):
        raise ValueError("declared-scope preflight projection owner does not match settled task")
    target_files = [str(path or "").strip() for path in existing_paths if str(path or "").strip()]
    if not target_files:
        raise ValueError("declared-scope preflight lacks existing declared paths")
    workspace = str(getattr(adapter, "workspace", "") or "")
    evidence_refs = _declared_scope_existing_file_evidence_refs(
        workspace=workspace,
        existing_paths=target_files,
    )
    if len(evidence_refs) != len(target_files):
        raise ValueError("declared-scope preflight could not bind evidence refs for every existing path")
    verdict = evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=external_task_id,
        run_id=run_id,
        target_files=target_files,
        completed_artifacts=target_files,
        evidence_refs=evidence_refs,
    )
    if verdict.ok is not True or verdict.status != "completed_verified":
        raise RuntimeError(f"declared-scope task boundary remained incomplete: {verdict.status}")
    project_id = str(projection.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("declared-scope preflight lacks project identity")
    payload = verdict.to_dict()
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(getattr(adapter, "workspace", "") or ""),
            run_id=run_id,
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": external_task_id,
                "run_id": run_id,
                "task_boundary_verdict": payload,
                "job_token": {
                    "run_id": run_id,
                    "task_id": external_task_id,
                    "project_id": project_id,
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {},
                },
            },
        )
    )
    return payload


def _project_dependency_artifact_tool_results(
    tool_results: Sequence[Any] | None,
) -> list[dict[str, Any]]:
    """Project write tool_results into receipt-bound rows for sibling exports.

    Dependent Director tasks build ``actual_sibling_exports`` from parent
    ``metadata.adapter_result`` (see dependency_artifact_evidence). Completion
    used to store only new_files/write_tool_evidence flags, so TASK-2 failed
    closed with ``missing_required_refs=actual_sibling_exports`` despite TASK-1
    materializing files (r129 L1-01).
    """

    projected: list[dict[str, Any]] = []
    if not isinstance(tool_results, Sequence) or isinstance(tool_results, (str, bytes)):
        return projected
    for raw in tool_results:
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "").strip().lower()
        success = raw.get("success")
        if success is False or status in {"failed", "error"}:
            continue
        nested = raw.get("result")
        result_payload = dict(nested) if isinstance(nested, Mapping) else {}
        effect_receipt = raw.get("effect_receipt")
        if not isinstance(effect_receipt, Mapping):
            effect_receipt = result_payload.get("effect_receipt")
        if not isinstance(effect_receipt, Mapping):
            continue
        commit = raw.get("effect_receipt_commit")
        if not isinstance(commit, Mapping):
            commit = result_payload.get("effect_receipt_commit")
        file_path = str(
            result_payload.get("file") or result_payload.get("path") or raw.get("file") or raw.get("path") or ""
        ).strip()
        if not file_path:
            continue
        row: dict[str, Any] = {
            "status": "success",
            "success": True,
            "tool": str(raw.get("tool") or raw.get("tool_name") or "write_file").strip() or "write_file",
            "tool_name": str(raw.get("tool_name") or raw.get("tool") or "write_file").strip() or "write_file",
            "result": {"file": file_path},
            "effect_receipt": dict(effect_receipt),
        }
        if isinstance(commit, Mapping) and commit:
            row["effect_receipt_commit"] = dict(commit)
        projected.append(row)
    return projected


def _attach_dependency_artifact_receipt_evidence(
    adapter_result: dict[str, Any],
    *,
    tool_results: Sequence[Any] | None = None,
    primary_llm_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ensure adapter_result carries receipt rows for sibling-export projection."""

    projected = _project_dependency_artifact_tool_results(tool_results)
    if projected:
        adapter_result["tool_results"] = projected
    for nested_key in ("no_write_materialization_retry", "empty_write_content_retry"):
        nested = adapter_result.get(nested_key)
        if not isinstance(nested, dict):
            continue
        nested_batch = nested.get("batch_receipt")
        if isinstance(nested_batch, dict) and nested_batch and "batch_receipt" not in adapter_result:
            adapter_result["batch_receipt"] = dict(nested_batch)
        nested_tools = nested.get("tool_results")
        if isinstance(nested_tools, list) and nested_tools and "tool_results" not in adapter_result:
            nested_projected = _project_dependency_artifact_tool_results(nested_tools)
            if nested_projected:
                adapter_result["tool_results"] = nested_projected
    if isinstance(primary_llm_summary, dict):
        batch_receipt = primary_llm_summary.get("batch_receipt")
        if isinstance(batch_receipt, dict) and batch_receipt:
            adapter_result["batch_receipt"] = dict(batch_receipt)
        metadata = primary_llm_summary.get("metadata")
        if isinstance(metadata, dict):
            nested_batch = metadata.get("batch_receipt")
            if isinstance(nested_batch, dict) and nested_batch and "batch_receipt" not in adapter_result:
                adapter_result["batch_receipt"] = dict(nested_batch)
            nested_tools = metadata.get("tool_results")
            if isinstance(nested_tools, list) and nested_tools and "tool_results" not in adapter_result:
                nested_projected = _project_dependency_artifact_tool_results(nested_tools)
                if nested_projected:
                    adapter_result["tool_results"] = nested_projected
    return adapter_result


_TASK_NUMERIC_OWNER_RE = re.compile(r"(?i:task[-_])?0*(?P<num>\d+)$")
_TASK_SPLIT_OWNER_RE = re.compile(
    r"(?i:task[-_])?0*(?P<num>\d+)-(?P<kind>"
    r"foundation|tests|docs|source-models|source-core|source-modules|entrypoints)$"
)


def _canonical_task_owner_identity(value: Any) -> str:
    """Normalize the TaskRuntime integer alias without weakening task ownership.

    TaskRuntime stores the local row as an integer (``1``) while the PM/CE
    contract keeps the external owner id (``TASK-1``).  They are the same
    claimed task.  Only that exact numeric alias is normalized; named or
    compound task ids remain exact, so ``TASK-2`` can never authorize row 1.
    """

    token = str(value or "").strip()
    if not token:
        return ""
    match = _TASK_NUMERIC_OWNER_RE.fullmatch(token)
    if match is not None:
        return str(int(match.group("num")))
    return token


def _task_owner_compatible(left: Any, right: Any) -> bool:
    """Return whether two owner tokens name the same claimed TaskRuntime row.

    Live L2-19 remint-0: TASK-3-foundation existing-scope preflight saw
    ``requirements.txt`` on disk, then finalize compared projection
    ``TASK-3-foundation`` to claimed row ``3`` and raised
    ``owner does not match claimed task``. That fail-closed the whole
    TASK-3 split tree (tests/docs never claimed). ``TASK-3-tests`` still
    must not authorize ``TASK-3-foundation``.
    """

    a = _canonical_task_owner_identity(left)
    b = _canonical_task_owner_identity(right)
    if a and a == b:
        return True
    left_token = str(left or "").strip()
    right_token = str(right or "").strip()
    split_left = _TASK_SPLIT_OWNER_RE.fullmatch(left_token)
    split_right = _TASK_SPLIT_OWNER_RE.fullmatch(right_token)
    numeric_left = _TASK_NUMERIC_OWNER_RE.fullmatch(left_token)
    numeric_right = _TASK_NUMERIC_OWNER_RE.fullmatch(right_token)
    if split_left and numeric_right and int(split_left.group("num")) == int(numeric_right.group("num")):
        return True
    return bool(split_right and numeric_left and int(split_right.group("num")) == int(numeric_left.group("num")))


def _task_completion_projection_from_context(
    context: Mapping[str, Any] | None,
    *,
    target_task_id: str,
) -> dict[str, Any] | None:
    """Return the strict CE task-local completion projection from role context."""

    if not isinstance(context, Mapping):
        return None
    metadata = context.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    projection = metadata.get("task_completion_projection")
    if projection is None:
        return None
    if not isinstance(projection, Mapping):
        # Keep extraction side-effect free.  Validation happens inside
        # ``_finalize_claimed_execution`` so malformed authority still settles
        # the claimed lease as failed instead of raising during argument
        # evaluation and leaving TaskRuntime in_progress forever.
        return {
            "_projection_validation_error": "task completion projection must be a mapping",
        }
    return dict(projection)


def _finalize_claimed_execution(
    adapter: Any,
    *,
    target_task_id: str,
    authority: TaskRuntimeExecutionAttemptAuthorityV1 | None,
    outcome: str,
    result_summary: str = "",
    error: str = "",
    metadata: dict[str, Any] | None = None,
    task_completion_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalize a runtime task and surface terminal-state conflicts as data."""

    if authority is None:
        return {"success": False, "reason": "missing_execution_attempt_authority"}
    settlement_metadata = dict(metadata or {})
    project_artifact_receipt_failure = ""
    try:
        if outcome == "completed":
            settlement_outcome: TaskRuntimeExecutionAttemptSettlementOutcomeV1 = "completed"
            summary = result_summary
        elif outcome == "failed":
            settlement_outcome = "failed"
            summary = error or "director_execution_failed"
        else:
            return {"success": False, "reason": "invalid_outcome", "outcome": outcome}
        if task_completion_projection is not None:
            try:
                authority_snapshot = authority.snapshot(lock_timeout_seconds=5.0)
                if (
                    authority_snapshot.success is not True
                    or authority_snapshot.identity is None
                    or not authority_snapshot.identity.external_task_id
                ):
                    raise RuntimeError("task runtime external task identity is unavailable")
                project_artifact_receipts, missing_owned_artifacts = _record_project_artifacts_before_settlement(
                    adapter,
                    contract_task_id=authority_snapshot.identity.external_task_id,
                    task_completion_projection=task_completion_projection,
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                # Receipt authority failure must fail closed without leaking the
                # claimed TaskRuntime lease.  The failed settlement is still the
                # canonical terminal fact; returning before ``settle`` leaves an
                # in-progress task that can block the whole Director cascade.
                project_artifact_receipt_failure = "project_artifact_receipt_failed"
                settlement_metadata["project_artifact_receipt_error"] = str(exc)
                logger.error(
                    "Director project artifact receipt failed for runtime_task=%s: %s",
                    target_task_id,
                    exc,
                    exc_info=True,
                )
                settlement_outcome = "failed"
                summary = project_artifact_receipt_failure
            else:
                if project_artifact_receipts:
                    settlement_metadata["project_artifact_receipts"] = project_artifact_receipts
                if missing_owned_artifacts:
                    settlement_metadata["missing_owned_artifacts"] = missing_owned_artifacts
                    if outcome == "completed":
                        project_artifact_receipt_failure = "project_artifact_receipt_incomplete"
                        settlement_outcome = "failed"
                        summary = project_artifact_receipt_failure
        verdict = authority.settle(
            outcome=settlement_outcome,
            summary=summary,
            lock_timeout_seconds=5.0,
            metadata=settlement_metadata,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "success": False,
            "reason": "task_runtime_terminal_transition_failed",
            "error": str(exc),
            "outcome": outcome,
        }
    task_runtime_verdict = (
        verdict.task_runtime_verdict.to_record() if verdict.task_runtime_verdict is not None else None
    )
    result = {
        "success": verdict.success,
        "code": verdict.code,
        "reason": str((task_runtime_verdict or {}).get("code") or verdict.code),
        "outcome": verdict.outcome,
        "identity": verdict.identity.to_record() if verdict.identity is not None else None,
        "callback_error_type": verdict.callback_error_type,
    }
    if task_runtime_verdict is not None:
        result["task_runtime_verdict"] = task_runtime_verdict
    if verdict.code == "settlement_callback_exception":
        result["reason"] = "task_runtime_terminal_transition_failed"
    if project_artifact_receipt_failure:
        return {
            **result,
            "success": False,
            "reason": project_artifact_receipt_failure,
            "error": str(settlement_metadata.get("project_artifact_receipt_error") or ""),
            "outcome": outcome,
        }
    if verdict.success is not True:
        return {
            **result,
            "success": False,
            "reason": str(result.get("reason") or "task_runtime_finalize_rejected"),
            "outcome": outcome,
        }
    return result


def _record_project_artifacts_before_settlement(
    adapter: Any,
    *,
    contract_task_id: str,
    task_completion_projection: Mapping[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Record final CE-owned artifact bytes before TaskRuntime settlement."""

    projection = dict(task_completion_projection)
    projection_error = str(projection.get("_projection_validation_error") or "").strip()
    if projection_error:
        raise TypeError(projection_error)
    if projection.get("schema_version") != "polaris.task_completion_projection.v1":
        raise ValueError("task completion projection schema is invalid")
    projected_task_id = str(projection.get("task_id") or "").strip()
    if not _task_owner_compatible(projected_task_id, contract_task_id):
        raise ValueError("task completion projection owner does not match claimed task")
    project_id = str(projection.get("project_id") or "").strip()
    run_id = str(projection.get("run_id") or "").strip()
    contract_hash = str(projection.get("project_contract_hash") or "").strip()
    if not project_id or not run_id or len(contract_hash) != 64:
        raise ValueError("task completion projection lacks exact project/run/contract identity")
    raw_artifacts = projection.get("owned_artifacts")
    if raw_artifacts in (None, [], ()):
        return [], []
    if not isinstance(raw_artifacts, (list, tuple)):
        raise TypeError("task completion projection owned_artifacts must be a sequence")

    artifacts: list[dict[str, str]] = []
    seen: dict[str, tuple[str, str]] = {}
    for index, raw_artifact in enumerate(raw_artifacts):
        if not isinstance(raw_artifact, Mapping):
            raise TypeError(f"owned_artifacts[{index}] must be a mapping")
        obligation_id = str(raw_artifact.get("obligation_id") or "").strip()
        owner_task_id = str(raw_artifact.get("owner_task_id") or "").strip()
        path = str(raw_artifact.get("path") or "").strip()
        if not obligation_id or not _task_owner_compatible(owner_task_id, projected_task_id) or not path:
            raise ValueError(f"owned_artifacts[{index}] lacks exact task-owned identity")
        identity = (owner_task_id, path)
        prior = seen.get(obligation_id)
        if prior is not None:
            if prior != identity:
                raise ValueError(f"artifact obligation {obligation_id!r} has conflicting duplicate identity")
            continue
        seen[obligation_id] = identity
        artifacts.append(
            {
                "obligation_id": obligation_id,
                "owner_task_id": owner_task_id,
                "path": path,
            }
        )

    materialized_paths, missing_paths = _adapter_materialized_file_paths(
        adapter,
        [artifact["path"] for artifact in artifacts],
    )
    materialized = set(materialized_paths)
    receipts: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for artifact in artifacts:
        path = artifact["path"]
        if path not in materialized:
            missing.append(
                {
                    "obligation_id": artifact["obligation_id"],
                    "path": path,
                }
            )
            continue
        receipt = record_project_artifact(
            RecordProjectArtifactCommandV1(
                workspace=str(getattr(adapter, "workspace", "") or ""),
                project_id=project_id,
                run_id=run_id,
                completion_contract_hash=contract_hash,
                obligation_id=artifact["obligation_id"],
                owner_task_id=artifact["owner_task_id"],
                path=path,
            )
        )
        receipt_identity = (
            str(getattr(receipt, "project_id", "")),
            str(getattr(receipt, "run_id", "")),
            str(getattr(receipt, "completion_contract_hash", "")),
            str(getattr(receipt, "obligation_id", "")),
            str(getattr(receipt, "owner_task_id", "")),
            str(getattr(receipt, "path", "")),
        )
        if receipt_identity != (
            project_id,
            run_id,
            contract_hash,
            artifact["obligation_id"],
            artifact["owner_task_id"],
            path,
        ):
            raise ValueError("project artifact receipt identity differs from CE task projection")
        receipts.append(
            {
                "obligation_id": artifact["obligation_id"],
                "path": path,
                "artifact_hash": str(getattr(receipt, "artifact_hash", "")),
                "receipt_hash": str(getattr(receipt, "receipt_hash", "")),
                "receipt_ref": str(getattr(receipt, "receipt_ref", "")),
            }
        )
    if missing_paths and len(missing) != len(missing_paths):
        raise RuntimeError("materialized artifact projection returned inconsistent missing paths")
    return receipts, missing


def _execution_attempt_authority_from_context(
    context: dict[str, Any],
) -> TaskRuntimeExecutionAttemptAuthorityV1 | None:
    """Read the one public execution-attempt authority carried by this turn."""

    authority = context.get("task_runtime_execution_attempt_authority")
    if isinstance(authority, TaskRuntimeExecutionAttemptAuthorityV1):
        return authority
    return None


def _execution_attempt_identity_from_context(
    context: dict[str, Any],
) -> TaskRuntimeExecutionAttemptIdentityV1 | None:
    """Resolve the TaskRuntime attempt identity for deferred repair planning/commit.

    Prefer the immutable claim-time identity stored on context so planning can
    preserve the exact TaskRuntime binding across a long turn. Physical commit
    still receives the live authority and must revalidate that attempt.
    """

    if not isinstance(context, dict):
        return None
    cached = context.get("task_runtime_execution_attempt")
    if type(cached) is TaskRuntimeExecutionAttemptIdentityV1:
        return cached
    authority = _execution_attempt_authority_from_context(context)
    if authority is None:
        return None
    try:
        snapshot = authority.snapshot(lock_timeout_seconds=5.0)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    if type(snapshot.identity) is TaskRuntimeExecutionAttemptIdentityV1:
        return snapshot.identity
    return None


def _project_deferred_followup_receipts_as_tool_results(
    followup_receipts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project DEO followup batch receipts into adapter tool_results shape."""

    projected: list[dict[str, Any]] = []
    for receipt in followup_receipts:
        if not isinstance(receipt, Mapping):
            continue
        raw_items = receipt.get("raw_results") or receipt.get("results") or ()
        if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
            continue
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                continue
            status = str(raw.get("status") or "").strip().lower()
            success = raw.get("success")
            if success is False or status in {"failed", "error"}:
                continue
            if success is not True and status not in {"success", "ok", ""}:
                continue
            tool_name = str(raw.get("tool_name") or raw.get("tool") or "write_file").strip() or "write_file"
            nested = raw.get("result")
            result_payload = dict(nested) if isinstance(nested, Mapping) else dict(raw)
            if "path" not in result_payload and "file" not in result_payload:
                path = str(raw.get("path") or raw.get("file") or result_payload.get("target_path") or "").strip()
                if path:
                    result_payload.setdefault("path", path)
                    result_payload.setdefault("file", path)
            projected.append(
                {
                    "tool": tool_name,
                    "tool_name": tool_name,
                    "success": True,
                    "status": "success",
                    "result": result_payload,
                    "effect_receipt": raw.get("effect_receipt"),
                    "deferred_repair_followup_batch_id": receipt.get("deferred_repair_followup_batch_id"),
                }
            )
    return projected


async def _commit_deferred_materialization_quality_results(
    adapter: Any,
    *,
    context: dict[str, Any],
    tool_results: Sequence[Mapping[str, Any]],
    task_id: str,
) -> list[dict[str, Any]]:
    """Commit deferred materialization repairs through DEO followup; no bypass writer."""

    from ..deferred_repair_commit_bridge import commit_materialization_deferred_repairs

    execution_attempt = _execution_attempt_identity_from_context(context)
    workspace = str(getattr(adapter, "workspace", "") or "").strip()
    if execution_attempt is not None and str(execution_attempt.workspace or "").strip():
        # Prefer the attempt's canonical workspace so DEO commit does not fail-closed
        # on non-canonical adapter.workspace path mismatch (L1-05 r89).
        workspace = str(execution_attempt.workspace).strip()
    followup_receipts = await commit_materialization_deferred_repairs(
        workspace=workspace,
        tool_results=tool_results,
        execution_attempt=execution_attempt,
        execution_attempt_authority=_execution_attempt_authority_from_context(context),
        turn_id=f"materialization-quality-{task_id}",
        context=context,
    )
    return _project_deferred_followup_receipts_as_tool_results(followup_receipts)


def _task_runtime_finalization_failed_result(
    *,
    target_task_id: str,
    requested_outcome: str,
    finalize_result: dict[str, Any],
    tool_results: list[Any] | None = None,
    decision_signals: list[dict[str, Any]] | None = None,
    materialization_mode: str = "",
) -> dict[str, Any]:
    reason = str(finalize_result.get("reason") or "task_runtime_finalize_rejected")
    detail = str(finalize_result.get("error") or finalize_result.get("detail") or reason)
    deterministic_tool_results = [item for item in (tool_results or []) if isinstance(item, dict)]
    deterministic_repair_profile_summary = _deterministic_repair_profile_summary_from_tool_results(
        deterministic_tool_results
    )
    signal = {
        "code": "director_task_runtime_finalization_failed",
        "severity": "error",
        "detail": detail,
        "requested_outcome": requested_outcome,
        "reason": reason,
    }
    result: dict[str, Any] = {
        "success": False,
        "task_id": target_task_id,
        "tools_executed": len(tool_results or []),
        "tool_results": tool_results or [],
        "deterministic_repair_profiles": deterministic_repair_profile_summary,
        "error": "director_task_runtime_finalization_failed",
        "error_code": "director_task_runtime_finalization_failed",
        "failure_stage": "director_task_runtime_finalization",
        "root_cause_hint": reason,
        "decision_signals": [*(decision_signals or []), signal],
        "qa_required_for_final_verdict": True,
        "artifacts": [],
        "task_runtime_finalize_result": finalize_result,
    }
    if materialization_mode:
        result["materialization_mode"] = materialization_mode
    return result


def _task_runtime_heartbeat_failed_signal(heartbeat_result: dict[str, Any]) -> dict[str, Any]:
    """Project a TaskRuntime heartbeat rejection into execution evidence."""

    reason = str(heartbeat_result.get("reason") or "task_runtime_heartbeat_failed").strip()
    if not reason:
        reason = "task_runtime_heartbeat_failed"
    detail = str(heartbeat_result.get("error") or heartbeat_result.get("detail") or reason).strip()
    signal: dict[str, Any] = {
        "code": "director_task_runtime_heartbeat_failed",
        "severity": "error",
        "detail": detail,
        "reason": reason,
        "heartbeat_result": dict(heartbeat_result),
    }
    failure_class = str(heartbeat_result.get("failure_class") or "").strip()
    if failure_class:
        signal["failure_class"] = failure_class
    return signal


def _task_runtime_heartbeat_exception_signal(exc: BaseException) -> dict[str, Any]:
    """Project a TaskRuntime heartbeat exception into execution evidence."""

    return _task_runtime_heartbeat_failed_signal(
        {
            "success": False,
            "reason": "task_runtime_heartbeat_exception",
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }
    )


_RETRYABLE_TASK_RUNTIME_HEARTBEAT_CODES = frozenset(
    {
        "authority_lock_timeout",
        "authority_operation_in_progress",
        "file_lock_timeout",
    }
)


def _task_runtime_heartbeat_is_retryable(reason: str) -> bool:
    """Return whether a heartbeat rejection is transient contention.

    Live L2-12 TASK-3-docs: execute held the attempt authority / session
    file lock while preflight/cognitive/sqlite ran. The 15s heartbeat then
    received ``authority_operation_in_progress`` or ``file_lock_timeout``
    and the loop exited. Lease died at 120s, execute kept running, health
    stalled. Contention is not a terminal heartbeat failure.
    """

    return str(reason or "").strip() in _RETRYABLE_TASK_RUNTIME_HEARTBEAT_CODES


def _with_decision_signals(
    result: dict[str, Any],
    decision_signals: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return ``result`` with appended decision signals without aliasing lists."""

    if not decision_signals:
        return result
    existing = result.get("decision_signals")
    merged = [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    merged.extend(dict(item) for item in decision_signals)
    return {**result, "decision_signals": merged}


def _task_runtime_finalize_failed_signal(
    *,
    requested_outcome: str,
    finalize_result: dict[str, Any],
) -> dict[str, Any]:
    """Project failed TaskRuntime finalization as control-plane evidence."""

    reason = str(finalize_result.get("reason") or "task_runtime_finalize_rejected").strip()
    if not reason:
        reason = "task_runtime_finalize_rejected"
    detail = str(finalize_result.get("error") or finalize_result.get("detail") or reason).strip()
    signal: dict[str, Any] = {
        "code": "director_task_runtime_finalization_failed",
        "severity": "error",
        "detail": detail,
        "requested_outcome": requested_outcome,
        "reason": reason,
    }
    failure_class = str(finalize_result.get("failure_class") or "").strip()
    if failure_class:
        signal["failure_class"] = failure_class
    return signal


def _with_task_runtime_finalize_evidence(
    result: dict[str, Any],
    *,
    requested_outcome: str,
    finalize_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return ``result`` with TaskRuntime finalization failure evidence attached."""

    if not isinstance(finalize_result, dict) or finalize_result.get("success") is True:
        return result
    signal = _task_runtime_finalize_failed_signal(
        requested_outcome=requested_outcome,
        finalize_result=finalize_result,
    )
    projected = _with_decision_signals(result, [signal])
    return {
        **projected,
        "control_plane_failure_code": "director_task_runtime_finalization_failed",
        "control_plane_failure_stage": "director_task_runtime_finalization",
        "task_runtime_finalization_failed": True,
        "task_runtime_finalize_result": dict(finalize_result),
        "qa_required_for_final_verdict": True,
    }


async def _suspend_claimed_execution_for_cancellation(
    adapter: Any,
    *,
    target_task_id: str,
    run_id: str,
    authority: TaskRuntimeExecutionAttemptAuthorityV1 | None,
) -> dict[str, Any]:
    """Suspend a claimed Director task during cancellation and emit failure evidence."""

    try:
        if authority is None:
            return {"success": False, "reason": "missing_execution_attempt_authority"}
        verdict = authority.settle(
            outcome="suspended",
            summary="director_execution_cancelled",
            lock_timeout_seconds=5.0,
            metadata={"adapter_phase": "pending"},
        )
        task_runtime_verdict = (
            verdict.task_runtime_verdict.to_record() if verdict.task_runtime_verdict is not None else None
        )
        suspend_result = {
            "success": verdict.success,
            "code": verdict.code,
            "reason": str((task_runtime_verdict or {}).get("code") or verdict.code),
            "outcome": verdict.outcome,
            "identity": verdict.identity.to_record() if verdict.identity is not None else None,
            "callback_error_type": verdict.callback_error_type,
        }
        if task_runtime_verdict is not None:
            suspend_result["task_runtime_verdict"] = task_runtime_verdict
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        suspend_result = {
            "success": False,
            "reason": "task_runtime_suspend_exception",
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }
    if isinstance(suspend_result, dict) and suspend_result.get("success") is True:
        return suspend_result

    result = suspend_result if isinstance(suspend_result, dict) else {}
    reason = str(result.get("reason") or "task_runtime_suspend_failed").strip()
    if not reason:
        reason = "task_runtime_suspend_failed"
    detail = str(result.get("error") or result.get("detail") or reason).strip()
    suspension_identity = result.get("identity")
    suspension_session_id = (
        str(suspension_identity.get("session_id") or "") if isinstance(suspension_identity, dict) else ""
    )
    try:
        await adapter._emit_task_trace_event(
            task_id=target_task_id,
            phase="executing",
            step_kind="task_runtime",
            step_title="Director cancellation suspend failed",
            step_detail=detail,
            status="failed",
            run_id=run_id,
            code="director_task_runtime_suspend_failed",
            reason=reason,
            refs={
                "task_runtime_suspend_result": dict(result),
                "task_runtime_session_id": suspension_session_id,
            },
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug(
            "Failed to emit Director cancellation suspend evidence for task %s: %s",
            target_task_id,
            exc,
        )
    return {
        **result,
        "success": False,
        "reason": reason,
        "task_runtime_suspend_failed": True,
    }


def _emit_director_adapter_cognitive_receipt(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    receipt_type: str,
    payload: dict[str, Any],
    export_handoff: bool = False,
) -> dict[str, Any]:
    """Record a Cognitive Runtime receipt for Director adapter materialization."""

    metadata_sources: list[dict[str, Any]] = []
    for candidate in (
        context.get("metadata") if isinstance(context, dict) else None,
        task.get("metadata") if isinstance(task, dict) else None,
    ):
        if isinstance(candidate, dict):
            metadata_sources.append(candidate)
    merged_metadata: dict[str, Any] = {}
    for item in metadata_sources:
        merged_metadata.update(item)

    try:
        from polaris.kernelone.context.runtime_feature_flags import (
            CognitiveRuntimeMode,
            resolve_cognitive_runtime_mode,
        )

        mode = resolve_cognitive_runtime_mode(context=context, metadata=merged_metadata)
        if mode is CognitiveRuntimeMode.OFF:
            return {"ok": False, "disabled": True, "mode": mode.value}

        from polaris.cells.factory.cognitive_runtime.public.contracts import (
            ExportHandoffPackCommandV1,
            RecordRuntimeReceiptCommandV1,
        )
        from polaris.cells.factory.cognitive_runtime.public.service import (
            get_cognitive_runtime_public_service,
        )

        workspace = str(getattr(adapter, "workspace", "") or "").strip()
        session_id = (
            str(merged_metadata.get("session_id") or context.get("session_id") or task.get("session_id") or "").strip()
            or None
        )
        effective_run_id = (
            str(run_id or merged_metadata.get("run_id") or context.get("run_id") or task.get("run_id") or "").strip()
            or None
        )
        turn_envelope_raw = merged_metadata.get("turn_envelope")
        turn_envelope = dict(turn_envelope_raw) if isinstance(turn_envelope_raw, dict) else {}
        turn_envelope.setdefault("role", "director")
        turn_envelope.setdefault("task_id", str(target_task_id or ""))
        if session_id:
            turn_envelope.setdefault("session_id", session_id)
        if effective_run_id:
            turn_envelope.setdefault("run_id", effective_run_id)

        service = get_cognitive_runtime_public_service()
        try:
            receipt_result = service.record_runtime_receipt(
                RecordRuntimeReceiptCommandV1(
                    workspace=workspace,
                    receipt_type=receipt_type,
                    session_id=session_id,
                    run_id=effective_run_id,
                    payload={
                        "source": "roles.adapters.director",
                        "task_id": str(target_task_id or ""),
                        "cognitive_runtime_mode": mode.value,
                        "context_os_expected": True,
                        **dict(payload or {}),
                    },
                    turn_envelope=turn_envelope,
                )
            )
            receipt = getattr(receipt_result, "receipt", None)
            receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
            if export_handoff and session_id:
                handoff_envelope = dict(turn_envelope)
                if receipt_id:
                    receipt_ids = list(handoff_envelope.get("receipt_ids") or [])
                    if receipt_id not in receipt_ids:
                        receipt_ids.append(receipt_id)
                    handoff_envelope["receipt_ids"] = receipt_ids
                service.export_handoff_pack(
                    ExportHandoffPackCommandV1(
                        workspace=workspace,
                        session_id=session_id,
                        run_id=effective_run_id,
                        reason=f"roles.adapters.director:{receipt_type}",
                        turn_envelope=handoff_envelope,
                    )
                )
            return {
                "ok": bool(getattr(receipt_result, "ok", False)),
                "receipt_id": receipt_id,
                "receipt_type": receipt_type,
                "mode": mode.value,
            }
        finally:
            service.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to emit Director adapter Cognitive Runtime receipt for task=%s type=%s",
            target_task_id,
            receipt_type,
            exc_info=True,
        )
        return {
            "ok": False,
            "receipt_type": receipt_type,
            "error": str(exc),
        }


async def _claim_task_with_retry(
    adapter: Any,
    task: dict[str, Any],
    target_task_id: str,
    selection_source: str,
    requested_task_id: str,
    run_id: str,
    input_metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str, str, bool, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """任务声明重试逻辑

    Uses the atomic ``claim_next_execution`` API to eliminate the race window
    between task selection and claim. When a specific task is requested, tries
    that task first; otherwise lets ``claim_next_execution`` enumerate candidates.
    """
    active_task = task
    active_task_id = str(target_task_id or "").strip()
    active_source = str(selection_source or "").strip() or "task_id_lookup"
    claim_metadata = dict(input_metadata or {})
    claim_metadata["adapter_phase"] = "claimed"
    exact_handoff_claim = any(
        str(claim_metadata.get(key) or "").strip()
        for key in (
            "chief_engineer_blueprint_id",
            "chief_engineer_handoff_id",
            "pm_task_id",
            "source_task_id",
            "external_task_id",
            "task_market_task_id",
        )
    )

    # If a specific task was requested, try to claim it first
    if active_task_id:
        claim_external_task_id = _resolve_claim_external_task_id(active_task, requested_task_id)
        claim_result = adapter.task_runtime.claim_execution(
            active_task_id,
            worker_id=adapter.role_id,
            role_id=adapter.role_id,
            run_id=run_id,
            lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
            selection_source=active_source,
            external_task_id=claim_external_task_id,
            context_summary=str(active_task.get("subject") or active_task.get("title") or "").strip(),
            metadata=claim_metadata,
        )
        last_claim_result = claim_result if isinstance(claim_result, dict) else {}
        claimed = bool(last_claim_result.get("success"))
        task_data = last_claim_result.get("task")
        claimed_task: dict[str, Any] = (
            task_data if isinstance(task_data, dict) else (active_task if isinstance(active_task, dict) else {})
        )
        active_task = claimed_task
        active_task_id = str(claimed_task.get("id") or "").strip() or active_task_id

        attempts = [
            {
                "attempt": 1,
                "task_id": active_task_id,
                "selection_source": active_source,
                "claimed": claimed,
                "reason": str(last_claim_result.get("reason") or "").strip(),
                "resumed": bool(last_claim_result.get("resumed")),
                "session_id": str(
                    last_claim_result.get("session", {}).get("session_id", "")
                    if isinstance(last_claim_result.get("session"), dict)
                    else ""
                ).strip(),
            }
        ]

        if claimed:
            snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
            return active_task, active_task_id, active_source, True, snapshot, attempts, last_claim_result

        # If lease_conflict or other failure, fall through to atomic claim_next_execution
        # to try other candidates deterministically
        reason = str(last_claim_result.get("reason") or "").strip()
        if exact_handoff_claim and reason in ("lease_conflict", "task_terminal", "task_blocked"):
            snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
            return active_task, active_task_id, active_source, False, snapshot, attempts, last_claim_result
        if reason not in ("lease_conflict", "task_terminal", "task_blocked"):
            # For non-retriable failures, return immediately
            snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
            return active_task, active_task_id, active_source, False, snapshot, attempts, last_claim_result

    # Use atomic claim_next_execution for deterministic candidate enumeration
    claim_next_result = adapter.task_runtime.claim_next_execution(
        worker_id=adapter.role_id,
        role_id=adapter.role_id,
        run_id=run_id,
        lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
        selection_source=active_source,
        prefer_resumable=True,
        metadata=claim_metadata,
    )

    success = bool(claim_next_result.get("success"))
    task_data = claim_next_result.get("task")
    claimed_task = task_data if isinstance(task_data, dict) else {}
    session_data = claim_next_result.get("session")
    claim_attempts = claim_next_result.get("attempts", [])

    # Convert claim_next_execution attempts to the adapter result format.
    attempts = []
    for i, attempt in enumerate(claim_attempts, 1):
        attempts.append(
            {
                "attempt": i,
                "task_id": attempt.get("task_id"),
                "selection_source": active_source,
                "claimed": attempt.get("success", False),
                "reason": attempt.get("reason", ""),
                "resumed": False,
                "session_id": str(
                    session_data.get("session_id", "")
                    if isinstance(session_data, dict) and success and i == len(claim_attempts)
                    else ""
                ).strip(),
            }
        )

    if success and claimed_task:
        active_task = claimed_task
        active_task_id = str(claimed_task.get("id") or "").strip()
        last_claim_result = {
            "success": True,
            "reason": "claimed",
            "task": claimed_task,
            "session": session_data,
            "execution_attempt": claim_next_result.get("execution_attempt"),
        }
        snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
        return active_task, active_task_id, active_source, True, snapshot, attempts, last_claim_result

    # All candidates exhausted
    last_claim_result = {
        "success": False,
        "reason": claim_next_result.get("reason", "all_candidates_unavailable"),
    }
    snapshot = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
    return active_task, active_task_id, active_source, False, snapshot, attempts, last_claim_result


def _resolve_claim_external_task_id(task: dict[str, Any], requested_task_id: str) -> str:
    """Return the canonical external id for the task that will actually be claimed."""

    metadata_raw = task.get("metadata") if isinstance(task, dict) else {}
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    runtime_execution_raw = metadata.get("runtime_execution")
    runtime_execution: dict[str, Any] = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
    for source in (metadata, runtime_execution, task):
        for key in ("source_task_id", "pm_task_id", "external_task_id", "task_id", "id"):
            token = str(source.get(key) or "").strip()
            if token:
                return token
    return str(requested_task_id or "").strip()


def _extract_resident_agi_repair_advisory_overlay(
    *,
    task: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """Read a Resident AGI repair advisory overlay from governed handoff metadata."""

    candidates: list[dict[str, Any]] = []
    for source in (context, task):
        metadata_raw = source.get("metadata") if isinstance(source, dict) else None
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        runtime_execution_raw = metadata.get("runtime_execution")
        runtime_execution = runtime_execution_raw if isinstance(runtime_execution_raw, dict) else {}
        candidates.extend([source, metadata, runtime_execution])
    for candidate in candidates:
        for key in (
            "resident_agi_repair_advisory_overlay",
            "repair_advisory_overlay",
        ):
            overlay = candidate.get(key)
            if isinstance(overlay, dict):
                return overlay
    return None


async def _handle_claim_required(
    adapter: Any,
    target_task_id: str,
    run_id: str,
    requested_task_id: str,
    selection_source: str,
    selected_from_board: bool,
    selected_subject: str,
    board_snapshot_before: dict[str, Any],
    board_snapshot_after_claim: dict[str, Any],
    claim_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """处理声明失败情况"""
    claim_attempt_evidence = [dict(item) for item in claim_attempts if isinstance(item, dict)]
    claim_failure_reason = "claim_required"
    for attempt in reversed(claim_attempt_evidence):
        reason = str(attempt.get("reason") or "").strip()
        if reason:
            claim_failure_reason = reason
            break
    claim_evidence: dict[str, Any] = {
        "requested_task_id": requested_task_id,
        "selected_task_id": target_task_id,
        "selection_source": selection_source,
        "selected_from_board": selected_from_board,
        "selected_subject": selected_subject,
        "taskboard_before": board_snapshot_before,
        "taskboard_after_claim": board_snapshot_after_claim,
        "board_claim_applied": False,
        "claim_attempts": claim_attempt_evidence,
        "claim_failure_reason": claim_failure_reason,
    }
    await adapter._emit_task_trace_event(
        task_id=target_task_id,
        phase="executing",
        step_kind="taskboard",
        step_title="Director claim required before execution",
        step_detail=(
            "Director must claim a TaskBoard task before execution; "
            f"{taskboard_snapshot_brief(board_snapshot_after_claim)}."
        ),
        status="failed",
        run_id=run_id,
        code="director.taskboard.claim_required",
        reason="claim_required",
        refs=claim_evidence,
    )
    return {
        "success": False,
        "task_id": target_task_id,
        "error": "Director must claim TaskBoard task before execution",
        "error_code": "director.task_claim_required",
        "failure_stage": "taskboard_claim",
        "root_cause_hint": "taskboard_claim_required",
        "decision_signals": [
            {
                "code": "director.taskboard.claim_required",
                "severity": "error",
                "detail": "taskboard_claim_required_before_execution_with_retries_exhausted",
                "claim_failure_reason": claim_failure_reason,
                "claim_attempt_count": len(claim_attempt_evidence),
            }
        ],
        "task_runtime_claim_required": True,
        "task_runtime_claim_evidence": claim_evidence,
        "task_runtime_claim_attempts": claim_attempt_evidence,
        "task_runtime_claim_failure_reason": claim_failure_reason,
        "qa_required_for_final_verdict": True,
        "artifacts": [],
    }
