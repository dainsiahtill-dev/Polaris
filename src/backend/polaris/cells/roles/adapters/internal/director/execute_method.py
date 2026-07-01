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
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.contracts import DirectorInterfaceDiscrepancyReceiptV1
from polaris.cells.director.runtime.public.service import (
    AttachDirectorRepairRevalidationEvidenceV1,
    project_director_repair_revalidation_evidence,
)
from polaris.cells.roles.adapters.public.contracts import RunDirectorMaterializationQualityRepairScheduleCommandV1
from polaris.kernelone.fs.materialization import materialized_file_paths

# ``scan_workspace_artifact_quality`` MUST stay a name on THIS module: the test
# suite monkeypatches ``execute_method.scan_workspace_artifact_quality`` and the
# moved quality/repair callers resolve it through this module namespace (``_em``)
# at call time, so the patch still takes effect. ``DirectorToolExecutor`` is kept
# for the original public surface.
from polaris.kernelone.quality import (
    scan_workspace_artifact_quality as scan_workspace_artifact_quality,
)

from .contract_verify import resolve_contract_step_verify_command
from .execution_tools import (
    DirectorToolExecutor as DirectorToolExecutor,
)
from .helpers import (
    _DEFAULT_TASK_LEASE_TTL_SECONDS,
    _TASK_LEASE_HEARTBEAT_INTERVAL_SECONDS,
    has_successful_write_tool,
    taskboard_snapshot_brief,
)
from .post_execution_repair_bridge import run_post_execution_language_repairs
from .repair_convergence_verifier import (
    build_artifact_quality_convergence_verifier,
    build_step_verify_convergence_verifier,
)
from .repair_profile_projection import summarize_deterministic_repair_source_tools

logger = logging.getLogger(__name__)


def _run_materialization_quality_public_boundary(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute materialization-quality repair via the typed roles public boundary."""

    from polaris.cells.roles.adapters.public.service import (
        run_director_materialization_quality_repair_schedule_result,
    )

    result = run_director_materialization_quality_repair_schedule_result(
        RunDirectorMaterializationQualityRepairScheduleCommandV1(
            adapter_port=adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=tuple(artifact_quality_errors),
            convergence_verifier=convergence_verifier,
        )
    )
    return [dict(item) for item in result.tool_results], dict(result.summary)


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


_DIAG_WRITE_TOOL_NAMES = frozenset(
    {"append_to_file", "edit_blocks", "edit_file", "patch_apply", "precision_edit", "repo_apply_diff", "write_file"}
)


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
    if tool_name == "write_file":
        registered = _registered_tool_definition("write_file")
        if registered is not None:
            return _pin_file_schema_to_declared_targets(registered, target_files) if pin_file_enum else registered
    file_schema: dict[str, Any] = {"type": "string"}
    if len(target_files) == 1:
        file_schema["enum"] = [target_files[0]]
    elif pin_file_enum and target_files:
        file_schema["enum"] = list(dict.fromkeys(target_files[:32]))
    if tool_name == "edit_blocks":
        return {
            "type": "function",
            "function": {
                "name": "edit_blocks",
                "description": "Edit a precise line range in an existing UTF-8 text file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file": file_schema,
                        "start": {"type": "integer", "minimum": 1},
                        "end": {"type": "integer", "minimum": 1},
                        "replace": {"type": "string", "minLength": 1},
                    },
                    "required": ["file", "start", "end", "replace"],
                },
            },
        }
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a complete UTF-8 text file at the requested target path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file": file_schema,
                    "content": {"type": "string", "minLength": 1},
                },
                "required": ["file", "content"],
            },
        },
    }


_NO_WRITE_MULTI_TARGET_RETRY_TOOL_NAMES = ("write_file", "edit_file")

_NO_WRITE_MULTI_TARGET_FALLBACK_TOOL_NAMES = frozenset({"write_file", "edit_file"})


def _pin_file_schema_to_declared_targets(definition: dict[str, Any], target_files: list[str]) -> dict[str, Any]:
    """Pin a file-parameter tool schema to the declared task boundary."""

    if not target_files:
        return dict(definition)
    pinned = json.loads(json.dumps(definition, ensure_ascii=False))
    function_payload = pinned.get("function")
    if not isinstance(function_payload, dict):
        return pinned
    parameters = function_payload.get("parameters")
    if not isinstance(parameters, dict):
        return pinned
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return pinned
    enum_values = list(dict.fromkeys(target_files[:32]))
    for property_name in (
        "file",
        "path",
        "filepath",
        "filePath",
        "file_path",
        "filename",
        "target",
        "target_file",
        "targetFile",
        "target_path",
        "targetPath",
    ):
        property_schema = properties.get(property_name)
        if isinstance(property_schema, dict):
            property_schema["enum"] = enum_values
    return pinned


def _registered_tool_definition(tool_name: str) -> dict[str, Any] | None:
    try:
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry
    except (ImportError, RuntimeError, ValueError):
        return None
    try:
        schema = ToolSpecRegistry.get_llm_schema(
            str(tool_name or "").strip(),
            include_arg_aliases=True,
            deterministic=True,
        )
    except (RuntimeError, TypeError, ValueError):
        return None
    return dict(schema) if isinstance(schema, dict) else None


def _no_write_materialization_retry_tool_definitions(
    target_files: list[str],
    *,
    strict_write_only: bool,
) -> list[dict[str, Any]]:
    if strict_write_only:
        return [_empty_write_retry_tool_definition("write_file", target_files, pin_file_enum=True)]

    definitions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool_name in _NO_WRITE_MULTI_TARGET_RETRY_TOOL_NAMES:
        definition = _registered_tool_definition(tool_name)
        if definition is None:
            continue
        function_payload = definition.get("function")
        canonical_name = ""
        if isinstance(function_payload, dict):
            canonical_name = str(function_payload.get("name") or "").strip()
        if not canonical_name or canonical_name in seen:
            continue
        if canonical_name in {"write_file", "edit_file"}:
            definition = _pin_file_schema_to_declared_targets(definition, target_files)
        definitions.append(definition)
        seen.add(canonical_name)

    if not any(
        isinstance(item.get("function"), dict) and item["function"].get("name") == "write_file" for item in definitions
    ):
        definitions.append(_empty_write_retry_tool_definition("write_file", target_files, pin_file_enum=True))
    return definitions


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
        fallback_tool_results = await adapter._execute_tools(
            retry_content,
            target_task_id,
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
        fallback_tool_results = await adapter._execute_tools(
            retry_content,
            target_task_id,
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


def _finalize_claimed_execution(
    adapter: Any,
    *,
    target_task_id: str,
    session_id: str,
    outcome: str,
    result_summary: str = "",
    error: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalize a runtime task and surface terminal-state conflicts as data."""

    if not str(session_id or "").strip():
        return {"success": False, "reason": "missing_session_id"}
    try:
        if outcome == "completed":
            result = adapter.task_runtime.complete_execution(
                target_task_id,
                session_id=session_id,
                result_summary=result_summary,
                metadata=metadata,
            )
        elif outcome == "failed":
            result = adapter.task_runtime.fail_execution(
                target_task_id,
                session_id=session_id,
                error=error or "director_execution_failed",
                metadata=metadata,
            )
        else:
            return {"success": False, "reason": "invalid_outcome", "outcome": outcome}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "success": False,
            "reason": "task_runtime_terminal_transition_failed",
            "error": str(exc),
            "outcome": outcome,
        }
    if not isinstance(result, dict):
        return {"success": False, "reason": "task_runtime_invalid_finalize_result", "outcome": outcome}
    if result.get("success") is not True:
        return {
            **result,
            "success": False,
            "reason": str(result.get("reason") or "task_runtime_finalize_rejected"),
            "outcome": outcome,
        }
    return result


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
    if board_claim_applied and task_claim_session_id:
        # Propagate the physical task-runtime lease into RoleRuntime/TransactionKernel.
        # The kernel checks this immediately before executing tools, so a late LLM
        # response from a cancelled/suspended Director claim cannot still write files.
        context = dict(context or {})
        context["session_id"] = task_claim_session_id
        context["task_runtime_session_id"] = task_claim_session_id
        context["task_runtime_guard"] = True
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
                    adapter.task_runtime.heartbeat_execution(
                        target_task_id,
                        session_id=task_claim_session_id,
                        lease_ttl_seconds=_DEFAULT_TASK_LEASE_TTL_SECONDS,
                        context_summary=selected_subject,
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    return

    async def _stop_task_claim_heartbeat() -> None:
        if heartbeat_task is None:
            return
        heartbeat_stop.set()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task

    if board_claim_applied and task_claim_session_id:
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

                if board_claim_applied and task_claim_session_id:
                    if bool(result.get("success")):
                        finalize_result = _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="completed",
                            session_id=task_claim_session_id,
                            result_summary=f"director_{'hybrid' if use_hybrid else 'sequential'}_completed",
                            metadata={"adapter_phase": "completed"},
                        )
                        if finalize_result.get("success") is not True:
                            return _task_runtime_finalization_failed_result(
                                target_task_id=target_task_id,
                                requested_outcome="completed",
                                finalize_result=finalize_result,
                            )
                    else:
                        _finalize_claimed_execution(
                            adapter,
                            target_task_id=target_task_id,
                            outcome="failed",
                            session_id=task_claim_session_id,
                            error=str(result.get("error") or "director_sequential_execution_failed"),
                            metadata={"adapter_phase": "failed"},
                        )
                return result
            except asyncio.CancelledError:
                if board_claim_applied and task_claim_session_id:
                    adapter.task_runtime.suspend_execution(
                        target_task_id,
                        session_id=task_claim_session_id,
                        reason="director_execution_cancelled",
                        metadata={"adapter_phase": "pending"},
                    )
                raise

        # 标准 LLM 执行路径
        llm_call_timeout = adapter._execution.resolve_llm_call_timeout_seconds(context)
        decision_signals: list[dict[str, Any]] = []

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
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            error = f"director_runtime_exception:{exc}"
            if board_claim_applied and task_claim_session_id:
                _finalize_claimed_execution(
                    adapter,
                    target_task_id=target_task_id,
                    outcome="failed",
                    session_id=task_claim_session_id,
                    error=error,
                    metadata={"adapter_phase": "failed", "exception_type": type(exc).__name__},
                )
            adapter._update_task_progress(target_task_id, "failed")
            return {
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

    except asyncio.CancelledError:
        if board_claim_applied and task_claim_session_id:
            adapter.task_runtime.suspend_execution(
                target_task_id,
                session_id=task_claim_session_id,
                reason="director_execution_cancelled",
                metadata={"adapter_phase": "pending"},
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

    # Convert claim_next_execution attempts to legacy format
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
        refs={
            "requested_task_id": requested_task_id,
            "selected_task_id": target_task_id,
            "selection_source": selection_source,
            "selected_from_board": selected_from_board,
            "selected_subject": selected_subject,
            "taskboard_before": board_snapshot_before,
            "taskboard_after_claim": board_snapshot_after_claim,
            "board_claim_applied": False,
            "claim_attempts": claim_attempts,
        },
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
            }
        ],
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
    write_tool_evidence = has_successful_write_tool(state.tool_results)
    can_accept_existing_scope = bool(existing_contract_evidence.get("ok")) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=write_tool_evidence,
        primary_llm_summary=primary_llm_summary,
    )

    (
        state,
        existing_contract_evidence,
        can_accept_existing_scope,
        write_tool_evidence,
        quality_repair_summary,
    ) = _phase_pre_materialization_quality(
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
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=failure_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        return {
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

    if board_claim_applied and task_claim_session_id:
        finalize_result = _finalize_claimed_execution(
            adapter,
            target_task_id=target_task_id,
            outcome="completed",
            session_id=task_claim_session_id,
            result_summary=f"changed_files={len(all_affected_files)}; tools_executed={len(tool_results)}",
            metadata=completion_metadata,
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
    preflight_can_accept_existing_scope = bool(
        preflight_existing_contract_evidence.get("ok")
    ) and _can_accept_existing_workspace_scope(
        task=task,
        requires_fresh_materialization=requires_fresh_materialization,
        write_tool_evidence=False,
        primary_llm_summary=None,
    )
    if (
        not all_affected_files
        and _director_existing_scope_preflight_enabled(context)
        and preflight_can_accept_existing_scope
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
        if board_claim_applied and task_claim_session_id:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                session_id=task_claim_session_id,
                result_summary=(
                    "preflight_verified_existing_workspace_scope="
                    f"{len(preflight_existing_contract_evidence.get('existing_paths') or [])}"
                ),
                metadata=completion_metadata,
            )
            if finalize_result.get("success") is not True:
                return _task_runtime_finalization_failed_result(
                    target_task_id=target_task_id,
                    requested_outcome="completed",
                    finalize_result=finalize_result,
                    decision_signals=decision_signals,
                    materialization_mode="preflight_verified_existing_workspace_scope",
                )
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
            fallback_tool_results = await adapter._execute_tools(content, target_task_id)
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


def _phase_pre_materialization_quality(
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
        )
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _run_materialization_quality_public_boundary(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=pre_materialization_quality_errors,
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
            write_tool_evidence = has_successful_write_tool(tool_results)
            can_accept_existing_scope = bool(
                existing_contract_evidence.get("ok")
            ) and _can_accept_existing_workspace_scope(
                task=task,
                requires_fresh_materialization=requires_fresh_materialization,
                write_tool_evidence=write_tool_evidence,
                primary_llm_summary=primary_llm_summary,
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
    )
    artifact_quality_errors += _collect_step_verify_errors(adapter, context)
    # Live factory-bench L1-01 (2026-06-17, after the symbol-coherence fix):
    # py_compile + scan_workspace_artifact_quality pass for a calculator.py
    # whose __main__ block raises at call time. The deterministic ladder
    # must actually run the code to surface this kind of failure.
    artifact_quality_errors += run_python_static_smoke(
        adapter,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
    )
    artifact_quality_errors += run_python_runtime_smoke(
        adapter,
        task_id=target_task_id,
        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
    )
    artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
        artifact_quality_errors,
        _adapter_workspace,
    )
    # Progress-aware repair budget: the base budget is 2 attempts, but while
    # an attempt makes measurable progress on EITHER dimension — fewer missing
    # declared targets OR fewer quality errors overall — the loop keeps going
    # (hard cap 5). Live factory-bench L2-11 r1: missing-count convergence
    # (3→2→1) was cut one file short by the fixed budget. L2-11 r6: an attempt
    # repaired the truncated index.html (errors 2→1, real progress) but the
    # missing-only metric (1→1) still cut the loop before diff.test.html.
    prev_missing_count = len(_missing_declared_target_files(task, _adapter_workspace))
    prev_error_count = len(artifact_quality_errors)
    prev_error_signature = _artifact_quality_error_signature(artifact_quality_errors)
    for repair_attempt in range(1, _QUALITY_REPAIR_ATTEMPT_HARD_CAP + 1):
        if not artifact_quality_errors:
            break
        current_missing_count = len(_missing_declared_target_files(task, _adapter_workspace))
        current_error_count = len(artifact_quality_errors)
        current_error_signature = _artifact_quality_error_signature(artifact_quality_errors)
        if repair_attempt > _QUALITY_REPAIR_BASE_ATTEMPTS:
            missing_progress = 0 < current_missing_count < prev_missing_count
            error_progress = 0 < current_error_count < prev_error_count
            signature_progress = bool(current_error_signature) and current_error_signature != prev_error_signature
            if not (missing_progress or error_progress or signature_progress):
                break
        prev_missing_count = current_missing_count
        prev_error_count = current_error_count
        prev_error_signature = current_error_signature
        deterministic_quality_made_progress = False
        deterministic_quality_tool_results, deterministic_quality_summary = (
            _run_materialization_quality_public_boundary(
                adapter,
                task=task,
                task_id=target_task_id,
                artifact_quality_errors=artifact_quality_errors,
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
            deterministic_quality_made_progress = True
            tool_results.extend(deterministic_quality_tool_results)
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
            )
            artifact_quality_errors += _collect_step_verify_errors(adapter, context)
            artifact_quality_errors += run_python_static_smoke(
                adapter,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            artifact_quality_errors += run_python_runtime_smoke(
                adapter,
                task_id=target_task_id,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                artifact_quality_errors,
                _adapter_workspace,
            )
            _mark_quality_repair_summary_revalidated(deterministic_quality_summary, artifact_quality_errors)
            if not artifact_quality_errors:
                break
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
            if deterministic_quality_made_progress and artifact_quality_errors:
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
            )
            artifact_quality_errors += _collect_step_verify_errors(adapter, context)
            artifact_quality_errors += run_python_static_smoke(
                adapter,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            artifact_quality_errors += run_python_runtime_smoke(
                adapter,
                task_id=target_task_id,
                all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
            )
            artifact_quality_errors = _filter_satisfied_declared_target_missing_errors(
                artifact_quality_errors,
                _adapter_workspace,
            )
            _mark_quality_repair_summary_revalidated(quality_repair_summary, artifact_quality_errors)
            if artifact_quality_errors:
                deterministic_quality_tool_results, deterministic_quality_summary = (
                    _run_materialization_quality_public_boundary(
                        adapter,
                        task=task,
                        task_id=target_task_id,
                        artifact_quality_errors=artifact_quality_errors,
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
                    )
                    artifact_quality_errors += _collect_step_verify_errors(adapter, context)
                    artifact_quality_errors += run_python_static_smoke(
                        adapter,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    )
                    artifact_quality_errors += run_python_runtime_smoke(
                        adapter,
                        task_id=target_task_id,
                        all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
                    )
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
        )
        artifact_quality_errors += _collect_step_verify_errors(adapter, context)
        artifact_quality_errors += run_python_static_smoke(
            adapter,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        )
        artifact_quality_errors += run_python_runtime_smoke(
            adapter,
            task_id=target_task_id,
            all_affected_files=_materialization_quality_scan_paths(all_affected_files, tool_results),
        )
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


def _phase_no_materialized_changes(
    adapter: Any,
    *,
    baseline_files: dict[str, str],
    board_claim_applied: bool,
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
            workspace_name=workspace_name,
        )
        out_of_scope_files = list(out_of_scope_diff.get("affected_files") or [])
        primary_llm_claimed_success = bool(primary_llm_summary.get("success")) if primary_llm_summary else False
        direct_side_effect_success = primary_llm_claimed_success and not tool_results
        if out_of_scope_files and (write_tool_evidence or direct_side_effect_success):
            error = "director_materialized_out_of_scope"
            materialization_mode = "materialized_out_of_scope"
            public_error_code = error
            failure_class = "BLUEPRINT_SCOPE_MISMATCH"
            responsible_layer = "director_scope_guard"
        else:
            error = "director_no_materialized_changes"
            materialization_mode = "no_materialized_changes"
            public_error_code = "incomplete_materialization"
            failure_class = "INCOMPLETE_MATERIALIZATION"
            responsible_layer = "director"
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
            },
        )
        completion_metadata["adapter_result"]["cognitive_runtime_receipt"] = cognitive_receipt
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        return {
            "success": False,
            "task_id": target_task_id,
            "tools_executed": len(tool_results),
            "tool_results": tool_results,
            "error": error,
            "error_code": public_error_code,
            "failure_class": failure_class,
            "responsible_layer": responsible_layer,
            "failure_stage": "director_materialization",
            "root_cause_hint": "no_changed_files",
            "cognitive_runtime_receipt": cognitive_receipt,
            "decision_signals": [
                {
                    "code": public_error_code,
                    "severity": "error",
                    "failure_class": failure_class,
                    "responsible_layer": responsible_layer,
                    "detail": (
                        "Director returned no workspace file changes; "
                        "fresh materialization is required for repair/update tasks."
                        if requires_fresh_materialization
                        else "Director returned no workspace file changes."
                    ),
                }
            ],
            "qa_required_for_final_verdict": True,
            "artifacts": [],
        }
    return None


def _phase_existing_scope_verified(
    adapter: Any,
    *,
    board_claim_applied: bool,
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
        if board_claim_applied and task_claim_session_id:
            finalize_result = _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="completed",
                session_id=task_claim_session_id,
                result_summary=(
                    "verified_existing_workspace_scope="
                    f"{len(existing_contract_evidence.get('existing_paths') or [])}; "
                    f"tools_executed={len(tool_results)}"
                ),
                metadata=completion_metadata,
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
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
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
        return {
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
    return None


def _cross_artifact_llm_escalation_enabled() -> bool:
    """Default OFF -> byte-identical legacy behaviour. Opt in via env to escalate
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
        )
        artifact_quality_errors += _collect_step_verify_errors(adapter, context)
        artifact_quality_errors += run_python_static_smoke(adapter, all_affected_files=scan_paths)
        artifact_quality_errors += run_python_runtime_smoke(
            adapter,
            task_id=target_task_id,
            all_affected_files=scan_paths,
        )
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
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
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
        return {
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
    return None


def _phase_semantic_quality_failed(
    adapter: Any,
    *,
    board_claim_applied: bool,
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
        if board_claim_applied and task_claim_session_id:
            _finalize_claimed_execution(
                adapter,
                target_task_id=target_task_id,
                outcome="failed",
                session_id=task_claim_session_id,
                error=error,
                metadata=completion_metadata,
            )
        adapter._update_task_progress(target_task_id, "failed")
        semantic_signal = {
            "code": error,
            "severity": "error",
            "detail": semantic_quality_error,
        }
        return {
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
# Lossless helper re-export surface (decomposition shim)
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
    _QUALITY_REPAIR_BASE_ATTEMPTS as _QUALITY_REPAIR_BASE_ATTEMPTS,
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
    _first_failing_verify_clause as _first_failing_verify_clause,
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
