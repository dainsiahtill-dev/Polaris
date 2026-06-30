"""Director deterministic repair helper facade.

This package keeps shared constants, content builders, and error-path parse
helpers extracted from ``execute_method.py`` during the lossless decomposition
of that god-module.

Cross-module calls that must honor a test ``monkeypatch`` on the
``execute_method`` module namespace (``scan_workspace_artifact_quality``) and
the ``deterministic_repairs`` <-> ``quality_gate`` reference cycle are resolved
through ``execute_method`` (aliased ``_em``) at call time. The canonical import
path remains ``execute_method`` for those helper surfaces.

Concrete file-mutating repair functions intentionally are not imported into
this package facade. Production callers must reach deterministic repairs
through ``polaris.cells.director.runtime.public`` or the Director adapter bridge
modules. Tests that characterize a specific rule should import the explicit
language submodule instead of the package root.
"""

from __future__ import annotations

from ..repair_profile_projection import (
    summarize_deterministic_repair_source_tools as summarize_deterministic_repair_source_tools,
)
from ._common import (
    _DECLARED_TARGET_FILE_MISSING_ERROR_RE as _DECLARED_TARGET_FILE_MISSING_ERROR_RE,
    _KNOWN_DEV_DEPENDENCY_VERSIONS as _KNOWN_DEV_DEPENDENCY_VERSIONS,
    _KNOWN_RUNTIME_DEPENDENCY_VERSIONS as _KNOWN_RUNTIME_DEPENDENCY_VERSIONS,
    _NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE as _NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE,
    _PATCH_RESIDUE_LINE_RE as _PATCH_RESIDUE_LINE_RE,
    _PYTHON_MAIN_BLOCK_RE as _PYTHON_MAIN_BLOCK_RE,
    _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS as _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS,
    _PYTHON_RUNTIME_TEST_FAILURE_RE as _PYTHON_RUNTIME_TEST_FAILURE_RE,
    _SCAFFOLD_MARKER_REPLACEMENTS as _SCAFFOLD_MARKER_REPLACEMENTS,
    _TS_CLASS_FIELD_DECL_RE as _TS_CLASS_FIELD_DECL_RE,
    _TS_COMMA_EXPECTED_SYNTAX_ERROR_RE as _TS_COMMA_EXPECTED_SYNTAX_ERROR_RE,
    _TS_DECORATOR_LINE_RE as _TS_DECORATOR_LINE_RE,
    _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE as _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE,
    _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE as _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE,
    _TS_MISSING_CLOSING_BRACE_ERROR_RE as _TS_MISSING_CLOSING_BRACE_ERROR_RE,
    _TS_NAMED_IMPORT_RE as _TS_NAMED_IMPORT_RE,
    _TS_NODE_BUILTIN_TYPES_ERROR_RE as _TS_NODE_BUILTIN_TYPES_ERROR_RE,
    _TS_OBJECT_LITERAL_START_RE as _TS_OBJECT_LITERAL_START_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE as _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE,
    _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE as _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE,
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
    _dedupe_paths as _dedupe_paths,
    _dependency_root_name as _dependency_root_name,
    _filter_satisfied_declared_target_missing_errors as _filter_satisfied_declared_target_missing_errors,
    _find_nearby_declared_target_source as _find_nearby_declared_target_source,
    _missing_unresolved_relative_import_target_files as _missing_unresolved_relative_import_target_files,
    _nearby_declared_target_source_candidates as _nearby_declared_target_source_candidates,
    _package_declared_in_manifest as _package_declared_in_manifest,
    _parse_materialization_quality_error_paths as _parse_materialization_quality_error_paths,
    _parse_missing_declared_target_files as _parse_missing_declared_target_files,
    _parse_named_import_symbols as _parse_named_import_symbols,
    _parse_required_dev_dependency_packages as _parse_required_dev_dependency_packages,
    _parse_typescript_escaped_newline_paths as _parse_typescript_escaped_newline_paths,
    _parse_typescript_return_object_semicolon_paths as _parse_typescript_return_object_semicolon_paths,
    _parse_typescript_zod_type_class_collision_paths as _parse_typescript_zod_type_class_collision_paths,
    _parse_undeclared_runtime_import_packages as _parse_undeclared_runtime_import_packages,
    _parse_undeclared_runtime_import_paths as _parse_undeclared_runtime_import_paths,
    _path_inside_workspace as _path_inside_workspace,
    _relative_import_repair_target_candidates as _relative_import_repair_target_candidates,
    _relative_import_suffix_order as _relative_import_suffix_order,
)
from .generic_repairs import (
    _filter_pre_materialization_declared_target_errors as _filter_pre_materialization_declared_target_errors,
    _pre_materialization_declared_target_repair_allowed as _pre_materialization_declared_target_repair_allowed,
    _remove_patch_residue_lines as _remove_patch_residue_lines,
    _replace_deterministic_scaffold_markers as _replace_deterministic_scaffold_markers,
    _task_allows_scaffold_marker_cleanup as _task_allows_scaffold_marker_cleanup,
)
from .javascript_repairs import (
    _build_javascript_frontend_smoke_test_content as _build_javascript_frontend_smoke_test_content,
    _build_substantive_node_test_script as _build_substantive_node_test_script,
    _is_javascript_test_target_path as _is_javascript_test_target_path,
    _is_overstrict_node_test_script_contract as _is_overstrict_node_test_script_contract,
    _is_plain_frontend_declared_path as _is_plain_frontend_declared_path,
)
from .npm_repairs import (
    _is_repairable_npm_test_script_error as _is_repairable_npm_test_script_error,
)
from .python_repairs import (
    _build_python_symbol_stub as _build_python_symbol_stub,
    _build_python_unittest_smoke_content as _build_python_unittest_smoke_content,
    _build_unresolved_import_symbol_repair_block as _build_unresolved_import_symbol_repair_block,
    _declared_existing_python_module_names as _declared_existing_python_module_names,
    _python_module_name_from_path as _python_module_name_from_path,
    _python_symbol_defined as _python_symbol_defined,
)
from .typeorm_repairs import (
    _normalize_ts_class_field_initialization as _normalize_ts_class_field_initialization,
    _normalize_undeclared_typeorm_model_source as _normalize_undeclared_typeorm_model_source,
)
from .typescript_repairs import (
    _build_typescript_reexport_line as _build_typescript_reexport_line,
    _extract_relative_import_refs as _extract_relative_import_refs,
    _find_typescript_runtime_symbol_source as _find_typescript_runtime_symbol_source,
    _iter_typescript_files as _iter_typescript_files,
    _looks_like_typescript_reexport_failure as _looks_like_typescript_reexport_failure,
    _relative_import_specifier_for_actual_path as _relative_import_specifier_for_actual_path,
    _resolve_case_variant_relative_path as _resolve_case_variant_relative_path,
    _resolve_relative_ts_module as _resolve_relative_ts_module,
    _typescript_file_declares_runtime_export as _typescript_file_declares_runtime_export,
    _typescript_module_runtime_exports_symbol as _typescript_module_runtime_exports_symbol,
    _typescript_relative_import_without_suffix as _typescript_relative_import_without_suffix,
)

__all__ = [
    "_DECLARED_TARGET_FILE_MISSING_ERROR_RE",
    "_KNOWN_DEV_DEPENDENCY_VERSIONS",
    "_KNOWN_RUNTIME_DEPENDENCY_VERSIONS",
    "_NODE_TEST_RUNNER_WITHOUT_TEST_FILES_ERROR_RE",
    "_PATCH_RESIDUE_LINE_RE",
    "_PYTHON_MAIN_BLOCK_RE",
    "_PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS",
    "_PYTHON_RUNTIME_TEST_FAILURE_RE",
    "_SCAFFOLD_MARKER_REPLACEMENTS",
    "_TS_CLASS_FIELD_DECL_RE",
    "_TS_COMMA_EXPECTED_SYNTAX_ERROR_RE",
    "_TS_DECORATOR_LINE_RE",
    "_TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE",
    "_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE",
    "_TS_MISSING_CLOSING_BRACE_ERROR_RE",
    "_TS_NAMED_IMPORT_RE",
    "_TS_NODE_BUILTIN_TYPES_ERROR_RE",
    "_TS_OBJECT_LITERAL_START_RE",
    "_TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE",
    "_TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE",
    "_TS_RETURN_OBJECT_END_RE",
    "_TS_RETURN_OBJECT_SEMICOLON_ERROR_RE",
    "_TS_RETURN_OBJECT_START_RE",
    "_TS_RUNTIME_EXPORT_TEMPLATE",
    "_TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE",
    "_TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE",
    "_TYPEORM_IMPORT_LINE_RE",
    "_UNDECLARED_RUNTIME_IMPORT_ERROR_RE",
    "_UNRESOLVED_IMPORT_SYMBOL_ERROR_RE",
    "_UNRESOLVED_RELATIVE_IMPORT_ERROR_RE",
    "_build_javascript_frontend_smoke_test_content",
    "_build_python_symbol_stub",
    "_build_python_unittest_smoke_content",
    "_build_substantive_node_test_script",
    "_build_typescript_reexport_line",
    "_build_unresolved_import_symbol_repair_block",
    "_declared_existing_python_module_names",
    "_dedupe_paths",
    "_dependency_root_name",
    "_extract_relative_import_refs",
    "_filter_pre_materialization_declared_target_errors",
    "_filter_satisfied_declared_target_missing_errors",
    "_find_nearby_declared_target_source",
    "_find_typescript_runtime_symbol_source",
    "_is_javascript_test_target_path",
    "_is_overstrict_node_test_script_contract",
    "_is_plain_frontend_declared_path",
    "_is_repairable_npm_test_script_error",
    "_iter_typescript_files",
    "_looks_like_typescript_reexport_failure",
    "_missing_unresolved_relative_import_target_files",
    "_nearby_declared_target_source_candidates",
    "_normalize_ts_class_field_initialization",
    "_normalize_undeclared_typeorm_model_source",
    "_package_declared_in_manifest",
    "_parse_materialization_quality_error_paths",
    "_parse_missing_declared_target_files",
    "_parse_named_import_symbols",
    "_parse_required_dev_dependency_packages",
    "_parse_typescript_escaped_newline_paths",
    "_parse_typescript_return_object_semicolon_paths",
    "_parse_typescript_zod_type_class_collision_paths",
    "_parse_undeclared_runtime_import_packages",
    "_parse_undeclared_runtime_import_paths",
    "_path_inside_workspace",
    "_pre_materialization_declared_target_repair_allowed",
    "_python_module_name_from_path",
    "_python_symbol_defined",
    "_relative_import_repair_target_candidates",
    "_relative_import_specifier_for_actual_path",
    "_relative_import_suffix_order",
    "_remove_patch_residue_lines",
    "_replace_deterministic_scaffold_markers",
    "_resolve_case_variant_relative_path",
    "_resolve_relative_ts_module",
    "_task_allows_scaffold_marker_cleanup",
    "_typescript_file_declares_runtime_export",
    "_typescript_module_runtime_exports_symbol",
    "_typescript_relative_import_without_suffix",
    "summarize_deterministic_repair_source_tools",
]
