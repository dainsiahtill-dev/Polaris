"""Deterministic generic repairs and the top materialization-repair orchestrator.

Patch-residue/scaffold-marker cleanup, declared-target repairs, the prompt
repair block, and ``_apply_deterministic_materialization_quality_repairs``
(which fans in to each language submodule). Carved verbatim from the original
``deterministic_repairs`` module.

Cross-module calls that must honor a test ``monkeypatch`` on the
``execute_method`` module namespace (``scan_workspace_artifact_quality`` and
``_declared_target_file_quality_errors``) are resolved through
``execute_method`` (aliased ``_em``) at call time only.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public import RepairAdvisoryV1

from .. import execute_method as _em
from ..execution_tools import DirectorToolExecutor
from ..helpers import has_successful_write_tool
from ..repair_profile_projection import project_repair_kernel_summary, summarize_deterministic_repair_source_tools
from ..task_scope_paths import (
    _extract_task_path_candidates,
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
from ._runtime_bridge import run_runtime_repair_with_director_tools
from .go_repairs import (
    repair_go_bare_local_imports,
    repair_go_duplicate_declarations,
    repair_go_import_subpaths,
    repair_go_module_imports,
    repair_go_nested_import_keyword,
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
        match = _SCAFFOLD_MARKER_ERROR_RE.search(str(error or ""))
        if not match:
            continue
        normalized = _normalize_declared_task_path(str(match.group("path") or ""))
        if normalized and not any(ch in normalized for ch in ("*", "?")):
            paths.append(normalized)
    return list(dict.fromkeys(paths))


def _apply_deterministic_patch_residue_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Clean leaked patch markers from declared task files through the runtime kernel."""

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []
    workspace_name = workspace_path.name
    base_files: dict[str, str] = {}
    for candidate in _extract_task_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized:
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
        }:
            continue
        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        base_files[normalized] = text
    if not base_files:
        return []

    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_patch_residue_cleanup",
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
    )


def _apply_deterministic_go_module_import_repair(
    adapter: Any,
    *,
    task_id: str,
    advisor_notes: Sequence[RepairAdvisoryV1] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    """Repair Go import paths and cross-file coherence issues.

    Runs three repair passes in order:
    1. Module prefix normalization (``wrong-prefix/src/x`` → ``module/src/x``)
    2. Sub-path hallucination repair (``module/hallucinated/src/x`` → ``module/src/x``)
    3. Duplicate declaration merge (when ``go vet`` reports redeclaration errors)
    """
    workspace = Path(getattr(adapter, "workspace", "") or "")
    if not workspace.is_dir():
        return []
    workspace_path = workspace.resolve()
    go_files = list(workspace_path.rglob("*.go"))
    if not go_files:
        return []
    results: list[dict[str, Any]] = []

    # Pass -1: Bare import string repair (add missing "import" keyword).
    # Must run BEFORE nested import keyword repair to avoid contradiction:
    # bare_strings adds "import" to bare strings; nested_import removes extra
    # "import" inside import blocks. Order matters.
    base_files: dict[str, str] = {}
    for go_file in go_files:
        if go_file.name.endswith("_test.go"):
            continue
        try:
            relative_path = go_file.relative_to(workspace_path).as_posix()
        except ValueError:
            continue
        try:
            base_files[relative_path] = go_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    if base_files:
        bare_import_results = run_runtime_repair_with_director_tools(
            adapter,
            workspace_path=workspace_path,
            task_id=task_id,
            source_tool="deterministic_go_bare_import_string_repair",
            executor_factory=DirectorToolExecutor,
            base_files=base_files,
            advisor_notes=advisor_notes,
            use_editor=False,
            convergence_verifier=convergence_verifier,
        )
        if any(not bool(item.get("success", False)) for item in bare_import_results):
            return bare_import_results
        results.extend(bare_import_results)

    # Pass 0: Nested import keyword repair (remove extra "import" inside import blocks).
    # Runs AFTER bare import string repair so we don't undo each other.
    nested_repairs = repair_go_nested_import_keyword(workspace_path)
    for record in nested_repairs:
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_go_nested_import_repair",
                    "file": record["file"],
                    "before": record["before"],
                    "after": record["after"],
                },
            }
        )

    # Pass 1: Prefix normalization.
    if (workspace_path / "go.mod").is_file():
        prefix_repairs = repair_go_module_imports(workspace_path)
        for record in prefix_repairs:
            results.append(
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": "deterministic_go_module_import_repair",
                        "file": record["file"],
                        "before": record["before"],
                        "after": record["after"],
                    },
                }
            )

    # Pass 1b: Bare local import prefix repair (e.g. "src/models" → "module/src/models").
    if (workspace_path / "go.mod").is_file():
        bare_repairs = repair_go_bare_local_imports(workspace_path)
        for record in bare_repairs:
            results.append(
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": "deterministic_go_bare_import_repair",
                        "file": record["file"],
                        "before": record["before"],
                        "after": record["after"],
                    },
                }
            )

    # Pass 2: Sub-path hallucination repair.
    subpath_repairs = repair_go_import_subpaths(workspace_path)
    for record in subpath_repairs:
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_go_subpath_repair",
                    "file": record["file"],
                    "before": record["before"],
                    "after": record["after"],
                },
            }
        )

    # Pass 3: Duplicate declaration merge.
    dedup_repairs = repair_go_duplicate_declarations(workspace_path)
    for record in dedup_repairs:
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_go_dedup_repair",
                    "file": record["file"],
                    "action": record["action"],
                },
            }
        )

    return results


def _apply_deterministic_materialization_quality_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Legacy facade: materialization repair orchestration is runtime-scheduled."""

    from ..materialization_quality_repair_bridge import run_materialization_quality_repairs

    return run_materialization_quality_repairs(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
    )


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
