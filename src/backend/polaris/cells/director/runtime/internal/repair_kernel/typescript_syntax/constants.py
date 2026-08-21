from __future__ import annotations

import re
"""TypeScript repair constants and compiled patterns (constants)."""

TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"

TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL = "deterministic_typescript_nullable_canvas_context_repair"

TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL = "deterministic_typescript_duplicate_object_property_repair"

TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL = "deterministic_typescript_enum_member_separator_repair"

TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL = "deterministic_typescript_missing_closing_brace_repair"

TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL = "deterministic_typescript_number_to_string_argument_repair"

TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL = "deterministic_typescript_number_property_call_repair"

TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL = "deterministic_typescript_readonly_assignment_repair"

TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL = "deterministic_typescript_implicit_return_type_repair"

TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL = "deterministic_typescript_object_assign_assertion_repair"

TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL = "deterministic_typescript_readonly_array_mutation_repair"

TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL = "deterministic_typescript_param_object_property_repair"

TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL = "deterministic_typescript_truncated_eof_repair"

TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL = "deterministic_typescript_object_literal_missing_props_repair"

TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL = "deterministic_typescript_identifier_suggestion_repair"

TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL = "deterministic_typescript_argument_shape_adapter_repair"

TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL = "deterministic_typescript_unused_local_repair"

TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL = "deterministic_typescript_duplicate_function_repair"

TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL = "deterministic_typescript_json_as_source_repair"

TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL = "deterministic_typescript_literal_union_expand_repair"

TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL = "deterministic_typescript_init_property_alias_repair"

TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL = "deterministic_typescript_arg_type_function_alias_repair"

TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL = "deterministic_typescript_shorthand_property_scope_repair"

TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL = "deterministic_typescript_string_literal_suggestion_repair"

TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL = "deterministic_typescript_unknown_member_access_repair"

TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL = "deterministic_typescript_canvas_scale_return_type_repair"

HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL = "deterministic_html_typescript_module_script_repair"

JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL = "deterministic_javascript_typescript_annotation_repair"

TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL = "deterministic_typeorm_model_normalization_repair"

TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL = "deterministic_typescript_commonjs_package_type_repair"

TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL = "deterministic_typescript_strict_null_relaxation_repair"

TYPESCRIPT_TIMER_HANDLE_SOURCE_TOOL = "deterministic_typescript_timer_handle_repair"

TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL = "deterministic_typescript_config_key_split_repair"

TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL = "deterministic_typescript_entrypoint_repair"

TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL = "deterministic_typescript_escaped_newline_repair"

TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL = "deterministic_typescript_expect_error_placement_repair"

TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL = "deterministic_typescript_hyphenated_identifier_repair"

TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL = "deterministic_typescript_import_specifier_keyword_repair"

TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL = "deterministic_typescript_member_alias_repair"

TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL = "deterministic_typescript_missing_export_repair"

TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL = "deterministic_typescript_missing_member_repair"

TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL = "deterministic_typescript_private_constructor_access_repair"

TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL = "deterministic_typescript_private_property_access_repair"
TYPESCRIPT_NONFINITE_ALTITUDE_GUARD_SOURCE_TOOL = "deterministic_typescript_nonfinite_altitude_guard_repair"

TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL = "deterministic_typescript_export_ambiguity_repair"

TYPESCRIPT_REEXPORT_SOURCE_TOOL = "deterministic_typescript_reexport_repair"

TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL = "deterministic_typescript_reexported_type_binding_repair"

TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL = "deterministic_typescript_relative_import_case_repair"

TYPESCRIPT_SCAFFOLD_SOURCE_TOOL = "deterministic_typescript_scaffold_repair"

TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL = "deterministic_typescript_sourcefile_diagnostics_repair"

TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL = "deterministic_typescript_too_few_arguments_repair"

TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL = "deterministic_typescript_tsconfig_lib_repair"

TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL = "deterministic_typescript_tsconfig_rootdir_repair"

TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL = "deterministic_typescript_uninitialized_property_repair"

TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL = "deterministic_typescript_unique_export_import_repair"

TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL = "deterministic_typescript_value_used_as_type_repair"

TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL = "deterministic_typescript_branded_literal_cast_repair"

TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL = "deterministic_typescript_literal_union_value_facade_repair"

TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL = "deterministic_typescript_unresolved_identifier_repair"

TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL = "deterministic_typescript_unused_import_repair"

TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL = "deterministic_typescript_test_block_residue_repair"

TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL = "deterministic_typescript_vitest_globals_repair"

TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL = "deterministic_typescript_zod_type_class_collision_repair"

TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL = "deterministic_typescript_html_container_selector_repair"

TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL = "deterministic_typescript_dom_local_shim_cleanup_repair"

TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL = "deterministic_typescript_missing_relative_module_repair"

TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL = (
    "deterministic_typescript_invalid_module_augmentation_repair"
)

_REMOVED_TYPESCRIPT_COMPILER_OPTIONS = frozenset({"charset"})

_TS_INLINE_OBJECT_MISSING_COMMA_RE = re.compile(
    r"(?P<value>\b[A-Za-z_$][A-Za-z0-9_$]*\b|\)|\]|\}|['\"][^'\"]*['\"]|-?\d+(?:\.\d+)?)"
    r"(?P<gap>[ \t]{2,})"
    r"(?P<key>[A-Za-z_$][A-Za-z0-9_$]*\s*:)"
)

_TS_OBJECT_PROPERTY_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z_$][A-Za-z0-9_$]*\s*:")

_TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)\s*;\s*$")

_TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<property>(?:\[[^\]]+\]|[A-Za-z_$][\w$]*|['\"][^'\"]+['\"])\s*:\s*[^;{}]+);\s*$"
)

_TS_RETURN_OBJECT_START_RE = re.compile(r"\breturn\s*\{\s*$")

_TS_OBJECT_LITERAL_START_RE = re.compile(r"(?:\breturn\s*|=\s*)\{\s*$")

_TS_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")

_TS_CONFIG_SPLIT_KEY_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<left>[A-Za-z_$][A-Za-z0-9_$]*)\s+"
    r"(?P<right>[A-Za-z_$][A-Za-z0-9_$]*)(?P<suffix>\s*:.*)$"
)

_TS_TEST_BLOCK_RESIDUE_STATEMENT_RE = re.compile(r"^\s{2,}(?:assert\.|expect\(|it\(|test\()")

_TS_TEST_BLOCK_RESIDUE_CLOSER_RE = re.compile(r"^\s*\}\s*\)\s*;?\s*$")

_TS_CONFIG_JOINABLE_KEYS = frozenset(
    {
        "allowJs",
        "allowSyntheticDefaultImports",
        "assetsDir",
        "baseUrl",
        "cacheDir",
        "checkJs",
        "declarationMap",
        "emptyOutDir",
        "envDir",
        "esModuleInterop",
        "forceConsistentCasingInFileNames",
        "moduleResolution",
        "noEmit",
        "outDir",
        "publicDir",
        "resolveJsonModule",
        "rootDir",
        "skipLibCheck",
        "sourceMap",
        "strictPort",
    }
)

_TS_HYPHENATED_VARIABLE_DECLARATION_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<left>[A-Za-z_$][A-Za-z0-9_$]*)-(?P<right>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\b(?=\s*(?::|=))"
)

_TS_POSSIBLY_NULL_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18047:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)['\"]\s+"
    r"is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)

_TS_POSSIBLY_NULL_MESSAGE_RE = re.compile(
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)['\"]\s+"
    r"is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)

_TS_POSSIBLY_UNDEFINED_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18048:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)['\"]\s+"
    r"is\s+possibly\s+['\"]undefined['\"]",
    re.IGNORECASE,
)

_TS_POSSIBLY_UNDEFINED_MESSAGE_RE = re.compile(
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)['\"]\s+"
    r"is\s+possibly\s+['\"]undefined['\"]",
    re.IGNORECASE,
)

_TS_NULLABLE_ARGUMENT_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\s*\|\s*null['\"]\s+is\s+not\s+assignable\s+"
    r"to\s+parameter\s+of\s+type\s+['\"](?P=type)['\"]",
    re.IGNORECASE,
)

_TS_DUPLICATE_OBJECT_PROPERTY_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1117:\s*"
    r"An\s+object\s+literal\s+cannot\s+have\s+multiple\s+properties\s+with\s+the\s+same\s+name",
    re.IGNORECASE,
)

_TS_DUPLICATE_IDENTIFIER_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2300:\s*"
    r"Duplicate\s+identifier\s+['\"](?P<name>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_TS_CANNOT_FIND_MODULE_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2307:\s*"
    r"Cannot\s+find\s+module\s+['\"](?P<module>[^'\"]+)['\"]"
    r"(?:\s+or\s+its\s+corresponding\s+type\s+declarations)?",
    re.IGNORECASE,
)

_TS_INVALID_MODULE_AUGMENTATION_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2664:\s*"
    r"Invalid\s+module\s+name\s+in\s+augmentation,\s+module\s+['\"](?P<module>[^'\"]+)['\"]\s+"
    r"cannot\s+be\s+found",
    re.IGNORECASE,
)

_TS_ENUM_MEMBER_SEPARATOR_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1357:\s*"
    r"An\s+enum\s+member\s+name\s+must\s+be\s+followed\s+by\s+a\s+',',\s*'=',\s*or\s*'}'",
    re.IGNORECASE,
)

_TS_MISSING_CLOSING_BRACE_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1005:\s*"
    r"['\"]?\}['\"]?\s+expected",
    re.IGNORECASE,
)

_TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"]number['\"]\s+is\s+not\s+assignable\s+to\s+parameter\s+"
    r"of\s+type\s+['\"]string['\"]",
    re.IGNORECASE,
)

_TS_NUMBER_PROPERTY_CALL_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2349:\s*"
    r"This\s+expression\s+is\s+not\s+callable",
    re.IGNORECASE,
)

_TS_READONLY_ASSIGNMENT_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2540:\s*"
    r"Cannot\s+assign\s+to\s+['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+"
    r"because\s+it\s+is\s+a\s+read-only\s+property",
    re.IGNORECASE,
)

_TS_READONLY_INDEX_ASSIGNMENT_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2542:\s*"
    r"Index\s+signature\s+in\s+type\s+['\"]readonly\s+",
    re.IGNORECASE,
)

_TS_DUPLICATE_FUNCTION_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2393|2323):\s*"
    r"(?:Duplicate\s+function\s+implementation|Cannot\s+redeclare\s+exported\s+variable\s+"
    r"['\"](?P<name>[A-Za-z_$][A-Za-z0-9_$]*)['\"])",
    re.IGNORECASE,
)

_TS_FUNCTION_DECL_RE = re.compile(
    r"(?P<full>^(?P<indent>[ \t]*)(?:export\s+)?(?:async\s+)?function\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*[<(])",
    re.MULTILINE,
)

_TS_INDEX_ASSIGN_PROP_RE = re.compile(
    r"(?:\.\s*(?P<prop>[A-Za-z_$][A-Za-z0-9_$]*)\s*\[|[A-Za-z_$][A-Za-z0-9_$]*\s*\.\s*"
    r"(?P<prop2>[A-Za-z_$][A-Za-z0-9_$]*)\s*\[)"
)

_TS_STRING_LITERAL_SUGGESTION_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2820:\s*"
    r"Type\s+(?P<actual_quote>['\"])(?P<actual>.*?)(?P=actual_quote)\s+is\s+not\s+assignable\s+to\s+type\s+"
    r"(?P<target_quote>['\"])(?P<target>.*?)(?P=target_quote)\.\s+Did\s+you\s+mean\s+"
    r"(?P<suggestion_quote>['\"])(?P<suggestion>.*?)(?P=suggestion_quote)\?",
    re.IGNORECASE,
)

_TS_VALUE_USED_AS_TYPE_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2749:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+refers\s+to\s+a\s+value,\s+"
    r"but\s+is\s+being\s+used\s+as\s+a\s+type\s+here",
    re.IGNORECASE,
)

_TS_VALUE_USED_AS_TYPE_MESSAGE_RE = re.compile(
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+refers\s+to\s+a\s+value,\s+"
    r"but\s+is\s+being\s+used\s+as\s+a\s+type\s+here",
    re.IGNORECASE,
)

_TS_PRIVATE_CONSTRUCTOR_ACCESS_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2673:\s*"
    r"Constructor\s+of\s+class\s+['\"](?P<class>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+"
    r"is\s+private\s+and\s+only\s+accessible\s+within\s+the\s+class\s+declaration",
    re.IGNORECASE,
)

_TS_PRIVATE_CONSTRUCTOR_ACCESS_MESSAGE_RE = re.compile(
    r"Constructor\s+of\s+class\s+['\"](?P<class>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+"
    r"is\s+private\s+and\s+only\s+accessible\s+within\s+the\s+class\s+declaration",
    re.IGNORECASE,
)

_TS_PRIVATE_PROPERTY_ACCESS_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2341|2345):\s*"
    r"(?:Argument of type '[^']+' is not assignable to parameter of type '[^']+'\.\s*)?"
    r"Property\s+['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+"
    r"is\s+private(?:\s+and\s+only\s+accessible\s+within\s+class|\s+in\s+type)\s+"
    r"['\"](?P<class>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE,
)

_TS_PRIVATE_PROPERTY_ACCESS_MESSAGE_RE = re.compile(
    r"Property\s+['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+"
    r"is\s+private(?:\s+and\s+only\s+accessible\s+within\s+class|\s+in\s+type)\s+"
    r"['\"](?P<class>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE,
)

_TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18004:\s*"
    r"No\s+value\s+exists\s+in\s+scope\s+for\s+the\s+shorthand\s+property\s+"
    r"['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE,
)

_TS_UNKNOWN_MEMBER_ACCESS_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18046:\s*"
    r"['\"](?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)\.(?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+"
    r"is\s+of\s+type\s+['\"]unknown['\"]",
    re.IGNORECASE,
)

_TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"]number['\"]\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+"
    r"['\"]\(\s*n\s*:\s*number\s*\)\s*=>\s*number['\"]",
    re.IGNORECASE,
)

_TS_CANVAS_SCALE_RETURN_TYPE_RE = re.compile(
    r"(?P<prefix>export\s+function\s+scaleToCanvas\s*\([\s\S]*?\)\s*:\s*)"
    r"(?P<return_type>\{\s*sx\s*:\s*number\s*;\s*sy\s*:\s*number\s*;\s*scale\s*:\s*number\s*;?\s*\})",
    re.MULTILINE,
)

_TS_ENUM_DECLARATION_LINE_RE = re.compile(r"\benum\s+[A-Za-z_$][A-Za-z0-9_$]*\b[^{}]*{")

_TS_ENUM_MEMBER_LINE_RE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z_$][A-Za-z0-9_$]*(?:\s*=\s*[^,;{}]+?)?)(?P<separator>[;,]?)(?P<space>\s*)(?P<comment>//.*)?$"
)

_TS_CANVAS_CONTEXT_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*[^;\n]*\.getContext\(\s*['\"]2d['\"]\s*\)\s*;?\s*$"
)

_TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<rhs>[^;\n]*\([^;\n]*\))\s*;?\s*$"
)

_TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<source>[^;\n]*"
    r"(?:document\.(?:getElementById|querySelector)|"
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
    r"\s*\([^;\n]*\)[^;\n]*)\s*;?\s*$"
)

_HTML_TS_MODULE_SCRIPT_ERROR_RE = re.compile(
    r"HTML\s+module\s+script\s+references\s+TypeScript\s+source\s+['\"](?P<src>[^'\"]+\.tsx?)['\"]\s+"
    r"in\s+(?P<path>\S+);\s+static\s+entrypoints\s+must\s+load\s+JavaScript",
    re.IGNORECASE,
)

_HTML_COMPILED_JS_MISSING_RE = re.compile(
    r"HTML\s+module\s+script\s+references\s+missing\s+compiled\s+JavaScript\s+"
    r"['\"](?P<src>[^'\"]+\.js)['\"]\s+in\s+(?P<path>[^;\s]+)"
    r"(?:;\s+TypeScript\s+build\s+emitted\s+['\"]?(?P<emitted>[^'\"\s]+\.js)['\"]?)?",
    re.IGNORECASE,
)

_HTML_TRUNCATED_ERROR_RE = re.compile(
    r"syntax error in (?P<path>[^:]+):\s*truncated/incomplete HTML",
    re.IGNORECASE,
)

_HTML_MODULE_SCRIPT_SRC_RE = re.compile(
    r"src=(?P<quote>['\"])(?P<src>[^'\"]+)(?P=quote)",
    re.IGNORECASE,
)

_HTML_ID_ATTRIBUTE_RE = re.compile(r"\bid\s*=\s*(?P<quote>['\"])(?P<id>[^'\"]+)(?P=quote)", re.IGNORECASE)

_TS_EXACT_HTML_ID_TOKEN_REGEX_RE = re.compile(r"/id=\[\"'\]\((?P<tokens>[A-Za-z0-9_|-]+)\)\[\"'\]/(?P<flags>[A-Za-z]*)")

_UNDECLARED_RUNTIME_IMPORT_ERROR_RE = re.compile(
    r"undeclared runtime import ['\"](?P<package>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_UNRESOLVED_RELATIVE_IMPORT_ERROR_RE = re.compile(
    r"unresolved relative import ['\"](?P<specifier>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_UNRESOLVED_IMPORT_SYMBOL_ERROR_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_TS_NO_EXPORTED_MEMBER_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2305:\s*"
    r"Module\s+(?P<module>.+?)\s+has\s+no\s+exported\s+member\s+['\"](?P<symbol>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2724:\s*"
    r"(?P<module>.+?)\s+has\s+no\s+exported\s+member\s+named\s+['\"](?P<symbol>[^'\"]+)['\"]"
    r"(?:\.\s+Did\s+you\s+mean\s+['\"](?P<suggestion>[^'\"]+)['\"])?",
    re.IGNORECASE,
)

_TS_DECLARES_LOCALLY_NOT_EXPORTED_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2459:\s*"
    r"Module\s+(?P<module>.+?)\s+declares\s+['\"](?P<symbol>[^'\"]+)['\"]\s+locally,\s+"
    r"but\s+it\s+is\s+not\s+exported",
    re.IGNORECASE,
)

_TS_MISSING_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2339:\s*"
    r"Property\s+['\"](?P<member>[^'\"]+)['\"]\s+does\s+not\s+exist\s+on\s+type\s+['\"](?P<type>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_TS_OBJECT_MISSING_PROPERTIES_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2739:\s*"
    r"Type\s+.+?\s+is\s+missing\s+the\s+following\s+properties\s+from\s+type\s+['\"](?P<type>[^'\"]+)['\"]:\s*"
    r"(?P<members>[^\n]+)",
    re.IGNORECASE,
)

_TS_OBJECT_MISSING_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2741:\s*"
    r"Property\s+['\"](?P<member>[^'\"]+)['\"]\s+is\s+missing\s+in\s+type\s+.+?\s+but\s+required\s+in\s+type\s+['\"](?P<type>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_TS_UNUSED_DECLARATION_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS6133:\s*"
    r"['\"](?P<name>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+declared\s+but\s+its\s+value\s+is\s+never\s+read\.?",
    re.IGNORECASE,
)

_TS_TOO_FEW_ARGUMENTS_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2554:\s*"
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but\s+got\s+(?P<got>\d+)",
    re.IGNORECASE,
)

_TS_REEXPORTABLE_NAMED_IMPORT_RE = re.compile(
    r"(?ms)^(?P<indent>\s*)import\s+(?P<type_only>type\s+)?\{(?P<names>.*?)\}\s+from\s+"
    r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)\s*;?"
)

_TS_CANNOT_FIND_TEST_GLOBAL_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2304|2582):\s*"
    r"Cannot\s+find\s+name\s+['\"](?P<symbol>describe|it|test|expect|beforeEach|afterEach|beforeAll|afterAll)['\"]",
    re.IGNORECASE,
)

_TS_UNINITIALIZED_PROPERTY_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2564:\s*"
    r"Property\s+['\"](?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+has\s+no\s+initializer",
    re.IGNORECASE,
)

_TS_CANNOT_FIND_NAME_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2304:\s*"
    r"Cannot\s+find\s+name\s+['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE,
)

_TS_SOURCEFILE_DIAGNOSTICS_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2339|2871|7006):\s*"
    r"(?P<message>[^\n]*(?:parseDiagnostics|diagnostics|always\s+nullish|implicitly\s+has\s+an\s+['\"]any['\"]\s+type)[^\n]*)",
    re.IGNORECASE,
)

_TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE = re.compile(
    r"TypeScript escaped newline in line comment before code in (?P<path>\S+)",
    re.IGNORECASE,
)

_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE = re.compile(
    r"(?P<prefix>//[^\r\n]*?)\\n(?P<code>\s*(?:export|import|const|let|var|class|function|interface|type|enum)\b)",
    re.IGNORECASE,
)

_TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE = re.compile(
    r"TypeScript zod inferred type collides with class (?P<name>[A-Za-z_$][\w$]*) in (?P<path>\S+)",
)

_TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE = re.compile(
    r"(?m)^(?P<indent>\s*)(?P<export>export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<infer>z\.infer\s*<\s*typeof\s+[A-Za-z_$][\w$]*\s*>)\s*;\s*$"
)

_TS_NAMED_IMPORT_RE = re.compile(
    r"import\s*\{(?P<symbols>[^}]+)\}\s*from\s*['\"](?P<module>\.{1,2}/[^'\"]+)['\"]",
    re.DOTALL,
)

_TS_NAMED_IMPORT_BLOCK_START_LINE_RE = re.compile(r"^\s*import\s*\{\s*$")

_TS_NAMED_IMPORT_BLOCK_END_LINE_RE = re.compile(r"^\s*}\s*from\s*['\"][^'\"]+['\"]\s*;?\s*$")

_TS_EMBEDDED_IMPORT_TYPE_LINE_RE = re.compile(
    r"^(?P<indent>\s*)import\s+type\s+\{\s*(?P<symbols>[^}]+?)\s*\}\s+from\s+"
    r"(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)\s*;?\s*$"
)

_TS_IMPORT_SPECIFIER_KEYWORD_RE = re.compile(
    r"(?P<prefix>(?:^|,)\s*)(?P<keyword>export|import)\s+type\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)

_TS_NAMED_REEXPORT_RE = re.compile(
    r"export\s+(?:type\s+)?\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<module>[^'\"]+)['\"]\s*;?",
    re.MULTILINE | re.DOTALL,
)

_TS_LOCAL_NAMED_EXPORT_RE = re.compile(
    r"export\s*\{\s*(?P<symbols>[^}]+)\s*\}(?!\s*from\b)\s*;?",
    re.MULTILINE | re.DOTALL,
)

_TS_LOCAL_TYPE_NAMED_EXPORT_RE = re.compile(
    r"export\s+type\s*\{\s*(?P<symbols>[^}]+)\s*\}(?!\s*from\b)\s*;?",
    re.MULTILINE | re.DOTALL,
)

_TS_DUPLICATE_IDENTIFIER_MESSAGE_RE = re.compile(
    r"Duplicate identifier\s+['\"]?(?P<name>[A-Za-z_$][\w$]*)['\"]?",
    re.IGNORECASE,
)

_TS_IMPORT_TYPE_AS_VALUE_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1361:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][\w$]*)['\"]\s+cannot\s+be\s+used\s+as\s+a\s+value\s+"
    r"because\s+it\s+was\s+imported\s+using\s+['\"]import\s+type['\"]",
    re.IGNORECASE,
)

_TS_IMPORT_TYPE_AS_VALUE_MESSAGE_RE = re.compile(
    r"['\"](?P<symbol>[A-Za-z_$][\w$]*)['\"]\s+cannot\s+be\s+used\s+as\s+a\s+value\s+"
    r"because\s+it\s+was\s+imported\s+using\s+['\"]import\s+type['\"]",
    re.IGNORECASE,
)

_TS_EXPORT_AMBIGUITY_MESSAGE_RE = re.compile(
    r"Module\s+['\"](?P<module>[^'\"]+)['\"]\s+has\s+already\s+exported\s+a\s+member\s+named\s+"
    r"['\"](?P<symbol>[A-Za-z_$][\w$]*)['\"]",
    re.IGNORECASE,
)

_TS_EXPORT_STAR_RE = re.compile(
    r"(?m)^(?P<indent>\s*)export\s+\*\s+from\s+(?P<quote>['\"])(?P<module>[^'\"]+)(?P=quote)\s*;?\s*$"
)

_TS_BRANDED_STRING_ASSIGNMENT_MESSAGE_RE = re.compile(
    r"(?:Argument of type ['\"]?string['\"]? is not assignable to parameter of type|"
    r"Type ['\"]?string['\"]? is not assignable to type)\s+['\"]?(?P<type>[A-Za-z_$][\w$]*)['\"]?",
    re.IGNORECASE,
)

_TS_STRING_BRAND_TYPE_ALIAS_RE = re.compile(
    r"(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*string\s*&\s*\{(?P<body>[^}]*)__brand\b[^}]*\}",
    re.DOTALL,
)

_TS_TYPE_ONLY_VALUE_USAGE_MESSAGE_RE = re.compile(
    r"['\"](?P<name>[A-Za-z_$][\w$]*)['\"]\s+only\s+refers\s+to\s+a\s+type,\s+"
    r"but\s+is\s+being\s+used\s+as\s+a\s+value",
    re.IGNORECASE,
)

_TS_STRING_LITERAL_UNION_TYPE_ALIAS_RE = re.compile(
    r"(?P<export>export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<body>[^;]+);",
    re.DOTALL,
)

_TS_VITEST_IMPORT_RE = re.compile(
    r"import\s*\{\s*(?P<symbols>[^}]+)\}\s*from\s*['\"]vitest['\"]\s*;?",
    re.MULTILINE,
)

_TS_TEST_GLOBAL_NAMES = frozenset(
    {"describe", "it", "test", "expect", "beforeEach", "afterEach", "beforeAll", "afterAll"}
)

_TS_RUNTIME_EXPORT_TEMPLATE = r"(?:export\s+)?(?:enum|class|const|let|var|function)\s+{symbol}\b"

_TYPEORM_IMPORT_LINE_RE = re.compile(r"^\s*import\s+[^;\n]*\s+from\s+['\"]typeorm['\"];\s*$")

_TS_DECORATOR_LINE_RE = re.compile(r"^\s*@[A-Za-z_$][\w$]*(?:\(.*\))?\s*$")

_TS_CLASS_FIELD_DECL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z_$][\w$]*)(?P<optional>\?)?\s*:\s*(?P<type>[^;=]+);\s*$"
)

_JS_RUNTIME_FILE_RE = re.compile(r"(?P<path>[^\s'\"()]+\.js):")

_JS_FUNCTION_DECL_RE = re.compile(
    r"(?P<prefix>\b(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][\w$]*\s*)"
    r"\((?P<params>[^)]*)\)\s*(?::\s*[^({=;]+)?(?P<brace>\s*\{)",
    re.MULTILINE,
)

_JS_METHOD_DECL_RE = re.compile(
    r"(?P<prefix>^\s*(?:async\s+)?[A-Za-z_$][\w$]*\s*)"
    r"\((?P<params>[^)]*)\)\s*(?::\s*[^({=;]+)?(?P<brace>\s*\{)",
    re.MULTILINE,
)

_JS_VARIABLE_TYPE_RE = re.compile(
    r"\b(?P<kind>const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*:\s*[^=;\n]+(?P<assign>\s*=)",
    re.MULTILINE,
)

_TS_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\((?P<params>[^)]*)\)[^{]*{"
)

_TS_ARROW_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*"
    r"(?:async\s*)?\((?P<params>[^)]*)\)\s*=>\s*{"
)

_TS_IMPORT_FROM_SPECIFIER_TEMPLATE = (
    r"(?m)^(?P<indent>\s*)import\s+(?P<clause>.*?)\s+from\s+"
    r"(?P<quote>['\"])(?P<specifier>{specifier})(?P=quote)\s*;?\s*$"
)

_TS_IMPORT_FROM_ANY_RE = re.compile(
    r"(?m)^(?P<indent>\s*)import\s+(?P<clause>.*?)\s+from\s+"
    r"(?P<quote>['\"])(?P<specifier>[^'\"]+)(?P=quote)\s*;?\s*$"
)

_TS_UNUSED_LOCAL_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kind>const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=;]+)?=\s*(?P<expr>.+);\s*$"
)

_TS_UNUSED_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<async>async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)

_TS_ZERO_ARG_PROPERTY_CALL_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+\s*\(\s*\)")

_TS_UNRESOLVED_ARRAY_ASSERTION_LENGTH_ASSIGNMENT_TEMPLATE = r"\bas\s+{symbol}\s*\[\s*\]\s*\)\s*\.\s*length\s*="

_TS_LOCAL_DOM_DECLARE_CONST_START_RE = re.compile(
    r"^\s*declare\s+const\s+(?P<name>document|window)\s*:\s*\{",
)

_TS_LOCAL_DOM_INTERFACE_START_RE = re.compile(
    r"^\s*interface\s+"
    r"(?P<name>Document|HTMLElement|HTMLCanvasElement|CanvasRenderingContext2D|HTMLCollection|Element)"
    r"\b[^{]*\{",
)

_TS_LOCAL_DOM_SHIM_NAMES = frozenset(
    {
        "document",
        "window",
        "Document",
        "Element",
        "HTMLElement",
        "HTMLCanvasElement",
        "CanvasRenderingContext2D",
        "HTMLCollection",
    }
)

_TS_STRING_NOT_ASSIGNABLE_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2322:\s*"
    r"Type\s+(?:'\"(?P<literal1>[^'\"]+)\"'|\"'(?P<literal2>[^'\"]+)'\"|"
    r"'(?P<literal3>[^']+)'|\"(?P<literal4>[^\"]+)\")\s+"
    r"is\s+not\s+assignable\s+to\s+type\s+"
    r"(?:'\"(?P<type1>[A-Za-z_$][\w$]+)\"'|\"'(?P<type2>[A-Za-z_$][\w$]+)'\"|"
    r"'(?P<type3>[A-Za-z_$][\w$]+)'|\"(?P<type4>[A-Za-z_$][\w$]+)\")",
    re.IGNORECASE,
)

_TS_STRING_ENUM_DECL_RE_TEMPLATE = (
    r"(?ms)^(?P<indent>[ \t]*)(?P<export>export\s+)?enum\s+{type_name}\s*\{{"
    r"(?P<body>.*?)^\s*\}}\s*;?"
)

_TS_STRING_ENUM_MEMBER_RE = re.compile(
    r"(?m)^\s*(?P<member>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<quote>['\"])(?P<literal>[^'\"]+)(?P=quote)\s*,?\s*(?://.*)?$"
)

_TS_EXCESS_OBJECT_PROPERTY_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2353:\s*"
    r"Object\s+literal\s+may\s+only\s+specify\s+known\s+properties,?\s+and\s+"
    r"['\"](?P<property>[A-Za-z_$][\w$]*)['\"]\s+does\s+not\s+exist\s+in\s+type\s+"
    r"['\"](?P<type_name>[A-Za-z_$][\w$]*)['\"]",
    re.IGNORECASE,
)

_INIT_PROPERTY_ALIASES: dict[str, str] = {
    "fireflies": "fireflyCount",
    "flowers": "flowerCount",
    "humidity": "initialHumidity",
    "moonCycle": "moonCycleSeconds",
    "cycleSeconds": "moonCycleSeconds",
}

_TS_ARG_TYPE_MISMATCH_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument of type ['\"](?P<actual>[A-Za-z_$][\w$]*)['\"] is not assignable to parameter of type "
    r"['\"](?P<expected>[A-Za-z_$][\w$]*)['\"]",
    re.IGNORECASE,
)

_TS_EXPORTED_FUNCTION_PARAM_TYPE_RE = re.compile(
    r"(?m)^(?:export\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(\s*"
    r"(?:readonly\s+)?(?P<param>[A-Za-z_$][\w$]*)\s*:\s*(?P<type>[A-Za-z_$][\w$]*)\b",
)

_PACKAGE_MANIFEST_JSON_KEYS = frozenset(
    {
        "name",
        "version",
        "private",
        "scripts",
        "dependencies",
        "devDependencies",
        "type",
        "main",
        "module",
        "exports",
        "engines",
        "keywords",
        "license",
        "description",
    }
)

_PACKAGE_SCRIPT_TEST_PATH_RE = re.compile(
    r"(?:^|[\s\"'=])((?:\.\/)?tests\/[A-Za-z0-9_./\-]+\.(?:ts|js|mjs|cjs))",
    re.IGNORECASE,
)

_PACKAGE_SCRIPT_TEST_GLOB_RE = re.compile(
    r"tests\/(?:\*\*\/)?(?:\*\.test\.(?:ts|js|mjs|cjs)|\*\.(?:ts|js|mjs|cjs))",
    re.IGNORECASE,
)

_TS_STAR_REEXPORT_RE = re.compile(
    r"\bexport\s+\*\s+(?:as\s+[A-Za-z_$][\w$]*\s+)?from\s+['\"](?P<mod>[^'\"]+)['\"]",
    re.MULTILINE,
)

_TS7010_IMPLICIT_RETURN_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS7010:\s*"
    r"['\"](?P<name>[A-Za-z_$][\w$]*)['\"],\s*which lacks return-type annotation",
    re.IGNORECASE,
)

_TS2322_ASSIGN_TO_NAMED_TYPE_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2322:\s*"
    r"Type\s+.+\s+is\s+not\s+assignable\s+to\s+type\s+['\"](?P<type>[A-Za-z_$][\w$]*)['\"]",
    re.IGNORECASE | re.DOTALL,
)

_TS2339_PUSH_READONLY_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2339:\s*"
    r"Property\s+['\"]push['\"]\s+does\s+not\s+exist\s+on\s+type\s+"
    r"['\"](?:readonly\s+.+|ReadonlyArray<.+>)['\"]",
    re.IGNORECASE,
)

_TS2339_PROP_ON_PRIMITIVE_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2339:\s*"
    r"Property\s+['\"](?P<prop>[A-Za-z_$][\w$]*)['\"]\s+does\s+not\s+exist\s+on\s+type\s+"
    r"['\"](?P<type>number|string|boolean)['\"]",
    re.IGNORECASE,
)

_TS_MISSING_PROPS_FROM_TYPE_RE = re.compile(
    r"(?:is\s+)?missing\s+the\s+following\s+properties\s+from\s+type\s+"
    r"['\"](?P<type>[A-Za-z_$][\w$]*)['\"]\s*:\s*(?P<props>[A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)",
    re.IGNORECASE,
)

_TS_MISSING_PROPS_PRIMARY_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2345|2739|2740):\s*"
    r"(?P<body>[^\n]*(?:\n[ \t]+[^\n]+)*)",
    re.IGNORECASE,
)

_TS_KNOWN_METHOD_STUBS: dict[str, str] = {
    "getCurrentDirectory": "() => ''",
    "getCanonicalFileName": "(fileName: string) => fileName",
    "getNewLine": "() => '\\n'",
    "getDefaultLibFileName": "() => 'lib.d.ts'",
    "useCaseSensitiveFileNames": "() => true",
    "fileExists": "(fileName: string) => false",
    "readFile": "(fileName: string) => undefined",
    "writeFile": "() => undefined",
    "readDirectory": "() => [] as string[]",
    "directoryExists": "(path: string) => false",
    "getDirectories": "(path: string) => [] as string[]",
    "realpath": "(path: string) => path",
}

_TS2552_IDENTIFIER_SUGGESTION_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2552:\s*"
    r"Cannot\s+find\s+name\s+['\"](?P<actual>[A-Za-z_$][\w$]*)['\"]\.\s*"
    r"Did\s+you\s+mean\s+['\"](?P<suggestion>[A-Za-z_$][\w$]*)['\"]\?",
    re.IGNORECASE,
)

_TS2345_ARG_MISSING_PROPS_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"](?P<source>[^'\"]+)['\"]\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+"
    r"['\"](?P<target>\{[^'\"]*\})['\"]\.\s*"
    r"(?:Type\s+['\"][^'\"]+['\"]\s+is\s+missing\s+the\s+following\s+properties\s+from\s+type\s+"
    r"['\"][^'\"]+['\"]\s*:\s*(?P<props>[A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*))?",
    re.IGNORECASE | re.DOTALL,
)

_TS2345_ARG_MISSING_PROPS_LOOSE_RE = re.compile(
    r"Argument\s+of\s+type\s+['\"](?P<source>[^'\"]+)['\"]\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+"
    r"['\"](?P<target>\{[^'\"]*\})['\"]",
    re.IGNORECASE | re.DOTALL,
)

_TS2345_MISSING_PROPS_CLAUSE_RE = re.compile(
    r"missing\s+the\s+following\s+properties\s+from\s+type\s+['\"][^'\"]+['\"]\s*:\s*"
    r"(?P<props>[A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*)",
    re.IGNORECASE,
)

_TS_ANON_OBJECT_PROP_RE = re.compile(
    r"(?P<name>[A-Za-z_$][\w$]*)\s*:\s*(?P<type>number|string|boolean|[^;,}]+)",
    re.IGNORECASE,
)

_PROP_SOURCE_ALIASES: dict[str, tuple[str, ...]] = {
    "glow": ("intensity", "brightness", "alpha", "glow"),
    "phase": ("phase", "angle", "t", "time"),
    "intensity": ("glow", "brightness", "intensity"),
    "x": ("x", "left", "cx"),
    "y": ("y", "top", "cy"),
}

_TS6133_UNUSED_LOCAL_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS6133:\s*"
    r"['\"](?P<name>[A-Za-z_$][\w$]*)['\"]\s+is\s+declared\s+but\s+(?:its\s+value\s+is\s+never\s+read|never\s+used)",
    re.IGNORECASE,
)

__all__ = (
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL",
    "TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL",
    "TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL",
    "TYPESCRIPT_IMPLICIT_RETURN_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_OBJECT_ASSIGN_ASSERTION_SOURCE_TOOL",
    "TYPESCRIPT_READONLY_ARRAY_MUTATION_SOURCE_TOOL",
    "TYPESCRIPT_PARAM_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_TRUNCATED_EOF_SOURCE_TOOL",
    "TYPESCRIPT_OBJECT_LITERAL_MISSING_PROPS_SOURCE_TOOL",
    "TYPESCRIPT_IDENTIFIER_SUGGESTION_SOURCE_TOOL",
    "TYPESCRIPT_ARGUMENT_SHAPE_ADAPTER_SOURCE_TOOL",
    "TYPESCRIPT_UNUSED_LOCAL_SOURCE_TOOL",
    "TYPESCRIPT_DUPLICATE_FUNCTION_SOURCE_TOOL",
    "TYPESCRIPT_JSON_AS_SOURCE_SOURCE_TOOL",
    "TYPESCRIPT_LITERAL_UNION_EXPAND_SOURCE_TOOL",
    "TYPESCRIPT_INIT_PROPERTY_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_ARG_TYPE_FUNCTION_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL",
    "TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL",
    "TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL",
    "HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL",
    "JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL",
    "TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL",
    "TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_STRICT_NULL_RELAXATION_SOURCE_TOOL",
    "TYPESCRIPT_TIMER_HANDLE_SOURCE_TOOL",
    "TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL",
    "TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL",
    "TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL",
    "TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL",
    "TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL",
    "TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL",
    "TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_PRIVATE_PROPERTY_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_NONFINITE_ALTITUDE_GUARD_SOURCE_TOOL",
    "TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORT_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL",
    "TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL",
    "TYPESCRIPT_SCAFFOLD_SOURCE_TOOL",
    "TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL",
    "TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL",
    "TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL",
    "TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL",
    "TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL",
    "TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL",
    "TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL",
    "TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL",
    "TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL",
    "TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL",
    "TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL",
    "TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL",
    "TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_RELATIVE_MODULE_SOURCE_TOOL",
    "TYPESCRIPT_INVALID_MODULE_AUGMENTATION_SOURCE_TOOL",
    "_REMOVED_TYPESCRIPT_COMPILER_OPTIONS",
    "_TS_INLINE_OBJECT_MISSING_COMMA_RE",
    "_TS_OBJECT_PROPERTY_KEY_LINE_RE",
    "_TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE",
    "_TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE",
    "_TS_RETURN_OBJECT_START_RE",
    "_TS_OBJECT_LITERAL_START_RE",
    "_TS_IDENTIFIER_RE",
    "_TS_CONFIG_SPLIT_KEY_LINE_RE",
    "_TS_TEST_BLOCK_RESIDUE_STATEMENT_RE",
    "_TS_TEST_BLOCK_RESIDUE_CLOSER_RE",
    "_TS_CONFIG_JOINABLE_KEYS",
    "_TS_HYPHENATED_VARIABLE_DECLARATION_RE",
    "_TS_POSSIBLY_NULL_RAW_RE",
    "_TS_POSSIBLY_NULL_MESSAGE_RE",
    "_TS_POSSIBLY_UNDEFINED_RAW_RE",
    "_TS_POSSIBLY_UNDEFINED_MESSAGE_RE",
    "_TS_NULLABLE_ARGUMENT_RAW_RE",
    "_TS_DUPLICATE_OBJECT_PROPERTY_RAW_RE",
    "_TS_DUPLICATE_IDENTIFIER_RAW_RE",
    "_TS_CANNOT_FIND_MODULE_RAW_RE",
    "_TS_INVALID_MODULE_AUGMENTATION_RAW_RE",
    "_TS_ENUM_MEMBER_SEPARATOR_RAW_RE",
    "_TS_MISSING_CLOSING_BRACE_RAW_RE",
    "_TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE",
    "_TS_NUMBER_PROPERTY_CALL_RAW_RE",
    "_TS_READONLY_ASSIGNMENT_RAW_RE",
    "_TS_READONLY_INDEX_ASSIGNMENT_RAW_RE",
    "_TS_DUPLICATE_FUNCTION_RAW_RE",
    "_TS_FUNCTION_DECL_RE",
    "_TS_INDEX_ASSIGN_PROP_RE",
    "_TS_STRING_LITERAL_SUGGESTION_RAW_RE",
    "_TS_VALUE_USED_AS_TYPE_RAW_RE",
    "_TS_VALUE_USED_AS_TYPE_MESSAGE_RE",
    "_TS_PRIVATE_CONSTRUCTOR_ACCESS_RAW_RE",
    "_TS_PRIVATE_CONSTRUCTOR_ACCESS_MESSAGE_RE",
    "_TS_PRIVATE_PROPERTY_ACCESS_RAW_RE",
    "_TS_PRIVATE_PROPERTY_ACCESS_MESSAGE_RE",
    "_TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE",
    "_TS_UNKNOWN_MEMBER_ACCESS_RAW_RE",
    "_TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE",
    "_TS_CANVAS_SCALE_RETURN_TYPE_RE",
    "_TS_ENUM_DECLARATION_LINE_RE",
    "_TS_ENUM_MEMBER_LINE_RE",
    "_TS_CANVAS_CONTEXT_DECLARATION_LINE_RE",
    "_TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE",
    "_TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE",
    "_HTML_TS_MODULE_SCRIPT_ERROR_RE",
    "_HTML_COMPILED_JS_MISSING_RE",
    "_HTML_TRUNCATED_ERROR_RE",
    "_HTML_MODULE_SCRIPT_SRC_RE",
    "_HTML_ID_ATTRIBUTE_RE",
    "_TS_EXACT_HTML_ID_TOKEN_REGEX_RE",
    "_UNDECLARED_RUNTIME_IMPORT_ERROR_RE",
    "_UNRESOLVED_RELATIVE_IMPORT_ERROR_RE",
    "_UNRESOLVED_IMPORT_SYMBOL_ERROR_RE",
    "_TS_NO_EXPORTED_MEMBER_ERROR_RE",
    "_TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE",
    "_TS_DECLARES_LOCALLY_NOT_EXPORTED_ERROR_RE",
    "_TS_MISSING_PROPERTY_ERROR_RE",
    "_TS_OBJECT_MISSING_PROPERTIES_ERROR_RE",
    "_TS_OBJECT_MISSING_PROPERTY_ERROR_RE",
    "_TS_UNUSED_DECLARATION_ERROR_RE",
    "_TS_TOO_FEW_ARGUMENTS_RAW_RE",
    "_TS_REEXPORTABLE_NAMED_IMPORT_RE",
    "_TS_CANNOT_FIND_TEST_GLOBAL_RAW_RE",
    "_TS_UNINITIALIZED_PROPERTY_RAW_RE",
    "_TS_CANNOT_FIND_NAME_RAW_RE",
    "_TS_SOURCEFILE_DIAGNOSTICS_RAW_RE",
    "_TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE",
    "_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE",
    "_TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE",
    "_TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE",
    "_TS_NAMED_IMPORT_RE",
    "_TS_NAMED_IMPORT_BLOCK_START_LINE_RE",
    "_TS_NAMED_IMPORT_BLOCK_END_LINE_RE",
    "_TS_EMBEDDED_IMPORT_TYPE_LINE_RE",
    "_TS_IMPORT_SPECIFIER_KEYWORD_RE",
    "_TS_NAMED_REEXPORT_RE",
    "_TS_LOCAL_NAMED_EXPORT_RE",
    "_TS_LOCAL_TYPE_NAMED_EXPORT_RE",
    "_TS_DUPLICATE_IDENTIFIER_MESSAGE_RE",
    "_TS_IMPORT_TYPE_AS_VALUE_RAW_RE",
    "_TS_IMPORT_TYPE_AS_VALUE_MESSAGE_RE",
    "_TS_EXPORT_AMBIGUITY_MESSAGE_RE",
    "_TS_EXPORT_STAR_RE",
    "_TS_BRANDED_STRING_ASSIGNMENT_MESSAGE_RE",
    "_TS_STRING_BRAND_TYPE_ALIAS_RE",
    "_TS_TYPE_ONLY_VALUE_USAGE_MESSAGE_RE",
    "_TS_STRING_LITERAL_UNION_TYPE_ALIAS_RE",
    "_TS_VITEST_IMPORT_RE",
    "_TS_TEST_GLOBAL_NAMES",
    "_TS_RUNTIME_EXPORT_TEMPLATE",
    "_TYPEORM_IMPORT_LINE_RE",
    "_TS_DECORATOR_LINE_RE",
    "_TS_CLASS_FIELD_DECL_RE",
    "_JS_RUNTIME_FILE_RE",
    "_JS_FUNCTION_DECL_RE",
    "_JS_METHOD_DECL_RE",
    "_JS_VARIABLE_TYPE_RE",
    "_TS_FUNCTION_DECLARATION_LINE_RE",
    "_TS_ARROW_FUNCTION_DECLARATION_LINE_RE",
    "_TS_IMPORT_FROM_SPECIFIER_TEMPLATE",
    "_TS_IMPORT_FROM_ANY_RE",
    "_TS_UNUSED_LOCAL_DECLARATION_LINE_RE",
    "_TS_UNUSED_FUNCTION_DECLARATION_LINE_RE",
    "_TS_ZERO_ARG_PROPERTY_CALL_RE",
    "_TS_UNRESOLVED_ARRAY_ASSERTION_LENGTH_ASSIGNMENT_TEMPLATE",
    "_TS_LOCAL_DOM_DECLARE_CONST_START_RE",
    "_TS_LOCAL_DOM_INTERFACE_START_RE",
    "_TS_LOCAL_DOM_SHIM_NAMES",
    "_TS_STRING_NOT_ASSIGNABLE_RE",
    "_TS_STRING_ENUM_DECL_RE_TEMPLATE",
    "_TS_STRING_ENUM_MEMBER_RE",
    "_TS_EXCESS_OBJECT_PROPERTY_RE",
    "_INIT_PROPERTY_ALIASES",
    "_TS_ARG_TYPE_MISMATCH_RE",
    "_TS_EXPORTED_FUNCTION_PARAM_TYPE_RE",
    "_PACKAGE_MANIFEST_JSON_KEYS",
    "_PACKAGE_SCRIPT_TEST_PATH_RE",
    "_PACKAGE_SCRIPT_TEST_GLOB_RE",
    "_TS_STAR_REEXPORT_RE",
    "_TS7010_IMPLICIT_RETURN_RE",
    "_TS2322_ASSIGN_TO_NAMED_TYPE_RE",
    "_TS2339_PUSH_READONLY_RE",
    "_TS2339_PROP_ON_PRIMITIVE_RE",
    "_TS_MISSING_PROPS_FROM_TYPE_RE",
    "_TS_MISSING_PROPS_PRIMARY_RE",
    "_TS_KNOWN_METHOD_STUBS",
    "_TS2552_IDENTIFIER_SUGGESTION_RE",
    "_TS2345_ARG_MISSING_PROPS_RE",
    "_TS2345_ARG_MISSING_PROPS_LOOSE_RE",
    "_TS2345_MISSING_PROPS_CLAUSE_RE",
    "_TS_ANON_OBJECT_PROP_RE",
    "_PROP_SOURCE_ALIASES",
    "_TS6133_UNUSED_LOCAL_RE",
)
