"""Director execute 方法实现

包含 execute 方法及其辅助函数。此模块提供 Director 任务执行的核心逻辑。
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    FailureClassV1,
    FailureEvidenceV1,
    append_failure_evidence_to_metadata,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_claimed_materialization_without_tool_lifecycle_receipt,
    build_verified_existing_artifact_lifecycle_receipt,
    evaluate_task_boundary_verdict,
    is_failure_class,
    project_tool_lifecycle_event,
    project_tool_lifecycle_failure_status,
    summarize_tool_lifecycle_events,
    tool_call_lifecycle_receipts_from_metadata,
)
from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.director.runtime.public.service import (
    AttachDirectorRepairRevalidationEvidenceV1,
    project_director_repair_revalidation_evidence,
)
from polaris.cells.runtime.execution_broker.public import (
    RecordProjectArtifactCommandV1,
    record_project_artifact,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementOutcomeV1,
    create_task_runtime_execution_attempt_authority,
)
from polaris.kernelone.fs.materialization import materialized_file_paths

# ``scan_workspace_artifact_quality`` MUST stay a name on THIS module: the test
# suite monkeypatches ``execute_method.scan_workspace_artifact_quality`` and the
# moved quality/repair callers resolve it through this module namespace (``_em``)
# at call time, so the patch still takes effect.
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

from .contract_verify import resolve_contract_step_verify_command
from .dependency_artifact_evidence import (
    DirectorDependencyArtifactEvidenceError,
    build_current_task_project_artifact_receipt_evidence,
)
from .helpers import (
    _DEFAULT_TASK_LEASE_TTL_SECONDS,
    _TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
    has_successful_write_tool,
    taskboard_snapshot_brief,
)
from .materialization_quality_boundary import run_materialization_quality_public_boundary
from .post_execution_repair_bridge import run_post_execution_language_repairs
from .repair_convergence_verifier import (
    build_artifact_quality_convergence_verifier,
    build_step_verify_convergence_verifier,
)
from .repair_profile_projection import summarize_deterministic_repair_source_tools

logger = logging.getLogger(__name__)


def _attach_current_task_project_receipt_evidence(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    context: dict[str, Any],
    existing_contract_evidence: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Attach exact retry-delivery proof; never equate bare files with delivery."""

    candidate_task = dict(task)
    raw_metadata = task.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    if not isinstance(metadata.get("task_completion_projection"), dict):
        projection = context.get("task_completion_projection")
        if not isinstance(projection, dict):
            context_metadata = context.get("metadata")
            projection = (
                context_metadata.get("task_completion_projection") if isinstance(context_metadata, dict) else None
            )
        if isinstance(projection, dict):
            metadata["task_completion_projection"] = dict(projection)
    candidate_task["metadata"] = metadata
    try:
        receipt_evidence = build_current_task_project_artifact_receipt_evidence(
            task=candidate_task,
            task_id=target_task_id,
            workspace=str(getattr(adapter, "workspace", "") or ""),
        )
    except DirectorDependencyArtifactEvidenceError as exc:
        receipt_evidence = {
            "schema_version": "polaris.current_task_project_artifact_receipt_evidence.v1",
            "ok": False,
            "error_code": exc.code,
            "error_details": dict(exc.details),
        }
    combined = dict(existing_contract_evidence)
    combined["project_artifact_receipt_evidence"] = receipt_evidence
    return combined, receipt_evidence.get("ok") is True


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
        or receipt_evidence.get("schema_version")
        != "polaris.current_task_project_artifact_receipt_evidence.v1"
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
    if _canonical_task_owner_identity(projection.get("task_id")) != _canonical_task_owner_identity(
        external_task_id
    ):
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


def _run_materialization_quality_public_boundary(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    artifact_quality_issues: tuple[dict[str, Any], ...] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute materialization-quality repair via the typed roles public boundary."""

    return run_materialization_quality_public_boundary(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
        artifact_quality_issues=artifact_quality_issues,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )


_TRANSIENT_LLM_PROVIDER_ERROR_MARKERS = (
    "connection aborted",
    "connection reset",
    "connectionpool",
    "eof occurred",
    "httpsconnectionpool",
    "max retries exceeded",
    "read timed out",
    "server disconnected",
    "ssl",
    "ssleoferror",
    "temporarily unavailable",
    "timed out",
    "timeout",
)


def _is_transient_llm_provider_exception(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _TRANSIENT_LLM_PROVIDER_ERROR_MARKERS)


async def _invoke_role_dialogue_with_transient_provider_retry(
    adapter: Any,
    *,
    message: str,
    context: dict[str, Any],
    timeout_seconds: float,
    stage_label: str,
    target_task_id: str,
) -> dict[str, Any]:
    """Retry a Director LLM call once when the provider fails before a response."""

    for provider_attempt in range(2):
        try:
            return await adapter._invoke_role_dialogue_with_timeout(
                message,
                context=context,
                timeout_seconds=timeout_seconds,
                stage_label=stage_label,
            )
        except asyncio.CancelledError:
            raise
        except (OSError, RuntimeError) as exc:
            if provider_attempt == 0 and _is_transient_llm_provider_exception(exc):
                logger.warning(
                    "director %s transient provider failure; retrying once: task=%s error=%s",
                    stage_label,
                    target_task_id,
                    exc,
                )
                state_tracker = getattr(adapter, "_state_tracker", None)
                if state_tracker is not None and hasattr(state_tracker, "append_debug_event"):
                    state_tracker.append_debug_event(
                        target_task_id,
                        "llm_transient_provider_retry",
                        {
                            "stage": stage_label,
                            "attempt": provider_attempt + 1,
                            "error": str(exc),
                        },
                    )
                await asyncio.sleep(0)
                continue
            raise
    raise RuntimeError("director_llm_transient_provider_retry_exhausted")


_DIAG_WRITE_TOOL_NAMES = WRITE_TOOLS


def _diag_write_results_summary(tool_results: list[dict[str, Any]]) -> list[tuple[str, int]]:
    """Wall 2 diagnostic: ``(tool_name, max content length)`` per write-tool result.

    Standalone/defensive so the ``director_no_materialized_changes`` verdict log can
    reveal whether a forced write emitted with an EMPTY ``content`` argument
    (prose-vs-structured-field, F16 follow-up) rather than a non-authoritative write.
    ``write_tool_evidence`` and the file counts otherwise live only in
    ``completion_metadata``, which the bench logger (WARNING) never surfaces.
    Best-effort; never raises.
    """
    summary: list[tuple[str, int]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name") or item.get("tool") or "").strip().lower()
        if name not in _DIAG_WRITE_TOOL_NAMES:
            continue
        content_len = 0
        for source in (item, item.get("arguments"), item.get("result"), item.get("payload")):
            if isinstance(source, dict):
                for key in ("content", "new", "replace", "text", "patch"):
                    value = source.get(key)
                    if isinstance(value, str):
                        content_len = max(content_len, len(value))
        summary.append((name, content_len))
    return summary


def _empty_write_content_retry_needed(tool_results: list[dict[str, Any]]) -> bool:
    """Return True only when write tools were attempted with blank content."""
    write_summary = _diag_write_results_summary(tool_results)
    return bool(write_summary) and all(content_len <= 0 for _, content_len in write_summary)


def _deterministic_repair_source_tools_from_tool_results(tool_results: list[dict[str, Any]]) -> list[str]:
    """Extract deterministic repair source-tool ids from tool results."""

    source_tools: list[str] = []
    seen: set[str] = set()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        for source in (item, item.get("result"), item.get("payload")):
            if not isinstance(source, dict):
                continue
            source_tool = str(source.get("source_tool") or "").strip()
            if not source_tool.startswith("deterministic_") or source_tool in seen:
                continue
            seen.add(source_tool)
            source_tools.append(source_tool)
    return source_tools


def _deterministic_repair_profile_summary_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact audit summary for hard-coded Director repair actions."""

    source_tools = _deterministic_repair_source_tools_from_tool_results(tool_results)
    profiles = summarize_deterministic_repair_source_tools(source_tools)
    return {
        "schema_version": "director.deterministic_repair_profile_summary.v1",
        "source_tools": source_tools,
        "source_tool_profiles": profiles,
        "registered": all(bool(profile.get("registered")) for profile in profiles),
        "count": len(source_tools),
    }


_POST_EXECUTION_STEP_VERIFY_ERROR_PREFIXES = (
    "step verify failed",
    "step verify could not run",
    "step verify command rejected by safety policy",
    "step verify target mismatch",
)


def _build_post_execution_repair_convergence_verifier(
    adapter: Any,
    *,
    task_id: str,
    all_affected_files: list[str],
    context: dict[str, Any] | None = None,
    artifact_quality_errors: list[str] | None = None,
) -> Callable[[Any], Any] | None:
    workspace_raw = str(getattr(adapter, "workspace", "") or "").strip()
    if not workspace_raw:
        return None
    try:
        workspace_path = Path(workspace_raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not workspace_path.is_dir():
        return None

    step_verify_command = _post_execution_convergence_step_verify_command(context)
    if _post_execution_convergence_prefers_step_verify(
        step_verify_command,
        artifact_quality_errors=artifact_quality_errors,
    ):
        try:
            return build_step_verify_convergence_verifier(
                workspace_path,
                task_id=task_id,
                verify_command=step_verify_command,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning(
                "post-execution step-verify convergence verifier factory failed; continuing without verifier evidence",
                extra={
                    "task_id": task_id,
                    "workspace": str(workspace_path),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None

    relative_paths = _post_execution_convergence_relative_paths(
        workspace_path,
        all_affected_files,
    )
    if not relative_paths:
        return None
    try:
        return build_artifact_quality_convergence_verifier(
            workspace_path,
            task_id=task_id,
            relative_paths=relative_paths,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "post-execution artifact-quality convergence verifier factory failed; continuing without verifier evidence",
            extra={
                "task_id": task_id,
                "workspace": str(workspace_path),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return None


def _build_post_execution_artifact_quality_convergence_verifier(
    adapter: Any,
    *,
    task_id: str,
    all_affected_files: list[str],
) -> Callable[[Any], Any] | None:
    return _build_post_execution_repair_convergence_verifier(
        adapter,
        task_id=task_id,
        all_affected_files=all_affected_files,
    )


def _post_execution_convergence_step_verify_command(context: dict[str, Any] | None) -> str:
    try:
        return resolve_contract_step_verify_command(context)
    except (OSError, RuntimeError, TypeError, ValueError):
        return ""


def _post_execution_convergence_prefers_step_verify(
    step_verify_command: str,
    *,
    artifact_quality_errors: list[str] | None,
) -> bool:
    if not step_verify_command:
        return False
    if artifact_quality_errors is None:
        return False
    normalized_errors = [
        str(error or "").strip().lower() for error in artifact_quality_errors if str(error or "").strip()
    ]
    if not normalized_errors:
        return True
    return all(_post_execution_convergence_error_is_step_verify(error) for error in normalized_errors)


def _post_execution_convergence_error_is_step_verify(error: str) -> bool:
    return any(error.startswith(prefix) for prefix in _POST_EXECUTION_STEP_VERIFY_ERROR_PREFIXES)


def _post_execution_convergence_relative_paths(
    workspace_path: Path,
    all_affected_files: list[str],
) -> tuple[str, ...]:
    relative_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in all_affected_files:
        text = str(raw_path or "").strip()
        if not text:
            continue
        try:
            candidate = Path(text)
            if candidate.is_absolute():
                normalized = candidate.expanduser().resolve().relative_to(workspace_path).as_posix()
            else:
                normalized = Path(text.replace("\\", "/")).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        if not normalized or normalized == ".":
            continue
        if normalized.startswith("../") or "/../" in normalized or normalized == "..":
            continue
        try:
            (workspace_path / normalized).resolve().relative_to(workspace_path)
        except (OSError, RuntimeError, ValueError):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        relative_paths.append(normalized)
    return tuple(relative_paths)


def _artifact_quality_error_signature(errors: list[str]) -> tuple[str, ...]:
    """Return a stable semantic-ish signature for repair-loop progress checks."""

    normalized: list[str] = []
    for error in errors:
        text = re.sub(r"\s+", " ", str(error or "")).strip()
        if not text:
            continue
        text = re.sub(r"\(\d+,\d+\)", "(line,col)", text)
        text = re.sub(r":\d+:\d+", ":line:col", text)
        normalized.append(text[:400])
    return tuple(sorted(set(normalized)))


_QUALITY_REPAIR_STAGNATION_LIMIT = 2


def _quality_repair_progress_evidence(
    *,
    before_files: dict[str, str],
    after_files: dict[str, str],
    before_errors: list[str],
    after_errors: list[str],
    before_missing_count: int,
    after_missing_count: int,
    successful_write_paths: list[str],
) -> dict[str, Any]:
    """Project one repair attempt into strict, machine-readable progress evidence.

    A different diagnostic is not necessarily progress.  The attempt advances only
    when an authoritative write receipt corresponds to an actual workspace mutation,
    no new diagnostic signature was introduced, and either the diagnostic count or
    missing-target count decreased.  This keeps a weak-model edit loop local to the
    owning Director task without allowing read-only/no-op/worsening attempts to renew
    the Provider budget indefinitely.
    """

    before_signature = set(_artifact_quality_error_signature(before_errors))
    after_signature = set(_artifact_quality_error_signature(after_errors))
    mutated_paths = sorted(
        path for path in set(before_files) | set(after_files) if before_files.get(path) != after_files.get(path)
    )

    def _matches_responsible_path(mutated_path: str, raw_responsible_path: str) -> bool:
        responsible_path = str(raw_responsible_path or "").strip().replace("\\", "/")
        mutated_path = str(mutated_path or "").strip().replace("\\", "/")
        if not responsible_path or not mutated_path:
            return False
        return (
            mutated_path == responsible_path
            or responsible_path.endswith(f"/{mutated_path}")
            or mutated_path.endswith(f"/{responsible_path}")
        )

    responsible_mutated_paths = sorted(
        path
        for path in mutated_paths
        if any(_matches_responsible_path(path, candidate) for candidate in successful_write_paths)
    )
    introduced = sorted(after_signature - before_signature)
    resolved = sorted(before_signature - after_signature)
    error_reduction = len(before_signature) - len(after_signature)
    missing_reduction = max(0, int(before_missing_count) - int(after_missing_count))
    mutation_evidenced = bool(successful_write_paths and responsible_mutated_paths)
    converged = not after_signature and int(after_missing_count) == 0
    effective_progress = bool(
        mutation_evidenced and not introduced and (converged or error_reduction > 0 or missing_reduction > 0)
    )
    return {
        "schema_version": "director.quality_repair_progress.v1",
        "status": "converged" if converged and effective_progress else "progress" if effective_progress else "stalled",
        "workspace_mutation_evidenced": mutation_evidenced,
        "successful_write_paths": successful_write_paths[:20],
        "mutated_paths": mutated_paths[:20],
        "responsible_mutated_paths": responsible_mutated_paths[:20],
        "errors_before": len(before_signature),
        "errors_after": len(after_signature),
        "net_error_reduction": error_reduction,
        "missing_targets_before": int(before_missing_count),
        "missing_targets_after": int(after_missing_count),
        "missing_target_reduction": missing_reduction,
        "resolved_diagnostic_signatures": resolved[:20],
        "introduced_diagnostic_signatures": introduced[:20],
        "effective_progress": effective_progress,
    }


def _annotate_quality_repair_progress(
    summary: dict[str, Any] | None,
    *,
    evidence: dict[str, Any],
    stagnant_attempts: int,
    stopped: bool,
) -> None:
    if not isinstance(summary, dict):
        return
    summary["progress_evidence"] = dict(evidence)
    summary["net_error_reduction"] = int(evidence.get("net_error_reduction") or 0)
    summary["workspace_mutation_evidenced"] = bool(evidence.get("workspace_mutation_evidenced"))
    summary["stagnant_attempts"] = int(stagnant_attempts)
    if stopped:
        summary.update(
            {
                "success": False,
                "convergence_status": "repair_stalled",
                "stopped_reason": "quality_repair_no_net_progress",
                "error_code": "director_quality_repair_stalled",
                "failure_class": "model_ceiling",
                "responsible_layer": "director",
                "retry_scope": "same_director_task_only",
                "pm_ce_restart_allowed": False,
            }
        )


def _build_empty_write_content_retry_message(
    task: dict[str, Any],
    *,
    original_message: str,
    tool_results: list[dict[str, Any]],
    forced_tool_name: str = "write_file",
) -> str:
    target_files = _extract_task_target_path_candidates(task)
    target_line = ""
    if target_files:
        target_line = "Allowed target files: " + ", ".join(target_files[:24]) + ".\n"
    write_summary = ", ".join(
        f"{name}:content_len={content_len}" for name, content_len in _diag_write_results_summary(tool_results)
    )
    if forced_tool_name == "edit_blocks":
        tool_instruction = (
            "Do not explain or plan. Emit exactly one valid edit_blocks tool call now.\n"
            "Use the line-range form with file/start/end/replace. The replace argument must be non-empty "
            "and limited to the repaired range; do not use write_file or whole-file text blocks.\n"
        )
    else:
        forced_tool_name = "write_file"
        tool_instruction = (
            "Do not explain or plan. Emit exactly one valid write_file tool call now.\n"
            "The write_file `content` argument must be the complete non-empty file body, never an empty string.\n"
        )
    return (
        "[mode:materialize]\n"
        "RETRY: previous write tool call had blank content and produced no files.\n"
        f"Observed write arguments: {write_summary or '(none)'}.\n"
        f"{tool_instruction}"
        "Use only task-scoped relative paths. Do not write TODO/FIXME/placeholder content.\n"
        f"{target_line}"
        "Original task follows:\n"
        f"{original_message[:6000]}"
    )


def _task_targets_missing_in_workspace(task: dict[str, Any], workspace: str) -> bool:
    workspace_path = Path(str(workspace or "")).resolve()
    if not workspace_path.is_dir():
        return False
    for candidate in _extract_task_target_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate)
        if not normalized or any(ch in normalized for ch in ("*", "?")):
            continue
        if not _workspace_path_exists_case_insensitive(workspace_path, normalized):
            return True
    return False


def _adapter_materialized_file_paths(
    adapter: Any,
    reported_paths: list[str],
) -> tuple[list[str], list[str]]:
    return materialized_file_paths(str(getattr(adapter, "workspace", "") or ""), reported_paths)


def _select_empty_write_content_retry_tool_name(
    task: dict[str, Any],
    *,
    context: dict[str, Any],
    workspace: str,
) -> str:
    """Choose the forced retry write tool after an empty write attempt.

    Missing/create targets still need whole-file creation. Existing targets are
    repair work, so forcing write_file turns a blank write retry into an
    unscoped full-file rewrite; use edit_blocks instead.
    """

    quality_repair = context.get("director_quality_repair")
    if isinstance(quality_repair, dict):
        if quality_repair.get("missing_target_files"):
            return "write_file"
        if quality_repair.get("runtime_smoke_target_files"):
            return "write_file"
    target_files = _extract_task_target_path_candidates(task)
    if not target_files:
        return "write_file"
    if _task_targets_missing_in_workspace(task, workspace):
        return "write_file"
    return "edit_blocks"


def _empty_write_retry_tool_definition(
    tool_name: str,
    target_files: list[str],
    *,
    pin_file_enum: bool = False,
) -> dict[str, Any]:
    """Registry-faithful retry tool; path pin only for write_file (R127 SSOT)."""
    from polaris.kernelone.tool_execution.forced_tool_surface import (
        ForcedToolSurfaceError,
        build_forced_tool_surface,
        resolve_registry_tool_schema,
    )

    name = str(tool_name or "").strip() or "write_file"
    if name == "write_file":
        surface = build_forced_tool_surface(
            ("write_file",),
            pin_write_paths=target_files if pin_file_enum else None,
        )
        return surface[0]
    # Non-write tools: registry only, never invent schemas, never pin paths
    # (qualification rejects path enums on edit_file/edit_blocks).
    try:
        return resolve_registry_tool_schema(name)
    except ForcedToolSurfaceError:
        # Last resort: write_file registry surface so retry remains qualifiable.
        surface = build_forced_tool_surface(
            ("write_file",),
            pin_write_paths=target_files if pin_file_enum else None,
        )
        return surface[0]


_NO_WRITE_MULTI_TARGET_RETRY_TOOL_NAMES = ("write_file", "edit_file")

_NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES = frozenset({"write_file", "edit_file"})


def _pin_file_schema_to_declared_targets(definition: dict[str, Any], target_files: list[str]) -> dict[str, Any]:
    """Pin write_file path properties only (qualification-safe; R127).

    Historical callers pinned edit_file as well, which raised
    tool_registry_scoped_enum_unauthorized at final-provider qualification.
    """
    from polaris.kernelone.tool_execution.forced_tool_surface import (
        ForcedToolSurfaceError,
        pin_write_file_paths,
        tool_definition_name,
    )

    if not target_files:
        return dict(definition)
    name = tool_definition_name(definition)
    if name != "write_file":
        # Drop unauthorized path enums on non-write tools: return registry clone.
        return dict(definition)
    try:
        return pin_write_file_paths(definition, target_files)
    except ForcedToolSurfaceError:
        return dict(definition)


def _registered_tool_definition(tool_name: str) -> dict[str, Any] | None:
    from polaris.kernelone.tool_execution.forced_tool_surface import (
        ForcedToolSurfaceError,
        resolve_registry_tool_schema,
    )

    try:
        return resolve_registry_tool_schema(str(tool_name or "").strip())
    except ForcedToolSurfaceError:
        return None


def _no_write_materialization_retry_tool_definitions(
    target_files: list[str],
    *,
    strict_write_only: bool,
) -> list[dict[str, Any]]:
    """Empty-write retry tools via Forced Tool Surface SSOT (R127).

    Only write_file may receive path enums. edit_file is registry-faithful
    without path pinning so final-provider qualification does not fail closed.
    """
    from polaris.kernelone.tool_execution.forced_tool_surface import build_forced_tool_surface

    if strict_write_only:
        return build_forced_tool_surface(("write_file",), pin_write_paths=target_files)

    # write_file pinned + edit_file unpinned (registry only)
    write_surface = build_forced_tool_surface(("write_file",), pin_write_paths=target_files)
    edit_surface = build_forced_tool_surface(("edit_file",))
    return [*write_surface, *edit_surface]


def _no_write_retry_strict_write_only(target_files: list[str]) -> bool:
    return len(target_files) <= 1


def _declared_write_retry_target_files(task: dict[str, Any]) -> list[str]:
    """Return declared file targets without inventing project-specific paths."""

    sources: list[Any] = []
    if isinstance(task, dict):
        sources.append(task.get("target_files"))
        metadata = task.get("metadata")
        if isinstance(metadata, dict):
            sources.append(metadata.get("target_files"))
    targets: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if isinstance(source, str):
            items = [source]
        elif isinstance(source, (list, tuple, set)):
            items = list(source)
        else:
            continue
        for item in items:
            normalized = _normalize_declared_task_path(item)
            if not normalized or any(ch in normalized for ch in ("*", "?")):
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            targets.append(normalized)
    if targets:
        return targets
    return _extract_task_target_path_candidates(task)


def _build_no_write_materialization_retry_message(
    task: dict[str, Any],
    *,
    original_message: str,
    tool_results: list[dict[str, Any]],
    forced_tool_name: str = "write_file",
    strict_write_only: bool | None = None,
) -> str:
    target_files = _declared_write_retry_target_files(task)
    strict_retry = _no_write_retry_strict_write_only(target_files) if strict_write_only is None else strict_write_only
    target_line = ""
    if target_files:
        target_line = "Allowed target files: " + ", ".join(target_files[:32]) + ".\n"
    observed_tools: list[str] = []
    seen_tools: set[str] = set()
    for result in tool_results:
        if not isinstance(result, dict):
            continue
        tool_name = str(result.get("tool_name") or result.get("tool") or "").strip()
        if tool_name and tool_name not in seen_tools:
            seen_tools.add(tool_name)
            observed_tools.append(tool_name)
    observed_line = ", ".join(observed_tools) if observed_tools else "(none)"
    if strict_retry:
        tool_instruction = (
            f"Emit valid {forced_tool_name} tool calls now. "
            "Do not call read, search, tree, or shell tools in this retry.\n"
            "Each write_file call must use a complete non-empty UTF-8 file body. "
            "For multi-file tasks, create every declared target file with separate write_file calls when "
            "the provider supports multiple tool calls.\n"
        )
    else:
        tool_instruction = (
            "Emit valid write_file or edit_file tool calls now. Do not call read, search, tree, or shell tools "
            "in this retry; this recovery turn exists only to materialize declared files.\n"
            "Each write_file call must use a complete non-empty UTF-8 file body; each edit_file call must "
            "contain a precise non-empty edit.\n"
        )
    return (
        "[mode:materialize]\n"
        "RETRY: previous Director turn completed without any write/edit receipt and produced no files.\n"
        f"Observed tools: {observed_line}.\n"
        f"{tool_instruction}"
        "Use only task-scoped relative paths. Do not write TODO/FIXME/placeholder content.\n"
        f"{target_line}"
        "Original task follows:\n"
        f"{original_message[:8000]}"
    )


def _no_write_materialization_retry_needed(
    *,
    primary_llm_summary: dict[str, Any] | None,
    task: dict[str, Any],
    tool_results: list[dict[str, Any]],
    workspace: str,
) -> bool:
    if not primary_llm_summary or primary_llm_summary.get("success") is not True:
        return False
    if has_successful_write_tool(tool_results):
        return False
    if not _declared_write_retry_target_files(task):
        return False
    return _task_targets_missing_in_workspace(task, workspace)


async def _run_no_write_materialization_retry(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    context: dict[str, Any],
    original_message: str,
    tool_results: list[dict[str, Any]],
    llm_call_timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    forced_tool_name = "write_file"
    target_files = _declared_write_retry_target_files(task)
    strict_write_only = _no_write_retry_strict_write_only(target_files)
    retry_message = _build_no_write_materialization_retry_message(
        task,
        original_message=original_message,
        tool_results=tool_results,
        forced_tool_name=forced_tool_name,
        strict_write_only=strict_write_only,
    )
    retry_context = _pin_materialize_context_delivery_mode(dict(context), True)
    if isinstance(task, dict):
        retry_context["task"] = dict(task)
    rebind_dependency_artifact = getattr(adapter, "_rebind_director_dependency_artifact_for_dialogue", None)
    if callable(rebind_dependency_artifact):
        rebind_dependency_artifact(retry_context)
    retry_context["_transaction_kernel_forced_tool_definitions"] = _no_write_materialization_retry_tool_definitions(
        target_files,
        strict_write_only=strict_write_only,
    )
    if strict_write_only:
        retry_context["_transaction_kernel_forced_tool_choice"] = {
            "type": "function",
            "function": {"name": forced_tool_name},
        }
        retry_context["_transaction_kernel_force_exact_tools"] = True
        retry_context["director_no_write_materialization_retry"] = {
            "write_only_declared_targets": {
                "tool": forced_tool_name,
                "target_files": target_files[:32],
            }
        }
        fallback_allowed_tool_names = {forced_tool_name}
    else:
        retry_context["_transaction_kernel_forced_tool_choice"] = "required"
        retry_context["director_no_write_materialization_retry"] = {
            "multi_file_declared_targets": {
                "required_write_tools": sorted(_NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES),
                "target_files": target_files[:32],
            }
        }
        fallback_allowed_tool_names = set(_NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES)
    try:
        retry_result = await adapter._invoke_role_dialogue_with_timeout(
            retry_message,
            context=retry_context,
            timeout_seconds=llm_call_timeout,
            stage_label="no_write_materialization_retry",
        )
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "tool_results": 0,
            "forced_tool": forced_tool_name,
            "target_files": target_files[:32],
        }

    retry_summary = _summarize_llm_stage_result(retry_result, stage="no_write_materialization_retry")
    retry_tool_results = adapter._execution.extract_kernel_tool_results(retry_result)
    retry_content = str(retry_result.get("content") or retry_result.get("response") or "")
    if not retry_tool_results or not has_successful_write_tool(retry_tool_results):
        fallback_tool_results = await adapter._execution.execute_tools(
            retry_content,
            target_task_id,
            adapter._update_task_progress,
            allowed_tool_names=fallback_allowed_tool_names,
            allow_patch_fallback=True,
        )
        if fallback_tool_results:
            retry_tool_results.extend(fallback_tool_results)

    retry_summary["attempted"] = True
    retry_summary["tool_results"] = len(retry_tool_results)
    retry_summary["forced_tool"] = forced_tool_name
    retry_summary["strict_write_only"] = strict_write_only
    retry_summary["target_files"] = target_files[:32]
    retry_summary["write_args"] = _diag_write_results_summary(retry_tool_results)
    retry_summary["recovered_write_tool_evidence"] = has_successful_write_tool(retry_tool_results)
    return retry_tool_results, retry_summary


async def _run_empty_write_content_materialization_retry(
    adapter: Any,
    *,
    task: dict[str, Any],
    target_task_id: str,
    context: dict[str, Any],
    original_message: str,
    tool_results: list[dict[str, Any]],
    llm_call_timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_files = _extract_task_target_path_candidates(task)
    forced_tool_name = _select_empty_write_content_retry_tool_name(
        task,
        context=context,
        workspace=str(getattr(adapter, "workspace", "") or ""),
    )
    retry_message = _build_empty_write_content_retry_message(
        task,
        original_message=original_message,
        tool_results=tool_results,
        forced_tool_name=forced_tool_name,
    )
    retry_context = _pin_materialize_context_delivery_mode(dict(context), True)
    if isinstance(task, dict):
        retry_context["task"] = dict(task)
    rebind_dependency_artifact = getattr(adapter, "_rebind_director_dependency_artifact_for_dialogue", None)
    if callable(rebind_dependency_artifact):
        rebind_dependency_artifact(retry_context)
    retry_context["_transaction_kernel_forced_tool_choice"] = {
        "type": "function",
        "function": {"name": forced_tool_name},
    }
    retry_context["_transaction_kernel_forced_tool_definitions"] = [
        _empty_write_retry_tool_definition(forced_tool_name, target_files)
    ]
    if len(target_files) == 1:
        retry_context["_transaction_kernel_force_exact_tools"] = True
        retry_context["director_empty_write_retry"] = {
            "write_only_single_target": {
                "tool": forced_tool_name,
                "target_file": target_files[0],
            }
        }
    try:
        retry_result = await adapter._invoke_role_dialogue_with_timeout(
            retry_message,
            context=retry_context,
            timeout_seconds=llm_call_timeout,
            stage_label="empty_write_content_retry",
        )
    except asyncio.CancelledError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return [], {
            "attempted": True,
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "tool_results": 0,
        }

    retry_summary = _summarize_llm_stage_result(retry_result, stage="empty_write_content_retry")
    retry_tool_results = adapter._execution.extract_kernel_tool_results(retry_result)
    retry_content = str(retry_result.get("content") or retry_result.get("response") or "")
    if (
        not retry_tool_results
        or not has_successful_write_tool(retry_tool_results)
        or _empty_write_content_retry_needed(retry_tool_results)
    ):
        fallback_tool_results = await adapter._execution.execute_tools(
            retry_content,
            target_task_id,
            adapter._update_task_progress,
            allowed_tool_names={forced_tool_name},
            allow_patch_fallback=forced_tool_name == "write_file",
        )
        if fallback_tool_results:
            retry_tool_results.extend(fallback_tool_results)

    retry_summary["attempted"] = True
    retry_summary["tool_results"] = len(retry_tool_results)
    retry_summary["write_args"] = _diag_write_results_summary(retry_tool_results)
    retry_summary["recovered_write_tool_evidence"] = has_successful_write_tool(retry_tool_results)
    return retry_tool_results, retry_summary


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
    match = re.fullmatch(r"(?i:task[-_])?0*(\d+)", token)
    if match is not None:
        return str(int(match.group(1)))
    return token


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
    if _canonical_task_owner_identity(projected_task_id) != _canonical_task_owner_identity(contract_task_id):
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
        if (
            not obligation_id
            or _canonical_task_owner_identity(owner_task_id) != _canonical_task_owner_identity(projected_task_id)
            or not path
        ):
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

    from .deferred_repair_commit_bridge import commit_materialization_deferred_repairs

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


async def execute_director_task(
    adapter: Any,
    task_id: str,
    input_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """执行 Director 任务的核心逻辑

    Args:
        adapter: DirectorAdapter 实例
        task_id: 任务标识
        input_data: 包含 task_id 或任务描述
        context: 执行上下文，包含 workspace 等

    Returns:
        执行结果字典
    """
    input_metadata_raw = input_data.get("metadata")
    input_metadata: dict[str, Any] = input_metadata_raw if isinstance(input_metadata_raw, dict) else {}
    requested_task_id = (
        str(
            input_data.get("task_id")
            or input_data.get("pm_task_id")
            or input_metadata.get("task_id")
            or input_metadata.get("pm_task_id")
            or input_metadata.get("id")
            or task_id
            or ""
        ).strip()
        or str(task_id or "").strip()
    )
    target_task_id = requested_task_id
    selection_source = "task_id_lookup"
    selected_from_board = False
    board_snapshot_before = adapter._state_tracker.build_taskboard_observation_snapshot(adapter.task_runtime)
    task_market_exact_claim = bool(str(input_metadata.get("task_market_task_id") or "").strip()) or str(
        input_metadata.get("source") or ""
    ).strip().startswith("runtime.task_market")
    exact_handoff_claim = any(
        str(input_metadata.get(key) or "").strip()
        for key in (
            "chief_engineer_blueprint_id",
            "chief_engineer_handoff_id",
            "pm_task_id",
            "source_task_id",
            "external_task_id",
        )
    )

    task = adapter._get_task(target_task_id)
    if task:
        selected_from_board = True
    if not task:
        if task_market_exact_claim or exact_handoff_claim:
            selection_source = "materialized_orchestration_task"
            task = adapter._materialize_runtime_task(requested_task_id, input_data)
            selected_from_board = True
        else:
            task = adapter._select_pending_board_task()
            if task:
                selected_from_board = True
                resume_state = str(task.get("resume_state") or "").strip().lower()
                selection_source = "resumable_queue_fallback" if resume_state == "resumable" else "ready_queue_fallback"
    if not task:
        selection_source = "materialized_orchestration_task"
        task = adapter._materialize_runtime_task(requested_task_id, input_data)
        selected_from_board = True

    selected_task_id = str(task.get("id") or "").strip()
    if selected_task_id:
        target_task_id = selected_task_id
    context = dict(context or {})
    metadata = dict(context.get("metadata") or {})
    context["task_id"] = target_task_id
    context["target_task_id"] = target_task_id
    context.setdefault("pm_task_id", requested_task_id or target_task_id)
    metadata["task_id"] = target_task_id
    metadata["target_task_id"] = target_task_id
    metadata.setdefault("pm_task_id", requested_task_id or target_task_id)
    context["metadata"] = metadata
    baseline_files = adapter._state_tracker.collect_workspace_code_files()
    run_id = str(context.get("run_id") or "").strip()

    # 任务声明阶段
    (
        task,
        target_task_id,
        selection_source,
        board_claim_applied,
        board_snapshot_after_claim,
        claim_attempts,
        task_claim_result,
    ) = await _claim_task_with_retry(
        adapter,
        task,
        target_task_id,
        selection_source,
        requested_task_id,
        run_id,
        input_metadata,
    )

    selected_subject = str(task.get("subject") or task.get("title") or "").strip()
    session_raw = task_claim_result.get("session")
    task_claim_session: dict[str, Any] = session_raw if isinstance(session_raw, dict) else {}
    task_claim_session_id = str(task_claim_session.get("session_id") or "").strip()
    attempt_record = task_claim_result.get("execution_attempt")
    try:
        task_execution_attempt = (
            TaskRuntimeExecutionAttemptIdentityV1.from_record(attempt_record)
            if isinstance(attempt_record, dict)
            else None
        )
    except (TypeError, ValueError):
        task_execution_attempt = None
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None
    if board_claim_applied and task_execution_attempt is None:
        return {
            "success": False,
            "task_id": target_task_id,
            "error": "director_task_runtime_execution_attempt_missing",
            "control_plane_failure_code": "director_task_runtime_execution_attempt_missing",
        }
    if board_claim_applied and task_execution_attempt is not None:
        if task_claim_session_id and task_claim_session_id != task_execution_attempt.session_id:
            return {
                "success": False,
                "task_id": target_task_id,
                "error": "director_task_runtime_execution_attempt_session_mismatch",
                "control_plane_failure_code": "director_task_runtime_execution_attempt_session_mismatch",
            }
        task_claim_session_id = task_execution_attempt.session_id
        # Propagate the physical task-runtime lease into RoleRuntime/TransactionKernel.
        # The kernel checks this immediately before executing tools, so a late LLM
        # response from a cancelled/suspended Director claim cannot still write files.
        task_execution_attempt_authority = create_task_runtime_execution_attempt_authority(task_execution_attempt)
        context = dict(context or {})
        context["session_id"] = task_claim_session_id
        context["task_runtime_session_id"] = task_claim_session_id
        context["task_runtime_guard"] = True
        # Preserve the immutable claim identity for deferred planning. Commit still
        # consumes the live attempt authority and therefore remains fail-closed.
        context["task_runtime_execution_attempt"] = task_execution_attempt
        context["task_runtime_execution_attempt_authority"] = task_execution_attempt_authority
        metadata = dict(context.get("metadata") or {})
        metadata.setdefault("session_id", task_claim_session_id)
        metadata["task_runtime_session_id"] = task_claim_session_id
        metadata["task_runtime_guard"] = True
        context["metadata"] = metadata

    promote_task_contract = getattr(adapter, "_promote_task_contract_to_runtime_context", None)
    if callable(promote_task_contract):
        promote_task_contract(
            task=task,
            context=context,
            workspace=str(getattr(adapter, "workspace", "") or ""),
        )

    if selection_source in {"claim_retry_ready_queue_fallback", "claim_retry_resumable_queue_fallback"}:
        selected_from_board = True

    if board_claim_applied:
        adapter._state_tracker.mark_rework_round_started(
            target_task_id,
            adapter._get_task,
            adapter._update_board_task,
        )
        adapter._update_task_progress(target_task_id, "executing")

    # 心跳任务
    decision_signals: list[dict[str, Any]] = []
    heartbeat_stop = asyncio.Event()
    heartbeat_task: asyncio.Task[Any] | None = None

    async def _run_task_claim_heartbeat() -> None:
        while True:
            try:
                await asyncio.wait_for(
                    heartbeat_stop.wait(),
                    timeout=_TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
                )
                return
            except asyncio.TimeoutError:
                try:
                    if task_execution_attempt_authority is None:
                        raise RuntimeError("director_task_runtime_execution_attempt_authority_missing")
                    heartbeat_verdict = task_execution_attempt_authority.heartbeat(
                        lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
                        lock_timeout_seconds=5.0,
                        context_summary=selected_subject,
                    )
                    if heartbeat_verdict.success is not True:
                        decision_signals.append(
                            _task_runtime_heartbeat_failed_signal(
                                {
                                    "success": False,
                                    "reason": heartbeat_verdict.code,
                                    "identity": (
                                        heartbeat_verdict.identity.to_record()
                                        if heartbeat_verdict.identity is not None
                                        else None
                                    ),
                                }
                            )
                        )
                        return
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    decision_signals.append(_task_runtime_heartbeat_exception_signal(exc))
                    return

    async def _stop_task_claim_heartbeat() -> None:
        if heartbeat_task is None:
            return
        heartbeat_stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    if board_claim_applied and task_execution_attempt_authority is not None:
        heartbeat_task = asyncio.create_task(_run_task_claim_heartbeat())

    try:
        if not board_claim_applied:
            return await _handle_claim_required(
                adapter,
                target_task_id,
                run_id,
                requested_task_id,
                selection_source,
                selected_from_board,
                selected_subject,
                board_snapshot_before,
                board_snapshot_after_claim,
                claim_attempts,
            )

        # 执行后端解析
        execution_backend_request = adapter._resolve_execution_backend_request(
            task_id=target_task_id,
            task=task,
            input_data=input_data,
            context=context,
        )
        adapter._persist_execution_backend_metadata(target_task_id, execution_backend_request)

        # Sequential Engine 检查
        sequential_config = adapter._get_sequential_config(context)
        if sequential_config:
            if not board_claim_applied:
                return await _handle_claim_required(
                    adapter,
                    target_task_id,
                    run_id,
                    requested_task_id,
                    selection_source,
                    selected_from_board,
                    selected_subject,
                    board_snapshot_before,
                    board_snapshot_after_claim,
                    claim_attempts,
                )

            try:
                use_hybrid = sequential_config.get("use_hybrid", False)
                if use_hybrid:
                    result = await adapter._execute_hybrid(
                        task=task, task_id=target_task_id, run_id=run_id, context=context
                    )
                else:
                    result = await adapter._execute_sequential(
                        task=task, task_id=target_task_id, run_id=run_id, context=context
                    )

                if board_claim_applied and task_execution_attempt_authority is not None:
                    if bool(result.get("success")):
                        finalize_result = _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="completed",
                            authority=task_execution_attempt_authority,
                            result_summary=f"director_{'hybrid' if use_hybrid else 'sequential'}_completed",
                            metadata={"adapter_phase": "completed"},
                            task_completion_projection=_task_completion_projection_from_context(
                                context,
                                target_task_id=target_task_id,
                            ),
                        )
                        if finalize_result.get("success") is not True:
                            return _task_runtime_finalization_failed_result(
                                target_task_id=target_task_id,
                                requested_outcome="completed",
                                finalize_result=finalize_result,
                                decision_signals=decision_signals,
                            )
                    else:
                        finalize_result = _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="failed",
                            authority=task_execution_attempt_authority,
                            error=str(result.get("error") or "director_sequential_execution_failed"),
                            metadata={"adapter_phase": "failed"},
                            task_completion_projection=_task_completion_projection_from_context(
                                context,
                                target_task_id=target_task_id,
                            ),
                        )
                        if isinstance(result, dict):
                            result = _with_task_runtime_finalize_evidence(
                                result,
                                requested_outcome="failed",
                                finalize_result=finalize_result,
                            )
                return _with_decision_signals(result, decision_signals) if isinstance(result, dict) else result
            except asyncio.CancelledError:
                if board_claim_applied and task_execution_attempt_authority is not None:
                    await _suspend_claimed_execution_for_cancellation(
                        adapter,
                        target_task_id=target_task_id,
                        run_id=run_id,
                        authority=task_execution_attempt_authority,
                    )
                raise

        # 标准 LLM 执行路径
        llm_call_timeout = adapter._execution.resolve_llm_call_timeout_seconds(context)

        # 执行流程...
        try:
            return await _execute_standard_llm_flow(
                adapter,
                task,
                target_task_id,
                run_id,
                context,
                execution_backend_request,
                board_claim_applied,
                task_claim_session_id,
                llm_call_timeout,
                decision_signals,
                baseline_files,
                selected_subject,
                task_execution_attempt_authority=task_execution_attempt_authority,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = f"director_runtime_exception:{exc}"
            runtime_exception_finalize_result: dict[str, Any] | None = None
            if board_claim_applied and task_execution_attempt_authority is not None:
                runtime_exception_finalize_result = _finalize_claimed_execution(
                    adapter,
                    target_task_id=target_task_id,
                    outcome="failed",
                    authority=task_execution_attempt_authority,
                    error=error,
                    metadata={"adapter_phase": "failed", "exception_type": type(exc).__name__},
                    task_completion_projection=_task_completion_projection_from_context(
                        context,
                        target_task_id=target_task_id,
                    ),
                )
            adapter._update_task_progress(target_task_id, "failed")
            result = {
                "success": False,
                "task_id": target_task_id,
                "error": error,
                "error_code": "director.runtime.exception",
                "failure_stage": "director_execution",
                "root_cause_hint": str(exc),
                "decision_signals": [
                    {
                        "code": "director.runtime.exception",
                        "severity": "error",
                        "detail": str(exc),
                    }
                ],
                "qa_required_for_final_verdict": True,
                "artifacts": [],
            }
            return _with_task_runtime_finalize_evidence(
                result,
                requested_outcome="failed",
                finalize_result=runtime_exception_finalize_result,
            )

    except asyncio.CancelledError:
        if board_claim_applied and task_execution_attempt_authority is not None:
            await _suspend_claimed_execution_for_cancellation(
                adapter,
                target_task_id=target_task_id,
                run_id=run_id,
                authority=task_execution_attempt_authority,
            )
        raise
    finally:
        await _stop_task_claim_heartbeat()


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


def _pin_materialize_delivery_mode(message: str, requires_fresh_materialization: bool) -> str:
    """Pin ``[mode:materialize]`` for a from-scratch build task.

    The kernel resolves the delivery contract by TEXT-CLASSIFYING the Director's
    turn message (``resolve_delivery_mode``). A weak or terse build goal can fall
    through to the default ``ANALYZE_ONLY``, whose delivery-mode-filter then
    DROPS the Director's write tools -> ``director_no_materialized_changes`` even
    though the Director DID emit writes (factory-bench L4-23: 3 write tools
    dropped in analyze_only mode, 0 files materialised). A task that requires
    fresh materialisation must always materialise, so pin the contract
    deterministically with the explicit highest-priority marker
    (``intent_classifier`` rule 1) instead of relying on stochastic signal
    matching. Inert when the task is not a fresh create or the marker is already
    present.
    """
    text = str(message or "")
    if requires_fresh_materialization and "[mode:materialize]" not in text.lower():
        logger.warning("[F31] pinned [mode:materialize] for requires_fresh build turn (delivery-mode determinism)")
        return f"[mode:materialize]\n{text}"
    return message


def _pin_materialize_context_delivery_mode(
    context: dict[str, Any],
    requires_fresh_materialization: bool,
) -> dict[str, Any]:
    """Carry the materialize contract on the control plane for ContextOS turns.

    F31's text marker is still the TransactionKernel classifier's input, but
    ContextOS can re-project messages before the transaction turn. The control
    field gives the kernel a deterministic way to restore the marker after
    projection without relying on the raw Director prompt surviving verbatim.
    """

    if not requires_fresh_materialization:
        return context
    context["delivery_mode"] = "materialize_changes"
    metadata = context.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        context["metadata"] = metadata
    metadata["delivery_mode"] = "materialize_changes"
    return context


@dataclass(frozen=True)
class MaterializationState:
    """Immutable accumulator threaded through the standard-LLM-flow phases.

    Carries the five mutable workspace-diff values that every repair stage in
    ``_execute_standard_llm_flow`` recomputes after a write attempt. Each phase
    helper receives this state and returns a successor produced via
    :func:`dataclasses.replace`, so the linear retry/repair ladder stays a pure
    state-threading pipeline instead of a 1,200-line mutation soup.
    """

    current_files: dict[str, str]
    new_files: list[str]
    modified_files: list[str]
    all_affected_files: list[str]
    tool_results: list[dict[str, Any]]

    @classmethod
    def from_locals(
        cls,
        current_files: dict[str, str],
        new_files: list[str],
        modified_files: list[str],
        all_affected_files: list[str],
        tool_results: list[dict[str, Any]],
    ) -> MaterializationState:
        """Pack the orchestrator's plain locals into a state before a phase call."""
        return cls(
            current_files=current_files,
            new_files=new_files,
            modified_files=modified_files,
            all_affected_files=all_affected_files,
            tool_results=tool_results,
        )

    def as_locals(
        self,
    ) -> tuple[dict[str, str], list[str], list[str], list[str], list[dict[str, Any]]]:
        """Unpack a state back into the orchestrator's plain locals after a phase."""
        return (
            self.current_files,
            self.new_files,
            self.modified_files,
            self.all_affected_files,
            self.tool_results,
        )

    def with_diff(
        self,
        diff: tuple[dict[str, str], list[str], list[str], list[str]],
    ) -> MaterializationState:
        """Return a successor state from a ``_collect_workspace_code_diff`` tuple."""
        current_files, new_files, modified_files, all_affected_files = diff
        return replace(
            self,
            current_files=current_files,
            new_files=new_files,
            modified_files=modified_files,
            all_affected_files=all_affected_files,
        )

    def with_affected(self, all_affected_files: list[str]) -> MaterializationState:
        """Return a successor state with a merged ``all_affected_files`` list."""
        return replace(self, all_affected_files=all_affected_files)


async def _execute_standard_llm_flow(
    adapter: Any,
    task: dict[str, Any],
    target_task_id: str,
    run_id: str,
    context: dict[str, Any],
    execution_backend_request: Any,
    board_claim_applied: bool,
    task_claim_session_id: str,
    llm_call_timeout: float,
    decision_signals: list[dict[str, Any]],
    baseline_files: dict[str, str],
    selected_subject: str,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
) -> dict[str, Any]:
    """执行标准 LLM 流程"""
    await _attach_director_file_event_bus(adapter)
    message = adapter._build_director_message(task, context=context)
    requires_fresh_materialization = _task_requires_fresh_materialization(task)
    context = _pin_materialize_context_delivery_mode(context, requires_fresh_materialization)
    message = _pin_materialize_delivery_mode(message, requires_fresh_materialization)
    workspace_name = Path(str(getattr(adapter, "workspace", "") or "")).resolve().name
    direct_fallback_summary: dict[str, Any] | None = None
    empty_write_content_retry_summary: dict[str, Any] | None = None
    no_write_materialization_retry_summary: dict[str, Any] | None = None
    all_affected_files: list[str] = []
    primary_llm_summary: dict[str, Any] | None = None
    quality_repair_summary: dict[str, Any] | None = None
    quality_repair_attempts: list[dict[str, Any]] = []
    state = MaterializationState(
        current_files=baseline_files,
        new_files=[],
        modified_files=[],
        all_affected_files=[],
        tool_results=[],
    )
    state = _phase_deterministic_cleanup(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        workspace_name=workspace_name,
        state=state,
    )

    preflight_result = _phase_existing_scope_preflight(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        task_claim_session_id=task_claim_session_id,
        decision_signals=decision_signals,
        requires_fresh_materialization=requires_fresh_materialization,
        workspace_name=workspace_name,
        state=state,
    )
    if preflight_result is not None:
        return preflight_result

    state, primary_llm_summary = await _phase_first_llm_call(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        decision_signals=decision_signals,
        workspace_name=workspace_name,
        state=state,
    )

    state, no_write_materialization_retry_summary = await _phase_no_write_materialization_retry(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        primary_llm_summary=primary_llm_summary,
        workspace_name=workspace_name,
        state=state,
    )

    state, direct_fallback_summary = _phase_direct_fallback(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        workspace_name=workspace_name,
        state=state,
    )

    state, empty_write_content_retry_summary = await _phase_empty_write_retry(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        workspace_name=workspace_name,
        state=state,
    )

    state = _phase_typescript_reexport_repair(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        workspace_name=workspace_name,
        state=state,
    )

    state = _phase_python_unittest_repair(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        workspace_name=workspace_name,
        state=state,
    )

    state, quality_repair_summary = _phase_pre_materialization_target_repair(
        adapter,
        task=task,
        target_task_id=target_task_id,
        baseline_files=baseline_files,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        workspace_name=workspace_name,
        state=state,
    )

    existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=state.current_files,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        workspace_name=workspace_name,
    )
    existing_contract_evidence, project_artifact_receipt_evidence = _attach_current_task_project_receipt_evidence(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        existing_contract_evidence=existing_contract_evidence,
    )
    write_tool_evidence = has_successful_write_tool(state.tool_results)
    can_accept_existing_scope = bool(existing_contract_evidence.get("ok")) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=write_tool_evidence,
        project_artifact_receipt_evidence=project_artifact_receipt_evidence,
    )

    (
        state,
        existing_contract_evidence,
        can_accept_existing_scope,
        write_tool_evidence,
        quality_repair_summary,
    ) = await _phase_pre_materialization_quality(
        adapter,
        task=task,
        target_task_id=target_task_id,
        context=context,
        baseline_files=baseline_files,
        existing_contract_evidence=existing_contract_evidence,
        can_accept_existing_scope=can_accept_existing_scope,
        write_tool_evidence=write_tool_evidence,
        requires_fresh_materialization=requires_fresh_materialization,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        workspace_name=workspace_name,
        state=state,
    )
    all_affected_files = state.all_affected_files

    if not all_affected_files and not can_accept_existing_scope:
        acceptance_verify_satisfied, acceptance_verify_evidence = _evaluate_acceptance_verify_exists(
            task=task,
            workspace_full=str(getattr(adapter, "workspace", "") or ""),
            write_tool_evidence=write_tool_evidence,
        )
        if acceptance_verify_satisfied:
            # Acceptance exemption: the contract's own machine checks pass and
            # the Director has successful write receipts — route through the
            # verified-existing-scope success path instead of a pseudo-failure.
            can_accept_existing_scope = True
            existing_contract_evidence = dict(existing_contract_evidence)
            existing_contract_evidence["acceptance_verify_exists"] = acceptance_verify_evidence

    no_materialized_result = _phase_no_materialized_changes(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        baseline_files=baseline_files,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        can_accept_existing_scope=can_accept_existing_scope,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        existing_contract_evidence=existing_contract_evidence,
        primary_llm_summary=primary_llm_summary,
        requires_fresh_materialization=requires_fresh_materialization,
        task_claim_session_id=task_claim_session_id,
        workspace_name=workspace_name,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if no_materialized_result is not None:
        return no_materialized_result

    existing_verified_result = _phase_existing_scope_verified(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        can_accept_existing_scope=can_accept_existing_scope,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        existing_contract_evidence=existing_contract_evidence,
        primary_llm_summary=primary_llm_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if existing_verified_result is not None:
        return existing_verified_result

    materialization_mode = (
        "write_tool_and_workspace_diff" if write_tool_evidence else "workspace_diff_without_write_tool"
    )

    missing_receipt_result = _phase_missing_write_receipt(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if missing_receipt_result is not None:
        return missing_receipt_result

    _adapter_workspace = str(getattr(adapter, "workspace", "") or "")

    (
        state,
        artifact_quality_errors,
        quality_repair_summary,
        write_tool_evidence,
    ) = await _phase_quality_repair_loop(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        adapter_workspace=_adapter_workspace,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        workspace_name=workspace_name,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )

    if _cross_artifact_llm_escalation_enabled():
        state, artifact_quality_errors = await _phase_cross_artifact_unplannable_llm_escalation(
            adapter,
            adapter_workspace=_adapter_workspace,
            baseline_files=baseline_files,
            context=context,
            llm_call_timeout=llm_call_timeout,
            message=message,
            run_id=run_id,
            target_task_id=target_task_id,
            task=task,
            workspace_name=workspace_name,
            artifact_quality_errors=artifact_quality_errors,
            quality_repair_attempts=quality_repair_attempts,
            state=state,
        )

    quality_failed_result = _phase_quality_failed(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        artifact_quality_errors=artifact_quality_errors,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if quality_failed_result is not None:
        return quality_failed_result

    (
        state,
        semantic_quality_error,
        semantic_quality_repair_summary,
        semantic_quality_repair_attempts,
    ) = await _phase_semantic_quality_repair_loop(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        message=message,
        baseline_files=baseline_files,
        llm_call_timeout=llm_call_timeout,
        adapter_workspace=_adapter_workspace,
        workspace_name=workspace_name,
        state=state,
    )

    semantic_failed_result = _phase_semantic_quality_failed(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        semantic_quality_error=semantic_quality_error,
        semantic_quality_repair_attempts=semantic_quality_repair_attempts,
        semantic_quality_repair_summary=semantic_quality_repair_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )
    if semantic_failed_result is not None:
        return semantic_failed_result

    return _phase_finalize_materialization(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        board_claim_applied=board_claim_applied,
        task_execution_attempt_authority=task_execution_attempt_authority,
        decision_signals=decision_signals,
        direct_fallback_summary=direct_fallback_summary,
        empty_write_content_retry_summary=empty_write_content_retry_summary,
        no_write_materialization_retry_summary=no_write_materialization_retry_summary,
        materialization_mode=materialization_mode,
        primary_llm_summary=primary_llm_summary,
        quality_repair_attempts=quality_repair_attempts,
        quality_repair_summary=quality_repair_summary,
        semantic_quality_repair_attempts=semantic_quality_repair_attempts,
        semantic_quality_repair_summary=semantic_quality_repair_summary,
        task_claim_session_id=task_claim_session_id,
        write_tool_evidence=write_tool_evidence,
        state=state,
    )


def _phase_finalize_materialization(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    run_id: str,
    semantic_quality_repair_attempts: list[dict[str, Any]],
    semantic_quality_repair_summary: dict[str, Any] | None,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
    no_write_materialization_retry_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialized-paths reconcile + completion-metadata + finalize (Block D).

    Reconciles reported changed files against what actually materialized on
    disk, returning the ``no_physical_files`` failure dict when nothing
    materialized, otherwise assembling the completion metadata, emitting the
    cognitive receipt, finalizing the board claim, and returning the success
    result dict. This is the success/failure epilogue of the standard flow.
    """
    _current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    deterministic_repair_profile_summary = _deterministic_repair_profile_summary_from_tool_results(tool_results)
    reported_affected_files = list(all_affected_files)
    all_affected_files, unmaterialized_affected_files = _adapter_materialized_file_paths(
        adapter,
        reported_affected_files,
    )
    new_files = [path for path in new_files if path in all_affected_files]
    modified_files = [path for path in modified_files if path in all_affected_files]
    if unmaterialized_affected_files:
        decision_signals.append(
            {
                "code": "director.materialization.unmaterialized_reported_files",
                "severity": "error",
                "detail": "Director reported changed_files that did not materialize on disk",
                "reported_changed_files": reported_affected_files,
                "unmaterialized_reported_changed_files": unmaterialized_affected_files,
            }
        )
    if not all_affected_files:
        error = "Director reported no physically materialized changed files"
        failure_metadata = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "deterministic_repair_profiles": deterministic_repair_profile_summary,
                "reported_changed_files": reported_affected_files,
                "unmaterialized_reported_changed_files": unmaterialized_affected_files,
                "materialization_mode": materialization_mode,
            }
        }
        if no_write_materialization_retry_summary is not None:
            failure_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=failure_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        result = {
            "success": False,
            "task_id": target_task_id,
            "error": error,
            "error_code": "director.materialization.no_physical_files",
            "failure_stage": "director_materialization",
            "root_cause_hint": error,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "deterministic_repair_profiles": deterministic_repair_profile_summary,
            "changed_files": [],
            "new_files": [],
            "modified_files": [],
            "reported_changed_files": reported_affected_files,
            "unmaterialized_reported_changed_files": unmaterialized_affected_files,
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )

    # 返回结果
    completion_metadata: dict[str, Any] = {
        "adapter_result": {
            "tools_executed": len(tool_results),
            "write_tool_evidence": write_tool_evidence,
            "qa_passed": None,
            "qa_required_for_final_verdict": True,
            "new_files": new_files[:20],
            "new_file_count": len(new_files),
            "modified_files": modified_files[:20],
            "modified_file_count": len(modified_files),
            "reported_changed_files": reported_affected_files[:40],
            "unmaterialized_reported_changed_files": unmaterialized_affected_files[:40],
            "materialization_mode": materialization_mode,
            "deterministic_repair_profiles": deterministic_repair_profile_summary,
        }
    }
    if primary_llm_summary is not None:
        completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
    if direct_fallback_summary is not None:
        completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
    if no_write_materialization_retry_summary is not None:
        completion_metadata["adapter_result"]["no_write_materialization_retry"] = no_write_materialization_retry_summary
    if empty_write_content_retry_summary is not None:
        completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
    if quality_repair_summary is not None:
        completion_metadata["adapter_result"]["quality_repair"] = quality_repair_summary
    if quality_repair_attempts:
        completion_metadata["adapter_result"]["quality_repair_attempts"] = quality_repair_attempts
    if semantic_quality_repair_summary is not None:
        completion_metadata["adapter_result"]["semantic_quality_repair"] = semantic_quality_repair_summary
    if semantic_quality_repair_attempts:
        completion_metadata["adapter_result"]["semantic_quality_repair_attempts"] = semantic_quality_repair_attempts
    _attach_dependency_artifact_receipt_evidence(
        completion_metadata["adapter_result"],
        tool_results=tool_results,
        primary_llm_summary=primary_llm_summary if isinstance(primary_llm_summary, dict) else None,
    )
    cognitive_receipt = _emit_director_adapter_cognitive_receipt(
        adapter,
        task=task,
        target_task_id=target_task_id,
        run_id=run_id,
        context=context,
        receipt_type="director_adapter_materialization_completed",
        payload={
            "status": "completed",
            "materialization_mode": materialization_mode,
            "changed_files": all_affected_files,
            "new_files": new_files[:20],
            "modified_files": modified_files[:20],
            "tools_executed": len(tool_results),
            "write_tool_evidence": write_tool_evidence,
            "primary_llm": primary_llm_summary or {},
            "direct_fallback": direct_fallback_summary or {},
            "no_write_materialization_retry": no_write_materialization_retry_summary or {},
            "quality_repair": quality_repair_summary or {},
            "quality_repair_attempts": quality_repair_attempts,
            "deterministic_repair_profiles": deterministic_repair_profile_summary,
        },
        export_handoff=True,
    )
    completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt

    if board_claim_applied:
        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id=target_task_id,
            outcome="completed",
            authority=task_execution_attempt_authority,
            result_summary=f"changed_files={len(all_affected_files)}; tools_executed={len(tool_results)}",
            metadata=completion_metadata,
            task_completion_projection=_task_completion_projection_from_context(
                context,
                target_task_id=target_task_id,
            ),
        )
        if finalize_result.get("success") is not True:
            return _task_runtime_finalization_failed_result(
                target_task_id=target_task_id,
                requested_outcome="completed",
                finalize_result=finalize_result,
                tool_results=tool_results,
                decision_signals=decision_signals,
                materialization_mode=materialization_mode,
            )

    adapter._update_task_progress(target_task_id, "completed")

    return {
        "success": True,
        "task_id": target_task_id,
        "tools_executed": len(tool_results),
        "tool_results": tool_results,
        "deterministic_repair_profiles": deterministic_repair_profile_summary,
        "changed_files": all_affected_files,
        "new_files": new_files,
        "modified_files": modified_files,
        "cognitive_runtime_receipt": cognitive_receipt,
        "decision_signals": decision_signals,
        "qa_required_for_final_verdict": True,
        "artifacts": [],
        "materialization_mode": materialization_mode,
    }


def _phase_deterministic_cleanup(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> MaterializationState:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    deterministic_tool_results: list[dict[str, Any]] = []
    deterministic_tool_results.extend(
        run_scaffold_marker_cleanup(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    deterministic_tool_results.extend(
        run_node_test_script_contract_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    deterministic_tool_results.extend(
        run_patch_residue_cleanup(
            adapter,
            task=task,
            task_id=target_task_id,
        )
    )
    if deterministic_tool_results:
        tool_results.extend(deterministic_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
    return MaterializationState.from_locals(
        current_files,
        new_files,
        modified_files,
        all_affected_files,
        tool_results,
    )


def _mark_quality_repair_summary_revalidated(
    summary: dict[str, Any] | None, artifact_quality_errors: list[str]
) -> None:
    if not isinstance(summary, dict):
        return
    revalidated_summary = _project_repair_revalidation_summary(
        summary,
        artifact_quality_errors=artifact_quality_errors,
        stage="director_materialization_quality",
    )
    summary.clear()
    summary.update(revalidated_summary)
    _mark_nested_repair_kernel_summaries_revalidated(summary, artifact_quality_errors)
    residual_error_count = len(artifact_quality_errors)
    summary["revalidated"] = True
    summary["residual_error_count"] = residual_error_count
    summary["success"] = residual_error_count == 0


def _project_repair_revalidation_summary(
    summary: dict[str, Any],
    *,
    artifact_quality_errors: list[str],
    stage: str,
) -> dict[str, Any]:
    return dict(
        project_director_repair_revalidation_evidence(
            AttachDirectorRepairRevalidationEvidenceV1(
                summary=summary,
                residual_artifact_quality_errors=tuple(artifact_quality_errors),
                command=("materialization_quality_revalidation",),
                metadata={"stage": stage},
            )
        ).summary
    )


def _mark_nested_repair_kernel_summaries_revalidated(
    summary: dict[str, Any],
    artifact_quality_errors: list[str],
) -> None:
    """Attach the same post-check evidence to nested repair-kernel projections."""

    nested_kernel = summary.get("post_execution_repair_kernel")
    if isinstance(nested_kernel, dict):
        summary["post_execution_repair_kernel"] = _project_repair_revalidation_summary(
            nested_kernel,
            artifact_quality_errors=artifact_quality_errors,
            stage="director_post_execution_language_repairs",
        )

    repair_attempts = summary.get("repair_attempts")
    if not isinstance(repair_attempts, list):
        return
    for attempt in repair_attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_kernel = attempt.get("repair_kernel")
        if not isinstance(attempt_kernel, dict):
            continue
        attempt["repair_kernel"] = _project_repair_revalidation_summary(
            attempt_kernel,
            artifact_quality_errors=artifact_quality_errors,
            stage=str(attempt.get("stage") or "director_materialization_quality_attempt"),
        )


def _phase_existing_scope_preflight(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    requires_fresh_materialization: bool,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    workspace_name: str,
    state: MaterializationState,
) -> dict[str, Any] | None:
    current_files, all_affected_files = (
        state.current_files,
        state.all_affected_files,
    )
    preflight_existing_contract_evidence = _build_existing_workspace_task_evidence(
        task=task,
        current_files=current_files,
        workspace_full=str(getattr(adapter, "workspace", "") or ""),
        workspace_name=workspace_name,
    )
    preflight_existing_contract_evidence, project_artifact_receipt_evidence = (
        _attach_current_task_project_receipt_evidence(
            adapter,
            task=task,
            target_task_id=target_task_id,
            context=context,
            existing_contract_evidence=preflight_existing_contract_evidence,
        )
    )
    preflight_can_accept_existing_scope = bool(
        preflight_existing_contract_evidence.get("ok")
    ) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=False,
        project_artifact_receipt_evidence=project_artifact_receipt_evidence,
    )
    preflight_quality_errors: list[str] = []
    if preflight_can_accept_existing_scope:
        preflight_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=list(preflight_existing_contract_evidence.get("existing_paths") or []),
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        if preflight_quality_errors:
            decision_signals.append(
                {
                    "code": "director.existing_workspace_scope_preflight_quality_failed",
                    "severity": "warning",
                    "detail": (
                        "Declared scope exists but failed materialization quality checks; "
                        "execution must continue through the authorized repair path."
                    ),
                    "error_count": len(preflight_quality_errors),
                    "errors": preflight_quality_errors[:20],
                }
            )
    if (
        not all_affected_files
        and _director_existing_scope_preflight_enabled(context)
        and preflight_can_accept_existing_scope
        and not preflight_quality_errors
    ):
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": 0,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": [],
                "new_file_count": 0,
                "modified_files": [],
                "modified_file_count": 0,
                "materialization_mode": "preflight_verified_existing_workspace_scope",
                "existing_contract_evidence": preflight_existing_contract_evidence,
            }
        }
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_existing_scope_preflight",
            payload={
                "status": "completed",
                "materialization_mode": "preflight_verified_existing_workspace_scope",
                "changed_files": [],
                "tools_executed": 0,
            },
            export_handoff=True,
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                authority=task_execution_attempt_authority,
                result_summary=(
                    "preflight_verified_existing_workspace_scope="
                    f"{len(preflight_existing_contract_evidence.get('existing_paths') or [])}"
                ),
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
            if finalize_result.get("success") is not True:
                return _task_runtime_finalization_failed_result(
                    target_task_id=target_task_id,
                    requested_outcome="completed",
                    finalize_result=finalize_result,
                    decision_signals=decision_signals,
                    materialization_mode="preflight_verified_existing_workspace_scope",
                )
            if requires_fresh_materialization and project_artifact_receipt_evidence:
                try:
                    task_boundary_verdict = _append_receipt_bound_preflight_task_boundary(
                        adapter,
                        context=context,
                        target_task_id=target_task_id,
                        run_id=run_id,
                        finalize_result=finalize_result,
                        receipt_evidence=preflight_existing_contract_evidence.get(
                            "project_artifact_receipt_evidence",
                            {},
                        ),
                    )
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    return {
                        "success": False,
                        "task_id": target_task_id,
                        "error": "director_task_boundary_receipt_projection_failed",
                        "error_code": "director.task_boundary_receipt_projection_failed",
                        "failure_class": "TASK_BOUNDARY_FAILED",
                        "root_cause_hint": str(exc),
                        "retry_scope": "same_director_task_only",
                        "pm_ce_restart_allowed": False,
                        "decision_signals": decision_signals,
                    }
                completion_metadata["adapter_result"]["task_boundary_verdict"] = task_boundary_verdict
        adapter._update_task_progress(target_task_id, "completed")
        decision_signals.append(
            {
                "code": "director.existing_workspace_scope_preflight_verified",
                "severity": "info",
                "detail": "Declared task scope already exists in workspace before Director writes.",
            }
        )
        return {
            "success": True,
            "task_id": target_task_id,
            "tools_executed": 0,
            "tool_results": [],
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": "preflight_verified_existing_workspace_scope",
            "existing_contract_evidence": preflight_existing_contract_evidence,
        }
    return None


async def _phase_first_llm_call(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    llm_call_timeout: float,
    message: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    primary_llm_summary: dict[str, Any] | None = None
    if not all_affected_files:
        if _director_direct_text_patch_only_enabled(context):
            result = {
                "content": "",
                "success": False,
                "error": "director_direct_text_patch_only",
                "raw_response": {"direct_text_patch_only": True},
            }
        else:
            try:
                result = await _invoke_role_dialogue_with_transient_provider_retry(
                    adapter,
                    message=message,
                    context=context,
                    timeout_seconds=llm_call_timeout,
                    stage_label="first_call",
                    target_task_id=target_task_id,
                )
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                if not _is_recoverable_no_write_mutation_contract_exception(exc):
                    raise
                error_text = str(exc)
                if not error_text.lower().startswith("transactionkernel execution failed"):
                    error_text = f"TransactionKernel execution failed: {error_text}"
                result = {
                    "content": "",
                    "success": False,
                    "error": error_text,
                    "raw_response": {
                        "recoverable_mutation_contract_exception": True,
                        "exception_type": type(exc).__name__,
                    },
                }
                decision_signals.append(
                    {
                        "code": "director.recoverable_no_write_mutation_contract_exception",
                        "severity": "warning",
                        "detail": str(exc),
                    }
                )
        primary_llm_summary = _summarize_llm_stage_result(result, stage="first_call")
        content = result.get("content", "")

        # 执行工具
        extracted_tool_results = adapter._execution.extract_kernel_tool_results(result)
        tool_results.extend(extracted_tool_results)
        if not extracted_tool_results or not has_successful_write_tool(extracted_tool_results):
            fallback_tool_results = await adapter._execution.execute_tools(
                content,
                target_task_id,
                adapter._update_task_progress,
            )
            if fallback_tool_results:
                tool_results.extend(fallback_tool_results)

        # 收集变更文件
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        primary_llm_summary,
    )


async def _phase_no_write_materialization_retry(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    primary_llm_summary: dict[str, Any] | None,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    no_write_retry_summary: dict[str, Any] | None = None
    if not all_affected_files and _no_write_materialization_retry_needed(
        primary_llm_summary=primary_llm_summary,
        task=task,
        tool_results=tool_results,
        workspace=str(getattr(adapter, "workspace", "") or ""),
    ):
        retry_tool_results, no_write_retry_summary = await _run_no_write_materialization_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            context=context,
            original_message=message,
            tool_results=tool_results,
            llm_call_timeout=llm_call_timeout,
        )
        if retry_tool_results:
            tool_results.extend(retry_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
        all_affected_files = _merge_successful_write_paths(
            all_affected_files,
            _extract_successful_write_paths(retry_tool_results),
        )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        no_write_retry_summary,
    )


def _phase_direct_fallback(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    direct_fallback_summary: dict[str, Any] | None = None
    if not all_affected_files:
        direct_timeout = adapter._execution.resolve_direct_fallback_timeout_seconds(context, llm_call_timeout)
        direct_content = ""
        direct_tool_results: list[dict[str, Any]] = []
        direct_fallback_summary = {
            "timeout_seconds": direct_timeout,
            "content_length": len(direct_content),
            "error": "",
            "skipped_reason": "runtime_provider_bypass_removed",
            "tool_results": len(direct_tool_results),
            "provider": "",
            "model": "",
            "success": False,
        }
        adapter._state_tracker.append_debug_event(
            target_task_id,
            "direct_patch_fallback_result",
            direct_fallback_summary,
        )
        if direct_tool_results:
            tool_results.extend(direct_tool_results)

        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        direct_fallback_summary,
    )


async def _phase_empty_write_retry(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    empty_write_content_retry_summary: dict[str, Any] | None = None
    if not all_affected_files and _empty_write_content_retry_needed(tool_results):
        (
            empty_retry_tool_results,
            empty_write_content_retry_summary,
        ) = await _run_empty_write_content_materialization_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            context=context,
            original_message=message,
            tool_results=tool_results,
            llm_call_timeout=llm_call_timeout,
        )
        if empty_retry_tool_results:
            tool_results.extend(empty_retry_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        empty_write_content_retry_summary,
    )


def _phase_typescript_reexport_repair(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> MaterializationState:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    if not all_affected_files:
        deterministic_tool_results = run_typescript_reexport_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
        if deterministic_tool_results:
            tool_results.extend(deterministic_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
    return MaterializationState.from_locals(
        current_files,
        new_files,
        modified_files,
        all_affected_files,
        tool_results,
    )


def _phase_python_unittest_repair(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> MaterializationState:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    if not all_affected_files:
        deterministic_tool_results = run_python_unittest_missing_target_repair(
            adapter,
            task=task,
            task_id=target_task_id,
        )
        if deterministic_tool_results:
            tool_results.extend(deterministic_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_tool_results),
            )
    return MaterializationState.from_locals(
        current_files,
        new_files,
        modified_files,
        all_affected_files,
        tool_results,
    )


def _phase_pre_materialization_target_repair(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any] | None]:
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    quality_repair_summary: dict[str, Any] | None = None
    missing_declared_targets = _missing_declared_target_files(
        task,
        str(getattr(adapter, "workspace", "") or ""),
    )
    if (
        missing_declared_targets
        or not all_affected_files
        or (
            not has_successful_write_tool(tool_results)
            and _stage_summary_has_recoverable_no_write_mutation_contract_exception(primary_llm_summary)
        )
    ):
        deterministic_prematerialization_tool_results, deterministic_prematerialization_summary = (
            run_pre_materialization_declared_target_repairs(
                adapter,
                task=task,
                task_id=target_task_id,
                workspace_name=workspace_name,
            )
        )
        if deterministic_prematerialization_tool_results:
            tool_results.extend(deterministic_prematerialization_tool_results)
            quality_repair_summary = deterministic_prematerialization_summary
            quality_repair_attempts.append(deterministic_prematerialization_summary)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_prematerialization_tool_results),
            )
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        quality_repair_summary,
    )


async def _phase_pre_materialization_quality(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    can_accept_existing_scope: bool,
    context: dict[str, Any],
    existing_contract_evidence: dict[str, Any],
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    requires_fresh_materialization: bool,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> tuple[MaterializationState, dict[str, Any], bool, bool, dict[str, Any] | None]:
    """Pre-materialization deterministic quality recompute (Block A).

    When the Director produced a write receipt but no in-scope diff yet, run the
    deterministic materialization-quality repairs once and recompute the
    existing-contract evidence / acceptance gate. Returns the updated state, the
    (possibly updated) existing-contract evidence, the can-accept-existing-scope
    and write-tool-evidence flags, and the latest quality-repair summary.
    ``quality_repair_attempts`` is appended in place.
    """
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    if (
        not all_affected_files
        and not can_accept_existing_scope
        and write_tool_evidence
        and (requires_fresh_materialization or not bool(existing_contract_evidence.get("ok")))
    ):
        pre_materialization_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _run_materialization_quality_public_boundary(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=pre_materialization_quality_errors,
                execution_attempt=_execution_attempt_identity_from_context(context),
                convergence_verifier=_build_post_execution_repair_convergence_verifier(
                    adapter,
                    task_id=target_task_id,
                    all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    context=context,
                    artifact_quality_errors=pre_materialization_quality_errors,
                ),
            )
        )
        if deterministic_quality_tool_results:
            tool_results.extend(deterministic_quality_tool_results)
            committed_writes = await _commit_deferred_materialization_quality_results(
                adapter,
                context=context,
                tool_results=deterministic_quality_tool_results,
                task_id=target_task_id,
            )
            if committed_writes:
                tool_results.extend(committed_writes)
                deterministic_quality_tool_results = [
                    *deterministic_quality_tool_results,
                    *committed_writes,
                ]
            quality_repair_summary = deterministic_quality_summary
            quality_repair_attempts.append(deterministic_quality_summary)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            if all_affected_files:
                all_affected_files = _merge_successful_write_paths(
                    all_affected_files,
                    _extract_successful_write_paths(deterministic_quality_tool_results),
                )
            existing_contract_evidence = _build_existing_workspace_task_evidence(
                task=task,
                current_files=current_files,
                workspace_full=str(getattr(adapter, "workspace", "") or ""),
                workspace_name=workspace_name,
            )
            existing_contract_evidence, project_artifact_receipt_evidence = (
                _attach_current_task_project_receipt_evidence(
                    adapter,
                    task=task,
                    target_task_id=target_task_id,
                    context=context,
                    existing_contract_evidence=existing_contract_evidence,
                )
            )
            write_tool_evidence = has_successful_write_tool(tool_results)
            can_accept_existing_scope = bool(
                existing_contract_evidence.get("ok")
            ) and _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=requires_fresh_materialization,
                write_tool_evidence=write_tool_evidence,
                project_artifact_receipt_evidence=project_artifact_receipt_evidence,
            )
    # Post-execution language-specific repair pass: always run deterministic
    # repairs after Director finishes writing files, regardless of quality gate
    # outcome. This catches import/syntax/dedup/field issues that QA might not
    # detect.
    if write_tool_evidence:
        resident_agi_repair_advisory_overlay = _extract_resident_agi_repair_advisory_overlay(
            task=task,
            context=context,
        )
        convergence_verifier = _build_post_execution_repair_convergence_verifier(
            adapter,
            task_id=target_task_id,
            all_affected_files=all_affected_files,
            context=context,
            artifact_quality_errors=[],
        )
        post_execution_tool_results, post_execution_repair_summary = run_post_execution_language_repairs(
            adapter,
            task_id=target_task_id,
            resident_agi_repair_advisory_overlay=resident_agi_repair_advisory_overlay,
            convergence_verifier=convergence_verifier,
            execution_attempt=_execution_attempt_identity_from_context(context),
        )
        if post_execution_tool_results and post_execution_repair_summary is not None:
            tool_results.extend(post_execution_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            quality_repair_attempts.append(post_execution_repair_summary)
            quality_repair_summary = dict(quality_repair_summary or {})
            quality_repair_summary["post_execution_repair_kernel"] = post_execution_repair_summary["repair_kernel"]
    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        existing_contract_evidence,
        can_accept_existing_scope,
        write_tool_evidence,
        quality_repair_summary,
    )


async def _phase_quality_repair_loop(
    adapter: Any,
    *,
    adapter_workspace: str,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> tuple[MaterializationState, list[str], dict[str, Any] | None, bool]:
    """Progress-aware deterministic + LLM quality-repair ladder (Block B).

    Runs the declared-target contract repair, then a progress-budgeted repair
    loop that interleaves deterministic materialization-quality repairs with an
    LLM repair retry, recomputing the artifact-quality error set after each
    write attempt. Returns the updated state, the residual artifact-quality
    errors, the latest quality-repair summary, and the (possibly updated)
    write-tool evidence flag. ``quality_repair_attempts`` is appended in place.
    """
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    _adapter_workspace = adapter_workspace

    deterministic_contract_tool_results, deterministic_contract_summary = run_declared_target_contract_repairs(
        adapter,
        task=task,
        task_id=target_task_id,
    )
    if deterministic_contract_tool_results:
        tool_results.extend(deterministic_contract_tool_results)
        quality_repair_summary = deterministic_contract_summary
        quality_repair_attempts.append(deterministic_contract_summary)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
        all_affected_files = _merge_successful_write_paths(
            all_affected_files,
            _extract_successful_write_paths(deterministic_contract_tool_results),
        )
        write_tool_evidence = has_successful_write_tool(tool_results)

    artifact_quality_errors = _collect_materialization_quality_errors(
        adapter,
        task=task,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        workspace_name=workspace_name,
        context=context,
        task_boundary=True,
    )
    step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
        adapter,
        context,
        task_id=target_task_id,
        task=task,
        workspace_name=workspace_name,
    )
    artifact_quality_errors += step_verify_errors
    tool_results.extend(step_verify_tool_results)
    # Live factory-bench L1-01 (2026-06-17, after the symbol-coherence fix):
    # py_compile + scan_workspace_artifact_quality pass for a calculator.py
    # whose __main__ block raises at call time. The deterministic ladder
    # must actually run the code to surface this kind of failure.
    artifact_quality_errors += run_python_static_smoke(
        adapter,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
    )
    runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
        adapter,
        task_id=target_task_id,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        context=context,
    )
    artifact_quality_errors += runtime_smoke_errors
    tool_results.extend(runtime_smoke_tool_results)
    artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
        artifact_quality_errors,
        _adapter_workspace,
    )
    # Each LLM attempt must prove a responsible workspace mutation and net
    # verifier improvement.  A changed diagnostic signature alone is not
    # progress: r14 changed one TypeScript error into multiple syntax/name
    # errors, yet the old predicate renewed the loop until the hard cap.
    stagnant_attempts = 0
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        if not artifact_quality_errors:
            break
        deterministic_before_files = dict(current_files)
        deterministic_before_errors = list(artifact_quality_errors)
        deterministic_before_missing_count = len(_missing_declared_target_files(task, _adapter_workspace))
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _run_materialization_quality_public_boundary(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=artifact_quality_errors,
                execution_attempt=_execution_attempt_identity_from_context(context),
                convergence_verifier=_build_post_execution_repair_convergence_verifier(
                    adapter,
                    task_id=target_task_id,
                    all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    context=context,
                    artifact_quality_errors=artifact_quality_errors,
                ),
            )
        )
        if _materialization_plan_probe_requires_task_boundary_triage(deterministic_quality_summary):
            quality_repair_summary = _materialization_task_boundary_triage_summary(
                deterministic_quality_summary,
                repair_attempt=repair_attempt,
                artifact_quality_errors=artifact_quality_errors,
            )
            quality_repair_attempts.append(quality_repair_summary)
            break
        if deterministic_quality_tool_results:
            tool_results.extend(deterministic_quality_tool_results)
            # DEO: plan returns deferred_request only; commit physical writes via kernel followup.
            committed_writes = await _commit_deferred_materialization_quality_results(
                adapter,
                context=context,
                tool_results=deterministic_quality_tool_results,
                task_id=target_task_id,
            )
            if committed_writes:
                tool_results.extend(committed_writes)
                deterministic_quality_tool_results = [
                    *deterministic_quality_tool_results,
                    *committed_writes,
                ]
            quality_repair_summary = deterministic_quality_summary
            quality_repair_attempts.append(deterministic_quality_summary)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(deterministic_quality_tool_results),
            )
            artifact_quality_errors = _collect_materialization_quality_errors(
                adapter,
                task=task,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                workspace_name=workspace_name,
                context=context,
                task_boundary=True,
            )
            step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
                adapter,
                context,
                task_id=target_task_id,
                task=task,
                workspace_name=workspace_name,
            )
            artifact_quality_errors += step_verify_errors
            tool_results.extend(step_verify_tool_results)
            artifact_quality_errors += run_python_static_smoke(
                adapter,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
                adapter,
                task_id=target_task_id,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                context=context,
            )
            artifact_quality_errors += runtime_smoke_errors
            tool_results.extend(runtime_smoke_tool_results)
            artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                artifact_quality_errors,
                _adapter_workspace,
            )
            _mark_quality_repair_summary_revalidated(deterministic_quality_summary, artifact_quality_errors)
            deterministic_progress = _quality_repair_progress_evidence(
                before_files=deterministic_before_files,
                after_files=current_files,
                before_errors=deterministic_before_errors,
                after_errors=artifact_quality_errors,
                before_missing_count=deterministic_before_missing_count,
                after_missing_count=len(_missing_declared_target_files(task, _adapter_workspace)),
                successful_write_paths=_extract_successful_write_paths(deterministic_quality_tool_results),
            )
            _annotate_quality_repair_progress(
                deterministic_quality_summary,
                evidence=deterministic_progress,
                stagnant_attempts=0,
                stopped=False,
            )
            if not artifact_quality_errors:
                break
            # A deterministic repair can expose the next compiler/verifier
            # layer (for example adding tsconfig reveals a missing TypeScript
            # dependency).  When both the physical mutation and changed
            # diagnostic signature are proven, run the bounded deterministic
            # ladder again before spending a Provider call.  A no-op or equal
            # diagnostic stays on the LLM fallback/stagnation path below.
            if bool(deterministic_progress.get("workspace_mutation_evidenced")) and (
                _artifact_quality_error_signature(deterministic_before_errors)
                != _artifact_quality_error_signature(artifact_quality_errors)
            ):
                continue
        llm_before_files = dict(current_files)
        llm_before_errors = list(artifact_quality_errors)
        llm_before_missing_count = len(_missing_declared_target_files(task, _adapter_workspace))
        repair_tool_results, quality_repair_summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            original_message=message,
            llm_call_timeout=llm_call_timeout,
            artifact_quality_errors=artifact_quality_errors,
            changed_files=all_affected_files,
            repair_attempt=repair_attempt,
        )
        quality_repair_attempts.append(quality_repair_summary)
        if not repair_tool_results:
            progress_evidence = _quality_repair_progress_evidence(
                before_files=llm_before_files,
                after_files=dict(current_files),
                before_errors=llm_before_errors,
                after_errors=artifact_quality_errors,
                before_missing_count=llm_before_missing_count,
                after_missing_count=len(_missing_declared_target_files(task, _adapter_workspace)),
                successful_write_paths=[],
            )
            stagnant_attempts += 1
            stopped = stagnant_attempts >= _QUALITY_REPAIR_STAGNATION_LIMIT
            _annotate_quality_repair_progress(
                quality_repair_summary,
                evidence=progress_evidence,
                stagnant_attempts=stagnant_attempts,
                stopped=stopped,
            )
            if not stopped and artifact_quality_errors:
                continue
            break
        if repair_tool_results:
            tool_results.extend(repair_tool_results)
            current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                adapter,
                baseline_files,
                task=task,
                workspace_name=workspace_name,
            )
            all_affected_files = _merge_successful_write_paths(
                all_affected_files,
                _extract_successful_write_paths(repair_tool_results),
            )
            artifact_quality_errors = _collect_materialization_quality_errors(
                adapter,
                task=task,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                workspace_name=workspace_name,
                context=context,
                task_boundary=True,
            )
            step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
                adapter,
                context,
                task_id=target_task_id,
                task=task,
                workspace_name=workspace_name,
            )
            artifact_quality_errors += step_verify_errors
            tool_results.extend(step_verify_tool_results)
            artifact_quality_errors += run_python_static_smoke(
                adapter,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
                adapter,
                task_id=target_task_id,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                context=context,
            )
            artifact_quality_errors += runtime_smoke_errors
            tool_results.extend(runtime_smoke_tool_results)
            artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                artifact_quality_errors,
                _adapter_workspace,
            )
            _mark_quality_repair_summary_revalidated(quality_repair_summary, artifact_quality_errors)
            progress_evidence = _quality_repair_progress_evidence(
                before_files=llm_before_files,
                after_files=current_files,
                before_errors=llm_before_errors,
                after_errors=artifact_quality_errors,
                before_missing_count=llm_before_missing_count,
                after_missing_count=len(_missing_declared_target_files(task, _adapter_workspace)),
                successful_write_paths=_extract_successful_write_paths(repair_tool_results),
            )
            if bool(progress_evidence.get("effective_progress")):
                stagnant_attempts = 0
            else:
                stagnant_attempts += 1
            stopped = bool(artifact_quality_errors) and stagnant_attempts >= _QUALITY_REPAIR_STAGNATION_LIMIT
            _annotate_quality_repair_progress(
                quality_repair_summary,
                evidence=progress_evidence,
                stagnant_attempts=stagnant_attempts,
                stopped=stopped,
            )
            if stopped:
                break
            if artifact_quality_errors:
                deterministic_quality_tool_results, deterministic_quality_summary = (
                    _run_materialization_quality_public_boundary(
                        adapter,
                        task=task,
                        task_id=target_task_id,
                        artifact_quality_errors=artifact_quality_errors,
                        execution_attempt=_execution_attempt_identity_from_context(context),
                        convergence_verifier=_build_post_execution_repair_convergence_verifier(
                            adapter,
                            task_id=target_task_id,
                            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                            context=context,
                            artifact_quality_errors=artifact_quality_errors,
                        ),
                    )
                )
                if _materialization_plan_probe_requires_task_boundary_triage(deterministic_quality_summary):
                    quality_repair_summary = _materialization_task_boundary_triage_summary(
                        deterministic_quality_summary,
                        repair_attempt=repair_attempt,
                        artifact_quality_errors=artifact_quality_errors,
                    )
                    quality_repair_attempts.append(quality_repair_summary)
                    break
                if deterministic_quality_tool_results:
                    tool_results.extend(deterministic_quality_tool_results)
                    committed_writes = await _commit_deferred_materialization_quality_results(
                        adapter,
                        context=context,
                        tool_results=deterministic_quality_tool_results,
                        task_id=target_task_id,
                    )
                    if committed_writes:
                        tool_results.extend(committed_writes)
                        deterministic_quality_tool_results = [
                            *deterministic_quality_tool_results,
                            *committed_writes,
                        ]
                    quality_repair_summary = deterministic_quality_summary
                    quality_repair_attempts.append(deterministic_quality_summary)
                    current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
                        adapter,
                        baseline_files,
                        task=task,
                        workspace_name=workspace_name,
                    )
                    all_affected_files = _merge_successful_write_paths(
                        all_affected_files,
                        _extract_successful_write_paths(deterministic_quality_tool_results),
                    )
                    artifact_quality_errors = _collect_materialization_quality_errors(
                        adapter,
                        task=task,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                        workspace_name=workspace_name,
                        context=context,
                        task_boundary=True,
                    )
                    step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
                        adapter,
                        context,
                        task_id=target_task_id,
                        task=task,
                        workspace_name=workspace_name,
                    )
                    artifact_quality_errors += step_verify_errors
                    tool_results.extend(step_verify_tool_results)
                    artifact_quality_errors += run_python_static_smoke(
                        adapter,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    )
                    runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
                        adapter,
                        task_id=target_task_id,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                        context=context,
                    )
                    artifact_quality_errors += runtime_smoke_errors
                    tool_results.extend(runtime_smoke_tool_results)
                    artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                        artifact_quality_errors,
                        _adapter_workspace,
                    )
                    _mark_quality_repair_summary_revalidated(deterministic_quality_summary, artifact_quality_errors)
                    if not artifact_quality_errors:
                        break

    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        artifact_quality_errors,
        quality_repair_summary,
        write_tool_evidence,
    )


def _materialization_task_boundary_triage_summary(
    summary: dict[str, Any],
    *,
    repair_attempt: int,
    artifact_quality_errors: list[str],
) -> dict[str, Any]:
    plan_probe = summary.get("plan_probe_preaudit")
    plan_probe_payload = dict(plan_probe) if isinstance(plan_probe, dict) else {}
    raw_evidence = summary.get("interface_discrepancy_evidence")
    existing_evidence: dict[str, Any] = raw_evidence if isinstance(raw_evidence, dict) else {}
    source_tools = [
        str(item)
        for item in plan_probe_payload.get(
            "covered_unplannable_source_tools",
            existing_evidence.get("covered_unplannable_source_tools", []),
        )
        if str(item or "").strip()
    ]
    covered_count = int(plan_probe_payload.get("covered_unplannable_diagnostic_count") or len(artifact_quality_errors))
    coverage_gap_count = int(plan_probe_payload.get("coverage_gap_count") or 0)
    existing_director_retry_allowed = bool(
        existing_evidence.get("director_retry_allowed")
        or summary.get("task_boundary_interface_discrepancy_retry_authorized")
    )
    existing_metadata = existing_evidence.get("metadata")
    receipt_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    receipt_metadata.update(
        {
            "route": "task_boundary_quality_loop",
            "coverage_gap_count": coverage_gap_count,
            "repair_attempt": repair_attempt,
        }
    )
    receipt = DirectorInterfaceDiscrepancyReceiptV1.from_mapping(
        {
            **existing_evidence,
            "task_id": str(summary.get("task_id") or summary.get("target_task_id") or "materialization-task"),
            "source": existing_evidence.get("source") or "roles.adapters.execute_method.materialization_quality_loop",
            "plan_probe_status": plan_probe_payload.get("status") or existing_evidence.get("plan_probe_status"),
            "covered_unplannable_source_tools": source_tools,
            "diagnostics": existing_evidence.get("diagnostics")
            or [{"message": str(item)} for item in artifact_quality_errors[:20]],
            "recommended_owner": existing_evidence.get("recommended_owner") or "chief_engineer",
            "recommended_route": existing_evidence.get("recommended_route") or "pending_design_interface_contract",
            "llm_fallback_blocked": not existing_director_retry_allowed,
            "director_retry_allowed": existing_director_retry_allowed,
            "reason": "coverage_matched_but_unplannable",
            "metadata": receipt_metadata,
        }
    ).to_dict()
    receipt.update(
        {
            "route": "task_boundary_quality_loop",
            "coverage_gap_count": coverage_gap_count,
            "covered_unplannable_diagnostic_count": covered_count,
        }
    )
    return {
        **dict(summary or {}),
        "stage": "runtime_plan_probe_unplannable",
        "attempted": True,
        "attempt": repair_attempt,
        "success": False,
        "success_reason": "task_boundary_interface_discrepancy_required",
        "tool_results": 0,
        "write_tool_evidence": False,
        "llm_fallback_blocked": not existing_director_retry_allowed,
        "director_retry_allowed": existing_director_retry_allowed,
        "task_boundary_interface_discrepancy_retry_authorized": existing_director_retry_allowed,
        "residual_error_count": len(artifact_quality_errors),
        "interface_discrepancy_evidence": receipt,
    }


async def _phase_semantic_quality_repair_loop(
    adapter: Any,
    *,
    adapter_workspace: str,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    state: MaterializationState,
) -> tuple[MaterializationState, str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Semantic-quality + missing-declared-target LLM repair loop (Block C).

    Runs ``validate_generated_output`` plus the missing-declared-target check,
    and while either fails drives an LLM repair retry (hard-capped), recomputing
    the artifact-quality error set after each write. Returns the updated state,
    the residual semantic-quality error (or ``None``), the latest repair summary,
    and the list of per-attempt repair summaries.
    """
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    _adapter_workspace = adapter_workspace

    semantic_quality_repair_summary: dict[str, Any] | None = None
    semantic_quality_repair_attempts: list[dict[str, Any]] = []
    semantic_quality_error = adapter._execution.validate_generated_output(task, all_affected_files)
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        missing_declared_targets = _missing_declared_target_files(task, _adapter_workspace)
        if not semantic_quality_error and not missing_declared_targets:
            break
        semantic_repair_errors: list[str] = []
        if semantic_quality_error:
            semantic_repair_errors.append(semantic_quality_error)
        semantic_repair_errors.extend(
            f"Artifact quality scan failed: declared target file missing '{path}'" for path in missing_declared_targets
        )
        if not semantic_repair_errors:
            break
        repair_tool_results, semantic_quality_repair_summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            original_message=message,
            llm_call_timeout=llm_call_timeout,
            artifact_quality_errors=semantic_repair_errors,
            changed_files=all_affected_files,
            repair_attempt=repair_attempt,
        )
        semantic_quality_repair_attempts.append(semantic_quality_repair_summary)
        if not repair_tool_results:
            break
        tool_results.extend(repair_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
        all_affected_files = _merge_successful_write_paths(
            all_affected_files,
            _extract_successful_write_paths(repair_tool_results),
        )
        artifact_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
            adapter,
            context,
            task_id=target_task_id,
            task=task,
            workspace_name=workspace_name,
        )
        artifact_quality_errors += step_verify_errors
        tool_results.extend(step_verify_tool_results)
        artifact_quality_errors += run_python_static_smoke(
            adapter,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        )
        runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
            adapter,
            task_id=target_task_id,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            context=context,
        )
        artifact_quality_errors += runtime_smoke_errors
        tool_results.extend(runtime_smoke_tool_results)
        artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
            artifact_quality_errors,
            str(getattr(adapter, "workspace", "") or ""),
        )
        if artifact_quality_errors:
            semantic_quality_error = "Director output quality gate failed after semantic repair: " + "; ".join(
                artifact_quality_errors[:6]
            )
            break
        semantic_quality_error = adapter._execution.validate_generated_output(task, all_affected_files)

    return (
        MaterializationState.from_locals(
            current_files,
            new_files,
            modified_files,
            all_affected_files,
            tool_results,
        ),
        semantic_quality_error,
        semantic_quality_repair_summary,
        semantic_quality_repair_attempts,
    )


def _summary_field_matches_failure_class(value: Any, expected: FailureClassV1) -> bool:
    if is_failure_class(value, expected):
        return True
    raw = str(value or "").strip()
    if not raw:
        return False
    for separator in (":", ";", "\n"):
        candidate = raw.split(separator, 1)[0].strip()
        if candidate != raw and is_failure_class(candidate, expected):
            return True
    return False


def _tool_dispatch_dropped_failure_payload() -> dict[str, str]:
    return {
        "error": "tool_dispatch_dropped",
        "error_code": "tool_dispatch_dropped",
        "failure_class": FailureClassV1.TOOL_DISPATCH_DROPPED.value,
        "responsible_layer": "execution_control_plane",
        "materialization_mode": "tool_dispatch_dropped",
        "failure_stage": "director_tool_lifecycle",
        "root_cause_hint": "required_tool_without_dispatch_receipt",
        "detail": "Director role runtime reported required/native tool calls without dispatch/effect receipt.",
    }


def _primary_llm_summary_text(primary_llm_summary: dict[str, Any]) -> str:
    """Return compact, structured LLM failure text for deterministic matching."""

    metadata_raw = primary_llm_summary.get("metadata")
    metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    parts: list[str] = []
    for source in (primary_llm_summary, metadata):
        for key in (
            "error",
            "error_code",
            "error_category",
            "last_transport_error",
            "retry_decision",
            "provider",
            "model",
        ):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n".join(parts)


def _primary_llm_provider_failure_payload(primary_llm_summary: dict[str, Any] | None) -> dict[str, str] | None:
    """Classify a failed primary role LLM call before materialization fallback.

    This only consumes structured LLM-stage summary fields. It deliberately does
    not infer provider failure from generic no-output/no-file symptoms; those
    still belong to the materialization boundary.
    """

    if not isinstance(primary_llm_summary, dict) or bool(primary_llm_summary.get("success")):
        return None

    error_text = _primary_llm_summary_text(primary_llm_summary)
    lowered = error_text.lower()
    error_category = str(primary_llm_summary.get("error_category") or "").strip().lower()
    if not error_text:
        return None

    timeout_markers = (
        "connecttimeouterror",
        "readtimeouterror",
        "timed out",
        "timeout",
        "time out",
    )
    if error_category == "timeout" or any(marker in lowered for marker in timeout_markers):
        return {
            "error": "model_provider_timeout",
            "error_code": "model_provider_timeout",
            "failure_class": FailureClassV1.MODEL_PROVIDER_TIMEOUT.value,
            "responsible_layer": "model_provider",
            "materialization_mode": "llm_call_failed",
            "failure_stage": "director_llm_call",
            "root_cause_hint": "provider_timeout",
            "detail": "Director primary LLM provider call timed out before tool dispatch or materialization.",
        }

    provider_markers = (
        "rate_limit",
        "429",
        "invalid_request",
        "provider_unavailable",
        "connection refused",
        "connection reset",
        "max retries exceeded",
        "httpconnectionpool",
    )
    if error_category in {"rate_limit", "provider", "provider_error", "unavailable"} or any(
        marker in lowered for marker in provider_markers
    ):
        return {
            "error": "model_provider_failure",
            "error_code": "model_provider_failure",
            "failure_class": FailureClassV1.MODEL_PROVIDER_FAILURE.value,
            "responsible_layer": "model_provider",
            "materialization_mode": "llm_call_failed",
            "failure_stage": "director_llm_call",
            "root_cause_hint": "provider_failure",
            "detail": "Director primary LLM provider call failed before tool dispatch or materialization.",
        }

    return None


def _lifecycle_tool_dispatch_failure_from_summary(
    primary_llm_summary: dict[str, Any],
) -> dict[str, str] | None:
    """Return the tool_dispatch_dropped failure payload if Run Ledger lifecycle
    evidence in *primary_llm_summary* indicates a dispatch-dropped failure.

    Boundary:
        Consumes only Run Ledger public helpers:
        - ``project_tool_lifecycle_failure_status`` for already-summarized data.
        - ``tool_call_lifecycle_receipts_from_metadata`` +
          ``project_tool_lifecycle_event`` + ``summarize_tool_lifecycle_events``
          for raw receipt evidence.
        No local count/precedence interpretation is maintained here.

    Complexity:
        O(r * s) where ``r`` is receipt count and ``s`` is receipt size for
        deduplication; O(r) additional memory.
    """
    # 1. Already-summarized lifecycle mapping → public failure status.
    for key in ("tool_lifecycle_summary", "tool_call_lifecycle_summary"):
        candidate = primary_llm_summary.get(key)
        if isinstance(candidate, dict) and candidate:
            failure_status = project_tool_lifecycle_failure_status(candidate)
            if failure_status.get("failed") and is_failure_class(
                failure_status.get("failure_class"), FailureClassV1.TOOL_DISPATCH_DROPPED
            ):
                return _tool_dispatch_dropped_failure_payload()
    # 2. Raw receipt evidence in metadata → public receipt helpers → status.
    metadata = primary_llm_summary.get("metadata")
    if isinstance(metadata, dict):
        receipts = tool_call_lifecycle_receipts_from_metadata(metadata)
        if receipts:
            events = tuple(project_tool_lifecycle_event(r) for r in receipts)
            summary = summarize_tool_lifecycle_events(events)
            failure_status = project_tool_lifecycle_failure_status(summary)
            if failure_status.get("failed") and is_failure_class(
                failure_status.get("failure_class"), FailureClassV1.TOOL_DISPATCH_DROPPED
            ):
                return _tool_dispatch_dropped_failure_payload()
    return None


def _primary_llm_tool_dispatch_failure(primary_llm_summary: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(primary_llm_summary, dict):
        return None
    # Lifecycle evidence from Run Ledger public helpers takes priority over
    # error/error_code/failure_class field matching.
    lifecycle_result = _lifecycle_tool_dispatch_failure_from_summary(primary_llm_summary)
    if lifecycle_result is not None:
        return lifecycle_result
    # Fallback: legacy error/error_code/failure_class field matching.
    if not any(
        _summary_field_matches_failure_class(
            primary_llm_summary.get(key),
            FailureClassV1.TOOL_DISPATCH_DROPPED,
        )
        for key in ("error", "error_code", "failure_class")
    ):
        return None
    return _tool_dispatch_dropped_failure_payload()


def _seal_claimed_materialization_without_tool_lifecycle(
    *,
    workspace: str,
    run_id: str,
    task_id: str,
    turn_id: str = "",
    reason: str,
    failure_class: str,
    primary_llm_summary: Mapping[str, Any] | None = None,
    completion_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """R137: seal blocked lifecycle when claimed materialization ends with no tools.

    Claimed Director materialization activates a tool-lifecycle requirement. If the
    attempt never appends a tool_call_lifecycle receipt (closed without tools,
    no_materialized_changes, fail-closed before dispatch), projection reports
    TOOL_LIFECYCLE_MISSING even though claim/fail facts exist. Seal one blocked
    receipt so missing_required_task_keys clears and failure stays attributable.
    """

    resolved_run_id = str(run_id or "").strip()
    resolved_task_id = str(task_id or "").strip()
    if not resolved_run_id or not resolved_task_id:
        return None

    # Skip when turn/adapter metadata already carries lifecycle receipts.
    candidates: list[Mapping[str, Any]] = []
    if isinstance(primary_llm_summary, Mapping):
        candidates.append(primary_llm_summary)
        nested = primary_llm_summary.get("metadata")
        if isinstance(nested, Mapping):
            candidates.append(nested)
    if isinstance(completion_metadata, Mapping):
        candidates.append(completion_metadata)
        adapter_result = completion_metadata.get("adapter_result")
        if isinstance(adapter_result, Mapping):
            candidates.append(adapter_result)
            nested_primary = adapter_result.get("primary_llm")
            if isinstance(nested_primary, Mapping):
                candidates.append(nested_primary)
                nested_meta = nested_primary.get("metadata")
                if isinstance(nested_meta, Mapping):
                    candidates.append(nested_meta)
    for candidate in candidates:
        if tool_call_lifecycle_receipts_from_metadata(dict(candidate)):
            return None

    lifecycle = build_claimed_materialization_without_tool_lifecycle_receipt(
        run_id=resolved_run_id,
        task_id=resolved_task_id,
        turn_id=str(turn_id or "").strip(),
        role="director",
        reason=str(reason or "claimed_materialization_without_tool_lifecycle"),
        failure_class=str(failure_class or FailureClassV1.INCOMPLETE_MATERIALIZATION.value),
    )
    if isinstance(completion_metadata, dict):
        completion_metadata["tool_call_lifecycle_receipt"] = dict(lifecycle)
        completion_metadata["tool_call_lifecycle"] = dict(lifecycle)
        adapter_result = completion_metadata.get("adapter_result")
        if isinstance(adapter_result, dict):
            adapter_result["tool_call_lifecycle_receipt"] = dict(lifecycle)
    try:
        append_tool_call_lifecycle_event(
            AppendToolCallLifecycleEventCommandV1(
                workspace=str(workspace or ""),
                run_id=resolved_run_id,
                task_id=resolved_task_id,
                turn_id=str(turn_id or "").strip(),
                role="director",
                lifecycle_receipt=lifecycle,
                stage="tool_batch",
                project_id=resolved_task_id,
                ok=False,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        logger.debug(
            "R137: failed to append claimed-without-tools lifecycle task_id=%s run_id=%s",
            resolved_task_id,
            resolved_run_id,
            exc_info=True,
        )
    return lifecycle


def _materialization_failure_evidence_row(
    *,
    error: str,
    error_code: str,
    failure_class: str,
    responsible_layer: str,
    failure_stage: str,
    root_cause_hint: str,
    detail: str,
    materialization_mode: str,
    run_id: str,
    target_task_id: str,
    cognitive_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build structured failure evidence for Director materialization failures.

    The Director adapter still owns the materialization decision, but the
    evidence row shape belongs to Run Ledger public contracts.
    """

    evidence_refs: list[str] = []
    if isinstance(cognitive_receipt, dict):
        for key in ("receipt_ref", "receipt_id", "id"):
            value = str(cognitive_receipt.get(key) or "").strip()
            if value:
                evidence_refs.append(value)
                break
    if not evidence_refs:
        evidence_refs.append(
            ":".join(
                token
                for token in (
                    "director_adapter_failure",
                    str(run_id or "").strip(),
                    str(target_task_id or "").strip(),
                    str(error_code or error or "").strip(),
                )
                if token
            )
        )
    return FailureEvidenceV1(
        failure_class=failure_class,
        responsible_layer=responsible_layer,
        reason=detail,
        evidence_refs=tuple(evidence_refs),
        metadata={
            "error": error,
            "error_code": error_code,
            "failure_stage": failure_stage,
            "root_cause_hint": root_cause_hint,
            "materialization_mode": materialization_mode,
            "run_id": run_id,
            "task_id": target_task_id,
        },
    ).to_dict()


def _phase_no_materialized_changes(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    can_accept_existing_scope: bool,
    context: dict[str, Any],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    existing_contract_evidence: dict[str, Any],
    primary_llm_summary: dict[str, Any] | None,
    requires_fresh_materialization: bool,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    workspace_name: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    current_files, new_files, modified_files, all_affected_files, tool_results = (
        state.current_files,
        state.new_files,
        state.modified_files,
        state.all_affected_files,
        state.tool_results,
    )
    if (
        not all_affected_files
        and not can_accept_existing_scope
        and (requires_fresh_materialization or not bool(existing_contract_evidence.get("ok")))
    ):
        out_of_scope_diff = _collect_workspace_out_of_scope_diff(
            task=task,
            baseline_files=baseline_files,
            current_files=current_files,
            workspace=str(getattr(adapter, "workspace", "") or ""),
            cache_root=_quality_repair_cache_root(task, context),
            workspace_name=workspace_name,
        )
        out_of_scope_files = list(out_of_scope_diff.get("affected_files") or [])
        task_boundary_scope_filter = out_of_scope_diff.get("task_boundary_scope_filter")
        if not isinstance(task_boundary_scope_filter, dict):
            task_boundary_scope_filter = None
        primary_llm_claimed_success = bool(primary_llm_summary.get("success")) if primary_llm_summary else False
        direct_side_effect_success = primary_llm_claimed_success and not tool_results
        lifecycle_failure = _primary_llm_tool_dispatch_failure(primary_llm_summary)
        if lifecycle_failure is not None:
            error = lifecycle_failure["error"]
            materialization_mode = lifecycle_failure["materialization_mode"]
            public_error_code = lifecycle_failure["error_code"]
            failure_class = lifecycle_failure["failure_class"]
            responsible_layer = lifecycle_failure["responsible_layer"]
            failure_stage = lifecycle_failure["failure_stage"]
            root_cause_hint = lifecycle_failure["root_cause_hint"]
            failure_detail = lifecycle_failure["detail"]
        elif (provider_failure := _primary_llm_provider_failure_payload(primary_llm_summary)) is not None:
            error = provider_failure["error"]
            materialization_mode = provider_failure["materialization_mode"]
            public_error_code = provider_failure["error_code"]
            failure_class = provider_failure["failure_class"]
            responsible_layer = provider_failure["responsible_layer"]
            failure_stage = provider_failure["failure_stage"]
            root_cause_hint = provider_failure["root_cause_hint"]
            failure_detail = provider_failure["detail"]
        elif out_of_scope_files and (write_tool_evidence or direct_side_effect_success):
            error = "director_materialized_out_of_scope"
            materialization_mode = "materialized_out_of_scope"
            public_error_code = error
            failure_class = FailureClassV1.BLUEPRINT_SCOPE_MISMATCH.value
            responsible_layer = "director_scope_guard"
            failure_stage = "director_materialization"
            root_cause_hint = "no_changed_files"
            failure_detail = "Director returned no workspace file changes."
        else:
            error = "director_no_materialized_changes"
            materialization_mode = "no_materialized_changes"
            public_error_code = "incomplete_materialization"
            failure_class = FailureClassV1.INCOMPLETE_MATERIALIZATION.value
            responsible_layer = "director"
            failure_stage = "director_materialization"
            root_cause_hint = "no_changed_files"
            failure_detail = (
                "Director returned no workspace file changes; "
                "fresh materialization is required for repair/update tasks."
                if requires_fresh_materialization
                else "Director returned no workspace file changes."
            )
        # Wall 2 diagnostic (F16 follow-up): the forced write emitted but the
        # workspace diff is empty. Surface the discriminating signals so a single
        # solo rerun reveals whether the write content ARG was empty (prose lands
        # in reasoning, structured `content` stays blank) or the write was
        # non-authoritative — directs the Wall 2 fix without guessing.
        logger.warning(
            "%s DIAGNOSTIC: write_tool_evidence=%s tools_executed=%s "
            "new_files=%s modified_files=%s out_of_scope_files=%s requires_fresh=%s "
            "write_args(name,content_len)=%s",
            error,
            write_tool_evidence,
            len(tool_results),
            len(new_files),
            len(modified_files),
            out_of_scope_files[:20],
            requires_fresh_materialization,
            _diag_write_results_summary(tool_results),
        )
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_error": error,
                "materialization_error_code": public_error_code,
                "failure_class": failure_class,
                "responsible_layer": responsible_layer,
                "materialization_mode": materialization_mode,
                "out_of_scope_files": out_of_scope_files[:20],
                "out_of_scope_file_count": len(out_of_scope_files),
                "out_of_scope_diff": out_of_scope_diff,
                "existing_contract_evidence": existing_contract_evidence,
            }
        }
        if task_boundary_scope_filter is not None:
            completion_metadata["adapter_result"]["task_boundary_scope_filter"] = task_boundary_scope_filter
            scope_authority = task_boundary_scope_filter.get("scope_authority")
            if isinstance(scope_authority, dict):
                completion_metadata["adapter_result"]["scope_authority"] = scope_authority
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        # R137: claimed materialization with no tool lifecycle must seal blocked
        # evidence before finalize; otherwise Run Ledger projects TOOL_LIFECYCLE_MISSING.
        if not tool_results and not write_tool_evidence:
            sealed_lifecycle = _seal_claimed_materialization_without_tool_lifecycle(
                workspace=str(getattr(adapter, "workspace", "") or ""),
                run_id=str(run_id or ""),
                task_id=str(target_task_id or ""),
                turn_id=str(
                    (primary_llm_summary or {}).get("turn_id") or (primary_llm_summary or {}).get("last_turn_id") or ""
                ),
                reason=str(error or "director_no_materialized_changes"),
                failure_class=str(failure_class or FailureClassV1.INCOMPLETE_MATERIALIZATION.value),
                primary_llm_summary=primary_llm_summary,
                completion_metadata=completion_metadata,
            )
            if sealed_lifecycle is not None:
                completion_metadata["adapter_result"]["tool_call_lifecycle_sealed"] = True
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_failed",
            payload={
                "status": "failed",
                "error": error,
                "error_code": public_error_code,
                "failure_class": failure_class,
                "responsible_layer": responsible_layer,
                "materialization_mode": materialization_mode,
                "changed_files": out_of_scope_files if out_of_scope_files else [],
                "out_of_scope_files": out_of_scope_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                **(
                    {"task_boundary_scope_filter": task_boundary_scope_filter}
                    if task_boundary_scope_filter is not None
                    else {}
                ),
            },
        )
        failure_evidence_row = _materialization_failure_evidence_row(
            error=error,
            error_code=public_error_code,
            failure_class=failure_class,
            responsible_layer=responsible_layer,
            failure_stage=failure_stage,
            root_cause_hint=root_cause_hint,
            detail=failure_detail,
            materialization_mode=materialization_mode,
            run_id=run_id,
            target_task_id=target_task_id,
            cognitive_receipt=cognitive_receipt if isinstance(cognitive_receipt, dict) else None,
        )
        failure_evidence_rows = append_failure_evidence_to_metadata(completion_metadata, failure_evidence_row)
        completion_metadata["adapter_result"]["failure_evidence"] = failure_evidence_rows
        completion_metadata["adapter_result"]["failure_evidence_summary"] = completion_metadata.get(
            "failure_evidence_summary",
            {},
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        result = {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": public_error_code,
            "failure_class": failure_class,
            "responsible_layer": responsible_layer,
            "failure_stage": failure_stage,
            "root_cause_hint": root_cause_hint,
            "failure_evidence": failure_evidence_rows,
            "failure_evidence_summary": completion_metadata.get("failure_evidence_summary", {}),
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [
                {
                    "code": public_error_code,
                    "severity": "error",
                    "failure_class": failure_class,
                    "responsible_layer": responsible_layer,
                    "detail": failure_detail,
                }
            ],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


def _phase_existing_scope_verified(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    can_accept_existing_scope: bool,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    existing_contract_evidence: dict[str, Any],
    primary_llm_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, tool_results = (
        state.all_affected_files,
        state.tool_results,
    )
    if not all_affected_files and can_accept_existing_scope:
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": [],
                "new_file_count": 0,
                "modified_files": [],
                "modified_file_count": 0,
                "materialization_mode": "verified_existing_workspace_scope",
                "existing_contract_evidence": existing_contract_evidence,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_existing_scope_verified",
            payload={
                "status": "completed",
                "materialization_mode": "verified_existing_workspace_scope",
                "changed_files": [],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
            },
            export_handoff=True,
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                authority=task_execution_attempt_authority,
                result_summary=(
                    "verified_existing_workspace_scope="
                    f"{len(existing_contract_evidence.get('existing_paths') or [])}; "
                    f"tools_executed={len(tool_results)}"
                ),
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
            if finalize_result.get("success") is not True:
                return _task_runtime_finalization_failed_result(
                    target_task_id=target_task_id,
                    requested_outcome="completed",
                    finalize_result=finalize_result,
                    tool_results=tool_results,
                    decision_signals=decision_signals,
                    materialization_mode="verified_existing_workspace_scope",
                )
        adapter._update_task_progress(target_task_id, "completed")
        decision_signals.append(
            {
                "code": "director.existing_workspace_scope_verified",
                "severity": "info",
                "detail": "No fresh file diff was required because declared task scope already exists in workspace.",
            }
        )
        return {
            "success": True,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": decision_signals,
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": "verified_existing_workspace_scope",
            "existing_contract_evidence": existing_contract_evidence,
        }
    return None


def _phase_missing_write_receipt(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, new_files, modified_files, tool_results = (
        state.all_affected_files,
        state.new_files,
        state.modified_files,
        state.tool_results,
    )
    if all_affected_files and not write_tool_evidence:
        error = "director_missing_write_receipt"
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_mode": materialization_mode,
                "materialization_error": error,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_receipt_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": materialization_mode,
                "changed_files": all_affected_files,
                "new_files": new_files[:20],
                "modified_files": modified_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        missing_receipt_signal = {
            "code": error,
            "severity": "error",
            "detail": (
                "Director observed workspace changes, but no normalized write-tool receipt was returned. "
                "Mutation tasks must fail closed instead of trusting ambient diffs."
            ),
            "new_file_count": len(new_files),
            "modified_file_count": len(modified_files),
        }
        result = {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization_receipt",
            "root_cause_hint": "missing_write_tool_receipt",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [*decision_signals, missing_receipt_signal],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


def _cross_artifact_llm_escalation_enabled() -> bool:
    """Default OFF -> preserve current deterministic fail-closed behavior.

    Opt in via env to escalate
    residual cross-artifact quality errors to a bounded Director LLM re-generation
    before the hard materialization-quality fail."""
    raw = str(os.environ.get("KERNELONE_DIRECTOR_CROSS_ARTIFACT_LLM_ESCALATION", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def _phase_cross_artifact_unplannable_llm_escalation(
    adapter: Any,
    *,
    adapter_workspace: str,
    baseline_files: dict[str, str],
    context: dict[str, Any],
    llm_call_timeout: float,
    message: str,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    workspace_name: str,
    artifact_quality_errors: list[str],
    quality_repair_attempts: list[dict[str, Any]],
    state: MaterializationState,
) -> tuple[MaterializationState, list[str]]:
    """Escalate deterministically-unplannable cross-artifact quality errors to a
    bounded Director LLM re-generation before ``_phase_quality_failed`` hard-fails.
    Cross-file symbol mismatches (consumer imports a symbol the sibling owner never
    defines) are ``coverage_matched_but_unplannable`` for the deterministic kernel,
    so without this the LLM is never asked to re-generate the files to cohere.
    Reuses the semantic-repair LLM-retry + recompute pipeline. Inert when empty."""
    if not artifact_quality_errors:
        return state, artifact_quality_errors
    current_files, new_files, modified_files, all_affected_files, tool_results = state.as_locals()
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        if not artifact_quality_errors:
            break
        repair_tool_results, repair_summary = await _run_materialization_quality_repair_retry(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            original_message=message,
            llm_call_timeout=llm_call_timeout,
            artifact_quality_errors=artifact_quality_errors,
            changed_files=all_affected_files,
            repair_attempt=repair_attempt,
        )
        if isinstance(repair_summary, dict):
            quality_repair_attempts.append({**repair_summary, "escalation": "cross_artifact_unplannable"})
        if not repair_tool_results:
            break
        tool_results.extend(repair_tool_results)
        current_files, new_files, modified_files, all_affected_files = _collect_workspace_code_diff(
            adapter,
            baseline_files,
            task=task,
            workspace_name=workspace_name,
        )
        all_affected_files = _merge_successful_write_paths(
            all_affected_files,
            _extract_successful_write_paths(repair_tool_results),
        )
        scan_paths = _materialization_quality_scan_paths(all_affected_files, tool_results)
        artifact_quality_errors = _collect_materialization_quality_errors(
            adapter,
            task=task,
            all_affected_files=scan_paths,
            workspace_name=workspace_name,
            context=context,
            task_boundary=True,
        )
        step_verify_errors, step_verify_tool_results = _collect_step_verify_errors(
            adapter,
            context,
            task_id=target_task_id,
            task=task,
            workspace_name=workspace_name,
        )
        artifact_quality_errors += step_verify_errors
        tool_results.extend(step_verify_tool_results)
        artifact_quality_errors += run_python_static_smoke(adapter, all_affected_files=scan_paths)
        runtime_smoke_errors, runtime_smoke_tool_results = run_python_runtime_smoke(
            adapter,
            task_id=target_task_id,
            all_affected_files=scan_paths,
            context=context,
        )
        artifact_quality_errors += runtime_smoke_errors
        tool_results.extend(runtime_smoke_tool_results)
        artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
            artifact_quality_errors,
            str(getattr(adapter, "workspace", "") or ""),
        )
    state = MaterializationState.from_locals(current_files, new_files, modified_files, all_affected_files, tool_results)
    return state, artifact_quality_errors


def _phase_quality_failed(
    adapter: Any,
    *,
    artifact_quality_errors: list[str],
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    quality_repair_attempts: list[dict[str, Any]],
    quality_repair_summary: dict[str, Any] | None,
    run_id: str,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, new_files, modified_files, tool_results = (
        state.all_affected_files,
        state.new_files,
        state.modified_files,
        state.tool_results,
    )
    if artifact_quality_errors:
        error = "director_materialization_quality_failed"
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_mode": materialization_mode,
                "materialization_error": error,
                "artifact_quality_errors": artifact_quality_errors[:20],
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        if quality_repair_summary is not None:
            completion_metadata["adapter_result"]["quality_repair"] = quality_repair_summary
        if quality_repair_attempts:
            completion_metadata["adapter_result"]["quality_repair_attempts"] = quality_repair_attempts
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_quality_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": materialization_mode,
                "changed_files": all_affected_files,
                "new_files": new_files[:20],
                "modified_files": modified_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "artifact_quality_errors": artifact_quality_errors[:20],
                "quality_repair": quality_repair_summary or {},
                "quality_repair_attempts": quality_repair_attempts,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        quality_signal = {
            "code": error,
            "severity": "error",
            "detail": (
                "Director changed workspace files, but the changed artifacts still contain known "
                "worthless scaffold or placeholder-test patterns."
            ),
            "artifact_quality_errors": artifact_quality_errors[:20],
        }
        result = {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization_quality",
            "root_cause_hint": "artifact_quality_failed",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [*decision_signals, quality_signal],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
            "artifact_quality_errors": artifact_quality_errors[:20],
            # Forensic trail: without this, a repair attempt that died before
            # its LLM call is indistinguishable from one that never ran.
            "quality_repair_attempts": quality_repair_attempts,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


def _phase_semantic_quality_failed(
    adapter: Any,
    *,
    board_claim_applied: bool,
    task_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
    context: dict[str, Any],
    decision_signals: list[dict[str, Any]],
    direct_fallback_summary: dict[str, Any] | None,
    empty_write_content_retry_summary: dict[str, Any] | None,
    no_write_materialization_retry_summary: dict[str, Any] | None,
    materialization_mode: str,
    primary_llm_summary: dict[str, Any] | None,
    run_id: str,
    semantic_quality_error: str | None,
    semantic_quality_repair_attempts: list[dict[str, Any]],
    semantic_quality_repair_summary: dict[str, Any] | None,
    target_task_id: str,
    task: dict[str, Any],
    task_claim_session_id: str,
    write_tool_evidence: bool,
    state: MaterializationState,
) -> dict[str, Any] | None:
    all_affected_files, new_files, modified_files, tool_results = (
        state.all_affected_files,
        state.new_files,
        state.modified_files,
        state.tool_results,
    )
    if semantic_quality_error:
        error = "director_materialization_semantic_quality_failed"
        completion_metadata: dict[str, Any] = {
            "adapter_result": {
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "qa_passed": None,
                "qa_required_for_final_verdict": True,
                "new_files": new_files[:20],
                "new_file_count": len(new_files),
                "modified_files": modified_files[:20],
                "modified_file_count": len(modified_files),
                "materialization_mode": materialization_mode,
                "materialization_error": error,
                "semantic_quality_error": semantic_quality_error,
            }
        }
        if primary_llm_summary is not None:
            completion_metadata["adapter_result"]["primary_llm"] = primary_llm_summary
        if direct_fallback_summary is not None:
            completion_metadata["adapter_result"]["direct_fallback"] = direct_fallback_summary
        if no_write_materialization_retry_summary is not None:
            completion_metadata["adapter_result"]["no_write_materialization_retry"] = (
                no_write_materialization_retry_summary
            )
        if empty_write_content_retry_summary is not None:
            completion_metadata["adapter_result"]["empty_write_content_retry"] = empty_write_content_retry_summary
        if semantic_quality_repair_summary is not None:
            completion_metadata["adapter_result"]["semantic_quality_repair"] = semantic_quality_repair_summary
        if semantic_quality_repair_attempts:
            completion_metadata["adapter_result"]["semantic_quality_repair_attempts"] = semantic_quality_repair_attempts
        cognitive_receipt = _emit_director_adapter_cognitive_receipt(
            adapter,
            task=task,
            target_task_id=target_task_id,
            run_id=run_id,
            context=context,
            receipt_type="director_adapter_materialization_semantic_quality_failed",
            payload={
                "status": "failed",
                "error": error,
                "materialization_mode": materialization_mode,
                "changed_files": all_affected_files,
                "new_files": new_files[:20],
                "modified_files": modified_files[:20],
                "tools_executed": len(tool_results),
                "write_tool_evidence": write_tool_evidence,
                "semantic_quality_error": semantic_quality_error,
                "semantic_quality_repair": semantic_quality_repair_summary or {},
                "semantic_quality_repair_attempts": semantic_quality_repair_attempts,
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        finalize_result: dict[str, Any] | None = None
        if board_claim_applied:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                authority=task_execution_attempt_authority,
                error=error,
                metadata=completion_metadata,
                task_completion_projection=_task_completion_projection_from_context(
                    context,
                    target_task_id=target_task_id,
                ),
            )
        adapter._update_task_progress(target_task_id, "failed")
        semantic_signal = {
            "code": error,
            "severity": "error",
            "detail": semantic_quality_error,
        }
        result = {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": error,
            "failure_stage": "director_materialization_semantic_quality",
            "root_cause_hint": "semantic_quality_failed",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [*decision_signals, semantic_signal],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
            "materialization_mode": materialization_mode,
            "semantic_quality_error": semantic_quality_error,
            "semantic_quality_repair_attempts": semantic_quality_repair_attempts,
        }
        return _with_task_runtime_finalize_evidence(
            result,
            requested_outcome="failed",
            finalize_result=finalize_result,
        )
    return None


async def _attach_director_file_event_bus(adapter: Any) -> None:
    """Attach the process MessageBus to Director file writers when available."""
    execution = getattr(adapter, "_execution", None)
    set_message_bus = getattr(execution, "set_message_bus", None)
    if not callable(set_message_bus):
        return

    message_bus = None
    resolve_message_bus = getattr(adapter, "_resolve_message_bus", None)
    if callable(resolve_message_bus):
        with contextlib.suppress(RuntimeError, ValueError, TypeError):
            message_bus = await resolve_message_bus()
    set_message_bus(message_bus)


# ---------------------------------------------------------------------------
# Lossless helper re-export surface (module decomposition boundary)
#
# ``execute_method`` stays the canonical import path. The bodies below were
# moved verbatim into sibling modules; non-repair helpers are re-imported here
# so the public + test-import surface resolves on this module exactly as
# before.
# ---------------------------------------------------------------------------
from .artifact_quality_diagnostics import (  # noqa: E402  (deferred for circular-import safety)
    _filter_satisfied_declared_target_missing_errors as _filter_satisfied_declared_target_missing_errors,
    _parse_missing_declared_target_files as _parse_missing_declared_target_files,
)
from .execute_method_repair_bridge import (  # noqa: E402  (deferred for circular-import safety)
    run_declared_target_contract_repairs as run_declared_target_contract_repairs,
    run_node_test_script_contract_repair as run_node_test_script_contract_repair,
    run_patch_residue_cleanup as run_patch_residue_cleanup,
    run_pre_materialization_declared_target_repairs as run_pre_materialization_declared_target_repairs,
    run_python_runtime_smoke as run_python_runtime_smoke,
    run_python_static_smoke as run_python_static_smoke,
    run_python_unittest_missing_target_repair as run_python_unittest_missing_target_repair,
    run_scaffold_marker_cleanup as run_scaffold_marker_cleanup,
    run_typescript_reexport_repair as run_typescript_reexport_repair,
)
from .quality_gate import (  # noqa: E402  (deferred for circular-import safety)
    _ACCEPTANCE_VERIFY_EXISTS_RE as _ACCEPTANCE_VERIFY_EXISTS_RE,
    _QUALITY_REPAIR_ATTEMPT_HARD_CAP as _QUALITY_REPAIR_ATTEMPT_HARD_CAP,
    _build_existing_workspace_task_evidence as _build_existing_workspace_task_evidence,
    _build_materialization_quality_repair_message as _build_materialization_quality_repair_message,
    _can_accept_existing_workspace_scope as _can_accept_existing_workspace_scope,
    _case_insensitive_file_match as _case_insensitive_file_match,
    _collect_materialization_quality_errors as _collect_materialization_quality_errors,
    _collect_step_verify_errors as _collect_step_verify_errors,
    _collect_workspace_code_diff as _collect_workspace_code_diff,
    _collect_workspace_out_of_scope_diff as _collect_workspace_out_of_scope_diff,
    _declared_target_file_quality_errors as _declared_target_file_quality_errors,
    _director_direct_text_patch_only_enabled as _director_direct_text_patch_only_enabled,
    _director_existing_scope_preflight_enabled as _director_existing_scope_preflight_enabled,
    _evaluate_acceptance_verify_exists as _evaluate_acceptance_verify_exists,
    _extract_successful_write_paths as _extract_successful_write_paths,
    _filter_materialization_quality_errors_for_repair_targets as _filter_materialization_quality_errors_for_repair_targets,
    _is_node_runtime_source_path as _is_node_runtime_source_path,
    _is_recoverable_no_write_mutation_contract_error_text as _is_recoverable_no_write_mutation_contract_error_text,
    _is_recoverable_no_write_mutation_contract_exception as _is_recoverable_no_write_mutation_contract_exception,
    _materialization_plan_probe_requires_task_boundary_triage as _materialization_plan_probe_requires_task_boundary_triage,
    _materialization_quality_scan_paths as _materialization_quality_scan_paths,
    _materialization_quality_scan_paths_with_package_manifest as _materialization_quality_scan_paths_with_package_manifest,
    _merge_successful_write_paths as _merge_successful_write_paths,
    _missing_declared_target_files as _missing_declared_target_files,
    _missing_materialization_quality_repair_target_files as _missing_materialization_quality_repair_target_files,
    _node_package_manifest_should_be_rescanned_for_test_files as _node_package_manifest_should_be_rescanned_for_test_files,
    _quality_repair_cache_root as _quality_repair_cache_root,
    _run_materialization_quality_repair_retry as _run_materialization_quality_repair_retry,
    _safe_int as _safe_int,
    _select_materialization_quality_repair_target_batch as _select_materialization_quality_repair_target_batch,
    _single_file_step_target as _single_file_step_target,
    _stage_summary_has_recoverable_no_write_mutation_contract_exception as _stage_summary_has_recoverable_no_write_mutation_contract_exception,
    _summarize_llm_stage_result as _summarize_llm_stage_result,
    _task_requires_fresh_materialization as _task_requires_fresh_materialization,
)
from .task_scope_paths import (  # noqa: E402  (deferred for circular-import safety)
    _BRACKETED_SCOPE_RE as _BRACKETED_SCOPE_RE,
    _LINE_SCOPE_RE as _LINE_SCOPE_RE,
    _coerce_path_candidate_list as _coerce_path_candidate_list,
    _dedupe_preserve_order as _dedupe_preserve_order,
    _extract_scope_markers_from_text as _extract_scope_markers_from_text,
    _extract_task_path_candidates as _extract_task_path_candidates,
    _extract_task_target_path_candidates as _extract_task_target_path_candidates,
    _filter_diff_to_task_declared_paths as _filter_diff_to_task_declared_paths,
    _glob_path_matches as _glob_path_matches,
    _looks_like_task_path_candidate as _looks_like_task_path_candidate,
    _normalize_declared_task_path as _normalize_declared_task_path,
    _path_candidate_exists_in_file_set as _path_candidate_exists_in_file_set,
    _path_matches_any_declared_candidate as _path_matches_any_declared_candidate,
    _path_matches_declared_candidate as _path_matches_declared_candidate,
    _strip_path_candidate_label as _strip_path_candidate_label,
    _task_has_declared_target_files as _task_has_declared_target_files,
    _task_text_blob as _task_text_blob,
    _workspace_path_exists_case_insensitive as _workspace_path_exists_case_insensitive,
)
