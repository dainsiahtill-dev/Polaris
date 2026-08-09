"""Canonical TypeScript syntax repair rules for Director Runtime.

Package facade: re-exports domain modules for stable
``from ...repair_kernel.typescript_syntax import ...`` consumers.
Implementation lives in domain modules + common helpers + constants.
"""

from __future__ import annotations

from .constants import *  # noqa: F403
from .common import *  # noqa: F403
from .object_literals import *  # noqa: F403
from .nullability import *  # noqa: F403
from .imports_exports import *  # noqa: F403
from .modules import *  # noqa: F403
from .members import *  # noqa: F403
from .config_scaffold import *  # noqa: F403
from .html_dom import *  # noqa: F403
from .type_shapes import *  # noqa: F403
from .text_repairs import *  # noqa: F403
from .dispatch import *  # noqa: F403

__all__ = (
    "HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL",
    "JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL",
    "TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL",
    "TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL",
    "TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL",
    "TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL",
    "TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL",
    "TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL",
    "TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL",
    "TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL",
    "TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL",
    "TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL",
    "TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL",
    "TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL",
    "TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL",
    "TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL",
    "TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL",
    "TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL",
    "TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL",
    "TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL",
    "TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL",
    "TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL",
    "TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL",
    "TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL",
    "TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL",
    "TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORT_SOURCE_TOOL",
    "TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL",
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "TYPESCRIPT_SCAFFOLD_SOURCE_TOOL",
    "TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL",
    "TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL",
    "TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL",
    "TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL",
    "TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL",
    "TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL",
    "TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL",
    "TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL",
    "TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL",
    "TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL",
    "TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL",
    "TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL",
    "TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL",
    "TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL",
    "build_typescript_arg_type_function_alias_plan",
    "build_typescript_argument_shape_adapter_plan",
    "build_typescript_canvas_scale_return_type_plan",
    "build_typescript_duplicate_function_plan",
    "build_typescript_duplicate_object_property_plan",
    "build_typescript_enum_member_separator_plan",
    "build_typescript_hyphenated_identifier_plan",
    "build_typescript_identifier_suggestion_plan",
    "build_typescript_implicit_return_type_plan",
    "build_typescript_init_property_alias_plan",
    "build_typescript_json_as_source_plan",
    "build_typescript_literal_union_expand_plan",
    "build_typescript_missing_closing_brace_plan",
    "build_typescript_nullable_canvas_context_plan",
    "build_typescript_number_property_call_plan",
    "build_typescript_number_to_string_argument_plan",
    "build_typescript_object_assign_assertion_plan",
    "build_typescript_object_literal_comma_plan",
    "build_typescript_object_literal_missing_props_plan",
    "build_typescript_param_object_property_plan",
    "build_typescript_readonly_array_mutation_plan",
    "build_typescript_readonly_assignment_plan",
    "build_typescript_runtime_plan_for_source_tool",
    "build_typescript_shorthand_property_scope_plan",
    "build_typescript_string_literal_suggestion_plan",
    "build_typescript_truncated_eof_plan",
    "build_typescript_unknown_member_access_plan",
    "build_typescript_unused_local_plan",
    "repair_typescript_escaped_newline_in_line_comments",
    "repair_typescript_missing_closing_braces",
    "repair_typescript_nullable_canvas_context_guards",
    "repair_typescript_object_literal_commas",
)
