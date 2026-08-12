# ruff: noqa: F403
"""TypeScript syntax repair module: type_shapes.

This package is the lossless successor of the former ``type_shapes`` module.
It re-exports every previously-public symbol from the same import path so
``import ...typescript_syntax.type_shapes`` and ``from ...type_shapes import X``
keep resolving identically. Import-time star-imports from ``constants`` and
``common``, plus stdlib/contracts re-exports that previously lived on the module
namespace, are preserved here for exact ``dir()`` surface parity.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ...contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ...javascript_syntax import repair_javascript_export_contract_placeholders
from ...path_files import normalize_base_files_strict, normalize_repair_path_strict
from ..common import *
from ..constants import *
from ._aliases import (
    _escape_typescript_string_literal,
    _is_string_literal_suggestion_diagnostic,
    _parse_string_literal_suggestion_targets,
    _string_literal_matches,
    _string_literal_suggestion_operations,
    _valid_string_literal_suggestion_target,
    build_typescript_arg_type_function_alias_plan,
    build_typescript_init_property_alias_plan,
    build_typescript_string_literal_suggestion_plan,
)
from ._canvas_return import (
    _canvas_scale_return_type_operation,
    _typescript_implicit_return_void_operation,
    build_typescript_canvas_scale_return_type_plan,
    build_typescript_implicit_return_type_plan,
    build_typescript_object_assign_assertion_plan,
)
from ._hyphenated import (
    _repair_typescript_hyphenated_identifiers,
    _typescript_camel_case_hyphenated_identifier,
    build_typescript_hyphenated_identifier_plan,
)
from ._idents_unused import (
    _parse_typescript_identifier_suggestion_diagnostic,
    _parse_typescript_unused_local_diagnostic,
    _typescript_adapt_argument_shape_operation,
    _typescript_build_argument_shape_adapter,
    _typescript_delete_unused_local_function_operation,
    _typescript_prefix_unused_local_operation,
    build_typescript_argument_shape_adapter_plan,
    build_typescript_identifier_suggestion_plan,
    build_typescript_unused_local_plan,
)
from ._literal_union import (
    _build_typescript_branded_literal_cast_plan,
    _build_typescript_literal_union_value_facade_plan,
    _find_string_literal_after_column,
    _typescript_branded_literal_cast_operation,
    _typescript_branded_literal_target_type,
    _typescript_identifier_string_literal_union_values,
    _typescript_literal_union_value_facade_operation,
    _typescript_string_literal_union_type_aliases,
    build_typescript_literal_union_expand_plan,
)
from ._number_coercion import (
    _is_number_property_call_diagnostic,
    _is_number_to_string_argument,
    _number_property_call_candidate,
    _number_property_call_operations,
    _number_to_string_argument_operations,
    _parse_number_property_call_targets,
    _parse_number_to_string_argument_targets,
    build_typescript_number_property_call_plan,
    build_typescript_number_to_string_argument_plan,
)
from ._readonly import (
    _is_readonly_assignment_diagnostic,
    _parse_readonly_assignment_targets,
    _parse_readonly_index_assignment_targets,
    _readonly_array_index_assignment_operations,
    _readonly_assignment_cast_operations,
    _readonly_assignment_class_field_operations,
    _readonly_assignment_operations,
    _readonly_class_field_declaration_spans,
    _readonly_property_declaration_spans,
    _typescript_readonly_array_binding_operation,
    build_typescript_readonly_array_mutation_plan,
    build_typescript_readonly_assignment_plan,
)
from ._value_args import (
    _build_typescript_too_few_arguments_plan,
    _build_typescript_unresolved_identifier_plan,
    _build_typescript_value_used_as_type_plan,
    _parse_typescript_too_few_arguments_errors,
    _parse_typescript_value_used_as_type_errors,
    _repair_typescript_unresolved_identifier_lines,
    _replace_typescript_value_used_as_type_reference,
    _select_typescript_unresolved_identifier_replacement,
    _too_few_arguments_callsite_operation,
    _too_few_arguments_operation,
    _typescript_unresolved_identifier_is_array_length_assertion,
)

__all__ = (
    "_build_typescript_branded_literal_cast_plan",
    "_build_typescript_literal_union_value_facade_plan",
    "_build_typescript_too_few_arguments_plan",
    "_build_typescript_unresolved_identifier_plan",
    "_build_typescript_value_used_as_type_plan",
    "_canvas_scale_return_type_operation",
    "_escape_typescript_string_literal",
    "_find_string_literal_after_column",
    "_is_number_property_call_diagnostic",
    "_is_number_to_string_argument",
    "_is_readonly_assignment_diagnostic",
    "_is_string_literal_suggestion_diagnostic",
    "_number_property_call_candidate",
    "_number_property_call_operations",
    "_number_to_string_argument_operations",
    "_parse_number_property_call_targets",
    "_parse_number_to_string_argument_targets",
    "_parse_readonly_assignment_targets",
    "_parse_readonly_index_assignment_targets",
    "_parse_string_literal_suggestion_targets",
    "_parse_typescript_identifier_suggestion_diagnostic",
    "_parse_typescript_too_few_arguments_errors",
    "_parse_typescript_unused_local_diagnostic",
    "_parse_typescript_value_used_as_type_errors",
    "_readonly_array_index_assignment_operations",
    "_readonly_assignment_cast_operations",
    "_readonly_assignment_class_field_operations",
    "_readonly_assignment_operations",
    "_readonly_class_field_declaration_spans",
    "_readonly_property_declaration_spans",
    "_repair_typescript_hyphenated_identifiers",
    "_repair_typescript_unresolved_identifier_lines",
    "_replace_typescript_value_used_as_type_reference",
    "_select_typescript_unresolved_identifier_replacement",
    "_string_literal_matches",
    "_string_literal_suggestion_operations",
    "_too_few_arguments_callsite_operation",
    "_too_few_arguments_operation",
    "_typescript_adapt_argument_shape_operation",
    "_typescript_branded_literal_cast_operation",
    "_typescript_branded_literal_target_type",
    "_typescript_build_argument_shape_adapter",
    "_typescript_camel_case_hyphenated_identifier",
    "_typescript_delete_unused_local_function_operation",
    "_typescript_identifier_string_literal_union_values",
    "_typescript_implicit_return_void_operation",
    "_typescript_literal_union_value_facade_operation",
    "_typescript_prefix_unused_local_operation",
    "_typescript_readonly_array_binding_operation",
    "_typescript_string_literal_union_type_aliases",
    "_typescript_unresolved_identifier_is_array_length_assertion",
    "_valid_string_literal_suggestion_target",
    "build_typescript_arg_type_function_alias_plan",
    "build_typescript_argument_shape_adapter_plan",
    "build_typescript_canvas_scale_return_type_plan",
    "build_typescript_hyphenated_identifier_plan",
    "build_typescript_identifier_suggestion_plan",
    "build_typescript_implicit_return_type_plan",
    "build_typescript_init_property_alias_plan",
    "build_typescript_literal_union_expand_plan",
    "build_typescript_number_property_call_plan",
    "build_typescript_number_to_string_argument_plan",
    "build_typescript_object_assign_assertion_plan",
    "build_typescript_readonly_array_mutation_plan",
    "build_typescript_readonly_assignment_plan",
    "build_typescript_string_literal_suggestion_plan",
    "build_typescript_unused_local_plan",
)
