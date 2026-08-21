from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ..javascript_syntax import repair_javascript_export_contract_placeholders
from ..path_files import normalize_base_files_strict, normalize_repair_path_strict
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

"""TypeScript syntax repair module: dispatch."""

def build_typescript_runtime_plan_for_source_tool(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build conservative runtime plans for migrated TypeScript/HTML source tools."""

    normalized_source_tool = str(source_tool or "").strip()
    builders = {
        HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL: _build_html_typescript_module_script_plan,
        JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL: _build_javascript_typescript_annotation_plan,
        TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL: _build_typeorm_model_normalization_plan,
        TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL: _build_typescript_commonjs_package_type_plan,
        TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL: _build_typescript_strict_null_relaxation_plan,
        TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL: _build_typescript_config_key_split_plan,
        TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL: _build_typescript_entrypoint_plan,
        TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL: _build_typescript_escaped_newline_plan,
        TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL: _build_typescript_expect_error_placement_plan,
        TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL: build_typescript_hyphenated_identifier_plan,
        TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL: _build_typescript_dom_local_shim_cleanup_plan,
        TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL: _build_typescript_html_container_selector_plan,
        TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL: _build_typescript_import_specifier_keyword_plan,
        TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL: _build_typescript_member_alias_plan,
        TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL: build_typescript_duplicate_object_property_plan,
        TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL: _build_typescript_missing_export_plan,
        TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL: _build_typescript_missing_member_plan,
        TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL: _build_typescript_missing_relative_module_plan,
        TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL: _build_typescript_invalid_module_augmentation_plan,
        TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL: _build_typescript_private_constructor_access_plan,
        TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL: _build_typescript_private_property_access_plan,
        TYPESCRIPT_NONFINITE_ALTITUDE_GUARD_SOURCE_TOOL: _build_typescript_nonfinite_altitude_guard_plan,
        TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL: build_typescript_number_property_call_plan,
        TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL: _build_typescript_export_ambiguity_plan,
        TYPESCRIPT_REEXPORT_SOURCE_TOOL: _build_typescript_reexport_plan,
        TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL: _build_typescript_reexported_type_binding_plan,
        TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL: _build_typescript_relative_import_case_plan,
        TYPESCRIPT_SCAFFOLD_SOURCE_TOOL: _build_typescript_scaffold_plan,
        TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL: build_typescript_shorthand_property_scope_plan,
        TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL: _build_typescript_sourcefile_diagnostics_plan,
        TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL: build_typescript_string_literal_suggestion_plan,
        TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL: _build_typescript_test_block_residue_plan,
        TYPESCRIPT_TIMER_HANDLE_SOURCE_TOOL: build_typescript_timer_handle_plan,
        TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL: _build_typescript_too_few_arguments_plan,
        TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL: _build_typescript_tsconfig_lib_plan,
        TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL: _build_typescript_tsconfig_rootdir_plan,
        TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL: build_typescript_duplicate_function_plan,
        TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL: build_typescript_json_as_source_plan,
        TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL: build_typescript_implicit_return_type_plan,
        TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL: build_typescript_object_assign_assertion_plan,
        TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL: build_typescript_readonly_array_mutation_plan,
        TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL: build_typescript_param_object_property_plan,
        TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL: build_typescript_truncated_eof_plan,
        TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL: (build_typescript_object_literal_missing_props_plan),
        TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL: build_typescript_identifier_suggestion_plan,
        TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL: build_typescript_argument_shape_adapter_plan,
        TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL: build_typescript_unused_local_plan,
        TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL: build_typescript_literal_union_expand_plan,
        TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL: build_typescript_init_property_alias_plan,
        TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL: build_typescript_arg_type_function_alias_plan,
        TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL: build_typescript_unknown_member_access_plan,
        TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL: _build_typescript_uninitialized_property_plan,
        TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL: _build_typescript_unique_export_import_plan,
        TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL: _build_typescript_value_used_as_type_plan,
        TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL: _build_typescript_branded_literal_cast_plan,
        TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL: _build_typescript_literal_union_value_facade_plan,
        TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL: _build_typescript_unresolved_identifier_plan,
        TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL: _build_typescript_unused_import_plan,
        TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL: _build_typescript_vitest_globals_plan,
        TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL: _build_typescript_zod_type_class_collision_plan,
    }
    builder = builders.get(normalized_source_tool)
    if builder is None:
        return None
    return builder(base_files=_normalized_base_files(base_files), diagnostics=tuple(diagnostics or ()), mode=mode)

__all__ = (
    "build_typescript_runtime_plan_for_source_tool",
)
