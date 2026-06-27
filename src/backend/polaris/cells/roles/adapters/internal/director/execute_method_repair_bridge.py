"""Controlled legacy deterministic repair bridge for ``execute_method``.

This module is the migration-time boundary for the remaining execute-method
calls into the legacy ``deterministic_repairs`` strategy host. It does not make
these helpers runtime-executable; it only keeps ``execute_method.py`` from
owning direct package imports or concrete helper calls while migration
continues.
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


def run_scaffold_marker_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _legacy_deterministic_repairs._apply_deterministic_scaffold_marker_cleanup(
        adapter,
        task=task,
        task_id=task_id,
    )


def run_node_test_script_contract_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _legacy_deterministic_repairs._apply_deterministic_node_test_script_contract_repair(
        adapter,
        task=task,
        task_id=task_id,
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
    return _legacy_deterministic_repairs._apply_deterministic_typescript_reexport_repair(
        adapter,
        task=task,
        task_id=task_id,
    )


def run_python_unittest_missing_target_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _legacy_deterministic_repairs._apply_deterministic_python_unittest_missing_target_repair(
        adapter,
        task=task,
        task_id=task_id,
    )


def run_pre_materialization_declared_target_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    workspace_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _legacy_deterministic_repairs._apply_deterministic_pre_materialization_declared_target_repairs(
        adapter,
        task=task,
        task_id=task_id,
        workspace_name=workspace_name,
    )


def run_declared_target_contract_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _legacy_deterministic_repairs._apply_deterministic_declared_target_contract_repairs(
        adapter,
        task=task,
        task_id=task_id,
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
