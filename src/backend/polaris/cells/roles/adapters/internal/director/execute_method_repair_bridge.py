"""Runtime-owned deterministic repair bridge for ``execute_method``.

This module keeps ``execute_method.py`` behind the Director Runtime public
repair boundary. File-mutating deterministic repairs must execute through
``run_runtime_repair_with_director_tools``; verifier-style helpers remain here
only as non-repair smoke checks until they move to a verifier cell.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import query_director_repair_strategy_catalog

from . import deterministic_repairs as _legacy_deterministic_repairs
from .deterministic_repairs import (
    _DECLARED_TARGET_FILE_MISSING_ERROR_RE as _DECLARED_TARGET_FILE_MISSING_ERROR_RE,
    _KNOWN_DEV_DEPENDENCY_VERSIONS as _KNOWN_DEV_DEPENDENCY_VERSIONS,
    _KNOWN_RUNTIME_DEPENDENCY_VERSIONS as _KNOWN_RUNTIME_DEPENDENCY_VERSIONS,
    _NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE as _NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE,
    _PATCH_RESIDUE_LINE_RE as _PATCH_RESIDUE_LINE_RE,
    _PYTHON_MAIN_BLOCK_RE as _PYTHON_MAIN_BLOCK_RE,
    _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS as _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS,
    _SCAFFOLD_MARKER_REPLACEMENTS as _SCAFFOLD_MARKER_REPLACEMENTS,
    _TS_CLASS_FIELD_DECL_RE as _TS_CLASS_FIELD_DECL_RE,
    _TS_DECORATOR_LINE_RE as _TS_DECORATOR_LINE_RE,
    _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE as _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE,
    _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE as _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE,
    _TS_MISSING_CLOSING_BRACE_ERROR_RE as _TS_MISSING_CLOSING_BRACE_ERROR_RE,
    _TS_NAMED_IMPORT_RE as _TS_NAMED_IMPORT_RE,
    _TS_NODE_BUILTIN_TYPES_ERROR_RE as _TS_NODE_BUILTIN_TYPES_ERROR_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE as _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE,
    _TS_RETURN_OBJECT_END_RE as _TS_RETURN_OBJECT_END_RE,
    _TS_RETURN_OBJECT_SEMICOLON_ERROR_RE as _TS_RETURN_OBJECT_SEMICOLON_ERROR_RE,
    _TS_RETURN_OBJECT_START_RE as _TS_RETURN_OBJECT_START_RE,
    _TS_RUNTIME_EXPORT_TEMPLATE as _TS_RUNTIME_EXPORT_TEMPLATE,
    _TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE as _TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE,
    _TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE as _TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE,
    _TYPEORM_IMPORT_LINE_RE as _TYPEORM_IMPORT_LINE_RE,
    _UNDECLARED_RUNTIME_IMPORT_ERROR_RE as _UNDECLARED_RUNTIME_IMPORT_ERROR_RE,
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE as _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
    _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE as _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE,
    _build_javascript_frontend_smoke_test_content as _build_javascript_frontend_smoke_test_content,
    _build_python_symbol_stub as _build_python_symbol_stub,
    _build_substantive_node_test_script as _build_substantive_node_test_script,
    _build_typescript_reexport_line as _build_typescript_reexport_line,
    _build_unresolved_import_symbol_repair_block as _build_unresolved_import_symbol_repair_block,
    _dedupe_paths as _dedupe_paths,
    _dependency_root_name as _dependency_root_name,
    _extract_relative_import_refs as _extract_relative_import_refs,
    _filter_pre_materialization_declared_target_errors as _filter_pre_materialization_declared_target_errors,
    _filter_satisfied_declared_target_missing_errors as _filter_satisfied_declared_target_missing_errors,
    _find_nearby_declared_target_source as _find_nearby_declared_target_source,
    _find_typescript_runtime_symbol_source as _find_typescript_runtime_symbol_source,
    _is_overstrict_node_test_script_contract as _is_overstrict_node_test_script_contract,
    _is_repairable_npm_test_script_error as _is_repairable_npm_test_script_error,
    _iter_typescript_files as _iter_typescript_files,
    _looks_like_typescript_reexport_failure as _looks_like_typescript_reexport_failure,
    _missing_unresolved_relative_import_target_files as _missing_unresolved_relative_import_target_files,
    _nearby_declared_target_source_candidates as _nearby_declared_target_source_candidates,
    _normalize_ts_class_field_initialization as _normalize_ts_class_field_initialization,
    _normalize_undeclared_typeorm_model_source as _normalize_undeclared_typeorm_model_source,
    _package_declared_in_manifest as _package_declared_in_manifest,
    _parse_materialization_quality_error_paths as _parse_materialization_quality_error_paths,
    _parse_missing_declared_target_files as _parse_missing_declared_target_files,
    _parse_named_import_symbols as _parse_named_import_symbols,
    _parse_required_dev_dependency_packages as _parse_required_dev_dependency_packages,
    _parse_typescript_escaped_newline_paths as _parse_typescript_escaped_newline_paths,
    _parse_typescript_zod_type_class_collision_paths as _parse_typescript_zod_type_class_collision_paths,
    _parse_undeclared_runtime_import_packages as _parse_undeclared_runtime_import_packages,
    _parse_undeclared_runtime_import_paths as _parse_undeclared_runtime_import_paths,
    _path_inside_workspace as _path_inside_workspace,
    _pre_materialization_declared_target_repair_allowed as _pre_materialization_declared_target_repair_allowed,
    _python_symbol_defined as _python_symbol_defined,
    _relative_import_repair_target_candidates as _relative_import_repair_target_candidates,
    _relative_import_suffix_order as _relative_import_suffix_order,
    _remove_patch_residue_lines as _remove_patch_residue_lines,
    _replace_deterministic_scaffold_markers as _replace_deterministic_scaffold_markers,
    _resolve_relative_ts_module as _resolve_relative_ts_module,
    _task_allows_scaffold_marker_cleanup as _task_allows_scaffold_marker_cleanup,
    _typescript_file_declares_runtime_export as _typescript_file_declares_runtime_export,
    _typescript_module_runtime_exports_symbol as _typescript_module_runtime_exports_symbol,
    _typescript_relative_import_without_suffix as _typescript_relative_import_without_suffix,
)
from .execution_tools import DirectorToolExecutor
from .repair_profile_projection import project_repair_kernel_summary
from .runtime_repair_tool_adapter import run_runtime_repair_with_director_tools
from .task_scope_paths import _extract_task_path_candidates, _normalize_declared_task_path

_LEGACY_DETERMINISTIC_REPAIR_COMPAT_PREFIXES = ("_apply_deterministic_", "repair_")
# Migration-only surface for old ``execute_method`` imports. Production calls
# must use the explicit run_* wrappers below or director.runtime public service.
_LEGACY_EXECUTE_METHOD_REPAIR_HELPER_ALLOWLIST: frozenset[str] = frozenset()
_RUNTIME_EXECUTABLE_REPAIR_SOURCE_TOOL_FALLBACKS = frozenset(
    {
        "deterministic_cpp_include_path_repair",
        "deterministic_cpp_missing_private_members_repair",
        "deterministic_cpp_placeholder_declaration_repair",
        "deterministic_cpp_standard_include_repair",
        "deterministic_cpp_struct_getter_field_access_repair",
        "deterministic_go_bare_import_string_repair",
        "deterministic_java_accessor_alias_repair",
        "deterministic_patch_residue_cleanup",
        "deterministic_typescript_duplicate_object_property_repair",
        "deterministic_typescript_enum_member_separator_repair",
        "deterministic_typescript_nullable_canvas_context_repair",
        "deterministic_typescript_return_object_semicolon_repair",
    }
)
_RUNTIME_REPAIR_SCAN_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "venv",
        ".venv",
    }
)
_RUNTIME_TEXT_REPAIR_SUFFIXES = frozenset(
    {
        ".cjs",
        ".css",
        ".go",
        ".html",
        ".js",
        ".jsx",
        ".json",
        ".mjs",
        ".py",
        ".ts",
        ".tsx",
    }
)
_PYTHON_REPAIR_SUFFIXES = frozenset({".py"})
_TYPESCRIPT_REPAIR_SUFFIXES = frozenset({".ts", ".tsx"})
_DECLARED_TARGET_REPAIR_SUFFIXES = frozenset(
    {
        ".cjs",
        ".css",
        ".go",
        ".html",
        ".js",
        ".jsx",
        ".json",
        ".mjs",
        ".py",
        ".ts",
        ".tsx",
    }
)


def _legacy_execute_method_helper_source_tool(name: str) -> str:
    if name.startswith("_apply_"):
        return name[len("_apply_") :]
    if name.startswith("repair_"):
        return name
    return ""


@lru_cache(maxsize=1)
def _runtime_executable_repair_source_tools() -> frozenset[str]:
    source_tools = set(_RUNTIME_EXECUTABLE_REPAIR_SOURCE_TOOL_FALLBACKS)
    try:
        catalog = query_director_repair_strategy_catalog()
        summary = catalog.summary if isinstance(catalog.summary, dict) else {}
        for source_tool in summary.get("executable_runtime_source_tools") or ():
            normalized = str(source_tool or "").strip()
            if normalized:
                source_tools.add(normalized)
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return frozenset(source_tools)


def get_legacy_execute_method_repair_helper(name: str) -> Any:
    """Return an allowlisted migration helper for old execute_method imports."""

    if name not in _LEGACY_EXECUTE_METHOD_REPAIR_HELPER_ALLOWLIST:
        source_tool = _legacy_execute_method_helper_source_tool(name)
        if source_tool in _runtime_executable_repair_source_tools():
            raise AttributeError(
                f"{name} is owned by director.runtime; use polaris.cells.director.runtime.public.service"
            )
        raise AttributeError(f"{name} is not an allowlisted execute_method legacy repair helper")
    return getattr(_legacy_deterministic_repairs, name)


def _adapter_workspace_path(adapter: Any) -> Path | None:
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace.exists() or not workspace.is_dir():
        return None
    return workspace


def _adapter_artifact_quality_errors(adapter: Any) -> tuple[str, ...]:
    errors = getattr(adapter, "artifact_quality_errors", ())
    if errors is None:
        return ()
    if isinstance(errors, str):
        return (errors,)
    try:
        return tuple(str(item) for item in errors if str(item or "").strip())
    except TypeError:
        return ()


def _dedupe_posix_paths(paths: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        token = str(path or "").strip().replace("\\", "/")
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return tuple(result)


def _task_declared_paths(task: dict[str, Any], *, workspace_name: str) -> tuple[str, ...]:
    paths: list[str] = []
    for candidate in _extract_task_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if normalized:
            paths.append(normalized)
    return _dedupe_posix_paths(paths)


def _runtime_base_files_from_paths(
    workspace_path: Path,
    paths: tuple[str, ...],
    *,
    suffixes: frozenset[str],
    extra_paths: tuple[str, ...] = (),
) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for raw_path in (*paths, *extra_paths):
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized:
            continue
        target = (workspace_path / normalized).resolve()
        try:
            target.relative_to(workspace_path)
        except ValueError:
            continue
        if not target.is_file() or target.suffix.lower() not in suffixes:
            continue
        try:
            base_files[normalized] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _runtime_base_files_by_scan(
    workspace_path: Path,
    *,
    suffixes: frozenset[str],
    max_files: int = 400,
) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for target in sorted(workspace_path.rglob("*")):
        if len(base_files) >= max_files:
            break
        if any(part in _RUNTIME_REPAIR_SCAN_EXCLUDED_DIRS for part in target.relative_to(workspace_path).parts):
            continue
        if not target.is_file() or target.suffix.lower() not in suffixes:
            continue
        rel_path = target.relative_to(workspace_path).as_posix()
        try:
            base_files[rel_path] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _runtime_base_files_for_task(
    workspace_path: Path,
    task: dict[str, Any],
    *,
    suffixes: frozenset[str],
    extra_paths: tuple[str, ...] = (),
    scan_when_unscoped: bool = False,
) -> tuple[dict[str, str], tuple[str, ...]]:
    workspace_name = workspace_path.name
    declared_paths = _task_declared_paths(task, workspace_name=workspace_name)
    base_files = _runtime_base_files_from_paths(
        workspace_path,
        declared_paths,
        suffixes=suffixes,
        extra_paths=extra_paths,
    )
    if not base_files and scan_when_unscoped:
        base_files = _runtime_base_files_by_scan(workspace_path, suffixes=suffixes)
    allowed_paths = _dedupe_posix_paths([*base_files.keys(), *declared_paths, *extra_paths])
    return base_files, allowed_paths


def _missing_declared_target_errors(
    workspace_path: Path,
    task: dict[str, Any],
    *,
    workspace_name: str,
) -> tuple[str, ...]:
    errors: list[str] = []
    for path in _task_declared_paths(task, workspace_name=workspace_name):
        target = (workspace_path / path).resolve()
        try:
            target.relative_to(workspace_path)
        except ValueError:
            continue
        if not target.exists():
            errors.append(f"declared target file missing {path} is missing")
    return tuple(errors)


def _runtime_repair_tool_results(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    source_tool: str,
    suffixes: frozenset[str],
    artifact_quality_errors: tuple[str, ...] = (),
    extra_paths: tuple[str, ...] = (),
    scan_when_unscoped: bool = False,
    use_editor: bool = True,
) -> list[dict[str, Any]]:
    workspace_path = _adapter_workspace_path(adapter)
    if workspace_path is None:
        return []
    base_files, allowed_paths = _runtime_base_files_for_task(
        workspace_path,
        task,
        suffixes=suffixes,
        extra_paths=extra_paths,
        scan_when_unscoped=scan_when_unscoped,
    )
    if not base_files:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths,
        use_editor=use_editor,
    )


def _runtime_repair_summary(
    *,
    stage: str,
    tool_results: list[dict[str, Any]],
    artifact_quality_errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "success": bool(tool_results),
        "repair_kernel": project_repair_kernel_summary(
            stage=stage,
            tool_results=tool_results,
            artifact_quality_errors=artifact_quality_errors,
        ),
        "repair_kernel_owner": "director.runtime",
        "legacy_strategy_host_used": False,
    }


def run_scaffold_marker_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    if not _task_allows_scaffold_marker_cleanup(task):
        return []
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_scaffold_marker_cleanup",
        suffixes=_RUNTIME_TEXT_REPAIR_SUFFIXES,
    )


def run_node_test_script_contract_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_node_test_script_contract_repair",
        suffixes=frozenset({".mjs"}),
        artifact_quality_errors=_adapter_artifact_quality_errors(adapter),
        extra_paths=("scripts/test.mjs",),
        use_editor=False,
    )


def run_patch_residue_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
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


def run_typescript_reexport_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_typescript_reexport_repair",
        suffixes=_TYPESCRIPT_REPAIR_SUFFIXES,
        artifact_quality_errors=_adapter_artifact_quality_errors(adapter),
        scan_when_unscoped=True,
    )


def run_python_unittest_missing_target_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_python_unittest_missing_target_repair",
        suffixes=_PYTHON_REPAIR_SUFFIXES,
        artifact_quality_errors=_adapter_artifact_quality_errors(adapter),
        scan_when_unscoped=True,
        use_editor=False,
    )


def run_pre_materialization_declared_target_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    workspace_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workspace_path = _adapter_workspace_path(adapter)
    if workspace_path is None:
        tool_results: list[dict[str, Any]] = []
        return tool_results, _runtime_repair_summary(
            stage="pre_materialization_declared_target_repair",
            tool_results=tool_results,
            artifact_quality_errors=(),
        )
    artifact_quality_errors = _missing_declared_target_errors(
        workspace_path,
        task,
        workspace_name=workspace_name,
    )
    tool_results = _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_pre_materialization_declared_target_repair",
        suffixes=_DECLARED_TARGET_REPAIR_SUFFIXES,
        artifact_quality_errors=artifact_quality_errors,
        scan_when_unscoped=True,
        use_editor=False,
    )
    return tool_results, _runtime_repair_summary(
        stage="pre_materialization_declared_target_repair",
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
    )


def run_declared_target_contract_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workspace_path = _adapter_workspace_path(adapter)
    if workspace_path is None:
        tool_results: list[dict[str, Any]] = []
        return tool_results, _runtime_repair_summary(
            stage="declared_target_contract_repair",
            tool_results=tool_results,
            artifact_quality_errors=(),
        )
    workspace_name = workspace_path.name
    artifact_quality_errors = (
        *_adapter_artifact_quality_errors(adapter),
        *_missing_declared_target_errors(workspace_path, task, workspace_name=workspace_name),
    )
    tool_results = _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_declared_target_contract_repair",
        suffixes=_DECLARED_TARGET_REPAIR_SUFFIXES,
        artifact_quality_errors=artifact_quality_errors,
        scan_when_unscoped=True,
        use_editor=False,
    )
    return tool_results, _runtime_repair_summary(
        stage="declared_target_contract_repair",
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
    )


def run_python_static_smoke(
    adapter: Any,
    *,
    all_affected_files: list[str],
) -> list[str]:
    return _legacy_deterministic_repairs._apply_deterministic_python_static_smoke(
        adapter,
        all_affected_files=all_affected_files,
    )


def run_python_runtime_smoke(
    adapter: Any,
    *,
    task_id: str,
    all_affected_files: list[str],
    timeout_seconds: float | None = None,
) -> list[str]:
    if timeout_seconds is None:
        return _legacy_deterministic_repairs._apply_deterministic_python_runtime_smoke(
            adapter,
            task_id=task_id,
            all_affected_files=all_affected_files,
        )
    return _legacy_deterministic_repairs._apply_deterministic_python_runtime_smoke(
        adapter,
        task_id=task_id,
        all_affected_files=all_affected_files,
        timeout_seconds=timeout_seconds,
    )
