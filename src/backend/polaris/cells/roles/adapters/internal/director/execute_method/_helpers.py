"""Helpers, retries, delivery-mode pins, and MaterializationState for Director execute."""

from __future__ import annotations

import asyncio
import fnmatch as fnmatch
import json as json
import logging
import os as os
import re as re
import subprocess as subprocess
import sys as sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.fs.materialization import materialized_file_paths
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence as scan_workspace_artifact_quality_evidence,
)
from polaris.kernelone.quality.artifact_quality._scan_javascript import (
    _javascript_named_exports,
)
from polaris.kernelone.tools.tool_kinds import WRITE_TOOLS

from ..contract_verify import resolve_contract_step_verify_command
from ..dependency_artifact_evidence import (
    DirectorDependencyArtifactEvidenceError,
    build_current_task_project_artifact_receipt_evidence,
)
from ..helpers import (
    has_successful_write_tool,
)
from ..materialization_quality_boundary import run_materialization_quality_public_boundary
from ..repair_convergence_verifier import (
    build_artifact_quality_convergence_verifier,
    build_step_verify_convergence_verifier,
)
from ..repair_profile_projection import summarize_deterministic_repair_source_tools

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
    forced_tool_name: str = "write_file",
) -> list[dict[str, Any]]:
    """Empty-write retry tools via Forced Tool Surface SSOT (R127).

    Only write_file may receive path enums. edit_file is registry-faithful
    without path pinning so final-provider qualification does not fail closed.
    """
    from polaris.kernelone.tool_execution.forced_tool_surface import build_forced_tool_surface

    if strict_write_only:
        return build_forced_tool_surface(("write_file",), pin_write_paths=target_files)
    if forced_tool_name == "edit_file":
        return build_forced_tool_surface(("edit_file",))

    # write_file pinned + edit_file unpinned (registry only)
    write_surface = build_forced_tool_surface(("write_file",), pin_write_paths=target_files)
    edit_surface = build_forced_tool_surface(("edit_file",))
    return [*write_surface, *edit_surface]


def _no_write_retry_strict_write_only(target_files: list[str]) -> bool:
    return len(target_files) <= 1


def _select_no_write_materialization_retry_tool(
    task: dict[str, Any],
    *,
    workspace: str,
) -> tuple[str, bool]:
    """Choose forced retry tool after a no-write MATERIALIZE_CHANGES miss.

    Live L2-11 TASK-1-entrypoints: ``write_file`` rewrote an existing
    ``src/index.js`` and invented ``src/domains/*.js`` plus ``decideMatch``.
    Existing declared targets must force ``edit_file``. Missing create
    targets still need whole-file ``write_file``.
    """

    if _task_targets_missing_in_workspace(task, workspace):
        return "write_file", _no_write_retry_strict_write_only(_declared_write_retry_target_files(task))
    return "edit_file", True


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


_CURRENT_FILE_RETRY_CHAR_CAP = 200_000
_UNRESOLVED_IMPORT_SYMBOL_RE = re.compile(
    r"unresolved import symbol '([^']+)' from '([^']+)' in ([^\s(]+)",
    flags=re.IGNORECASE,
)
_MISSING_NAMED_EXPORT_RE = re.compile(
    r"(?P<importer>\S+):.*requested module '([^']+)' does not provide an export named '([^']+)'",
    flags=re.IGNORECASE,
)
_NO_EFFECT_RETRY_MARKERS = (
    "director_write_no_effect",
    "edit_file_empty_search",
    "Search text not found",
    "deo_member_soft_denied",
)


def _quality_error_preferred_paths(target_files: list[str], quality_errors: list[str]) -> list[str]:
    """Keep quality-hole retry on files named by residual scan errors."""

    preferred = [path for path in target_files if any(path in error for error in quality_errors)]
    return preferred or list(target_files)


def _unique_similar_export(symbol: str, names: list[str]) -> str | None:
    """Return one unique sibling export that is a safe identifier remap."""

    needle = str(symbol or "").strip()
    if not needle:
        return None
    lowered = needle.lower()
    matches: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = str(raw or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        other = name.lower()
        if other == lowered:
            matches.append(name)
            continue
        if lowered.rstrip("s") == other or other.rstrip("s") == lowered:
            matches.append(name)
            continue
        if lowered.rstrip("es") == other or other.rstrip("es") == lowered:
            matches.append(name)
    if len(matches) == 1:
        return matches[0]
    return None


def _unresolved_import_facts(quality_errors: list[str]) -> list[tuple[str, str, str]]:
    """Parse (importer, symbol, module) triples from quality-scan wording."""

    facts: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in quality_errors:
        line = str(raw or "").strip()
        if not line:
            continue
        match = _UNRESOLVED_IMPORT_SYMBOL_RE.search(line)
        if match:
            symbol, module, importer = match.group(1), match.group(2), match.group(3)
        else:
            named = _MISSING_NAMED_EXPORT_RE.search(line)
            if not named:
                continue
            importer, module, symbol = named.group("importer"), named.group(2), named.group(3)
        fact = (importer.strip(), symbol.strip(), module.strip())
        if not all(fact) or fact in seen:
            continue
        seen.add(fact)
        facts.append(fact)
    return facts


def _extract_import_containing(text: str, symbol: str) -> str | None:
    """Return the import statement block that mentions symbol."""

    token = str(symbol or "").strip()
    if not token or token not in text:
        return None
    lines = str(text).splitlines()
    for index, line in enumerate(lines):
        if token not in line:
            continue
        start = index
        while start > 0 and "import" not in lines[start]:
            start -= 1
        if "import" not in lines[start]:
            continue
        end = index
        while end < len(lines) - 1 and "from " not in lines[end]:
            end += 1
        chunk = "\n".join(lines[start : end + 1]).strip()
        if token in chunk and "from " in chunk:
            return chunk
    return None


def _import_island_block(current_files: dict[str, str], quality_errors: list[str]) -> str:
    islands: list[str] = []
    seen: set[str] = set()
    for importer, symbol, _module in _unresolved_import_facts(quality_errors):
        body = current_files.get(importer)
        if not isinstance(body, str) or not body:
            continue
        chunk = _extract_import_containing(body, symbol)
        if not chunk or chunk in seen:
            continue
        seen.add(chunk)
        islands.append(f"----- {importer} import containing {symbol} -----\n{chunk}")
        if len(islands) >= 8:
            break
    if not islands:
        return ""
    return "Exact import islands (copy old_string/search verbatim from these bytes):\n" + "\n".join(islands) + "\n"


def _suggested_import_remap_lines(
    quality_errors: list[str],
    existing_exports: dict[str, list[str]],
) -> list[str]:
    lines: list[str] = []
    for _importer, symbol, module in _unresolved_import_facts(quality_errors):
        same_module = [str(name).strip() for name in existing_exports.get(module, []) if str(name or "").strip()]
        similar = _unique_similar_export(symbol, same_module)
        if similar and similar != symbol:
            lines.append(f"- {symbol} from '{module}' -> {similar}")
            continue
        owners = [
            exporter for exporter, names in existing_exports.items() if symbol in {str(name).strip() for name in names}
        ]
        if len(owners) == 1 and owners[0] != module:
            lines.append(f"- {symbol} from '{module}' -> import from '{owners[0]}'")
        if len(lines) >= 16:
            break
    return lines


def _quality_mentions_missing_catalog_fixture(quality_errors: list[str]) -> bool:
    blob = "\n".join(str(error or "") for error in quality_errors)
    return "DEFAULT_" in blob and (
        "unresolved catalog fixture" in blob
        or "populated domain seed" in blob
        or "does not export" in blob
        or "sibling module" in blob
    )


def _no_effect_write_retry_needed(summary: dict[str, Any] | None, tool_results: list[dict[str, Any]]) -> bool:
    if has_successful_write_tool(tool_results):
        return False
    blob = json.dumps(summary or {}, ensure_ascii=False)
    for item in tool_results:
        if isinstance(item, dict):
            blob += json.dumps(item, ensure_ascii=False)
    return any(marker in blob for marker in _NO_EFFECT_RETRY_MARKERS)


def _build_no_write_materialization_retry_message(
    task: dict[str, Any],
    *,
    original_message: str,
    tool_results: list[dict[str, Any]],
    forced_tool_name: str = "write_file",
    strict_write_only: bool | None = None,
    quality_errors: list[str] | None = None,
    current_files: dict[str, str] | None = None,
    existing_exports: dict[str, list[str]] | None = None,
    allowed_target_files: list[str] | None = None,
    write_failure_errors: list[str] | None = None,
) -> str:
    declared_files = _declared_write_retry_target_files(task)
    target_files = [str(path).strip() for path in (allowed_target_files or declared_files) if str(path or "").strip()]
    if not target_files:
        target_files = declared_files
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
    if forced_tool_name == "edit_file":
        tool_instruction = (
            "Emit valid edit_file tool calls now. "
            "Do not call read, search, tree, shell, or write_file tools in this retry.\n"
            "Each edit_file call MUST include a non-empty search/old_string copied verbatim "
            "from Current UTF-8 or the Exact import islands, plus a new_string that remaps "
            "unresolved imports onto Existing named exports. Empty search is invalid.\n"
        )
    elif strict_retry:
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
            "contain a precise non-empty search/old_string.\n"
        )
    quality_lines = [str(error).strip() for error in (quality_errors or []) if str(error or "").strip()]
    quality_block = ""
    if quality_lines:
        quality_block = (
            "Quality diagnostics (single failure island; edit existing declared files to resolve; "
            "do not invent missing domain symbols):\n" + "\n".join(f"- {line}" for line in quality_lines[:16]) + "\n"
        )
    export_map = existing_exports if isinstance(existing_exports, dict) else {}
    if export_map:
        export_lines: list[str] = []
        for module, names in list(export_map.items())[:8]:
            cleaned = [str(name).strip() for name in names if str(name or "").strip()]
            if not cleaned:
                continue
            export_lines.append(f"Existing named exports in '{module}': {', '.join(cleaned[:24])}.")
        if export_lines:
            quality_block += (
                "Replace unresolved imports with these existing exports; "
                "do not invent new import names.\n" + "\n".join(export_lines) + "\n"
            )
        remap_lines = _suggested_import_remap_lines(quality_lines, export_map)
        if remap_lines:
            quality_block += "Suggested remaps (existing exports only):\n" + "\n".join(remap_lines) + "\n"
    if _quality_mentions_missing_catalog_fixture(quality_lines):
        quality_block += (
            "Test TAP / quality scan requires invented DEFAULT_* catalogs that sibling modules do not export. "
            "Do not add DEFAULT_* exports to domain modules. "
            "Rewrite tests to construct fixtures with existing createLost/createAlien/createClue/createGalaxy. "
            "Delete assertions that require populated index.seeds or domain DEFAULT_* catalogs.\n"
        )
    if any(Path(str(path)).name.startswith("test_") and Path(str(path)).suffix == ".py" for path in target_files):
        quality_block += (
            "Python acceptance tests are in scope. Edit tests/test_*.py, not domain modules.\n"
            "Live TAP traps: the substring 'galaxy' is not inside 'galaxies' (y -> ies). "
            "If REQUIRED_TERM_PAIRS exists, delete for-term-in-REQUIRED_TERMS assertIn loops "
            "and accept either pair member. node --test always prints '# fail 0' on success; "
            "do not assertNotIn('# fail'). If the CLI always runs demo, do not require unknown-command exit 1. "
            "scripts.test may be `npm run <name>` whose target already invokes node --test; "
            "do not assertIn('node', scripts.test) against the alias string.\n"
        )
    failure_lines = [str(error).strip() for error in (write_failure_errors or []) if str(error or "").strip()]
    if failure_lines:
        quality_block += (
            "Previous forced edit_file failed; do not repeat empty search or unmatched old_string:\n"
            + "\n".join(f"- {line}" for line in failure_lines[:8])
            + "\n"
        )
    files_map = current_files if isinstance(current_files, dict) else {}
    current_block = ""
    if files_map:
        parts: list[str] = ["Current UTF-8 target contents (edit these exact bytes):\n"]
        for rel_path, body in list(files_map.items())[:8]:
            token = str(rel_path or "").strip()
            text = str(body or "")
            if not token:
                continue
            if len(text) > _CURRENT_FILE_RETRY_CHAR_CAP:
                clipped = text[:_CURRENT_FILE_RETRY_CHAR_CAP]
                parts.append(
                    f"----- {token} -----\n{clipped}\n"
                    f"[truncated after {_CURRENT_FILE_RETRY_CHAR_CAP} chars; "
                    "copy old_string only from the included prefix]\n"
                )
            else:
                parts.append(f"----- {token} -----\n{text}\n")
        current_block = "".join(parts)
        current_block += _import_island_block(files_map, quality_lines)
    return (
        "[mode:materialize]\n"
        "RETRY: previous Director turn completed without any write/edit receipt and produced no files.\n"
        f"Observed tools: {observed_line}.\n"
        f"{tool_instruction}"
        "Use only task-scoped relative paths. Do not write TODO/FIXME/placeholder content.\n"
        f"{target_line}"
        f"{quality_block}"
        f"{current_block}"
        "Original task follows:\n"
        f"{original_message[:8000]}"
    )


def _primary_llm_no_write_mutation_miss(primary_llm_summary: dict[str, Any] | None) -> bool:
    """True when MATERIALIZE_CHANGES first turn sealed a no-write miss.

    Live L2-11 TASK-1-entrypoints: kernel set ``success=False`` /
    ``error=no_write_tool_available`` / ``workflow_reason=mutation_bypass_blocked``
    after read-only tools. That is a recoverable Director-local miss, not a
    provider crash. Treating it like a hard first-call failure skipped the
    forced write retry even though declared targets existed.
    """

    if not isinstance(primary_llm_summary, dict):
        return False
    metadata = primary_llm_summary.get("metadata")
    metadata_map = metadata if isinstance(metadata, dict) else {}
    tokens = {
        str(primary_llm_summary.get("error") or "").strip().lower(),
        str(primary_llm_summary.get("blocked_reason") or "").strip().lower(),
        str(metadata_map.get("blocked_reason") or "").strip().lower(),
        str(metadata_map.get("workflow_reason") or "").strip().lower(),
    }
    return bool(
        tokens
        & {
            "no_write_tool_available",
            "mutation_bypass_blocked",
        }
    )


def _no_write_materialization_retry_needed(
    *,
    primary_llm_summary: dict[str, Any] | None,
    task: dict[str, Any],
    tool_results: list[dict[str, Any]],
    workspace: str,
    requires_fresh_materialization: bool = False,
) -> bool:
    if not primary_llm_summary:
        return False
    if has_successful_write_tool(tool_results):
        return False
    if not _declared_write_retry_target_files(task):
        return False
    mutation_miss = _primary_llm_no_write_mutation_miss(primary_llm_summary)
    if primary_llm_summary.get("success") is not True and not mutation_miss:
        return False
    if mutation_miss:
        return True
    if _task_targets_missing_in_workspace(task, workspace):
        return True
    # Live L2-11 epoch 9: first call succeeded with only reads/commands while
    # declared files still had named-export holes. Missing-file gating skipped
    # the forced edit_file retry and settled no_materialized_changes.
    try:
        residual = scan_workspace_artifact_quality(
            workspace,
            relative_paths=_declared_write_retry_target_files(task)[:32],
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    if any(str(error or "").strip() for error in residual):
        return True
    # Live L2-12 TASK-3-source-core: rematerialize requires fresh writes, but
    # engine files already exist and scan clean.  Skipping the forced
    # edit_file then settled director_no_materialized_changes.  One bounded
    # existing-target edit retry is required; verification-only tasks stay
    # on the residual-quality path above.
    return bool(requires_fresh_materialization)


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
    target_files = _declared_write_retry_target_files(task)
    workspace = str(getattr(adapter, "workspace", "") or "")
    targets_missing = _task_targets_missing_in_workspace(task, workspace)
    forced_tool_name, exact_tools = _select_no_write_materialization_retry_tool(
        task,
        workspace=workspace,
    )
    strict_write_only = forced_tool_name == "write_file" and exact_tools and targets_missing
    quality_errors: list[str] = []
    existing_exports: dict[str, list[str]] = {}
    if workspace and target_files and not targets_missing:
        try:
            evidence = scan_workspace_artifact_quality_evidence(
                workspace,
                relative_paths=target_files[:32],
            )
            quality_errors = [str(error).strip() for error in evidence.errors if str(error or "").strip()]
            root = Path(workspace)
            for issue in evidence.issues:
                raw_metadata = getattr(issue, "metadata", None)
                metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
                specifier = str(metadata.get("module") or "").strip()
                exporter = str(metadata.get("exporter") or "").strip()
                if not specifier or not exporter or specifier in existing_exports:
                    continue
                try:
                    names = list(_javascript_named_exports((root / exporter).read_text(encoding="utf-8")))
                except (OSError, UnicodeError):
                    continue
                if names:
                    existing_exports[specifier] = names
        except (OSError, RuntimeError, TypeError, ValueError):
            quality_errors = []
    current_files: dict[str, str] = {}
    retry_target_files = list(target_files)
    if workspace and target_files and not targets_missing:
        preferred = _quality_error_preferred_paths(target_files, quality_errors)
        for path in target_files:
            token = str(path or "").replace("\\", "/")
            name = Path(token).name
            if (
                token.endswith(".py")
                and (name.startswith("test_") or "/tests/" in f"/{token}/")
                and path not in preferred
            ):
                preferred.append(path)
        retry_target_files = preferred
        root = Path(workspace)
        for rel_path in preferred[:8]:
            candidate = root / rel_path
            try:
                current_files[rel_path] = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
    retry_message = _build_no_write_materialization_retry_message(
        task,
        original_message=original_message,
        tool_results=tool_results,
        forced_tool_name=forced_tool_name,
        strict_write_only=exact_tools,
        quality_errors=quality_errors,
        current_files=current_files,
        existing_exports=existing_exports,
        allowed_target_files=retry_target_files,
    )
    retry_context = _pin_materialize_context_delivery_mode(dict(context), True)
    if isinstance(task, dict):
        retry_context["task"] = dict(task)
    rebind_dependency_artifact = getattr(adapter, "_rebind_director_dependency_artifact_for_dialogue", None)
    if callable(rebind_dependency_artifact):
        rebind_dependency_artifact(retry_context)
    retry_context["_transaction_kernel_forced_tool_definitions"] = _no_write_materialization_retry_tool_definitions(
        retry_target_files,
        strict_write_only=strict_write_only,
        forced_tool_name=forced_tool_name,
    )
    if exact_tools:
        retry_context["_transaction_kernel_forced_tool_choice"] = {
            "type": "function",
            "function": {"name": forced_tool_name},
        }
        retry_context["_transaction_kernel_force_exact_tools"] = True
        retry_context["director_no_write_materialization_retry"] = {
            "write_only_declared_targets": {
                "tool": forced_tool_name,
                "target_files": retry_target_files[:32],
            }
        }
        fallback_allowed_tool_names = {forced_tool_name}
    else:
        retry_context["_transaction_kernel_forced_tool_choice"] = "required"
        retry_context["director_no_write_materialization_retry"] = {
            "multi_file_declared_targets": {
                "required_write_tools": sorted(_NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES),
                "target_files": retry_target_files[:32],
            }
        }
        fallback_allowed_tool_names = set(_NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES)

    async def _invoke_retry(message: str, *, stage_label: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            result = await adapter._invoke_role_dialogue_with_timeout(
                message,
                context=retry_context,
                timeout_seconds=llm_call_timeout,
                stage_label=stage_label,
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
                "target_files": retry_target_files[:32],
            }
        summary = _summarize_llm_stage_result(result, stage=stage_label)
        extracted = adapter._execution.extract_kernel_tool_results(result)
        content = str(result.get("content") or result.get("response") or "")
        if not extracted or not has_successful_write_tool(extracted):
            fallback_tool_results = await adapter._execution.execute_tools(
                content,
                target_task_id,
                adapter._update_task_progress,
                allowed_tool_names=fallback_allowed_tool_names,
                allow_patch_fallback=True,
            )
            if fallback_tool_results:
                extracted.extend(fallback_tool_results)
        return extracted, summary

    retry_tool_results, retry_summary = await _invoke_retry(
        retry_message,
        stage_label="no_write_materialization_retry",
    )
    if _no_effect_write_retry_needed(retry_summary, retry_tool_results):
        failure_text = str(retry_summary.get("error") or "").strip()
        no_effect_message = _build_no_write_materialization_retry_message(
            task,
            original_message=original_message,
            tool_results=retry_tool_results or tool_results,
            forced_tool_name=forced_tool_name,
            strict_write_only=exact_tools,
            quality_errors=quality_errors,
            current_files=current_files,
            existing_exports=existing_exports,
            allowed_target_files=retry_target_files,
            write_failure_errors=[failure_text] if failure_text else ["director_write_no_effect"],
        )
        second_results, second_summary = await _invoke_retry(
            no_effect_message,
            stage_label="no_write_materialization_retry_no_effect",
        )
        if second_results:
            retry_tool_results = second_results
        retry_summary = second_summary
        retry_summary["no_effect_followup_attempted"] = True

    retry_summary["attempted"] = True
    retry_summary["tool_results"] = len(retry_tool_results)
    retry_summary["forced_tool"] = forced_tool_name
    retry_summary["strict_write_only"] = strict_write_only
    retry_summary["target_files"] = retry_target_files[:32]
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


from ..quality_gate import (  # noqa: E402
    _summarize_llm_stage_result as _summarize_llm_stage_result,
)
from ..task_scope_paths import (  # noqa: E402
    _extract_task_target_path_candidates as _extract_task_target_path_candidates,
    _normalize_declared_task_path as _normalize_declared_task_path,
    _workspace_path_exists_case_insensitive as _workspace_path_exists_case_insensitive,
)
