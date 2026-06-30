"""Deterministic generic repairs for migration-only strategy hosts.

Patch-residue/scaffold-marker cleanup, declared-target repairs, the prompt
repair block were carved verbatim from the original ``deterministic_repairs``
module. Materialization orchestration is hard-cut to
``roles.adapters.public.run_director_materialization_quality_repair_schedule``.

Cross-module calls that must honor a test ``monkeypatch`` on the
``execute_method`` module namespace (``scan_workspace_artifact_quality`` and
``_declared_target_file_quality_errors``) are resolved through
``execute_method`` (aliased ``_em``) at call time only.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from .. import execute_method as _em
from ..execution_tools import DirectorToolExecutor
from ..helpers import has_successful_write_tool
from ..repair_profile_projection import project_repair_kernel_summary, summarize_deterministic_repair_source_tools
from ..task_scope_paths import (
    _extract_task_target_path_candidates,
    _normalize_declared_task_path,
    _task_text_blob,
)
from ._common import (
    _PATCH_RESIDUE_LINE_RE,
    _SCAFFOLD_MARKER_REPLACEMENTS,
    _find_nearby_declared_target_source,
    _parse_missing_declared_target_files,
)

_SCAFFOLD_MARKER_ERROR_RE = re.compile(
    r"deterministic scaffold marker ['\"][^'\"]+['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)


def _project_repair_kernel_summary(
    *,
    stage: str,
    tool_results: list[dict[str, Any]],
    artifact_quality_errors: list[str],
    mode: str = "commit",
) -> dict[str, Any]:
    return project_repair_kernel_summary(
        stage=stage,
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
        mode=mode,
    )


def _remove_patch_residue_lines(text: str) -> str:
    """Remove generated patch protocol markers that leaked into source files."""

    cleaned = _PATCH_RESIDUE_LINE_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if text.endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def _task_allows_scaffold_marker_cleanup(task: dict[str, Any]) -> bool:
    metadata_raw = task.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    if str(metadata.get("autofix_reason") or "").strip() == "deterministic_scaffold_residue_cleanup":
        return True
    task_text = _task_text_blob(task).lower()
    return "scaffold" in task_text and "residue" in task_text and "audit-seed" in task_text


def _replace_deterministic_scaffold_markers(text: str) -> str:
    cleaned = str(text or "")
    for marker, replacement in _SCAFFOLD_MARKER_REPLACEMENTS:
        cleaned = cleaned.replace(marker, replacement)
    return cleaned


def _apply_deterministic_scaffold_marker_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Clean deterministic scaffold markers from declared cleanup task files."""

    if not _task_allows_scaffold_marker_cleanup(task):
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    workspace_name = workspace_path.name
    results: list[dict[str, Any]] = []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    for candidate in _extract_task_target_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized or any(ch in normalized for ch in ("*", "?")):
            continue
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not target_path.is_file() or target_path.suffix.lower() not in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".py",
            ".go",
            ".html",
            ".css",
            ".json",
        }:
            continue
        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cleaned = _replace_deterministic_scaffold_markers(text)
        if cleaned == text:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": normalized, "content": cleaned},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=normalized)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_scaffold_marker_cleanup",
                    "file": normalized,
                    "bytes_written": int(write_result.get("bytes_written") or len(cleaned.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _apply_deterministic_scaffold_marker_error_cleanup(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []
    paths = _parse_scaffold_marker_error_paths(artifact_quality_errors)
    if not paths:
        return []
    results: list[dict[str, Any]] = []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    for normalized in paths:
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not target_path.is_file() or target_path.suffix.lower() not in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".py",
            ".go",
            ".html",
            ".css",
            ".json",
        }:
            continue
        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cleaned = _replace_deterministic_scaffold_markers(text)
        if cleaned == text:
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": normalized, "content": cleaned},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=normalized)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_scaffold_marker_quality_cleanup",
                    "file": normalized,
                    "bytes_written": int(write_result.get("bytes_written") or len(cleaned.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "modify"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _parse_scaffold_marker_error_paths(artifact_quality_errors: list[str]) -> list[str]:
    paths: list[str] = []
    for error in artifact_quality_errors:
        text = str(error or "")
        match = _SCAFFOLD_MARKER_ERROR_RE.search(text) or re.search(
            r"generic/placeholder content detected:\s*(?P<path>[^:\s]+):",
            text,
            re.IGNORECASE,
        )
        if not match:
            continue
        normalized = _normalize_declared_task_path(str(match.group("path") or ""))
        if normalized and not any(ch in normalized for ch in ("*", "?")):
            paths.append(normalized)
    return list(dict.fromkeys(paths))


def _apply_deterministic_pre_materialization_declared_target_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    workspace_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workspace_full = str(getattr(adapter, "workspace", "") or "")
    target_errors = _em._declared_target_file_quality_errors(
        workspace_full=workspace_full,
        task=task,
        workspace_name=workspace_name,
    )
    allowed_errors = _filter_pre_materialization_declared_target_errors(target_errors)
    results = _apply_deterministic_missing_declared_target_repair(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=allowed_errors,
    )
    source_tools: list[str] = []
    for item in results:
        result = item.get("result")
        if isinstance(result, dict):
            source_tools.append(str(result.get("source_tool") or ""))
    return results, {
        "stage": "deterministic_pre_materialization_declared_target_repair",
        "attempted": bool(allowed_errors),
        "success": bool(results),
        "tool_results": len(results),
        "write_tool_evidence": has_successful_write_tool(results),
        "source_tools": source_tools,
        "source_tool_profiles": summarize_deterministic_repair_source_tools(source_tools),
        "repair_kernel": _project_repair_kernel_summary(
            stage="deterministic_pre_materialization_declared_target_repair",
            tool_results=results,
            artifact_quality_errors=allowed_errors,
        ),
    }


def _filter_pre_materialization_declared_target_errors(artifact_quality_errors: list[str]) -> list[str]:
    filtered: list[str] = []
    for missing_path in _parse_missing_declared_target_files(artifact_quality_errors):
        if _pre_materialization_declared_target_repair_allowed(missing_path):
            filtered.append(f"Artifact quality scan failed: declared target file missing {missing_path!r}")
    return filtered


def _pre_materialization_declared_target_repair_allowed(relative_path: str) -> bool:
    lowered = str(relative_path or "").strip().replace("\\", "/").lower()
    if lowered in {"package.json", "pyproject.toml", "tsconfig.json", "readme.md"}:
        return True
    if lowered.startswith("src/") and lowered.endswith((".model.ts", ".repository.ts")):
        return True
    return (
        lowered == "src/models/task.model.ts"
        or lowered.endswith("/task.model.ts")
        or lowered == "src/models/tenant.model.ts"
        or lowered.endswith("/tenant.model.ts")
        or lowered == "src/services/taskgraph.ts"
        or lowered.endswith("/taskgraph.ts")
        or lowered == "tests/unit/taskgraph.test.ts"
        or lowered.endswith("/taskgraph.test.ts")
    )


def _apply_deterministic_declared_target_contract_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results: list[dict[str, Any]] = []
    source_tools: list[str] = []
    for item in results:
        result = item.get("result")
        if isinstance(result, dict):
            source_tools.append(str(result.get("source_tool") or ""))
    return results, {
        "stage": "deterministic_declared_target_contract_repair",
        "attempted": bool(results),
        "success": bool(results),
        "tool_results": len(results),
        "write_tool_evidence": has_successful_write_tool(results),
        "source_tools": source_tools,
        "source_tool_profiles": summarize_deterministic_repair_source_tools(source_tools),
        "repair_kernel": _project_repair_kernel_summary(
            stage="deterministic_declared_target_contract_repair",
            tool_results=results,
            artifact_quality_errors=[],
        ),
    }


def _apply_deterministic_missing_declared_target_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    missing_paths = _parse_missing_declared_target_files(artifact_quality_errors)
    if not missing_paths:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    task_candidates = {
        _normalize_declared_task_path(candidate, workspace_name=workspace_path.name)
        for candidate in _extract_task_target_path_candidates(task)
    }
    for missing_rel in missing_paths:
        if missing_rel not in task_candidates:
            continue
        source_path = _find_nearby_declared_target_source(workspace_path, missing_rel)
        if source_path is None:
            # No nearby source to copy: do not fabricate content (CLAUDE.md §8).
            continue
        try:
            content = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        source_file = source_path.relative_to(workspace_path).as_posix()
        if _em.scan_workspace_artifact_quality(str(workspace_path), relative_paths=[source_file]):
            # Nearby source is low quality: skip rather than fabricate (CLAUDE.md §8).
            continue
        write_result = executor.execute_tool(
            "write_file",
            {"file": missing_rel, "content": content},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=missing_rel)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_missing_declared_target_repair",
                    "file": missing_rel,
                    "source_file": source_file,
                    "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "create"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results
