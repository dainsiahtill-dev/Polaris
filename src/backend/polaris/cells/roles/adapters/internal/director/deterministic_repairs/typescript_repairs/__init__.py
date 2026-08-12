"""Deterministic TypeScript repair generators.

Re-export, return-object-semicolon, escaped-newline, and relative-import-case
repair clusters, carved verbatim from the original ``deterministic_repairs``
module.

This package is the lossless successor of the former ``typescript_repairs``
module. It re-exports every previously-public and private symbol from the same
import path so importers keep resolving identically.
"""

from __future__ import annotations

# Backward-compatible re-export of the standard-library / typing names that
# were module-level attributes of the former ``typescript_repairs`` module.
import os
import re
from pathlib import Path
from typing import Any

from ...task_scope_paths import (
    _dedupe_preserve_order,
    _normalize_declared_task_path,
)
from .._common import (
    _TS_MISSING_CLOSING_BRACE_ERROR_RE,
    _TS_OBJECT_LITERAL_START_RE,
    _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE,
    _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE,
    _TS_RETURN_OBJECT_START_RE,
    _TS_RUNTIME_EXPORT_TEMPLATE,
    _path_inside_workspace,
    _relative_import_suffix_order,
)
from ._constants import (
    _TS_CANNOT_FIND_TEST_GLOBAL_ERROR_RE,
    _TS_CANVAS_CONTEXT_DECLARATION_LINE_RE,
    _TS_CANVAS_SCALE_RETURN_TYPE_RE,
    _TS_COMMA_EXPECTED_ERROR_RE,
    _TS_DUPLICATE_OBJECT_PROPERTY_ERROR_RE,
    _TS_ENUM_DECLARATION_LINE_RE,
    _TS_ENUM_MEMBER_LINE_RE,
    _TS_ENUM_MEMBER_SEPARATOR_ERROR_RE,
    _TS_EXPORTED_CLASS_RE_TEMPLATE,
    _TS_IDENTIFIER_RE,
    _TS_MISSING_PROPERTY_ERROR_RE,
    _TS_NAMED_REEXPORT_RE,
    _TS_NO_EXPORTED_MEMBER_ERROR_RE,
    _TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE,
    _TS_NULLABLE_ARGUMENT_ERROR_RE,
    _TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE,
    _TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE,
    _TS_NUMBER_TO_FUNCTION_ARGUMENT_ERROR_RE,
    _TS_NUMBER_TO_STRING_ARGUMENT_ERROR_RE,
    _TS_NUMERIC_MEMBER_NAMES,
    _TS_POSSIBLY_NULL_ERROR_RE,
    _TS_REQUIRED_PROPERTIES_MISSING_ERROR_RE,
    _TS_REQUIRED_PROPERTY_MISSING_ERROR_RE,
    _TS_SOURCEFILE_DIAGNOSTICS_ERROR_RE,
    _TS_STRINGISH_MEMBER_NAMES,
    _TS_STRUCTURAL_TYPE_RE_TEMPLATE,
    _TS_TOO_FEW_ARGUMENTS_ERROR_RE,
    _TS_UNINITIALIZED_PROPERTY_ERROR_RE,
    _TS_UNKNOWN_VALUE_ERROR_RE,
)
from ._core import (
    _add_defaults_to_typescript_method_params,
    _build_typescript_missing_member_declaration,
    _build_typescript_missing_member_signature,
    _find_matching_brace,
    _find_matching_paren,
    _find_typescript_argument_span_at_column,
    _find_typescript_class_declaration,
    _find_typescript_object_literal_bounds_for_line,
    _find_typescript_structural_type_declaration,
    _find_unique_typescript_method_declaration,
    _infer_typescript_missing_member_value_type,
    _infer_typescript_object_shape_for_symbol,
    _infer_typescript_property_child_value_type,
    _insert_typescript_object_literal_defaults,
    _patch_typescript_function_return_object_literals,
    _repair_typescript_required_object_literals,
    _repair_typescript_return_object_literals_for_repaired_members,
    _repair_typescript_structural_property_shapes,
    _repair_typescript_too_few_arguments_callsite,
    _repair_typescript_unhostable_member_access_defaults,
    _replace_typescript_member_access_with_default,
    _replace_typescript_structural_property_type,
    _split_typescript_argument_spans,
    _split_typescript_params,
    _typescript_call_name_from_usage_line,
    _typescript_class_name_from_text,
    _typescript_class_text_has_member,
    _typescript_constructor_default_arguments,
    _typescript_declaration_type_name,
    _typescript_default_function_value_for_type,
    _typescript_default_object_literal_for_type,
    _typescript_default_value_for_type,
    _typescript_destructuring_source_for_local_symbol,
    _typescript_error_usage_line,
    _typescript_existing_member_names_for_type,
    _typescript_expression_default_value,
    _typescript_line_bounds,
    _typescript_line_start_offsets,
    _typescript_member_access_default_type,
    _typescript_member_alias_replacement,
    _typescript_member_name_suggests_number,
    _typescript_member_name_suggests_string,
    _typescript_method_params_from_usage_line,
    _typescript_missing_member_value_type,
    _typescript_object_literal_existing_properties,
    _typescript_object_literal_property_indent,
    _typescript_object_type_fields,
    _typescript_object_type_from_fields,
    _typescript_param_with_default,
    _typescript_parent_type_for_local_symbol,
    _typescript_property_line_with_default,
    _typescript_receiver_for_member_access,
    _typescript_source_lines_for_error_item,
    _typescript_static_member_access_context,
    _typescript_structural_member_type_map,
    _typescript_symbol_usage_treats_as_number,
    _typescript_type_for_local_variable,
    _typescript_type_has_structural_declaration,
    _typescript_unhostable_call_default_type,
    _typescript_usage_line_treats_member_as_number,
    _typescript_usage_line_treats_member_as_string,
    _wrap_typescript_argument_at_column_as_string,
)
from ._imports_exports import (
    _TS_IMPORT_FROM_SPECIFIER_TEMPLATE,
    _find_unique_typescript_export_for_import,
    _iter_typescript_files,
    _parse_named_export_symbols,
    _relative_import_specifier_for_actual_path,
    _remove_unused_typescript_import,
    _repair_typescript_enum_member_line,
    _repair_typescript_enum_member_separator_lines,
    _repair_typescript_missing_closing_braces,
    _repair_typescript_return_object_semicolon_lines,
    _resolve_case_variant_relative_path,
    _typescript_brace_balance_delta,
    _typescript_identifier_used_outside_span,
    _typescript_import_pairs_for_specifier,
    _typescript_import_statement_for_specifier,
    _typescript_module_runtime_exports_symbol,
    _typescript_relative_import_without_suffix,
)
from ._nullability import (
    _looks_like_single_line_typescript_object_property,
    _repair_typescript_duplicate_object_property_lines,
    _repair_typescript_multiline_dom_handle_declarations,
    _repair_typescript_nullable_canvas_context_guards,
    _typescript_canvas_context_non_null_assertion_line,
    _typescript_dom_handle_non_null_assertion_line,
    _typescript_nullable_guard_follows,
    _typescript_nullable_guard_in_text_window,
)
from ._parsers import (
    _parse_typescript_comma_expected_errors,
    _parse_typescript_duplicate_object_property_errors,
    _parse_typescript_enum_member_separator_errors,
    _parse_typescript_missing_closing_brace_errors,
    _parse_typescript_missing_member_errors,
    _parse_typescript_missing_required_property_errors,
    _parse_typescript_nullable_canvas_context_errors,
    _parse_typescript_number_to_function_argument_errors,
    _parse_typescript_number_to_string_argument_errors,
    _parse_typescript_too_few_arguments_errors,
    _parse_typescript_uninitialized_property_errors,
    _parse_typescript_unknown_value_errors,
    _strip_typescript_error_module_ref,
)
