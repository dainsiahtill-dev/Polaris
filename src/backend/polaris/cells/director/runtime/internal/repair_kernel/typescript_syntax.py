"""Canonical TypeScript syntax repair rules for Director Runtime."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from typing import Any

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from .javascript_syntax import repair_javascript_export_contract_placeholders

TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"
TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL = "deterministic_typescript_nullable_canvas_context_repair"
TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL = "deterministic_typescript_duplicate_object_property_repair"
TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL = "deterministic_typescript_enum_member_separator_repair"
TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL = "deterministic_typescript_missing_closing_brace_repair"
TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL = "deterministic_typescript_number_to_string_argument_repair"
TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL = "deterministic_typescript_number_property_call_repair"
TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL = "deterministic_typescript_readonly_assignment_repair"
TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL = "deterministic_typescript_shorthand_property_scope_repair"
TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL = "deterministic_typescript_string_literal_suggestion_repair"
TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL = "deterministic_typescript_unknown_member_access_repair"
TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL = "deterministic_typescript_canvas_scale_return_type_repair"
HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL = "deterministic_html_typescript_module_script_repair"
JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL = "deterministic_javascript_typescript_annotation_repair"
TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL = "deterministic_typeorm_model_normalization_repair"
TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL = "deterministic_typescript_commonjs_package_type_repair"
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


def repair_typescript_object_literal_commas(text: str) -> str:
    """Repair narrow object-literal comma omissions reported as TS1005."""

    lines = str(text or "").splitlines(keepends=True)
    repaired: list[str] = []
    object_literal_depths: list[int] = []
    brace_depth = 0
    changed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        starts_object_literal = bool(
            _TS_RETURN_OBJECT_START_RE.search(line_body) or _TS_OBJECT_LITERAL_START_RE.search(line_body)
        )
        has_inline_missing_object_comma = bool(
            "{" in line_body and ":" in line_body and _TS_INLINE_OBJECT_MISSING_COMMA_RE.search(line_body)
        )
        in_object_literal = bool(object_literal_depths) or starts_object_literal or has_inline_missing_object_comma
        if in_object_literal:
            repaired_line = _repair_object_property_semicolon_line(line_body)
            if repaired_line == line_body:
                repaired_line = _repair_missing_object_property_comma_line(line_body)
            if repaired_line != line_body:
                repaired.append(f"{repaired_line}{newline}")
                changed = True
            else:
                if _object_property_line_needs_previous_comma(line_body, repaired):
                    repaired[-1] = _append_object_property_comma(repaired[-1])
                    changed = True
                repaired.append(line)
        else:
            repaired.append(line)

        opens = line_body.count("{")
        closes = line_body.count("}")
        if starts_object_literal:
            object_literal_depths.append(brace_depth + max(opens, 1))
        brace_depth += opens - closes
        while object_literal_depths and brace_depth < object_literal_depths[-1]:
            object_literal_depths.pop()
    return "".join(repaired) if changed else str(text or "")


def _typescript_camel_case_hyphenated_identifier(left: str, right: str) -> str:
    if not left or not right:
        return ""
    return f"{left}{right[0].upper()}{right[1:]}"


def _repair_typescript_hyphenated_identifiers(
    *,
    original: str,
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    lines = str(original or "").splitlines(keepends=True)
    replacements: dict[str, str] = {}
    diagnostic_ids: list[str] = []
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        line_number = _typescript_diagnostic_line(diagnostic)
        if not line_number or line_number < 1 or line_number > len(lines):
            continue
        line = lines[line_number - 1].rstrip("\r\n")
        match = _TS_HYPHENATED_VARIABLE_DECLARATION_RE.search(line)
        if not match:
            continue
        old_name = f"{match.group('left')}-{match.group('right')}"
        new_name = _typescript_camel_case_hyphenated_identifier(match.group("left"), match.group("right"))
        if not old_name or not new_name or old_name == new_name:
            continue
        replacements[old_name] = new_name
        diagnostic_ids.append(diagnostic.diagnostic_id)

    repaired = str(original or "")
    for old_name, new_name in sorted(replacements.items(), key=lambda item: (-len(item[0]), item[0])):
        token_re = re.compile(rf"(?<![A-Za-z0-9_$]){re.escape(old_name)}(?![A-Za-z0-9_$])")
        repaired = token_re.sub(new_name, repaired)
    return repaired, replacements, tuple(diagnostic_ids)


def _typescript_diagnostic_line(diagnostic: RepairDiagnostic) -> int | None:
    if diagnostic.line:
        return diagnostic.line
    raw = str(diagnostic.raw or diagnostic.message or "")
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not raw or not path:
        return None
    match = re.search(rf"{re.escape(path)}\((?P<line>\d+),(?P<col>\d+)\)", raw)
    if not match:
        return None
    try:
        return int(match.group("line"))
    except (TypeError, ValueError):
        return None


def build_typescript_hyphenated_identifier_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS1005 repair plan for illegal hyphenated variable identifiers."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path or path not in normalized_base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path in sorted(diagnostics_by_path):
        original = str(normalized_base_files.get(path) or "")
        repaired, replacements, diagnostic_ids = _repair_typescript_hyphenated_identifiers(
            original=original,
            diagnostics=diagnostics_by_path[path],
        )
        if repaired == original or not replacements:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "diagnostic_ids": diagnostic_ids,
                    "repair_kind": "typescript_hyphenated_identifier",
                    "replacements": dict(replacements),
                },
            )
        )
        matched_diagnostics.extend(diagnostics_by_path[path])

    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.hyphenated_identifier",
        source_tool=TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"runtime_plan_scope": "same_file_hyphenated_variable_identifier"},
    )


def build_typescript_object_literal_comma_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1005 object literal comma omissions."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_comma_expected_diagnostic(diagnostic):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path or path not in normalized_base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path in sorted(diagnostics_by_path):
        original = str(normalized_base_files.get(path) or "")
        repaired = repair_typescript_object_literal_commas(original)
        if repaired == original:
            continue
        path_diagnostics = diagnostics_by_path[path]
        matched_diagnostics.extend(path_diagnostics)
        first_diagnostic = path_diagnostics[0]
        operations.append(
            RepairOperation(
                kind="write_file",
                path=path,
                content=repaired,
                before_hash=sha256_text(original),
                metadata={
                    "line": first_diagnostic.line,
                    "column": first_diagnostic.column,
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in path_diagnostics],
                    "repair_kind": "typescript_object_literal_missing_comma",
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.object_literal_missing_comma",
        source_tool=TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
    )


def repair_typescript_nullable_canvas_context_guards(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    """Repair nullable DOM/canvas handles by narrowing or adding explicit guards."""

    text, multiline_guarded = _repair_typescript_multiline_dom_handle_declarations(text, symbols)
    lines = text.splitlines()
    repaired_lines: list[str] = []
    guarded: list[str] = list(multiline_guarded)
    for index, line in enumerate(lines):
        global_symbol = _typescript_nullable_global_symbol_for_line(line, symbols)
        if global_symbol and not _typescript_global_guard_precedes(repaired_lines, global_symbol):
            indent_match = re.match(r"^\s*", line)
            indent = indent_match.group(0) if indent_match else ""
            repaired_lines.append(f'{indent}if (typeof {global_symbol} === "undefined") {{')
            repaired_lines.append(f'{indent}  throw new Error("{global_symbol} is unavailable");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(global_symbol)
        match = _TS_CANVAS_CONTEXT_DECLARATION_LINE_RE.match(line)
        if match:
            symbol = str(match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            repaired_line = _typescript_canvas_context_non_null_assertion_line(line)
            line_changed = repaired_line != line
            repaired_lines.append(repaired_line)
            if _typescript_nullable_guard_follows(lines, index, symbol):
                if line_changed:
                    guarded.append(symbol)
                continue
            indent = str(match.group("indent") or "")
            repaired_lines.append(f"{indent}if (!{symbol}) {{")
            repaired_lines.append(f'{indent}  throw new Error("Canvas 2D context unavailable");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(symbol)
            continue
        dom_match = _TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE.match(line)
        if dom_match:
            symbol = str(dom_match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            repaired_line = _typescript_dom_handle_non_null_assertion_line(line)
            line_changed = repaired_line != line
            repaired_lines.append(repaired_line)
            if _typescript_nullable_guard_follows(lines, index, symbol):
                if line_changed:
                    guarded.append(symbol)
                continue
            indent = str(dom_match.group("indent") or "")
            repaired_lines.append(f"{indent}if (!{symbol}) {{")
            repaired_lines.append(f'{indent}  throw new Error("DOM element unavailable: {symbol}");')
            repaired_lines.append(f"{indent}}}")
            guarded.append(symbol)
            continue
        func_match = _TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE.match(line)
        if func_match:
            symbol = str(func_match.group("symbol") or "").strip()
            if symbols and symbol not in symbols:
                repaired_lines.append(line)
                continue
            rhs = str(func_match.group("rhs") or "")
            if "!" in rhs:
                repaired_lines.append(line)
                continue
            repaired_line = line.rstrip().rstrip(";")
            repaired_line = re.sub(r"\)\s*$", ")!", repaired_line)
            if line.rstrip().endswith(";"):
                repaired_line += ";"
            repaired_lines.append(repaired_line)
            guarded.append(symbol)
            continue
        repaired_line, asserted_symbols = _typescript_nullable_property_chain_non_null_assertion_line(line, symbols)
        if asserted_symbols:
            repaired_lines.append(repaired_line)
            guarded.extend(asserted_symbols)
            continue
        repaired_lines.append(line)
    if not guarded:
        return text, []
    return "\n".join(repaired_lines) + ("\n" if text.endswith("\n") else ""), _dedupe_preserve_order(guarded)


def _typescript_nullable_property_chain_non_null_assertion_line(
    line: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    if not symbols:
        return line, []
    repaired = str(line or "")
    asserted: list[str] = []
    for symbol in sorted(symbols, key=len, reverse=True):
        if "." not in symbol or not _typescript_nullable_target_is_safe(symbol):
            continue
        pattern = re.compile(rf"(?<![\w$]){re.escape(symbol)}(?!\s*!)(?=\s*[.\[])")
        next_repaired = pattern.sub(f"{symbol}!", repaired)
        if next_repaired != repaired:
            repaired = next_repaired
            asserted.append(symbol)
    return repaired, asserted


def _typescript_nullable_target_is_safe(symbol: str) -> bool:
    parts = str(symbol or "").split(".")
    return bool(parts) and all(_TS_IDENTIFIER_RE.fullmatch(part) for part in parts)


def _typescript_nullable_global_symbol_for_line(line: str, symbols: set[str]) -> str:
    for symbol in ("window", "document"):
        if symbols and symbol not in symbols:
            continue
        if f"typeof {symbol}" in line:
            continue
        if re.search(rf"\b{re.escape(symbol)}\s*(?:\.|\[)", line):
            return symbol
    return ""


def _typescript_global_guard_precedes(repaired_lines: Sequence[str], symbol: str) -> bool:
    guard_fragments = (
        f'typeof {symbol} === "undefined"',
        f"typeof {symbol} === 'undefined'",
        f'typeof {symbol} !== "undefined"',
        f"typeof {symbol} !== 'undefined'",
        f"if (!{symbol})",
    )
    for previous in reversed(repaired_lines):
        stripped = previous.strip()
        if re.match(r"(?:export\s+)?(?:async\s+)?function\b", stripped):
            return False
        if any(fragment in previous for fragment in guard_fragments):
            return True
    return False


def build_typescript_nullable_canvas_context_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for nullable DOM/canvas TypeScript handles."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_nullable_canvas_context_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_symbols: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        symbols = targets_by_path[path] or set()
        repaired, guarded_symbols = repair_typescript_nullable_canvas_context_guards(original, symbols)
        if repaired == original or not guarded_symbols:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        repaired_symbols.extend({"file": path, "symbol": symbol} for symbol in guarded_symbols)
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_nullable_canvas_context_guard",
                    "guarded_symbols": list(guarded_symbols),
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in path_diagnostics],
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.nullable_canvas_context",
        source_tool=TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"guards": repaired_symbols},
    )


def build_typescript_duplicate_object_property_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1117 duplicate object property lines."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    line_numbers_by_path = _parse_duplicate_object_property_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    removed_lines: list[dict[str, str]] = []
    for path in sorted(line_numbers_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _duplicate_object_property_delete_operations(
            path=path,
            content=original,
            line_numbers=line_numbers_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        removed_lines.extend(
            {"file": path, "line": str(operation.metadata.get("line") or "")} for operation in path_operations
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.duplicate_object_property",
        source_tool=TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"duplicates": removed_lines},
    )


def build_typescript_enum_member_separator_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1357 enum member separators."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    line_numbers_by_path = _parse_enum_member_separator_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_members: list[dict[str, str]] = []
    for path in sorted(line_numbers_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _enum_member_separator_operations(
            path=path,
            content=original,
            line_numbers=line_numbers_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(
            diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) == path
        )
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_members.extend(
            {
                "file": path,
                "line": str(operation.metadata.get("line") or ""),
                "col": str(operation.metadata.get("column") or ""),
            }
            for operation in path_operations
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.enum_member_separator",
        source_tool=TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"enum_members": repaired_members},
    )


def repair_typescript_missing_closing_braces(text: str) -> str:
    """Repair narrow TypeScript TS1005 missing closing-brace diagnostics."""

    missing_count = _typescript_brace_balance_delta(str(text or ""))
    if missing_count <= 0 or missing_count > 8:
        return str(text or "")
    repaired = str(text or "").rstrip() + "\n"
    repaired += "\n".join("}" for _ in range(missing_count))
    return repaired + "\n"


def build_typescript_missing_closing_brace_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS1005 missing closing braces."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_missing_closing_brace_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        operation = _missing_closing_brace_operation(path=path, content=original)
        if operation is None:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.append(operation)
        repaired_items.append(
            {
                "file": path,
                "missing_count": str(operation.metadata.get("missing_count") or ""),
            }
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.missing_closing_brace",
        source_tool=TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"syntax_errors": repaired_items},
    )


def build_typescript_number_to_string_argument_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for TS2345 number-to-string arguments."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_number_to_string_argument_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _number_to_string_argument_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "line": str(operation.metadata.get("line") or ""),
                "columns": ",".join(str(column) for column in operation.metadata.get("columns") or ()),
            }
            for operation in path_operations
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="typescript.number_to_string_argument",
        source_tool=TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"arguments": repaired_items},
    )


def build_typescript_readonly_assignment_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS2540 repair plan for same-file readonly declarations."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_readonly_assignment_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _readonly_assignment_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "property": str(operation.metadata.get("property") or ""),
                "diagnostic_lines": tuple(operation.metadata.get("diagnostic_lines") or ()),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.readonly_assignment",
        source_tool=TYPESCRIPT_READONLY_ASSIGNMENT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"readonly_properties": repaired_items},
    )


def build_typescript_string_literal_suggestion_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS2820 string-literal suggestion repair plan."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_string_literal_suggestion_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _string_literal_suggestion_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "actual": str(operation.metadata.get("actual") or ""),
                "suggestion": str(operation.metadata.get("suggestion") or ""),
                "line": int(operation.metadata.get("line") or 0),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.string_literal_suggestion",
        source_tool=TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"string_literal_suggestions": repaired_items},
    )


def build_typescript_number_property_call_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS2349 repair plan for generated numeric property calls."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_number_property_call_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _number_property_call_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "line": int(operation.metadata.get("line") or 0),
                "call_expression": str(operation.metadata.get("call_expression") or ""),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.number_property_call",
        source_tool=TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"number_property_calls": repaired_items},
    )


def build_typescript_shorthand_property_scope_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS18004 repair plan for missing object-literal shorthand values."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets_by_path = _parse_shorthand_property_scope_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    for path in sorted(targets_by_path):
        if path not in normalized_base_files:
            continue
        original = str(normalized_base_files.get(path) or "")
        path_operations = _shorthand_property_scope_operations(
            path=path,
            content=original,
            targets=targets_by_path[path],
        )
        if not path_operations:
            continue
        path_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, path))
        matched_diagnostics.extend(path_diagnostics)
        operations.extend(path_operations)
        repaired_items.extend(
            {
                "file": path,
                "line": int(operation.metadata.get("line") or 0),
                "property": str(operation.metadata.get("property") or ""),
            }
            for operation in path_operations
        )
    return _repair_plan_or_none(
        rule_id="typescript.shorthand_property_scope",
        source_tool=TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"shorthand_properties": repaired_items},
    )


def build_typescript_unknown_member_access_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a narrow TS18046 repair plan for unknown typed member access."""

    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    targets = _parse_unknown_member_access_targets(diagnostics)
    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, str]] = []
    for target in targets:
        usage_path = str(target.get("file") or "")
        usage_text = str(normalized_base_files.get(usage_path) or "")
        line_number = _to_positive_int(target.get("line"))
        receiver = str(target.get("receiver") or "")
        member = str(target.get("member") or "")
        if not usage_text or not _TS_IDENTIFIER_RE.fullmatch(receiver) or not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_line = _typescript_line_at(usage_text, line_number)
        replacement_type = _typescript_usage_compatible_member_type(usage_line, member)
        if not replacement_type:
            continue
        type_name = _typescript_unknown_member_receiver_type(
            base_files=normalized_base_files,
            usage_path=usage_path,
            receiver=receiver,
        )
        if not type_name:
            continue
        operation = _typescript_unknown_member_type_operation(
            base_files=normalized_base_files,
            type_name=type_name,
            member=member,
            replacement_type=replacement_type,
        )
        if operation is None:
            continue
        operations.append(operation)
        matched_diagnostics.extend(
            diagnostic for diagnostic in diagnostics if _diagnostic_targets_path(diagnostic, usage_path)
        )
        repaired_items.append(
            {
                "file": operation.path,
                "type": type_name,
                "member": member,
                "replacement_type": replacement_type,
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.unknown_member_access",
        source_tool=TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"unknown_member_accesses": repaired_items},
    )


def build_typescript_canvas_scale_return_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Build a canonical repair plan for scaleToCanvas return type drift."""

    if not _has_number_to_function_argument_diagnostic(diagnostics):
        return None
    normalized_base_files = {
        _normalize_repair_path(path): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(path)
    }
    operations: list[RepairOperation] = []
    repaired_items: list[dict[str, str]] = []
    for path in sorted(normalized_base_files):
        if path.endswith(".d.ts") or not path.endswith((".ts", ".tsx")):
            continue
        original = str(normalized_base_files.get(path) or "")
        operation = _canvas_scale_return_type_operation(path=path, content=original)
        if operation is None:
            continue
        operations.append(operation)
        repaired_items.append({"file": path, "kind": "scaleToCanvas"})
    if not operations:
        return None
    matched_diagnostics = tuple(diagnostic for diagnostic in diagnostics if _is_number_to_function_argument(diagnostic))
    return RepairPlan(
        rule_id="typescript.canvas_scale_return_type",
        source_tool=TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"return_types": repaired_items},
    )


def _build_typescript_config_key_split_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Repair generated TypeScript config keys split by whitespace."""

    if not any(_is_typescript_config_key_split_diagnostic(diagnostic) for diagnostic in diagnostics):
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    seen_spans: set[tuple[str, int, int]] = set()
    target_paths = {
        _normalize_repair_path(str(diagnostic.path or ""))
        for diagnostic in diagnostics
        if _is_typescript_config_key_split_diagnostic(diagnostic)
    }
    target_paths = {path for path in target_paths if path in base_files}
    candidate_paths = target_paths or {path for path in base_files if _is_typescript_config_file(path)}

    for path in sorted(candidate_paths):
        if not _is_typescript_config_file(path):
            continue
        original = str(base_files.get(path) or "")
        if not original:
            continue
        diagnostic_line_numbers = {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if _is_typescript_config_key_split_diagnostic(diagnostic)
            and _normalize_repair_path(str(diagnostic.path or "")) == path
            and int(diagnostic.line or 0) > 0
        }
        path_operations = _typescript_config_key_split_operations(
            path=path,
            content=original,
            line_numbers=diagnostic_line_numbers,
            seen_spans=seen_spans,
        )
        if not path_operations:
            continue
        operations.extend(path_operations)
        matched_diagnostics.extend(
            diagnostic
            for diagnostic in diagnostics
            if _is_typescript_config_key_split_diagnostic(diagnostic)
            and (not diagnostic.path or _normalize_repair_path(str(diagnostic.path or "")) == path)
        )
        repaired_items.extend(
            {
                "file": path,
                "line": int(operation.metadata.get("line") or 0),
                "original_key": str(operation.metadata.get("original_key") or ""),
                "replacement_key": str(operation.metadata.get("replacement_key") or ""),
            }
            for operation in path_operations
        )

    return _repair_plan_or_none(
        rule_id="typescript.config_key_split",
        source_tool=TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"config_key_splits": repaired_items},
    )


def _build_typescript_test_block_residue_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    """Remove trailing duplicated test-block residue that leaves TS1128 closers."""

    if not any(_is_typescript_test_block_residue_diagnostic(diagnostic) for diagnostic in diagnostics):
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired_items: list[dict[str, object]] = []
    seen_spans: set[tuple[str, int, int]] = set()
    target_paths = {
        _normalize_repair_path(str(diagnostic.path or ""))
        for diagnostic in diagnostics
        if _is_typescript_test_block_residue_diagnostic(diagnostic)
    }
    target_paths = {path for path in target_paths if path in base_files}
    candidate_paths = target_paths or {path for path in base_files if _is_typescript_test_file(path)}

    for path in sorted(candidate_paths):
        if not _is_typescript_test_file(path):
            continue
        original = str(base_files.get(path) or "")
        if not original:
            continue
        diagnostic_line_numbers = {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if _is_typescript_test_block_residue_diagnostic(diagnostic)
            and _normalize_repair_path(str(diagnostic.path or "")) == path
            and int(diagnostic.line or 0) > 0
        }
        path_operations = _typescript_test_block_residue_operations(
            path=path,
            content=original,
            line_numbers=diagnostic_line_numbers,
            seen_spans=seen_spans,
        )
        if not path_operations:
            continue
        operations.extend(path_operations)
        matched_diagnostics.extend(
            diagnostic
            for diagnostic in diagnostics
            if _is_typescript_test_block_residue_diagnostic(diagnostic)
            and (not diagnostic.path or _normalize_repair_path(str(diagnostic.path or "")) == path)
        )
        repaired_items.extend(
            {
                "file": path,
                "start_line": int(operation.metadata.get("start_line") or 0),
                "end_line": int(operation.metadata.get("end_line") or 0),
            }
            for operation in path_operations
        )

    return _repair_plan_or_none(
        rule_id="typescript.test_block_residue",
        source_tool=TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"test_block_residue_removals": repaired_items},
    )


def _build_typescript_expect_error_placement_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    moved_comments: list[dict[str, int | str]] = []
    diagnostics_by_path = _typescript_expect_error_diagnostics_by_path(diagnostics)
    for path in sorted(diagnostics_by_path):
        original = str(base_files.get(path) or "")
        if not original:
            continue
        repaired, moved = _repair_typescript_expect_error_placement(
            original,
            diagnostics=diagnostics_by_path[path],
        )
        if repaired == original or not moved:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_expect_error_placement",
                    "moved_expect_error_comments": tuple(moved),
                },
            )
        )
        moved_comments.extend({"file": path, **item} for item in moved)

    matched_diagnostics = tuple(
        diagnostic
        for path_diagnostics in diagnostics_by_path.values()
        for diagnostic in path_diagnostics
        if _is_typescript_expect_error_placement_diagnostic(diagnostic)
    )
    return _repair_plan_or_none(
        rule_id="typescript.expect_error_placement",
        source_tool=TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"moved_comments": moved_comments},
    )


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
        TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL: _build_typescript_config_key_split_plan,
        TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL: _build_typescript_entrypoint_plan,
        TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL: _build_typescript_escaped_newline_plan,
        TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL: _build_typescript_expect_error_placement_plan,
        TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL: build_typescript_hyphenated_identifier_plan,
        TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL: _build_typescript_dom_local_shim_cleanup_plan,
        TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL: _build_typescript_html_container_selector_plan,
        TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL: _build_typescript_import_specifier_keyword_plan,
        TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL: _build_typescript_member_alias_plan,
        TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL: _build_typescript_missing_export_plan,
        TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL: _build_typescript_missing_member_plan,
        TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL: _build_typescript_private_constructor_access_plan,
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
        TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL: _build_typescript_too_few_arguments_plan,
        TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL: _build_typescript_tsconfig_lib_plan,
        TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL: _build_typescript_tsconfig_rootdir_plan,
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


def _build_html_typescript_module_script_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired: list[dict[str, str]] = []
    for item in _parse_html_typescript_module_script_errors(diagnostics):
        path = item["file"]
        source_ref = item["source"]
        original = str(base_files.get(path) or "")
        replacement = item.get("replacement") or _html_javascript_entrypoint_for_typescript_source(source_ref)
        if not replacement:
            replacement = _html_compiled_javascript_entrypoint_for_script(source_ref, base_files=base_files)
        if not original or not replacement:
            continue
        for quote in ('"', "'"):
            expected = f"src={quote}{source_ref}{quote}"
            start = original.find(expected)
            if start < 0:
                continue
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=start,
                    span_end=start + len(expected),
                    expected=expected,
                    replacement=f"src={quote}{replacement}{quote}",
                    before_hash=sha256_text(original),
                    metadata={
                        "repair_kind": "html_typescript_module_script",
                        "source": source_ref,
                        "replacement": replacement,
                    },
                )
            )
            repaired.append({"file": path, "source": source_ref, "replacement": replacement})
            break
    return _repair_plan_or_none(
        rule_id="html.typescript_module_script",
        source_tool=HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"scripts": repaired},
    )


def _build_typescript_html_container_selector_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if diagnostic.code == "html_container_contract_failed"
    )
    if not matched_diagnostics:
        return None

    html_ids = _html_container_ids(base_files)
    if not html_ids:
        return None

    operations: list[RepairOperation] = []
    repaired: list[dict[str, object]] = []
    for path, original in sorted(base_files.items()):
        if not path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
            continue
        text = str(original or "")
        for match in _TS_EXACT_HTML_ID_TOKEN_REGEX_RE.finditer(text):
            token_group = str(match.group("tokens") or "")
            tokens = _html_container_selector_tokens(token_group)
            if not tokens or not _html_ids_support_container_tokens(html_ids, tokens):
                continue
            flags = str(match.group("flags") or "")
            expected = str(match.group(0) or "")
            replacement = f"/id=[\"'][^\"']*({token_group})[^\"']*[\"']/{flags}"
            if expected == replacement:
                continue
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start(),
                    span_end=match.end(),
                    expected=expected,
                    replacement=replacement,
                    before_hash=sha256_text(text),
                    metadata={
                        "repair_kind": "typescript_html_container_selector",
                        "selector_tokens": tuple(tokens),
                        "html_ids": tuple(sorted(html_ids)),
                    },
                )
            )
            repaired.append({"file": path, "tokens": tuple(tokens), "html_ids": tuple(sorted(html_ids))})
            break

    return _repair_plan_or_none(
        rule_id="typescript.html_container_selector",
        source_tool=TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"selectors": repaired},
    )


def _build_typescript_dom_local_shim_cleanup_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    diagnostics_by_path: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_dom_local_shim_diagnostic(diagnostic, base_files=base_files):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path or path not in base_files:
            continue
        diagnostics_by_path.setdefault(path, []).append(diagnostic)

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    cleaned_files: list[dict[str, object]] = []
    for path in sorted(diagnostics_by_path):
        original = str(base_files.get(path) or "")
        repaired, removed_symbols = _remove_typescript_local_dom_shims(original)
        if repaired == original or not removed_symbols:
            continue
        path_diagnostics = tuple(diagnostics_by_path[path])
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_dom_local_shim_cleanup",
                    "removed_symbols": tuple(removed_symbols),
                    "diagnostic_ids": tuple(diagnostic.diagnostic_id for diagnostic in path_diagnostics),
                },
            )
        )
        matched_diagnostics.extend(path_diagnostics)
        cleaned_files.append({"file": path, "removed_symbols": tuple(removed_symbols)})

    return _repair_plan_or_none(
        rule_id="typescript.dom_local_shim_cleanup",
        source_tool=TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        metadata={"cleaned_files": cleaned_files},
    )


def _build_javascript_typescript_annotation_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    if not any(
        _looks_like_javascript_typescript_annotation_error(diagnostic.raw or diagnostic.message)
        for diagnostic in diagnostics
    ):
        return None
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _javascript_annotation_candidate_paths(base_files, diagnostics):
        original = str(base_files.get(path) or "")
        repaired = _strip_typescript_annotations_from_javascript(original)
        repaired = repair_javascript_export_contract_placeholders(
            path=path,
            text=repaired,
            base_files={**base_files, path: repaired},
        )
        if repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "javascript_typescript_annotation_cleanup"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.javascript_annotation_cleanup",
        source_tool=JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )


def _build_typeorm_model_normalization_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _parse_undeclared_runtime_import_paths(diagnostics, package_name="typeorm"):
        original = str(base_files.get(path) or "")
        if not original:
            continue
        repaired = _normalize_undeclared_typeorm_model_source(original)
        if repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typeorm_model_normalization"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.typeorm_model_normalization",
        source_tool=TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )


def _build_typescript_commonjs_package_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    package_text = str(base_files.get("package.json") or "")
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not package_text or not tsconfig_text:
        return None
    if not _typescript_commonjs_package_type_signal(diagnostics):
        return None
    package_payload = _json_object(package_text)
    tsconfig_payload = _json_object(tsconfig_text)
    compiler_options = tsconfig_payload.get("compilerOptions")
    module_value = str(compiler_options.get("module") if isinstance(compiler_options, Mapping) else "").lower()
    if str(package_payload.get("type") or "").lower() != "module" or "commonjs" not in module_value:
        return None
    operation = RepairOperation(
        kind="json_set",
        path="package.json",
        json_path=("type",),
        value="commonjs",
        before_hash=sha256_text(package_text),
        metadata={"repair_kind": "typescript_commonjs_package_type"},
    )
    return _repair_plan_or_none(
        rule_id="typescript.commonjs_package_type",
        source_tool=TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL,
        operations=[operation],
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"package_type": "commonjs"},
    )


def _build_typescript_entrypoint_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    if not _typescript_entrypoint_signal(diagnostics):
        return None
    package_text = str(base_files.get("package.json") or "")
    if not package_text:
        return None
    package_payload = _json_object(package_text)
    compiled_entrypoint = _detect_typescript_entrypoint_from_package(package_payload)
    source_entrypoint = _typescript_source_entrypoint_for_compiled_path(compiled_entrypoint)
    if not source_entrypoint or source_entrypoint in base_files:
        return None
    modules = [
        path
        for path in sorted(base_files)
        if path.startswith("src/")
        and path.endswith(".ts")
        and path != source_entrypoint
        and not path.endswith((".d.ts", ".test.ts", ".spec.ts"))
    ]
    content = _build_typescript_entrypoint_aggregator(
        modules=modules,
        entrypoint_dir=posixpath.dirname(source_entrypoint),
    )
    operation = RepairOperation(
        kind="write_file",
        path=source_entrypoint,
        content=content,
        before_hash=sha256_text(""),
        metadata={
            "repair_kind": "typescript_entrypoint_aggregator",
            "compiled_entrypoint": compiled_entrypoint,
            "modules": tuple(modules),
            "write_file_reason": "new_typescript_entrypoint_aggregator",
        },
    )
    return _repair_plan_or_none(
        rule_id="typescript.entrypoint",
        source_tool=TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL,
        operations=[operation],
        diagnostics=diagnostics,
        mode=mode,
        metadata={"compiled_entrypoint": compiled_entrypoint, "source_entrypoint": source_entrypoint},
    )


def _build_typescript_escaped_newline_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _parse_typescript_escaped_newline_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired = repair_typescript_escaped_newline_in_line_comments(original)
        if not original or repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typescript_escaped_newline_in_line_comment"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.escaped_newline",
        source_tool=TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )


def _build_typescript_member_alias_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    aliases: list[dict[str, str]] = []
    seen_aliases: set[tuple[str, int, str, str]] = set()
    for item in _parse_typescript_missing_member_errors(diagnostics):
        path = item["file"]
        original = str(base_files.get(path) or "")
        member = item["member"]
        type_name = _typescript_declaration_type_name(item["type"])
        line_number = _to_positive_int(item.get("line"))
        if not original or not type_name or line_number <= 0:
            continue
        lines = original.splitlines(keepends=True)
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        receiver = _typescript_receiver_for_member_access(lines[line_index], member)
        existing_members = _typescript_existing_member_names_for_type(base_files=base_files, type_name=type_name)
        replacement = _typescript_member_alias_replacement(
            receiver=receiver,
            missing_member=member,
            existing_members=existing_members,
        )
        if not receiver or not replacement:
            continue
        alias_key = (path, line_index, member, replacement)
        if alias_key in seen_aliases:
            continue
        seen_aliases.add(alias_key)
        repaired_line = re.sub(rf"\b{re.escape(receiver)}\s*\.\s*{re.escape(member)}\b", replacement, lines[line_index])
        if repaired_line == lines[line_index]:
            continue
        operations.append(
            _line_text_replace_operation(
                path=path,
                content=original,
                line_index=line_index,
                replacement=repaired_line,
                metadata={
                    "repair_kind": "typescript_member_alias",
                    "type": type_name,
                    "member": member,
                    "receiver": receiver,
                    "replacement": replacement,
                },
            )
        )
        aliases.append({"file": path, "type": type_name, "member": member, "replacement": replacement})
    return _repair_plan_or_none(
        rule_id="typescript.member_alias",
        source_tool=TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"aliases": aliases},
    )


def _build_typescript_private_constructor_access_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in _parse_typescript_private_constructor_access_errors(diagnostics):
        path = item["file"]
        class_name = item["class"]
        line_number = _to_positive_int(item.get("line"))
        original = str(base_files.get(path) or "")
        if not original or not class_name or line_number <= 0:
            continue
        lines = original.splitlines(keepends=True)
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        if not _typescript_line_invokes_constructor(lines[line_index], class_name):
            continue
        modifier_span = _typescript_exported_private_constructor_modifier_span(original, class_name)
        if modifier_span is None:
            continue
        modifier_line_index, start, end = modifier_span
        key = (path, class_name, start)
        if key in seen:
            continue
        seen.add(key)
        expected = original[start:end]
        if expected != "private ":
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="",
                before_hash=sha256_text(original),
                metadata={
                    "repair_kind": "typescript_private_constructor_access",
                    "class_name": class_name,
                    "diagnostic_line": line_number,
                    "constructor_line": modifier_line_index + 1,
                    "visibility_change": "private_to_public_default",
                    "precision_strategy": "diagnostic_new_expression_to_exported_class_private_constructor",
                },
            )
        )
        repairs.append(
            {
                "file": path,
                "class_name": class_name,
                "diagnostic_line": line_number,
                "constructor_line": modifier_line_index + 1,
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.private_constructor_access",
        source_tool=TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"repairs": repairs},
    )


def _build_typescript_import_specifier_keyword_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, object]] = []
    for path in _typescript_syntax_error_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired, replacements = _repair_typescript_import_specifier_keywords(original)
        if not original or repaired == original or not replacements:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_import_specifier_keyword",
                    "replacements": tuple(replacements),
                },
            )
        )
        repaired_files.append({"file": path, "replacements": tuple(replacements)})
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _normalize_repair_path(str(diagnostic.path or "")) in base_files
    )
    return _repair_plan_or_none(
        rule_id="typescript.import_specifier_keyword",
        source_tool=TYPESCRIPT_IMPORT_SPECIFIER_KEYWORD_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"files": repaired_files},
    )


def _build_typescript_missing_export_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    exports: list[dict[str, str]] = []
    updated: dict[str, str] = {}
    exports_by_path: dict[str, list[dict[str, str]]] = {}
    for item in _parse_typescript_missing_export_errors(diagnostics):
        operation, meta = _missing_export_operation(base_files={**base_files, **updated}, item=item)
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(
            updated.get(operation.path) or base_files[operation.path], operation
        )
        exports.append(meta)
        exports_by_path.setdefault(operation.path, []).append(meta)
    operations: list[RepairOperation] = []
    for path, repaired in sorted(updated.items()):
        original = str(base_files.get(path) or "")
        symbols = [item.get("symbol", "") for item in exports_by_path.get(path, []) if item.get("symbol")]
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_missing_export",
                    "symbols": symbols,
                    "batched_same_file_exports": True,
                },
            )
        )
    return _repair_plan_or_none(
        rule_id="typescript.missing_export",
        source_tool=TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"exports": exports},
    )


def _build_typescript_missing_member_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = list(
        _typescript_object_literal_missing_member_operations(base_files=base_files, diagnostics=diagnostics)
    )
    inline_operations, inline_members = _typescript_inline_object_missing_member_operations(
        base_files=base_files,
        diagnostics=diagnostics,
    )
    operations.extend(inline_operations)
    members: list[dict[str, str]] = []
    members.extend(inline_members)
    grouped_members: dict[str, dict[str, dict[str, object]]] = {}
    for item in _parse_typescript_missing_member_errors(diagnostics):
        raw_type_name = item["type"]
        type_name = _typescript_declaration_type_name(raw_type_name)
        member = item["member"]
        if not type_name or not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_path = item["file"]
        usage_text = str(base_files.get(usage_path) or "")
        line_number = _to_positive_int(item.get("line"))
        member_is_call = _typescript_member_usage_is_call(usage_text, line_number, member)
        static_context = str(raw_type_name or "").strip().startswith("typeof ")
        if member_is_call and _typescript_declared_type_kind(base_files=base_files, type_name=type_name) != "class":
            continue
        declared_type = _typescript_missing_member_declared_type(
            usage_text,
            line_number,
            member,
            member_is_call=member_is_call,
        )
        if not declared_type or declared_type == "unknown":
            continue
        existing_members = _typescript_existing_member_names_for_type(base_files=base_files, type_name=type_name)
        receiver = ""
        if line_number > 0:
            lines = usage_text.splitlines()
            if line_number <= len(lines):
                receiver = _typescript_receiver_for_member_access(lines[line_number - 1], member)
        if receiver and _typescript_member_alias_replacement(
            receiver=receiver,
            missing_member=member,
            existing_members=existing_members,
        ):
            continue
        type_members = grouped_members.setdefault(type_name, {})
        existing = type_members.get(member) or {}
        existing_type = str(existing.get("declared_type") or "")
        type_members[member] = {
            "is_call": bool(existing.get("is_call")) or member_is_call,
            "declared_type": existing_type if existing_type and existing_type != "unknown" else declared_type,
            "static_context": bool(existing.get("static_context")) or static_context,
        }
    for type_name, type_members in grouped_members.items():
        operation = _add_typescript_members_operation(
            base_files=base_files,
            type_name=type_name,
            members=tuple(
                (
                    member,
                    bool(spec.get("is_call")),
                    str(spec.get("declared_type") or "unknown"),
                    bool(spec.get("static_context")),
                )
                for member, spec in type_members.items()
            ),
        )
        if operation is None:
            continue
        operations.append(operation)
        for member in type_members:
            members.append({"file": operation.path, "type": type_name, "member": member})
    return _repair_plan_or_none(
        rule_id="typescript.missing_member",
        source_tool=TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"members": members},
    )


def _build_typescript_reexport_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    if not _looks_like_typescript_reexport_signal(diagnostics):
        return None
    operations: list[RepairOperation] = []
    reexports: list[dict[str, str]] = []
    for importer_path, importer_text in base_files.items():
        if not importer_path.endswith((".ts", ".tsx")):
            continue
        for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
            module_path = _resolve_relative_ts_module_path(importer_path, str(match.group("module") or ""), base_files)
            if not module_path:
                continue
            module_text = str(base_files.get(module_path) or "")
            for symbol in _parse_named_import_symbols(str(match.group("symbols") or "")):
                if _typescript_module_exports_symbol(module_text, symbol):
                    continue
                source_path = _find_unique_runtime_export_source(base_files, module_path, symbol)
                if not source_path:
                    continue
                export_line = _build_typescript_reexport_line(
                    module_path=module_path, source_path=source_path, symbol=symbol
                )
                if export_line in module_text:
                    continue
                start = len(module_text.rstrip())
                replacement = f"\n{export_line}\n" if start else f"{export_line}\n"
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=module_path,
                        span_start=start,
                        span_end=len(module_text),
                        expected=module_text[start:],
                        replacement=replacement,
                        before_hash=sha256_text(module_text),
                        metadata={
                            "repair_kind": "typescript_runtime_reexport",
                            "symbol": symbol,
                            "source": source_path,
                            "expected_context_before": module_text[max(0, start - 240) : start],
                            "expected_context_after": module_text[len(module_text) : len(module_text)],
                        },
                    )
                )
                reexports.append({"file": module_path, "symbol": symbol, "source": source_path})
    return _repair_plan_or_none(
        rule_id="typescript.reexport",
        source_tool=TYPESCRIPT_REEXPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"reexports": reexports},
    )


def _build_typescript_export_ambiguity_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    targets = _typescript_export_ambiguity_targets(diagnostics)
    if not targets:
        return None

    grouped: dict[tuple[str, int, int, str], list[str]] = {}
    grouped_metadata: dict[tuple[str, int, int, str], list[dict[str, object]]] = {}
    matched_diagnostics: list[RepairDiagnostic] = []
    for target in targets:
        path = str(target["path"])
        module = str(target["module"])
        symbol = str(target["symbol"])
        diagnostic = target["diagnostic"]
        if not isinstance(diagnostic, RepairDiagnostic):
            continue
        content = str(base_files.get(path) or "")
        if not content:
            continue
        star_match = next(
            (match for match in _TS_EXPORT_STAR_RE.finditer(content) if str(match.group("module") or "") == module),
            None,
        )
        if star_match is None:
            continue
        if _typescript_file_has_named_reexport(content, module=module, symbol=symbol):
            continue
        source_path = _resolve_relative_ts_module_path(path, module, base_files)
        if not source_path:
            continue
        source_text = str(base_files.get(source_path) or "")
        if not source_text or not _typescript_module_exports_symbol(source_text, symbol):
            continue
        export_keyword = "export type" if _typescript_exported_symbol_is_type_only(source_text, symbol) else "export"
        export_line = (
            f"{star_match.group('indent')}{export_keyword} {{ {symbol} }} "
            f"from {star_match.group('quote')}{module}{star_match.group('quote')};"
        )
        key = (path, star_match.start(), star_match.end(), str(star_match.group(0) or ""))
        lines = grouped.setdefault(key, [])
        if export_line not in lines:
            lines.append(export_line)
            grouped_metadata.setdefault(key, []).append(
                {
                    "file": path,
                    "module": module,
                    "symbol": symbol,
                    "source": source_path,
                    "type_only": export_keyword == "export type",
                }
            )
        matched_diagnostics.append(diagnostic)

    operations: list[RepairOperation] = []
    repaired: list[dict[str, object]] = []
    for (path, start, end, expected), export_lines in sorted(grouped.items()):
        content = str(base_files.get(path) or "")
        key = (path, start, end, expected)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement=f"{expected}\n" + "\n".join(export_lines),
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_export_ambiguity",
                    "exports": tuple(grouped_metadata.get(key, ())),
                },
            )
        )
        repaired.extend(grouped_metadata.get(key, ()))

    return _repair_plan_or_none(
        rule_id="typescript.export_ambiguity",
        source_tool=TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"explicit_reexports": repaired},
    )


def _build_typescript_reexported_type_binding_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    imports: list[dict[str, str]] = []
    updated = dict(base_files)
    for item in _parse_typescript_cannot_find_name_errors(diagnostics):
        path = item["file"]
        original = str(updated.get(path) or "")
        if not original or not _typescript_missing_identifier_usage_is_type_position(original, item):
            continue
        repaired, import_meta = _add_typescript_reexported_type_binding(original, missing_symbol=item["symbol"])
        if repaired == original or not import_meta:
            continue
        path_operations = _text_replace_operations_from_repair(
            path=path,
            original=original,
            repaired=repaired,
            metadata={"repair_kind": "typescript_reexported_type_binding", **import_meta},
        )
        operations.extend(path_operations)
        updated[path] = repaired
        imports.append({"file": path, **import_meta})
    return _repair_plan_or_none(
        rule_id="typescript.reexported_type_binding",
        source_tool=TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"imports": imports},
    )


def _build_typescript_relative_import_case_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    return _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL,
        rule_id="typescript.relative_import_case",
        mode_filter="case",
    )


def _build_typescript_unique_export_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    duplicate_plan = _build_typescript_duplicate_export_import_binding_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
    )
    if duplicate_plan is not None:
        return duplicate_plan
    return _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        rule_id="typescript.unique_export_import",
        mode_filter="unique_export",
    )


def _build_typescript_value_used_as_type_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    parsed = _parse_typescript_value_used_as_type_errors(diagnostics)
    if not parsed:
        return None

    updated: dict[str, str] = dict(base_files)
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    for item in parsed:
        path = str(item.get("file") or "")
        symbol = str(item.get("symbol") or "")
        original = str(updated.get(path) or "")
        if not original or not _TS_IDENTIFIER_RE.fullmatch(symbol):
            continue
        if not _typescript_imported_const_class_alias_available(
            base_files=base_files,
            importer_path=path,
            importer_text=original,
            symbol=symbol,
        ):
            continue
        next_text, line_repaired = _replace_typescript_value_used_as_type_reference(
            original,
            line_number=_to_positive_int(item.get("line")),
            column_number=_to_positive_int(item.get("col")),
            symbol=symbol,
        )
        if next_text == original or not line_repaired:
            continue
        updated[path] = next_text
        diagnostic = item.get("diagnostic")
        if isinstance(diagnostic, RepairDiagnostic):
            matched_diagnostics.append(diagnostic)
        repaired.append({"file": path, "symbol": symbol, "line": item.get("line")})

    operations: list[RepairOperation] = []
    for path, repaired_text in updated.items():
        original = str(base_files.get(path) or "")
        if not original or repaired_text == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired_text,
                metadata={
                    "repair_kind": "typescript_value_used_as_type",
                    "strategy": "instance_type_of_exported_const_class_alias",
                },
            )
        )

    return _repair_plan_or_none(
        rule_id="typescript.value_used_as_type",
        source_tool=TYPESCRIPT_VALUE_USED_AS_TYPE_SOURCE_TOOL,
        operations=operations,
        diagnostics=tuple(matched_diagnostics),
        mode=mode,
        metadata={"value_type_references": repaired},
    )


def _build_typescript_duplicate_export_import_binding_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    targets = _typescript_duplicate_identifier_targets(diagnostics)
    if not targets:
        return None

    operations: list[RepairOperation] = []
    repaired: list[dict[str, object]] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    for path, names in sorted(targets.items()):
        content = str(base_files.get(path) or "")
        if not content:
            continue
        path_operations = _typescript_duplicate_export_import_operations(
            path=path,
            content=content,
            duplicate_names=names,
        )
        if not path_operations:
            continue
        operations.extend(path_operations)
        matched_diagnostics.extend(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.path == path and diagnostic.code == "typescript_ts2300"
        )
        repaired.append({"file": path, "symbols": tuple(sorted(names)), "operation_count": len(path_operations)})

    return _repair_plan_or_none(
        rule_id="typescript.duplicate_export_import_binding",
        source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"duplicate_export_import_bindings": repaired},
    )


def _build_typescript_branded_literal_cast_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    brand_sources = _typescript_string_brand_type_sources(base_files)
    if not brand_sources:
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    import_requirements: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        target_type = _typescript_branded_literal_target_type(diagnostic)
        if not target_type or target_type not in brand_sources:
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        content = str(base_files.get(path) or "")
        if not path or not content:
            continue
        operation = _typescript_branded_literal_cast_operation(
            path=path,
            content=content,
            diagnostic=diagnostic,
            target_type=target_type,
        )
        if operation is None:
            continue
        operations.append(operation)
        import_requirements.setdefault(path, set()).add(target_type)
        matched_diagnostics.append(diagnostic)
        repaired.append(
            {
                "file": path,
                "target_type": target_type,
                "line": diagnostic.line,
                "column": diagnostic.column,
            }
        )

    for path, type_names in sorted(import_requirements.items()):
        content = str(base_files.get(path) or "")
        for type_name in sorted(type_names):
            source_path = brand_sources.get(type_name, "")
            if not source_path or source_path == path:
                continue
            if _typescript_file_has_type_name_import(content, type_name):
                continue
            import_operation = _typescript_insert_type_import_operation(
                path=path,
                content=content,
                type_name=type_name,
                source_path=source_path,
            )
            if import_operation is not None:
                operations.append(import_operation)

    return _repair_plan_or_none(
        rule_id="typescript.branded_literal_cast",
        source_tool=TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"branded_literal_casts": repaired},
    )


def _build_typescript_literal_union_value_facade_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    aliases = _typescript_string_literal_union_type_aliases(base_files)
    if not aliases:
        return None

    operations: list[RepairOperation] = []
    matched_diagnostics: list[RepairDiagnostic] = []
    repaired: list[dict[str, object]] = []
    processed_symbols: set[str] = set()
    for diagnostic in diagnostics:
        symbol = _typescript_type_only_value_usage_symbol(diagnostic)
        if not symbol or symbol in processed_symbols or symbol not in aliases:
            continue
        path, span_start, span_end, expected, literals, exported = aliases[symbol]
        use_member = _typescript_type_value_dot_member(
            base_files=base_files,
            diagnostic=diagnostic,
            symbol=symbol,
        )
        if not use_member or use_member not in literals:
            continue
        operation = _typescript_literal_union_value_facade_operation(
            path=path,
            content=str(base_files.get(path) or ""),
            span_start=span_start,
            span_end=span_end,
            expected=expected,
            type_name=symbol,
            literals=literals,
            exported=exported,
        )
        if operation is None:
            continue
        operations.append(operation)
        matched_diagnostics.append(diagnostic)
        processed_symbols.add(symbol)
        repaired.append(
            {
                "file": path,
                "type_name": symbol,
                "literals": tuple(literals),
                "usage_member": use_member,
                "diagnostic_path": diagnostic.path,
            }
        )

    return _repair_plan_or_none(
        rule_id="typescript.literal_union_value_facade",
        source_tool=TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL,
        operations=operations,
        diagnostics=matched_diagnostics,
        mode=mode,
        metadata={"literal_union_value_facades": repaired},
    )


def _build_typescript_unused_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    import_plan = _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        rule_id="typescript.unused_import",
        mode_filter="unused",
    )
    operations: list[RepairOperation] = list(import_plan.operations if import_plan is not None else ())
    import_repairs = list(import_plan.metadata.get("imports", ()) if import_plan is not None else ())
    parameter_operations, parameter_repairs = _typescript_unused_parameter_operations(
        base_files=base_files,
        diagnostics=diagnostics,
    )
    operations.extend(parameter_operations)
    rule_id = "typescript.unused_import"
    if parameter_operations and not import_repairs:
        rule_id = "typescript.unused_parameter"
    elif parameter_operations:
        rule_id = "typescript.unused_declaration"
    return _repair_plan_or_none(
        rule_id=rule_id,
        source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"imports": import_repairs, "parameters": parameter_repairs},
    )


def _build_typescript_scaffold_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    joined = _diagnostic_text(diagnostics).lower()
    operations: list[RepairOperation] = []
    files: list[dict[str, str]] = []
    needs_package = "package.json" in joined and ("missing" in joined or "not found" in joined)
    needs_tsconfig = "tsconfig.json" in joined and ("missing" in joined or "not found" in joined)
    if needs_package and "package.json" not in base_files:
        content = (
            json.dumps(_typescript_scaffold_package_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        operations.append(
            RepairOperation(
                kind="write_file",
                path="package.json",
                content=content,
                before_hash=sha256_text(""),
                metadata={"repair_kind": "typescript_scaffold_package", "write_file_reason": "new_package_manifest"},
            )
        )
        files.append({"file": "package.json"})
    if needs_tsconfig and "tsconfig.json" not in base_files:
        content = (
            json.dumps(_typescript_scaffold_tsconfig_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        operations.append(
            RepairOperation(
                kind="write_file",
                path="tsconfig.json",
                content=content,
                before_hash=sha256_text(""),
                metadata={"repair_kind": "typescript_scaffold_tsconfig", "write_file_reason": "new_tsconfig"},
            )
        )
        files.append({"file": "tsconfig.json"})
    return _repair_plan_or_none(
        rule_id="typescript.scaffold",
        source_tool=TYPESCRIPT_SCAFFOLD_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": files},
    )


def _build_typescript_sourcefile_diagnostics_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repaired_files: list[dict[str, str]] = []
    for path in _parse_typescript_sourcefile_diagnostics_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired = _repair_typescript_sourcefile_diagnostics_usage(original)
        if not original or repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typescript_sourcefile_diagnostics"},
            )
        )
        repaired_files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.sourcefile_diagnostics",
        source_tool=TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"diagnostics": repaired_files},
    )


def _build_typescript_too_few_arguments_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    methods: list[dict[str, str]] = []
    updated = dict(base_files)
    for item in _parse_typescript_too_few_arguments_errors(diagnostics):
        operation = _too_few_arguments_operation(updated, item)
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(updated[operation.path], operation)
        operations.append(operation)
        methods.append(
            {
                "file": operation.path,
                "method": str(operation.metadata.get("method") or ""),
                "repair": str(operation.metadata.get("repair") or ""),
            }
        )
    return _repair_plan_or_none(
        rule_id="typescript.too_few_arguments",
        source_tool=TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"methods": methods},
    )


def _build_typescript_tsconfig_lib_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not tsconfig_text:
        return None
    needs_dom_lib = _typescript_errors_require_dom_lib(diagnostics)
    needs_import_meta_module = _typescript_errors_require_import_meta_module(diagnostics)
    needs_es2021_lib = _typescript_errors_require_es2021_lib(diagnostics)
    if not needs_dom_lib and not needs_import_meta_module and not needs_es2021_lib:
        return None
    payload = _json_object(tsconfig_text)
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        compiler_options = {}
    operations: list[RepairOperation] = []
    libs_raw = compiler_options.get("lib")
    libs = [str(item) for item in libs_raw] if isinstance(libs_raw, list) else []
    normalized_libs = {item.lower() for item in libs}
    if needs_es2021_lib and not _typescript_libs_allow_es2021(libs):
        libs = _typescript_promote_libs_to_es2021(libs, compiler_options.get("target"))
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "lib"),
                value=libs,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_tsconfig_es2021_lib"},
            )
        )
        target_value = str(compiler_options.get("target") or "").strip()
        if target_value and target_value.lower() not in {"es2021", "es2022", "esnext"}:
            operations.append(
                RepairOperation(
                    kind="json_set",
                    path="tsconfig.json",
                    json_path=("compilerOptions", "target"),
                    value="ES2021",
                    before_hash=sha256_text(tsconfig_text),
                    metadata={"repair_kind": "typescript_tsconfig_es2021_target"},
                )
            )
    if needs_dom_lib and "dom" not in normalized_libs:
        if not libs:
            libs.append(str(compiler_options.get("target") or "ES2020"))
        libs.append("DOM")
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "lib"),
                value=libs,
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_tsconfig_dom_lib"},
            )
        )
    module_value = compiler_options.get("module")
    if needs_import_meta_module and not _typescript_module_allows_import_meta(module_value):
        operations.append(
            RepairOperation(
                kind="json_set",
                path="tsconfig.json",
                json_path=("compilerOptions", "module"),
                value="ES2020",
                before_hash=sha256_text(tsconfig_text),
                metadata={"repair_kind": "typescript_tsconfig_import_meta_module"},
            )
        )
    return _repair_plan_or_none(
        rule_id="typescript.tsconfig_lib",
        source_tool=TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={
            "libs": libs,
            "module": "ES2020" if needs_import_meta_module else module_value,
            "target": "ES2021" if needs_es2021_lib else compiler_options.get("target"),
        },
    )


def _build_typescript_tsconfig_rootdir_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    tsconfig_text = str(base_files.get("tsconfig.json") or "")
    if not tsconfig_text or not _typescript_errors_require_rootdir_widening(diagnostics):
        return None
    payload = _json_object(tsconfig_text)
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return None
    root_dir = _normalize_repair_path(str(compiler_options.get("rootDir") or ""))
    if root_dir not in {"src", "src/"}:
        return None
    outside_paths = _typescript_rootdir_outside_paths(diagnostics, root_dir=root_dir)
    include_entries = _typescript_tsconfig_include_entries(payload)
    include_has_outside_root = any(
        _typescript_glob_points_outside_root(entry, root_dir=root_dir) for entry in include_entries
    )
    if not outside_paths and not include_has_outside_root:
        return None
    operation = RepairOperation(
        kind="json_set",
        path="tsconfig.json",
        json_path=("compilerOptions", "rootDir"),
        value=".",
        before_hash=sha256_text(tsconfig_text),
        metadata={
            "repair_kind": "typescript_tsconfig_rootdir_outside_source",
            "previous_rootDir": root_dir,
            "outside_paths": tuple(outside_paths),
        },
    )
    return _repair_plan_or_none(
        rule_id="typescript.tsconfig_rootdir",
        source_tool=TYPESCRIPT_TSCONFIG_ROOTDIR_SOURCE_TOOL,
        operations=[operation],
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"previous_rootDir": root_dir, "rootDir": ".", "outside_paths": outside_paths},
    )


def _build_typescript_uninitialized_property_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    properties: list[dict[str, str]] = []
    for item in _parse_typescript_uninitialized_property_errors(diagnostics):
        path = item["file"]
        original = str(base_files.get(path) or "")
        line_index = _to_positive_int(item.get("line")) - 1
        if not original or line_index < 0:
            continue
        lines = original.splitlines(keepends=True)
        if line_index >= len(lines):
            continue
        line_body = lines[line_index].rstrip("\r\n")
        newline = lines[line_index][len(line_body) :]
        repaired_line = _typescript_property_line_with_default(line_body, item["member"]) + newline
        if repaired_line == lines[line_index]:
            continue
        operations.append(
            _line_text_replace_operation(
                path=path,
                content=original,
                line_index=line_index,
                replacement=repaired_line,
                metadata={"repair_kind": "typescript_uninitialized_property", "member": item["member"]},
            )
        )
        properties.append({"file": path, "member": item["member"]})
    return _repair_plan_or_none(
        rule_id="typescript.uninitialized_property",
        source_tool=TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"properties": properties},
    )


def _build_typescript_unresolved_identifier_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    identifiers: list[dict[str, str]] = []
    for item in _parse_typescript_cannot_find_name_errors(diagnostics):
        path = item["file"]
        original = str(base_files.get(path) or "")
        line_number = _to_positive_int(item.get("line"))
        repaired, replacement = _repair_typescript_unresolved_identifier_lines(
            original,
            target_line_number=line_number,
            missing_symbol=item["symbol"],
        )
        if not original or repaired == original or not replacement:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={
                    "repair_kind": "typescript_unresolved_identifier_alias",
                    "symbol": item["symbol"],
                    "replacement": replacement,
                },
            )
        )
        identifiers.append({"file": path, "symbol": item["symbol"], "replacement": replacement})
    return _repair_plan_or_none(
        rule_id="typescript.unresolved_identifier",
        source_tool=TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"identifiers": identifiers},
    )


def _build_typescript_vitest_globals_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    globals_repaired: list[dict[str, str]] = []
    by_file: dict[str, set[str]] = {}
    for item in _parse_typescript_missing_test_global_errors(diagnostics):
        by_file.setdefault(item["file"], set()).add(item["symbol"])
    if not by_file:
        return None
    for path, symbols in sorted(by_file.items()):
        original = str(base_files.get(path) or "")
        repaired = _add_vitest_import_to_typescript_test(original, symbols)
        if not original or repaired == original:
            continue
        metadata = {"repair_kind": "typescript_vitest_global_import", "symbols": tuple(sorted(symbols))}
        if _TS_VITEST_IMPORT_RE.search(original):
            operations.extend(
                _text_replace_operations_from_repair(
                    path=path,
                    original=original,
                    repaired=repaired,
                    metadata=metadata,
                )
            )
        else:
            operation = _prepend_typescript_vitest_import_operation(path=path, original=original, symbols=symbols)
            if operation is not None:
                operations.append(operation)
        globals_repaired.extend({"file": path, "symbol": symbol} for symbol in sorted(symbols))
    package_text = str(base_files.get("package.json") or "")
    if package_text:
        package_ops = _typescript_vitest_manifest_operations(package_text)
        operations.extend(package_ops)
        if package_ops:
            globals_repaired.append({"file": "package.json", "symbol": "vitest"})
    return _repair_plan_or_none(
        rule_id="typescript.vitest_globals",
        source_tool=TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        risk_level="medium",
        metadata={"test_globals": globals_repaired},
    )


def _build_typescript_zod_type_class_collision_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    files: list[dict[str, str]] = []
    for path in _parse_typescript_zod_type_class_collision_paths(diagnostics):
        original = str(base_files.get(path) or "")
        repaired = _repair_typescript_zod_type_class_collision(original)
        if not original or repaired == original:
            continue
        operations.extend(
            _text_replace_operations_from_repair(
                path=path,
                original=original,
                repaired=repaired,
                metadata={"repair_kind": "typescript_zod_type_class_collision"},
            )
        )
        files.append({"file": path})
    return _repair_plan_or_none(
        rule_id="typescript.zod_type_class_collision",
        source_tool=TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"files": files},
    )


def _repair_missing_object_property_comma_line(line_body: str) -> str:
    return _TS_INLINE_OBJECT_MISSING_COMMA_RE.sub(r"\g<value>, \g<key>", line_body)


def _typescript_syntax_error_paths(diagnostics: Sequence[RepairDiagnostic]) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        if diagnostic.code.lower() not in {"typescript_ts1003", "typescript_ts1005"}:
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if path and path.endswith((".ts", ".tsx")) and path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)


def _line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return "\n"


def _repair_typescript_embedded_import_type_declarations(
    original: str,
) -> tuple[str, tuple[dict[str, str], ...]]:
    lines = str(original or "").splitlines(keepends=True)
    if not lines:
        return str(original or ""), ()

    output: list[str] = []
    replacements: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _TS_NAMED_IMPORT_BLOCK_START_LINE_RE.match(line.rstrip("\r\n")):
            output.append(line)
            index += 1
            continue

        original_segment = [line]
        repaired_segment = [line]
        moved_imports: list[tuple[str, dict[str, str]]] = []
        index += 1
        block_closed = False
        while index < len(lines):
            inner_line = lines[index]
            original_segment.append(inner_line)
            stripped_inner = inner_line.rstrip("\r\n")
            embedded_match = _TS_EMBEDDED_IMPORT_TYPE_LINE_RE.match(stripped_inner)
            if embedded_match is not None:
                symbols = " ".join(str(embedded_match.group("symbols") or "").strip().split())
                module = str(embedded_match.group("module") or "").strip()
                quote = str(embedded_match.group("quote") or '"')
                import_line = f"import type {{ {symbols} }} from {quote}{module}{quote};{_line_ending(inner_line)}"
                moved_imports.append(
                    (
                        import_line,
                        {
                            "keyword": "import type",
                            "symbol": symbols,
                            "module": module,
                            "repair_kind": "embedded_import_type_declaration",
                        },
                    )
                )
                index += 1
                continue

            repaired_segment.append(inner_line)
            if _TS_NAMED_IMPORT_BLOCK_END_LINE_RE.match(stripped_inner):
                block_closed = True
                index += 1
                break
            index += 1

        if not block_closed or not moved_imports:
            output.extend(original_segment)
            continue

        seen_moved: set[str] = set()
        for import_line, metadata in moved_imports:
            normalized = " ".join(import_line.strip().rstrip(";").split())
            if normalized in seen_moved:
                continue
            seen_moved.add(normalized)
            output.append(import_line)
            replacements.append(metadata)
        output.extend(repaired_segment)

    if not replacements:
        return str(original or ""), ()
    return "".join(output), tuple(replacements)


def _repair_typescript_import_specifier_keywords(original: str) -> tuple[str, tuple[dict[str, str], ...]]:
    text, embedded_replacements = _repair_typescript_embedded_import_type_declarations(str(original or ""))
    replacements: list[dict[str, str]] = []
    replacements.extend(embedded_replacements)
    pieces: list[str] = []
    cursor = 0
    for match in _TS_NAMED_IMPORT_RE.finditer(text):
        symbols = str(match.group("symbols") or "")
        repaired_symbols = _TS_IMPORT_SPECIFIER_KEYWORD_RE.sub(r"\g<prefix>type \g<symbol>", symbols)
        if repaired_symbols == symbols:
            continue
        pieces.append(text[cursor : match.start("symbols")])
        pieces.append(repaired_symbols)
        cursor = match.end("symbols")
        for keyword_match in _TS_IMPORT_SPECIFIER_KEYWORD_RE.finditer(symbols):
            replacements.append(
                {
                    "keyword": str(keyword_match.group("keyword") or ""),
                    "symbol": str(keyword_match.group("symbol") or ""),
                    "module": str(match.group("module") or ""),
                }
            )
    if not pieces:
        return text, tuple(replacements)
    pieces.append(text[cursor:])
    return "".join(pieces), tuple(replacements)


def _normalized_base_files(base_files: Mapping[str, str]) -> dict[str, str]:
    return {
        _normalize_repair_path(str(path or "")): str(content or "")
        for path, content in dict(base_files or {}).items()
        if _normalize_repair_path(str(path or ""))
    }


def _repair_plan_or_none(
    *,
    rule_id: str,
    source_tool: str,
    operations: Sequence[RepairOperation],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    risk_level: str = "low",
    metadata: Mapping[str, object] | None = None,
) -> RepairPlan | None:
    if not operations:
        return None
    return RepairPlan(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=tuple(operations),
        diagnostics=tuple(diagnostics or ()),
        mode=mode,
        risk_level=risk_level,
        priority=1,
        metadata=dict(metadata or {}),
    )


def _diagnostic_text(diagnostics: Sequence[RepairDiagnostic]) -> str:
    return "\n".join(f"{diagnostic.message}\n{diagnostic.raw}" for diagnostic in diagnostics)


def _json_object(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(text or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _typescript_errors_require_rootdir_widening(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    return any(
        diagnostic.code.lower() == "typescript_ts6059"
        or "is not under 'rootDir'" in str(diagnostic.raw or diagnostic.message)
        or 'is not under "rootDir"' in str(diagnostic.raw or diagnostic.message)
        for diagnostic in diagnostics
    )


def _typescript_rootdir_outside_paths(diagnostics: Sequence[RepairDiagnostic], *, root_dir: str) -> list[str]:
    normalized_root = _normalize_repair_path(root_dir).rstrip("/")
    outside: list[str] = []
    for diagnostic in diagnostics:
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            path = _normalize_repair_path(str(diagnostic.metadata.get("raw_path") or ""))
        if not path:
            continue
        if normalized_root and not path.startswith(f"{normalized_root}/"):
            outside.append(path)
    return _dedupe_preserve_order(outside)


def _typescript_tsconfig_include_entries(payload: Mapping[str, Any]) -> list[str]:
    include = payload.get("include")
    if not isinstance(include, list):
        return []
    return [str(item or "").strip().replace("\\", "/") for item in include if str(item or "").strip()]


def _typescript_glob_points_outside_root(entry: str, *, root_dir: str) -> bool:
    normalized_entry = str(entry or "").strip().replace("\\", "/")
    normalized_root = _normalize_repair_path(root_dir).rstrip("/")
    if not normalized_entry or not normalized_root:
        return False
    if normalized_entry.startswith(f"{normalized_root}/") or normalized_entry == normalized_root:
        return False
    return normalized_entry.startswith(("tests/", "test/", "*."))


def _parse_html_typescript_module_script_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for match in _HTML_TS_MODULE_SCRIPT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "source": str(match.group("src") or "").strip(),
                "replacement": "",
            }
            key = (item["file"], item["source"])
            if item["file"] and item["source"] and key not in seen:
                seen.add(key)
                parsed.append(item)
        for match in _HTML_COMPILED_JS_MISSING_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "source": str(match.group("src") or "").strip(),
                "replacement": str(match.group("emitted") or "").strip(),
            }
            key = (item["file"], item["source"])
            if item["file"] and item["source"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed


def _html_javascript_entrypoint_for_typescript_source(source_ref: str) -> str:
    source = str(source_ref or "").strip().replace("\\", "/")
    if not source.endswith((".ts", ".tsx")):
        return ""
    source = source.lstrip("/")
    if source.startswith("src/"):
        source = "dist/" + source[len("src/") :]
    return re.sub(r"\.tsx?$", ".js", source)


def _html_compiled_javascript_entrypoint_for_script(source_ref: str, *, base_files: Mapping[str, str]) -> str:
    source = str(source_ref or "").strip().replace("\\", "/")
    normalized = source[2:] if source.startswith("./") else source.lstrip("/")
    if not normalized.startswith("dist/") or not normalized.endswith(".js"):
        return ""
    source_stem = PurePosixPath(normalized).stem
    for candidate in (f"src/{source_stem}.ts", f"src/{source_stem}.tsx", f"{source_stem}.ts", f"{source_stem}.tsx"):
        if candidate not in base_files:
            continue
        compiled = _html_compiled_typescript_output_path(base_files, candidate)
        return f"./{compiled}" if source.startswith("./") else compiled
    return ""


def _html_compiled_typescript_output_path(base_files: Mapping[str, str], source_entry: str) -> str:
    source_path = _normalize_repair_path(source_entry)
    out_dir = _html_typescript_compiler_option(base_files, "outDir") or "dist"
    root_dir = _html_typescript_compiler_option(base_files, "rootDir")
    normalized_out = _normalize_repair_path(out_dir) or "dist"
    normalized_root = _normalize_repair_path(root_dir or "")
    relative_source = source_path
    if normalized_root and normalized_root not in {".", "./"}:
        prefix = f"{normalized_root.rstrip('/')}/"
        if source_path.startswith(prefix):
            relative_source = source_path.removeprefix(prefix)
    elif not normalized_root and source_path.startswith("src/"):
        relative_source = source_path.removeprefix("src/")
    return f"{normalized_out.rstrip('/')}/{PurePosixPath(relative_source).with_suffix('.js').as_posix()}"


def _html_typescript_compiler_option(base_files: Mapping[str, str], key: str) -> str:
    tsconfig = _json_object(str(base_files.get("tsconfig.json") or ""))
    compiler_options = tsconfig.get("compilerOptions")
    if not isinstance(compiler_options, Mapping):
        return ""
    return str(compiler_options.get(key) or "").strip().replace("\\", "/")


def _html_container_ids(base_files: Mapping[str, str]) -> set[str]:
    ids: set[str] = set()
    for path, content in base_files.items():
        if not path.endswith((".html", ".htm")):
            continue
        for match in _HTML_ID_ATTRIBUTE_RE.finditer(str(content or "")):
            value = str(match.group("id") or "").strip()
            if value:
                ids.add(value)
    return ids


def _html_container_selector_tokens(token_group: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in str(token_group or "").split("|"):
        token = raw.strip()
        if token and re.fullmatch(r"[A-Za-z0-9_-]+", token):
            tokens.append(token)
    return tuple(_dedupe_preserve_order(tokens))


def _html_ids_support_container_tokens(html_ids: set[str], tokens: Sequence[str]) -> bool:
    lowered_ids = {item.lower() for item in html_ids}
    for token in tokens:
        lowered_token = str(token or "").lower()
        if any(lowered_token in html_id and html_id != lowered_token for html_id in lowered_ids):
            return True
    return False


def _looks_like_javascript_typescript_annotation_error(error: object) -> bool:
    text = str(error or "")
    lowered = text.lower()
    return ".js:" in text and "syntaxerror: unexpected token ':'" in lowered


def _javascript_annotation_candidate_paths(
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> list[str]:
    candidates: list[str] = []
    for diagnostic in diagnostics:
        for match in _JS_RUNTIME_FILE_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path.endswith(".js") and path in base_files:
                candidates.append(path)
    if not candidates:
        candidates.extend(path for path in base_files if path.endswith(".js"))
    return _dedupe_preserve_order(candidates)


def _strip_typescript_annotations_from_javascript(text: str) -> str:
    repaired = _JS_FUNCTION_DECL_RE.sub(_strip_javascript_callable_type_match, str(text or ""))
    repaired = _JS_METHOD_DECL_RE.sub(_strip_javascript_callable_type_match, repaired)
    return _JS_VARIABLE_TYPE_RE.sub(r"\g<kind> \g<name>\g<assign>", repaired)


def _strip_javascript_callable_type_match(match: re.Match[str]) -> str:
    params = []
    for raw_param in str(match.group("params") or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        default = ""
        head = param
        if "=" in param:
            head, default_value = param.split("=", 1)
            default = " = " + default_value.strip()
        head = re.sub(r"^(?P<name>\.\.\.[A-Za-z_$][\w$]*|[A-Za-z_$][\w$]*)\s*:\s*[^=,]+$", r"\g<name>", head.strip())
        params.append(f"{head}{default}")
    return f"{match.group('prefix')}({', '.join(params)}){match.group('brace')}"


def _parse_undeclared_runtime_import_paths(
    diagnostics: Sequence[RepairDiagnostic],
    *,
    package_name: str,
) -> list[str]:
    paths: list[str] = []
    expected = str(package_name or "").split("/", 1)[0].lower()
    for diagnostic in diagnostics:
        for match in _UNDECLARED_RUNTIME_IMPORT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            package = str(match.group("package") or "").split("/", 1)[0].lower()
            path = _normalize_repair_path(str(match.group("path") or ""))
            if package == expected and path:
                paths.append(path)
    return _dedupe_preserve_order(paths)


def _normalize_undeclared_typeorm_model_source(text: str) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if _TYPEORM_IMPORT_LINE_RE.match(raw_line) or _TS_DECORATOR_LINE_RE.match(raw_line):
            continue
        lines.append(_normalize_ts_class_field_initialization(raw_line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip() + "\n")


def _normalize_ts_class_field_initialization(line: str) -> str:
    match = _TS_CLASS_FIELD_DECL_RE.match(line)
    if not match:
        return line
    indent = str(match.group("indent") or "")
    name = str(match.group("name") or "")
    optional = str(match.group("optional") or "")
    type_text = _normalize_typeorm_detached_field_type(str(match.group("type") or "unknown").strip())
    if optional:
        return f"{indent}{name}?: {type_text};"
    return f"{indent}{name}: {type_text} = {_typescript_default_value_for_type(type_text)};"


def _normalize_typeorm_detached_field_type(type_text: str) -> str:
    stripped = str(type_text or "unknown").strip() or "unknown"
    if re.fullmatch(r"[A-Z][A-Za-z0-9_]*\[\]", stripped):
        return "unknown[]"
    if re.fullmatch(r"[A-Z][A-Za-z0-9_]*", stripped):
        return "unknown"
    return stripped


def _typescript_commonjs_package_type_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return "commonjs" in text and "type" in text and "module" in text


def _typescript_entrypoint_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    if not text:
        return False
    if "entrypoint" in text or "entry point" in text:
        return True
    return "cannot find module" in text and any(prefix in text for prefix in ("dist/", "build/", "out/", "bin/"))


def _detect_typescript_entrypoint_from_package(package_data: Mapping[str, Any]) -> str:
    candidates: list[str] = []
    for key in ("main", "module", "browser"):
        value = package_data.get(key)
        if isinstance(value, str):
            candidates.append(value)
    scripts = package_data.get("scripts")
    if isinstance(scripts, Mapping):
        candidates.extend(str(value) for value in scripts.values() if isinstance(value, str))
    for candidate in candidates:
        match = re.search(r"(?:^|\s)(?:node\s+)?(?P<path>(?:dist|build|out|bin)/[^\s;&|'\"]+\.m?js)", candidate)
        token = str(match.group("path") if match else candidate).strip().replace("\\", "/")
        if token.startswith(("dist/", "build/", "out/", "bin/")) and token.endswith((".js", ".mjs", ".cjs")):
            return token
    return ""


def _typescript_source_entrypoint_for_compiled_path(compiled_path: str) -> str:
    token = str(compiled_path or "").strip().replace("\\", "/")
    if not token.startswith(("dist/", "build/", "out/", "bin/")):
        return ""
    parts = token.split("/")
    if len(parts) < 2:
        return ""
    return posixpath.join("src", *parts[1:-1], re.sub(r"\.m?js$|\.cjs$", ".ts", parts[-1]))


def _build_typescript_entrypoint_aggregator(*, modules: Sequence[str], entrypoint_dir: str) -> str:
    imports: list[str] = []
    exports: list[str] = []
    for module in modules:
        module_ref = posixpath.relpath(module.removesuffix(".ts"), entrypoint_dir or ".")
        if not module_ref.startswith("."):
            module_ref = f"./{module_ref}"
        alias = re.sub(r"[^A-Za-z0-9_$]", "_", module.removesuffix(".ts").removeprefix("src/"))
        if not alias or not re.match(r"[A-Za-z_$]", alias):
            alias = f"module_{alias}"
        imports.append(f"import * as {alias} from '{module_ref}';")
        exports.append(f"export {{ {alias} }};")
    return "\n".join([*imports, "", *exports, ""]) if imports else "export {};\n"


def _parse_typescript_escaped_newline_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        typed_path = _typed_typescript_escaped_newline_path(diagnostic)
        if typed_path:
            paths.append(typed_path)
            continue
        match = _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)


def _typed_typescript_escaped_newline_path(diagnostic: RepairDiagnostic) -> str:
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not path:
        return ""
    code = str(diagnostic.code or "").casefold()
    if "escaped_newline" in code:
        return path
    metadata_kind = str(diagnostic.metadata.get("issue_kind") or diagnostic.metadata.get("archetype") or "").casefold()
    if "escaped_newline" in metadata_kind:
        return path
    return ""


def repair_typescript_escaped_newline_in_line_comments(text: str) -> str:
    changed = False
    repaired_lines: list[str] = []

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group('prefix')}\n{match.group('code')}"

    for line in str(text or "").splitlines(keepends=True):
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if "//" not in line_body or "\\n" not in line_body:
            repaired_lines.append(line)
            continue
        comment_index = line_body.find("//")
        prefix = line_body[:comment_index]
        repaired_lines.append(
            f"{prefix}{_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE.sub(replace, line_body[comment_index:])}{newline}"
        )
    return "".join(repaired_lines) if changed else str(text or "")


def _parse_typescript_missing_member_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_MISSING_PROPERTY_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "member": str(match.group("member") or ""),
                    "type": str(match.group("type") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["member"]]


def _parse_typescript_private_constructor_access_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_PRIVATE_CONSTRUCTOR_ACCESS_RAW_RE.finditer(text):
            item = {
                "file": _normalize_repair_path(str(match.group("file") or "")),
                "line": str(match.group("line") or ""),
                "column": str(match.group("col") or ""),
                "class": str(match.group("class") or ""),
            }
            key = (item["file"], item["line"], item["class"])
            if item["file"] and item["line"] and item["class"] and key not in seen:
                seen.add(key)
                parsed.append(item)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        line = str(diagnostic.line or "")
        message_match = _TS_PRIVATE_CONSTRUCTOR_ACCESS_MESSAGE_RE.search(text)
        if diagnostic.code.lower() == "typescript_ts2673" and path and line and message_match:
            item = {
                "file": path,
                "line": line,
                "column": str(diagnostic.column or ""),
                "class": str(message_match.group("class") or ""),
            }
            key = (item["file"], item["line"], item["class"])
            if item["class"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed


def _parse_typescript_object_missing_member_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_OBJECT_MISSING_PROPERTIES_ERROR_RE.finditer(raw):
            members = [
                member.strip() for member in re.split(r",|\band\b", str(match.group("members") or "")) if member.strip()
            ]
            for member in members:
                if _TS_IDENTIFIER_RE.fullmatch(member):
                    parsed.append(
                        {
                            "file": _normalize_repair_path(str(match.group("file") or "")),
                            "line": str(match.group("line") or ""),
                            "member": member,
                            "type": str(match.group("type") or ""),
                        }
                    )
        for match in _TS_OBJECT_MISSING_PROPERTY_ERROR_RE.finditer(raw):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "member": str(match.group("member") or ""),
                    "type": str(match.group("type") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["member"] and item["type"]]


def _parse_typescript_unused_declaration_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for diagnostic in diagnostics:
        raw = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_UNUSED_DECLARATION_ERROR_RE.finditer(raw):
            item = {
                "file": _normalize_repair_path(str(match.group("file") or "")),
                "line": str(match.group("line") or ""),
                "column": str(match.group("col") or ""),
                "name": str(match.group("name") or ""),
            }
            key = (item["file"], item["line"], item["column"], item["name"])
            if item["file"] and item["line"] and item["name"] and key not in seen:
                seen.add(key)
                parsed.append(item)
    return [item for item in parsed if item["file"] and item["line"] and item["name"]]


def _typescript_unused_parameter_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]]]:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    items = _parse_typescript_unused_declaration_errors(diagnostics)
    named_import_operations, named_import_repairs, consumed_item_keys = (
        _typescript_unused_named_import_binding_group_operations(base_files=base_files, items=items)
    )
    operations.extend(named_import_operations)
    repairs.extend(named_import_repairs)
    for item in items:
        if _typescript_unused_declaration_item_key(item) in consumed_item_keys:
            continue
        path = item["file"]
        name = item["name"]
        content = str(base_files.get(path) or "")
        line_number = _to_positive_int(item.get("line"))
        column = _to_positive_int(item.get("column"))
        operation = _typescript_unused_import_declaration_operation(
            path=path,
            content=content,
            name=name,
            line_number=line_number,
        )
        if operation is None:
            operation = _typescript_unused_parameter_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
                column=column,
            )
        if operation is None:
            operation = _typescript_unused_function_declaration_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
            )
        if operation is None:
            operation = _typescript_unused_local_declaration_operation(
                path=path,
                content=content,
                name=name,
                line_number=line_number,
            )
        if operation is None:
            continue
        operations.append(operation)
        repairs.append({"file": path, "parameter": name, "replacement": str(operation.replacement or "")})
    return tuple(operations), repairs


def _typescript_unused_declaration_item_key(item: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (
        str(item.get("file") or ""),
        str(item.get("line") or ""),
        str(item.get("column") or ""),
        str(item.get("name") or ""),
    )


def _typescript_unused_named_import_binding_group_operations(
    *,
    base_files: Mapping[str, str],
    items: Sequence[Mapping[str, str]],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]], set[tuple[str, str, str, str]]]:
    grouped: dict[tuple[str, int, int], dict[str, object]] = {}
    consumed_item_keys: set[tuple[str, str, str, str]] = set()
    for item in items:
        path = str(item.get("file") or "")
        name = str(item.get("name") or "")
        line_number = _to_positive_int(item.get("line"))
        content = str(base_files.get(path) or "")
        if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
            continue
        for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
            start_line = content.count("\n", 0, match.start()) + 1
            end_line = content.count("\n", 0, match.end()) + 1
            if line_number < start_line or line_number > end_line:
                continue
            pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
            if len(pairs) <= 1 or not any(local == name for _, local in pairs):
                continue
            group_key = (path, match.start(), match.end())
            group = grouped.setdefault(
                group_key,
                {
                    "content": content,
                    "import_text": content[match.start() : match.end()],
                    "module_specifier": str(match.group("module") or ""),
                    "names": set(),
                    "lines": [],
                },
            )
            names = group["names"]
            lines = group["lines"]
            if isinstance(names, set):
                names.add(name)
            if isinstance(lines, list):
                lines.append(line_number)
            consumed_item_keys.add(_typescript_unused_declaration_item_key(item))
            break

    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    for (path, start, end), group in sorted(grouped.items()):
        content = str(group.get("content") or "")
        import_text = str(group.get("import_text") or "")
        raw_names = group.get("names", set())
        raw_lines = group.get("lines", [])
        names = {str(name) for name in raw_names if str(name)} if isinstance(raw_names, set) else set()
        diagnostic_lines = (
            [int(line) for line in raw_lines if isinstance(line, int) and int(line) > 0]
            if isinstance(raw_lines, list)
            else []
        )
        if not content or not import_text or not names:
            continue
        replacement = _remove_typescript_named_import_bindings(import_text=import_text, names=names)
        if replacement == import_text:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=import_text,
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_unused_import_specifier",
                    "compiler_reported_unused_binding": True,
                    "bindings": tuple(sorted(names)),
                    "module_specifier": str(group.get("module_specifier") or ""),
                    "diagnostic_lines": tuple(diagnostic_lines),
                },
            )
        )
        repairs.extend({"file": path, "parameter": name, "replacement": replacement} for name in sorted(names))
    return tuple(operations), repairs, consumed_item_keys


def _typescript_unused_import_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    operation = _typescript_unused_named_import_binding_operation(
        path=path,
        content=content,
        name=name,
        line_number=line_number,
    )
    if operation is not None:
        return operation
    for match in _TS_IMPORT_FROM_ANY_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line_number < start_line or line_number > end_line:
            continue
        pairs = _typescript_import_pairs_from_clause(str(match.group("clause") or ""))
        if len(pairs) != 1 or pairs[0][1] != name:
            continue
        start, end = match.span()
        if end < len(content) and content[end : end + 1] == "\n":
            end += 1
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=start,
            span_end=end,
            expected=content[start:end],
            replacement="",
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("specifier") or ""),
                "diagnostic_line": line_number,
            },
        )
    return None


def _typescript_unused_named_import_binding_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(content):
        start_line = content.count("\n", 0, match.start()) + 1
        end_line = content.count("\n", 0, match.end()) + 1
        if line_number < start_line or line_number > end_line:
            continue
        import_text = content[match.start() : match.end()]
        pairs = _typescript_import_pairs_from_clause("{" + str(match.group("names") or "") + "}")
        if len(pairs) <= 1 or not any(local == name for _, local in pairs):
            continue
        replacement = _remove_typescript_named_import_binding(import_text=import_text, name=name)
        if not replacement or replacement == import_text:
            continue
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=match.start(),
            span_end=match.end(),
            expected=import_text,
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unused_import_specifier",
                "compiler_reported_unused_binding": True,
                "binding": name,
                "module_specifier": str(match.group("module") or ""),
                "diagnostic_line": line_number,
            },
        )
    return None


def _remove_typescript_named_import_binding(*, import_text: str, name: str) -> str:
    return _remove_typescript_named_import_bindings(import_text=import_text, names={name})


def _remove_typescript_named_import_bindings(*, import_text: str, names: set[str]) -> str:
    normalized_names = {name for name in names if _TS_IDENTIFIER_RE.fullmatch(name)}
    if not normalized_names:
        return import_text
    if "\n" in import_text:
        replacement = _remove_typescript_multiline_named_import_bindings(
            import_text=import_text,
            names=normalized_names,
        )
        if replacement != import_text:
            return replacement
    match = _TS_REEXPORTABLE_NAMED_IMPORT_RE.fullmatch(import_text)
    if match is None:
        return import_text
    names_clause = str(match.group("names") or "")
    kept_parts: list[str] = []
    removed = False
    for raw_part in names_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        local = _typescript_named_import_local_name(part)
        if local in normalized_names:
            removed = True
            continue
        kept_parts.append(part)
    if not removed:
        return import_text
    if not kept_parts:
        return ""
    return (
        f"{match.group('indent') or ''}import "
        f"{match.group('type_only') or ''}{{ {', '.join(kept_parts)} }} "
        f"from {match.group('quote')}{match.group('module')}{match.group('quote')};"
    )


def _remove_typescript_multiline_named_import_bindings(*, import_text: str, names: set[str]) -> str:
    lines = import_text.splitlines(keepends=True)
    kept_lines: list[str] = []
    removed = False
    for line in lines:
        line_body = line.rstrip("\r\n")
        part = line_body.strip().rstrip(",").strip()
        if not part or _typescript_named_import_local_name(part) not in names:
            kept_lines.append(line)
            continue
        removed = True
    if not removed:
        return import_text
    remaining_names = [
        _typescript_named_import_local_name(line.strip().rstrip(",").strip())
        for line in kept_lines
        if _typescript_named_import_local_name(line.strip().rstrip(",").strip())
    ]
    if not remaining_names:
        return ""
    return "".join(kept_lines)


def _typescript_named_import_local_name(part: str) -> str:
    normalized = str(part or "").strip().rstrip(",").strip()
    if normalized.startswith("type "):
        normalized = normalized[5:].strip()
    alias_parts = re.split(r"\s+as\s+", normalized, maxsplit=1, flags=re.IGNORECASE)
    local = alias_parts[-1].strip()
    return local if _TS_IDENTIFIER_RE.fullmatch(local) else ""


def _typescript_unused_local_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    original_line = lines[line_index]
    line_body = original_line.rstrip("\r\n")
    newline = original_line[len(line_body) :]
    match = _TS_UNUSED_LOCAL_DECLARATION_LINE_RE.match(line_body)
    if match is None or str(match.group("name") or "") != name:
        return None
    expression = str(match.group("expr") or "").strip()
    if not expression or _typescript_unused_local_expression_requires_binding(expression):
        return None
    replacement = f"{match.group('indent') or ''!s}{expression};{newline}"
    if replacement == original_line:
        return None
    return _line_text_replace_operation(
        path=path,
        content=content,
        line_index=line_index,
        replacement=replacement,
        metadata={
            "repair_kind": "typescript_unused_local_declaration",
            "binding": name,
            "diagnostic_line": line_number,
            "replacement_strategy": "initializer_expression_statement",
        },
    )


def _typescript_unused_function_declaration_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    first_line = lines[line_index].rstrip("\r\n")
    if first_line.lstrip().startswith("export "):
        return None
    match = _TS_UNUSED_FUNCTION_DECLARATION_LINE_RE.match(first_line)
    if match is None or str(match.group("name") or "") != name:
        return None
    offsets = _line_start_offsets(lines)
    brace_depth = 0
    saw_open_brace = False
    end_line_index = -1
    for current_index in range(line_index, len(lines)):
        line = lines[current_index]
        if "{" in line:
            saw_open_brace = True
        brace_depth += line.count("{") - line.count("}")
        if saw_open_brace and brace_depth <= 0:
            end_line_index = current_index
            break
    if not saw_open_brace or end_line_index < line_index:
        return None
    span_start = offsets[line_index]
    span_end = offsets[end_line_index + 1]
    expected = content[span_start:span_end]
    if not expected.strip():
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement="",
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_unused_function_declaration",
            "binding": name,
            "diagnostic_line": line_number,
            "replacement_strategy": "delete_non_exported_function_declaration",
        },
    )


def _typescript_unused_local_expression_requires_binding(expression: str) -> bool:
    stripped = str(expression or "").lstrip()
    if not stripped:
        return True
    if stripped.startswith(("{", "function ", "class ", "interface ", "type ")):
        return True
    return "=>" in stripped


def _typescript_unused_parameter_operation(
    *,
    path: str,
    content: str,
    name: str,
    line_number: int,
    column: int,
) -> RepairOperation | None:
    if not path or not content or line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(name):
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    candidate_indexes: list[int] = []
    if 0 <= line_index < len(lines):
        candidate_indexes.append(line_index)
    candidate_indexes.extend(index for index in range(len(lines)) if index not in candidate_indexes)
    for candidate_index in candidate_indexes:
        original_line = lines[candidate_index]
        repaired_line = _typescript_unused_parameter_line_replacement(
            line=original_line,
            name=name,
            column=column if candidate_index == line_index else 0,
        )
        if not repaired_line or repaired_line == original_line:
            continue
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=candidate_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_unused_parameter",
                "parameter": name,
                "replacement": f"_{name}",
                "diagnostic_line": line_number,
                "matched_line": candidate_index + 1,
            },
        )
    return None


def _typescript_unused_parameter_line_replacement(*, line: str, name: str, column: int) -> str:
    if name.startswith("_") or f"_{name}" in line:
        return ""
    occurrences = list(re.finditer(rf"\b{re.escape(name)}\b", line))
    if not occurrences:
        return ""
    column_index = max(0, column - 1)
    occurrences.sort(key=lambda match: abs(match.start() - column_index))
    for match in occurrences:
        if not _typescript_identifier_occurrence_is_parameter(line, match.start(), match.end()):
            continue
        return f"{line[: match.start()]}_{name}{line[match.end() :]}"
    return ""


def _typescript_identifier_occurrence_is_parameter(line: str, start: int, end: int) -> bool:
    open_index = line.rfind("(", 0, start)
    close_index = line.find(")", end)
    if open_index < 0 or close_index < 0:
        return False
    segment_before = line[open_index + 1 : start]
    segment_after = line[end:close_index]
    if "{" in segment_before or "}" in segment_before:
        return False
    before_token = segment_before.rsplit(",", 1)[-1].strip()
    if before_token:
        return False
    tail = segment_after.lstrip()
    return not tail or tail.startswith((":", "?", "=", ","))


def _typescript_object_literal_missing_member_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[RepairOperation, ...]:
    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for item in _parse_typescript_object_missing_member_errors(diagnostics):
        type_name = _typescript_declaration_type_name(item["type"])
        if not type_name:
            continue
        key = (item["file"], type_name)
        grouped.setdefault(key, {})[item["member"]] = _to_positive_int(item.get("line"))

    operations: list[RepairOperation] = []
    for (path, type_name), member_lines in sorted(grouped.items()):
        content = str(base_files.get(path) or "")
        if not content:
            continue
        method_specs = _typescript_method_delegate_specs(
            content=content,
            type_name=type_name,
            members=tuple(member_lines),
        )
        if method_specs:
            operations.extend(
                _typescript_interface_method_return_operations(
                    path=path,
                    content=content,
                    type_name=type_name,
                    method_specs=method_specs,
                )
            )
            object_operation = _typescript_object_literal_method_implementation_operation(
                path=path,
                content=content,
                type_name=type_name,
                member_lines=member_lines,
                method_specs=method_specs,
            )
            if object_operation is not None:
                operations.append(object_operation)
        property_operation = _typescript_object_literal_required_properties_operation(
            base_files=base_files,
            path=path,
            content=content,
            type_name=type_name,
            member_lines=member_lines,
        )
        if property_operation is not None:
            operations.append(property_operation)
    return tuple(operations)


def _typescript_declaration_type_name(raw: str) -> str:
    match = re.search(r"[A-Za-z_$][A-Za-z0-9_$]*", str(raw or ""))
    return str(match.group(0) if match else "")


def _typescript_inline_object_missing_member_operations(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairOperation, ...], list[dict[str, str]]]:
    operations: list[RepairOperation] = []
    members: list[dict[str, str]] = []
    for item in _parse_typescript_missing_member_errors(diagnostics):
        member = item["member"]
        shape_members = _typescript_inline_object_shape_members(item["type"])
        if not shape_members or member in shape_members or not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_path = item["file"]
        usage_text = str(base_files.get(usage_path) or "")
        line_number = _to_positive_int(item.get("line"))
        declared_type = _typescript_missing_member_declared_type(
            usage_text,
            line_number,
            member,
            member_is_call=False,
        )
        if not _typescript_safe_structural_member_type(declared_type):
            continue
        type_operation = _typescript_inline_object_type_member_operation(
            base_files=base_files,
            shape_members=shape_members,
            member=member,
            declared_type=declared_type,
        )
        if type_operation is None:
            continue
        operations.append(type_operation)
        members.append({"file": type_operation.path, "type": "inline_object", "member": member})
        literal_operation = _typescript_inline_object_literal_member_operation(
            base_files=base_files,
            shape_members=shape_members,
            member=member,
            declared_type=declared_type,
        )
        if literal_operation is not None:
            operations.append(literal_operation)
    return tuple(operations), members


def _typescript_inline_object_shape_members(raw_type: str) -> dict[str, str]:
    text = str(raw_type or "").strip()
    if not text.startswith("{") or not text.endswith("}"):
        return {}
    members: dict[str, str] = {}
    body = text[1:-1]
    for segment in re.split(r";|,", body):
        cleaned = re.sub(r"^\s*readonly\s+", "", segment.strip())
        match = re.match(r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\??\s*:\s*(?P<type>.+)$", cleaned)
        if not match:
            continue
        name = str(match.group("name") or "")
        ts_type = str(match.group("type") or "").strip()
        if name and ts_type:
            members[name] = ts_type
    return members


def _typescript_inline_object_type_member_operation(
    *,
    base_files: Mapping[str, str],
    shape_members: Mapping[str, str],
    member: str,
    declared_type: str,
) -> RepairOperation | None:
    for path, content in base_files.items():
        for open_brace in _typescript_inline_array_object_type_braces(str(content or "")):
            close_brace = _typescript_matching_brace_index(str(content or ""), open_brace)
            if close_brace < 0:
                continue
            body = str(content or "")[open_brace + 1 : close_brace]
            existing_members = _typescript_inline_object_shape_members(f"{{{body}}}")
            if member in existing_members or not set(shape_members).issubset(existing_members):
                continue
            operation = _typescript_insert_object_member_operation(
                path=path,
                content=str(content or ""),
                close_brace=close_brace,
                member=member,
                value=f"{declared_type};",
                readonly=True,
                repair_kind="typescript_inline_object_type_missing_member",
            )
            if operation is not None:
                return operation
    return None


def _typescript_inline_array_object_type_braces(content: str) -> tuple[int, ...]:
    starts: list[int] = []
    for match in re.finditer(r"\b(?:ReadonlyArray|Array)\s*<\s*\{", str(content or "")):
        open_brace = str(content or "").rfind("{", 0, match.end())
        if open_brace >= 0:
            close_brace = _typescript_matching_brace_index(str(content or ""), open_brace)
            if close_brace >= 0 and str(content or "")[close_brace + 1 :].lstrip().startswith(">"):
                starts.append(open_brace)
    return tuple(starts)


def _typescript_inline_object_literal_member_operation(
    *,
    base_files: Mapping[str, str],
    shape_members: Mapping[str, str],
    member: str,
    declared_type: str,
) -> RepairOperation | None:
    default_value = _typescript_default_value_for_required_property_type(declared_type)
    if not default_value:
        return None
    for path, content in base_files.items():
        text = str(content or "")
        for open_brace in (match.start() for match in re.finditer(r"\{", text)):
            close_brace = _typescript_matching_brace_index(text, open_brace)
            if close_brace < 0:
                continue
            body = text[open_brace + 1 : close_brace]
            if len(body) > 600:
                continue
            object_members = _typescript_object_literal_member_names(body)
            if member in object_members or not set(shape_members).issubset(object_members):
                continue
            operation = _typescript_insert_object_member_operation(
                path=path,
                content=text,
                close_brace=close_brace,
                member=member,
                value=f"{default_value},",
                readonly=False,
                repair_kind="typescript_inline_object_literal_missing_member",
            )
            if operation is not None:
                return operation
    return None


def _typescript_object_literal_member_names(body: str) -> set[str]:
    names: set[str] = set()
    depth = 0
    for line in str(body or "").splitlines():
        if depth == 0 and (match := re.match(r"\s*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?::|,|$)", line)):
            names.add(str(match.group("name") or ""))
        depth += line.count("{") - line.count("}")
        if depth < 0:
            depth = 0
    return names


def _typescript_insert_object_member_operation(
    *,
    path: str,
    content: str,
    close_brace: int,
    member: str,
    value: str,
    readonly: bool,
    repair_kind: str,
) -> RepairOperation | None:
    close_line_start = content.rfind("\n", 0, close_brace) + 1
    if close_line_start <= 0:
        return None
    close_indent_match = re.match(r"\s*", content[close_line_start:close_brace])
    close_indent = close_indent_match.group(0) if close_indent_match else ""
    body = content[content.rfind("{", 0, close_brace) + 1 : close_brace]
    member_indent = _typescript_member_insert_indent(body, fallback=f"{close_indent}  ")
    prefix = "readonly " if readonly else ""
    declaration = f"{member_indent}{prefix}{member}: {value}\n"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=close_line_start,
        span_end=close_line_start,
        expected="",
        replacement=declaration,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": repair_kind,
            "member": member,
            "declared_type_or_value": value,
            "expected_context_before": content[max(0, close_line_start - 240) : close_line_start],
            "expected_context_after": content[close_line_start : close_line_start + 80],
        },
    )


def _typescript_member_insert_indent(body: str, *, fallback: str) -> str:
    for line in reversed(str(body or "").splitlines()):
        if not line.strip():
            continue
        indent_match = re.match(r"\s*", line)
        return indent_match.group(0) if indent_match else fallback
    return fallback


def _typescript_receiver_for_member_access(line: str, member: str) -> str:
    match = re.search(rf"\b(?P<receiver>[A-Za-z_$][\w$]*!?)\s*\.\s*{re.escape(member)}\b", str(line or ""))
    return str(match.group("receiver") if match else "")


def _typescript_existing_member_names_for_type(
    *,
    base_files: Mapping[str, str],
    type_name: str,
) -> set[str]:
    members: set[str] = set()
    escaped = re.escape(type_name)
    for text in base_files.values():
        match = re.search(
            rf"(?:interface\s+{escaped}\b[^{{]*{{|class\s+{escaped}\b[^{{]*{{)(?P<body>[\s\S]*?)^\s*}}",
            text,
            re.MULTILINE,
        )
        if not match:
            continue
        for member_match in re.finditer(
            r"^\s*(?:(?:public|private|protected|readonly|static|abstract)\s+)*"
            r"(?P<name>[A-Za-z_$][\w$]*)\s*(?:[:=(])",
            match.group("body"),
            re.MULTILINE,
        ):
            members.add(str(member_match.group("name") or ""))
    return members


def _typescript_method_delegate_specs(
    *,
    content: str,
    type_name: str,
    members: Sequence[str],
) -> dict[str, dict[str, str]]:
    specs: dict[str, dict[str, str]] = {}
    escaped_type = re.escape(type_name)
    for member in members:
        if not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        match = re.search(
            rf"(?m)^export\s+function\s+"
            rf"(?P<delegate>{re.escape(member)}[A-Za-z0-9_$]*)\s*"
            rf"\(\s*(?P<self>[A-Za-z_$][\w$]*)\s*:\s*{escaped_type}\s*,\s*(?P<params>[^)]*)\)\s*:\s*{escaped_type}\s*{{",
            content,
        )
        if not match:
            continue
        params = _typescript_normalized_parameter_list(str(match.group("params") or ""))
        if not params:
            continue
        specs[member] = {
            "delegate": str(match.group("delegate") or ""),
            "params": params,
            "args": _typescript_parameter_argument_list(params),
        }
    return specs


def _typescript_normalized_parameter_list(params: str) -> str:
    normalized: list[str] = []
    for raw in str(params or "").split(","):
        part = raw.strip()
        if not part:
            continue
        if "=" in part:
            part = part.split("=", 1)[0].strip()
        if not re.match(r"^[A-Za-z_$][\w$]*\??\s*:", part):
            return ""
        normalized.append(part)
    return ", ".join(normalized)


def _typescript_parameter_argument_list(params: str) -> str:
    args: list[str] = []
    for raw in str(params or "").split(","):
        name = raw.strip().split(":", 1)[0].strip().rstrip("?")
        if not _TS_IDENTIFIER_RE.fullmatch(name):
            return ""
        args.append(name)
    return ", ".join(args)


def _typescript_interface_method_return_operations(
    *,
    path: str,
    content: str,
    type_name: str,
    method_specs: Mapping[str, Mapping[str, str]],
) -> tuple[RepairOperation, ...]:
    operations: list[RepairOperation] = []
    escaped_type = re.escape(type_name)
    interface_match = re.search(rf"(?m)^export\s+interface\s+{escaped_type}\b[^\n]*{{", content)
    if not interface_match:
        return ()
    interface_end = content.find("\n}", interface_match.end())
    if interface_end < 0:
        return ()
    interface_body = content[interface_match.end() : interface_end]
    body_start = interface_match.end()
    for member, spec in method_specs.items():
        member_match = re.search(
            rf"(?m)^(?P<indent>\s*){re.escape(member)}\s*\([^;\n]*\)\s*:\s*unknown\s*;",
            interface_body,
        )
        if not member_match:
            continue
        indent = str(member_match.group("indent") or "  ")
        replacement = f"{indent}{member}({spec['params']}): {type_name};"
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=body_start + member_match.start(),
                span_end=body_start + member_match.end(),
                expected=str(member_match.group(0) or ""),
                replacement=replacement,
                before_hash=sha256_text(content),
                metadata={
                    "repair_kind": "typescript_interface_method_return",
                    "type": type_name,
                    "member": member,
                    "delegate": str(spec.get("delegate") or ""),
                },
            )
        )
    return tuple(operations)


def _typescript_object_literal_method_implementation_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    member_lines: Mapping[str, int],
    method_specs: Mapping[str, Mapping[str, str]],
) -> RepairOperation | None:
    line_number = max((line for line in member_lines.values() if line > 0), default=0)
    line_offsets = _text_line_start_offsets(content)
    search_end = line_offsets[line_number - 1] if 0 < line_number <= len(line_offsets) else len(content)
    return_match = None
    for match in re.finditer(r"return\s+Object\.freeze\s*\(\s*{", content[:search_end]):
        return_match = match
    if return_match is None:
        for match in re.finditer(r"return\s+Object\.freeze\s*\(\s*{", content):
            if match.start() >= search_end:
                return_match = match
                break
    if return_match is None:
        return None
    close_match = re.search(r"(?m)^(?P<indent>\s*)}\s*\)\s*;", content[return_match.end() :])
    if close_match is None:
        return None
    span_start = return_match.end() + close_match.start()
    object_body = content[return_match.end() : span_start]
    declarations: list[str] = []
    close_indent = str(close_match.group("indent") or "")
    entry_indent = f"{close_indent}  "
    body_indent = f"{entry_indent}  "
    for member, spec in method_specs.items():
        if re.search(rf"(?m)^\s*{re.escape(member)}\s*[:(]", object_body):
            continue
        args = str(spec.get("args") or "")
        delegate = str(spec.get("delegate") or "")
        params = str(spec.get("params") or "")
        if not args or not delegate:
            continue
        declarations.append(
            f"{entry_indent}{member}({params}): {type_name} {{\n"
            f"{body_indent}return {delegate}(this, {args});\n"
            f"{entry_indent}}},\n"
        )
    if not declarations:
        return None
    context_start = max(return_match.start(), span_start - 240)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_start,
        expected="",
        replacement="".join(declarations),
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_object_literal_missing_member_implementation",
            "type": type_name,
            "members": tuple(method_specs),
            "expected_context_before": content[context_start:span_start],
            "expected_context_after": content[span_start : span_start + 8],
        },
    )


def _typescript_object_literal_required_properties_operation(
    *,
    base_files: Mapping[str, str],
    path: str,
    content: str,
    type_name: str,
    member_lines: Mapping[str, int],
) -> RepairOperation | None:
    member_types = _typescript_declared_member_types_for_type(base_files=base_files, type_name=type_name)
    if not member_types:
        return None
    object_bounds = _typescript_object_literal_bounds_near_line(
        content=content,
        line_number=max((line for line in member_lines.values() if line > 0), default=0),
    )
    if object_bounds is None:
        return None
    body_start, body_end, close_indent = object_bounds
    object_body = content[body_start:body_end]
    entry_indent = f"{close_indent}  "
    declarations: list[str] = []
    repaired_members: list[str] = []
    for member in sorted(member_lines):
        if not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        if re.search(rf"(?m)^\s*{re.escape(member)}\s*:", object_body):
            continue
        default_value = _typescript_default_value_for_required_property_type(member_types.get(member, ""))
        if not default_value:
            continue
        declarations.append(f"{entry_indent}{member}: {default_value},\n")
        repaired_members.append(member)
    if not declarations:
        return None
    context_start = max(0, body_end - 240)
    span_end = body_end + len(close_indent)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=body_end,
        span_end=span_end,
        expected=close_indent,
        replacement="".join(declarations) + close_indent,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_object_literal_required_properties",
            "type": type_name,
            "members": tuple(repaired_members),
            "expected_context_before": content[context_start:body_end],
            "expected_context_after": content[span_end : span_end + 8],
        },
    )


def _typescript_declared_member_types_for_type(*, base_files: Mapping[str, str], type_name: str) -> dict[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return {}
    escaped = re.escape(type_name)
    member_types: dict[str, str] = {}
    for content in base_files.values():
        declaration_match = re.search(
            rf"(?m)^(?:export\s+)?(?:interface|class)\s+{escaped}\b[^\n]*{{",
            str(content or ""),
        )
        if not declaration_match:
            continue
        declaration_end = str(content or "").find("\n}", declaration_match.end())
        if declaration_end < 0:
            continue
        body = str(content or "")[declaration_match.end() : declaration_end]
        for member_match in re.finditer(
            r"(?m)^\s*(?:(?:public|private|protected|readonly|static|abstract)\s+)*"
            r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?P<type>[^;=\n]+)\s*(?:;|=)",
            body,
        ):
            name = str(member_match.group("name") or "")
            ts_type = str(member_match.group("type") or "").strip()
            if _TS_IDENTIFIER_RE.fullmatch(name) and ts_type:
                member_types[name] = ts_type
    return member_types


def _typescript_object_literal_bounds_near_line(
    *,
    content: str,
    line_number: int,
) -> tuple[int, int, str] | None:
    text = str(content or "")
    line_offsets = _text_line_start_offsets(text)
    search_start = line_offsets[line_number - 1] if 0 < line_number <= len(line_offsets) else 0
    candidates: list[re.Match[str]] = []
    for match in re.finditer(r"\breturn\s+(?:Object\.freeze\s*\(\s*)?{", text):
        if match.start() <= search_start + 240:
            candidates.append(match)
    for match in re.finditer(r"=\s*{", text):
        if abs(match.start() - search_start) <= 240:
            candidates.append(match)
    for match in sorted(candidates, key=lambda item: abs(item.start() - search_start)):
        open_brace = text.find("{", match.start(), match.end())
        if open_brace < 0:
            continue
        close_brace = _typescript_matching_brace_index(text, open_brace)
        if close_brace <= open_brace:
            continue
        close_line_start = text.rfind("\n", 0, close_brace) + 1
        close_indent = text[close_line_start:close_brace]
        if close_indent.strip():
            close_indent = ""
        return (open_brace + 1, close_line_start, close_indent)
    return None


def _typescript_matching_brace_index(text: str, open_brace: int) -> int:
    if open_brace < 0 or open_brace >= len(text) or text[open_brace] != "{":
        return -1
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_brace, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _typescript_line_invokes_constructor(line: str, class_name: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return False
    return bool(re.search(rf"\bnew\s+{re.escape(class_name)}\s*\(", str(line or "")))


def _typescript_exported_private_constructor_modifier_span(text: str, class_name: str) -> tuple[int, int, int] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return None
    class_pattern = re.compile(
        rf"\bexport\s+(?:default\s+)?class\s+{re.escape(class_name)}\b[^\{{]*\{{",
        re.MULTILINE,
    )
    line_offsets = _text_line_start_offsets(text)
    for class_match in class_pattern.finditer(text):
        open_brace = str(text or "").find("{", class_match.start(), class_match.end())
        if open_brace < 0:
            continue
        close_brace = _typescript_matching_brace_index(text, open_brace)
        if close_brace <= open_brace:
            continue
        body = text[open_brace + 1 : close_brace]
        constructor_match = re.search(r"(?m)^(?P<indent>\s*)private\s+constructor\s*\(", body)
        if constructor_match is None:
            continue
        start = open_brace + 1 + constructor_match.start() + len(str(constructor_match.group("indent") or ""))
        end = start + len("private ")
        line_index = _line_index_for_offset(line_offsets, start)
        if line_index < 0:
            continue
        return line_index, start, end
    return None


def _typescript_safe_structural_member_type(ts_type: str) -> bool:
    normalized = " ".join(str(ts_type or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    if lowered in {"unknown", "any", "string", "number", "boolean", "object"}:
        return True
    if re.fullmatch(r"readonlyarray\s*<\s*unknown\s*>", lowered):
        return True
    if re.fullmatch(r"record\s*<\s*(?:string|number)\s*,\s*unknown\s*>", lowered):
        return True
    if re.fullmatch(r"\{[\w\s:;,<>|\"'[\].-]*}", normalized):
        return True
    if re.fullmatch(r"(?:readonly\s+)?[A-Za-z_$][A-Za-z0-9_$]*(?:\[\])+", normalized):
        return True
    return bool(re.fullmatch(r"(?:\"[^\"]+\"|'[^']+')(?:\s*\|\s*(?:\"[^\"]+\"|'[^']+'))*", normalized))


def _typescript_default_value_for_required_property_type(ts_type: str) -> str:
    normalized = " ".join(str(ts_type or "").strip().split())
    lowered = normalized.lower()
    if not normalized:
        return ""
    if "null" in {part.strip() for part in lowered.split("|")}:
        return "null"
    if lowered in {"unknown", "any", "object"}:
        return "{}"
    if "[]" in lowered or lowered.startswith("readonlyarray"):
        return "[]"
    if lowered.startswith("record<") or (normalized.startswith("{") and normalized.endswith("}")):
        return "{}"
    literal_match = re.match(r"^(?:'([^']+)'|\"([^\"]+)\")", normalized)
    if literal_match:
        value = str(literal_match.group(1) or literal_match.group(2) or "")
        return json.dumps(value)
    default_value = _typescript_default_value_for_type(normalized)
    return default_value if default_value != "undefined" else ""


def _text_line_start_offsets(text: str) -> list[int]:
    offsets = [0]
    for match in re.finditer(r"\n", str(text or "")):
        offsets.append(match.end())
    return offsets


def _line_index_for_offset(offsets: Sequence[int], offset: int) -> int:
    if offset < 0:
        return -1
    for index, start in enumerate(offsets):
        next_start = offsets[index + 1] if index + 1 < len(offsets) else None
        if offset >= start and (next_start is None or offset < next_start):
            return index
    return -1


def _typescript_member_alias_replacement(*, receiver: str, missing_member: str, existing_members: set[str]) -> str:
    if missing_member == "checks" and "results" in existing_members:
        return f"{receiver}.results"
    if missing_member == "failures" and "results" in existing_members:
        return f"{receiver}.results.filter((result) => !result.ok)"
    if missing_member in {"x", "y"} and "position" in existing_members:
        return f"{receiver}.position.{missing_member}"
    if missing_member == "brightness" and "intensity" in existing_members:
        return f"{receiver}.intensity"
    if missing_member == "glow" and "brightness" in existing_members:
        return f"{receiver}.brightness"
    if missing_member == "size":
        if "petalRadius" in existing_members:
            return f"{receiver}.petalRadius"
        if "radius" in existing_members:
            return f"{receiver}.radius"
    if missing_member == "color":
        if {"hue", "saturation", "lightness"}.issubset(existing_members):
            return (
                f"`hsl(${{{receiver}.hue}}, "
                f"${{Math.round({receiver}.saturation * 100)}}%, "
                f"${{Math.round({receiver}.lightness * 100)}}%)`"
            )
        if "hue" in existing_members:
            return f"`hsl(${{{receiver}.hue}}, 70%, 62%)`"
    if missing_member != "id" and missing_member.endswith("Id") and "id" in existing_members:
        return f"{receiver}.id"
    return ""


def _line_text_replace_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    replacement: str,
    metadata: Mapping[str, object],
) -> RepairOperation:
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=offsets[line_index],
        span_end=offsets[line_index + 1],
        expected=lines[line_index],
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata=dict(metadata),
    )


def _parse_typescript_missing_export_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for pattern in (
            _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
            _TS_NO_EXPORTED_MEMBER_ERROR_RE,
            _TS_NO_EXPORTED_MEMBER_NAMED_ERROR_RE,
            _TS_DECLARES_LOCALLY_NOT_EXPORTED_ERROR_RE,
        ):
            for match in pattern.finditer(text):
                module = _normalize_typescript_module_ref(match.group("module"))
                parsed.append(
                    {
                        "file": _normalize_repair_path(
                            str(match.groupdict().get("path") or match.groupdict().get("file") or "")
                        ),
                        "module": module,
                        "symbol": str(match.group("symbol") or "").strip(),
                        "suggestion": str(match.groupdict().get("suggestion") or "").strip(),
                    }
                )
    return [item for item in parsed if item["file"] and item["module"] and item["symbol"]]


def _parse_typescript_value_used_as_type_errors(
    diagnostics: Sequence[RepairDiagnostic],
) -> list[dict[str, object]]:
    parsed: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        matched = False
        for match in _TS_VALUE_USED_AS_TYPE_RAW_RE.finditer(text):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "col": str(match.group("col") or ""),
                    "symbol": str(match.group("symbol") or "").strip(),
                    "diagnostic": diagnostic,
                }
            )
            matched = True
        if matched:
            continue
        if diagnostic.code.lower() != "typescript_ts2749":
            continue
        message_match = _TS_VALUE_USED_AS_TYPE_MESSAGE_RE.search(text)
        if not message_match:
            continue
        parsed.append(
            {
                "file": _normalize_repair_path(str(diagnostic.path or "")),
                "line": "",
                "col": "",
                "symbol": str(message_match.group("symbol") or "").strip(),
                "diagnostic": diagnostic,
            }
        )
    return [
        item
        for item in parsed
        if str(item.get("file") or "") and _TS_IDENTIFIER_RE.fullmatch(str(item.get("symbol") or ""))
    ]


def _missing_export_operation(
    *,
    base_files: Mapping[str, str],
    item: Mapping[str, str],
) -> tuple[RepairOperation | None, dict[str, str]]:
    importer = str(item.get("file") or "")
    module_ref = str(item.get("module") or "")
    symbol = str(item.get("symbol") or "")
    exporter = _resolve_relative_ts_module_path(importer, module_ref, base_files)
    if not exporter or not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return None, {}
    original = str(base_files.get(exporter) or "")
    if _typescript_module_exports_symbol(original, symbol):
        return None, {}
    importer_text = str(base_files.get(importer) or "")
    suggestion = str(item.get("suggestion") or "").strip()
    if not suggestion:
        suggestion = _find_typescript_similar_runtime_declaration(original, symbol)
    if suggestion:
        declaration_kind, declaration = _build_typescript_suggested_export_alias_declaration(
            symbol=symbol,
            suggestion=suggestion,
            importer_text=importer_text,
            module_text=original,
        )
        operation = _append_typescript_missing_export_declaration_operation(
            path=exporter,
            original=original,
            declaration=declaration,
            symbol=symbol,
            declaration_kind=declaration_kind,
        )
        if operation is not None:
            return operation, {"file": exporter, "symbol": symbol, "kind": declaration_kind}
        return None, {
            "file": exporter,
            "symbol": symbol,
            "kind": "unsafe_alias_rejected",
            "blocked_reason": "missing_export_alias_candidate_not_type_compatible",
        }
    exported, declaration_kind = _reexport_imported_typescript_symbol(original, symbol)
    if exported == original:
        exported = _export_existing_typescript_declaration(original, symbol)
        declaration_kind = "export_existing"
    if exported == original:
        declaration_kind, declaration = _build_typescript_missing_export_declaration(
            symbol=symbol,
            importer_text=importer_text,
        )
        operation = _append_typescript_missing_export_declaration_operation(
            path=exporter,
            original=original,
            declaration=declaration,
            symbol=symbol,
            declaration_kind=declaration_kind,
        )
        if operation is not None:
            return operation, {"file": exporter, "symbol": symbol, "kind": declaration_kind}
        return None, {
            "file": exporter,
            "symbol": symbol,
            "kind": "interface_contract_required",
            "blocked_reason": "missing_export_declaration_not_found",
        }
    ops = _text_replace_operations_from_repair(
        path=exporter,
        original=original,
        repaired=exported,
        metadata={
            "repair_kind": "typescript_missing_export",
            "symbol": symbol,
            "declaration_kind": declaration_kind,
        },
    )
    return (ops[0], {"file": exporter, "symbol": symbol, "kind": declaration_kind}) if len(ops) == 1 else (None, {})


def _build_typescript_missing_export_declaration(*, symbol: str, importer_text: str) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return "", ""
    if _typescript_symbol_is_constructed(importer_text, symbol):
        if not _typescript_symbol_has_named_constructor_binding(importer_text, symbol):
            return "", ""
        return "class", _build_typescript_missing_export_class_declaration(symbol=symbol, importer_text=importer_text)
    if _typescript_symbol_is_called(importer_text, symbol):
        return "function", f"export function {symbol}(..._args: unknown[]): any {{\n  return undefined;\n}}"
    if symbol[:1].isupper():
        return "type", f"export type {symbol} = any;"
    return "const", f"export const {symbol}: unknown = undefined;"


def _build_typescript_suggested_export_alias_declaration(
    *,
    symbol: str,
    suggestion: str,
    importer_text: str,
    module_text: str,
) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol) or not _TS_IDENTIFIER_RE.fullmatch(suggestion):
        return "", ""
    if symbol == suggestion or not _typescript_module_declares_symbol(module_text, suggestion):
        return "", ""
    suggestion_kind = _typescript_module_declared_symbol_kind(module_text, suggestion)
    if _typescript_symbol_is_constructed(importer_text, symbol):
        if suggestion_kind == "class":
            return "runtime_alias", f"export {{ {suggestion} as {symbol} }};"
        return "", ""
    if _typescript_symbol_is_called(importer_text, symbol):
        if suggestion_kind in {"const", "function", "let", "var"}:
            return "runtime_alias", f"export {{ {suggestion} as {symbol} }};"
        return "", ""
    if suggestion_kind in {"class", "enum", "interface", "type"}:
        return "type_alias", f"export type {symbol} = {suggestion};"
    if suggestion_kind in {"const", "let", "var", "function"}:
        return "runtime_alias", f"export {{ {suggestion} as {symbol} }};"
    return "", ""


def _typescript_symbol_is_constructed(text: str, symbol: str) -> bool:
    return bool(re.search(rf"\bnew\s+{re.escape(symbol)}\s*\(", str(text or "")))


def _typescript_symbol_is_called(text: str, symbol: str) -> bool:
    token = str(text or "")
    call_re = re.compile(rf"(?<!new\s)\b{re.escape(symbol)}\s*\(")
    return bool(call_re.search(token))


def _typescript_symbol_has_named_constructor_binding(text: str, symbol: str) -> bool:
    token = str(text or "")
    escaped = re.escape(symbol)
    return bool(
        re.search(
            rf"\b(?:const|let|var)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*new\s+{escaped}\s*\(",
            token,
        )
        or re.search(
            rf"\b(?:public|private|protected)?\s*(?:readonly\s+)?[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*new\s+{escaped}\s*\(",
            token,
        )
    )


def _build_typescript_missing_export_class_declaration(*, symbol: str, importer_text: str) -> str:
    methods = _typescript_methods_used_on_constructed_symbol(importer_text, symbol)
    lines = [
        f"export class {symbol} {{",
        "  public constructor(..._args: unknown[]) {}",
    ]
    for method in methods:
        return_type = "string" if method in {"report", "render", "toString"} else "any"
        return_value = f'"{symbol} ready"' if return_type == "string" else "undefined"
        lines.extend(
            [
                f"  public {method}(..._args: unknown[]): {return_type} {{",
                f"    return {return_value};",
                "  }",
            ]
        )
    lines.append("}")
    return "\n".join(lines)


def _typescript_methods_used_on_constructed_symbol(text: str, symbol: str) -> list[str]:
    token = str(text or "")
    variables: list[str] = []
    constructed_var_re = re.compile(
        rf"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*new\s+{re.escape(symbol)}\s*\("
    )
    for match in constructed_var_re.finditer(token):
        variables.append(str(match.group("name") or ""))

    methods: list[str] = []
    for variable in _dedupe_preserve_order([name for name in variables if name]):
        for match in re.finditer(rf"\b{re.escape(variable)}\.(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", token):
            methods.append(str(match.group("method") or ""))
    direct_re = re.compile(rf"\bnew\s+{re.escape(symbol)}\s*\([^)]*\)\s*\.\s*(?P<method>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
    for match in direct_re.finditer(token):
        methods.append(str(match.group("method") or ""))
    return _dedupe_preserve_order([method for method in methods if method and method != "constructor"])


def _typescript_module_declares_symbol(module_text: str, symbol: str) -> bool:
    return bool(_typescript_module_declared_symbol_kind(module_text, symbol))


def _typescript_module_declared_symbol_kind(module_text: str, symbol: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return ""
    escaped = re.escape(symbol)
    declaration_re = re.compile(
        rf"^(?:export\s+)?(?:abstract\s+)?(?:async\s+)?"
        rf"(?P<kind>enum|class|interface|type|const|let|var|function)\s+{escaped}\b",
        re.MULTILINE,
    )
    match = declaration_re.search(module_text)
    return str(match.group("kind") or "").strip() if match else ""


def _find_typescript_similar_runtime_declaration(module_text: str, symbol: str) -> str:
    wanted = _normalize_typescript_identifier_for_similarity(symbol)
    if not wanted:
        return ""
    declaration_re = re.compile(
        r"^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|enum)\s+"
        r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\b",
        re.MULTILINE,
    )
    best = ""
    best_score = 0
    for match in declaration_re.finditer(module_text):
        name = str(match.group("name") or "").strip()
        if name == symbol:
            continue
        candidate = _normalize_typescript_identifier_for_similarity(name)
        if not candidate:
            continue
        score = 0
        if wanted.startswith(candidate):
            score = len(candidate)
        elif candidate.startswith(wanted):
            score = len(wanted)
        if score > best_score and score >= min(4, len(wanted)):
            best = name
            best_score = score
    return best


def _normalize_typescript_identifier_for_similarity(symbol: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "", str(symbol or "")).lower()
    for suffix in ("checks", "check", "results", "result", "items", "item"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix) + 2:
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _typescript_declared_type_kind(*, base_files: Mapping[str, str], type_name: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name):
        return ""
    escaped = re.escape(type_name)
    for content in base_files.values():
        match = re.search(rf"(?P<kind>interface|class)\s+{escaped}\b[^{{]*{{", str(content or ""))
        if match:
            return str(match.group("kind") or "")
    return ""


def _normalize_typescript_module_ref(raw: object) -> str:
    value = str(raw or "").strip().rstrip(".")
    previous = None
    while value != previous:
        previous = value
        value = value.strip().strip("'\"`").strip()
    return value.rstrip(".")


def _reexport_imported_typescript_symbol(text: str, symbol: str) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return text, "interface_contract_required"
    for match in _TS_REEXPORTABLE_NAMED_IMPORT_RE.finditer(str(text or "")):
        specifiers = _typescript_import_specifiers(match.group("names"))
        imported = specifiers.get(symbol)
        if imported is None:
            continue
        declaration_kind = (
            "export_type_reexport" if match.group("type_only") or imported == "type" else "export_reexport"
        )
        export_prefix = "export type" if declaration_kind == "export_type_reexport" else "export"
        quote = str(match.group("quote") or '"')
        module_ref = str(match.group("module") or "")
        declaration = f"{export_prefix} {{ {symbol} }} from {quote}{module_ref}{quote};"
        if declaration in text:
            return text, declaration_kind
        insert_at = match.end()
        separator = "\n" if text[insert_at : insert_at + 1] == "\n" else "\n\n"
        return f"{text[:insert_at]}{separator}{declaration}{text[insert_at:]}", declaration_kind
    return text, "interface_contract_required"


def _typescript_import_specifiers(raw: str) -> dict[str, str]:
    specifiers: dict[str, str] = {}
    for item in str(raw or "").replace("\n", " ").split(","):
        token = item.strip()
        if not token:
            continue
        kind = "value"
        if token.startswith("type "):
            kind = "type"
            token = token[5:].strip()
        imported = token.split(" as ", 1)[0].strip()
        if _TS_IDENTIFIER_RE.fullmatch(imported):
            specifiers[imported] = kind
    return specifiers


def _append_typescript_missing_export_declaration_operation(
    *,
    path: str,
    original: str,
    declaration: str,
    symbol: str,
    declaration_kind: str,
) -> RepairOperation | None:
    token = str(original or "")
    declaration_text = "\n\n" + str(declaration or "").rstrip() + "\n"
    if not declaration_text.strip():
        return None
    if token.endswith("\n"):
        span_start = len(token) - 1
        span_end = len(token)
        expected = "\n"
        replacement = declaration_text
    elif token:
        span_start = len(token) - 1
        span_end = len(token)
        expected = token[-1]
        replacement = f"{token[-1]}{declaration_text}"
    else:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(token),
        metadata={
            "repair_kind": "typescript_missing_export",
            "symbol": symbol,
            "declaration_kind": declaration_kind,
            "append_declaration": True,
        },
    )


def _apply_single_text_operation(content: str, operation: RepairOperation) -> str:
    if operation.span_start is None or operation.span_end is None:
        return content
    return content[: operation.span_start] + str(operation.replacement or "") + content[operation.span_end :]


def _typescript_line_at(text: str, line_number: int) -> str:
    lines = str(text or "").splitlines()
    if line_number <= 0 or line_number > len(lines):
        return ""
    return lines[line_number - 1]


def _typescript_member_usage_is_call(text: str, line_number: int, member: str) -> bool:
    return bool(re.search(rf"\.\s*{re.escape(member)}\s*\(", _typescript_line_at(text, line_number)))


def _typescript_missing_member_declared_type(text: str, line_number: int, member: str, *, member_is_call: bool) -> str:
    if member_is_call:
        return "number"
    return _typescript_usage_compatible_member_type(_typescript_line_at(text, line_number), member) or "unknown"


def _typescript_usage_compatible_member_type(usage_line: str, member: str) -> str:
    if not _TS_IDENTIFIER_RE.fullmatch(member):
        return ""
    escaped = re.escape(member)
    line = str(usage_line or "")
    if _typescript_member_name_suggests_string(member) and (
        re.search(rf"\.\s*{escaped}\s*(?:={2,3}|!==?)", line)
        or re.search(rf"(?:={2,3}|!==?)\s*[^;\n]*\.\s*{escaped}\b", line)
        or re.search(rf"\.\s*{escaped}\s*\.\s*(?:length|trim|toLowerCase|toUpperCase|includes)\b", line)
    ):
        return "string"
    if _typescript_member_name_strongly_suggests_string(member) and re.search(rf"=\s*[^;\n]*\.\s*{escaped}\b", line):
        return "string"
    if _typescript_member_name_suggests_number(member) and re.search(
        rf"(?:\.\s*{escaped}\b\s*(?:[*/%+\-]|[<>]=?)|(?:[*/%+\-]|[<>]=?)\s*[^;\n]*\.\s*{escaped}\b)",
        line,
    ):
        return "number"
    if re.search(rf"\.\s*{escaped}\s*\[", line):
        return "Record<string, unknown>"
    if re.search(rf"\.\s*{escaped}\s*\.\s*(?:length|map|filter|reduce|forEach|some|every|find)\b", line):
        return "ReadonlyArray<unknown>"
    if re.search(rf"\.\s*{escaped}\s*\.\s*(?:toFixed|toExponential|toPrecision)\s*\(", line):
        return "number"
    if re.search(rf"\.\s*{escaped}\s*\.\s*(?:trim|toLowerCase|toUpperCase|includes|startsWith|endsWith)\s*\(", line):
        return "string"
    return ""


def _typescript_member_name_suggests_string(member: str) -> bool:
    lowered = str(member or "").lower()
    return lowered in {
        "id",
        "key",
        "name",
        "title",
        "label",
        "slug",
        "type",
        "status",
        "color",
        "colour",
    } or lowered.endswith(("id", "key", "name", "title", "label", "slug", "type", "status", "color", "colour"))


def _typescript_member_name_strongly_suggests_string(member: str) -> bool:
    lowered = str(member or "").lower()
    return lowered in {"color", "colour"} or lowered.endswith(("color", "colour"))


def _typescript_member_name_suggests_number(member: str) -> bool:
    lowered = str(member or "").lower()
    return lowered in {
        "x",
        "y",
        "z",
        "r",
        "g",
        "b",
        "width",
        "height",
        "size",
        "radius",
        "count",
        "total",
        "amount",
        "quantity",
        "price",
        "score",
        "rating",
        "brightness",
        "intensity",
        "opacity",
        "alpha",
    } or lowered.endswith(
        (
            "x",
            "y",
            "z",
            "width",
            "height",
            "size",
            "radius",
            "count",
            "total",
            "amount",
            "quantity",
            "price",
            "score",
            "rating",
            "brightness",
            "intensity",
            "opacity",
            "alpha",
        )
    )


def _typescript_unknown_member_receiver_type(
    *,
    base_files: Mapping[str, str],
    usage_path: str,
    receiver: str,
) -> str:
    usage_text = str(base_files.get(usage_path) or "")
    if not usage_text or not _TS_IDENTIFIER_RE.fullmatch(receiver):
        return ""
    explicit_match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(receiver)}\s*:\s*(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\b",
        usage_text,
    )
    if explicit_match:
        return str(explicit_match.group("type") or "")
    initializer_match = re.search(
        rf"\b(?:const|let|var)\s+{re.escape(receiver)}\s*=\s*(?P<callee>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(",
        usage_text,
    )
    if not initializer_match:
        return ""
    callee = str(initializer_match.group("callee") or "")
    if not _TS_IDENTIFIER_RE.fullmatch(callee):
        return ""
    for content in base_files.values():
        return_type_match = re.search(
            rf"\b(?:export\s+)?function\s+{re.escape(callee)}\s*\([^)]*\)\s*:\s*"
            rf"(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\b",
            str(content or ""),
        )
        if return_type_match:
            return str(return_type_match.group("type") or "")
    return ""


def _typescript_unknown_member_type_operation(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    member: str,
    replacement_type: str,
) -> RepairOperation | None:
    if not _TS_IDENTIFIER_RE.fullmatch(type_name) or not _TS_IDENTIFIER_RE.fullmatch(member):
        return None
    if not _typescript_safe_structural_member_type(replacement_type):
        return None
    escaped_type = re.escape(type_name)
    escaped_member = re.escape(member)
    for path, content in base_files.items():
        declaration_match = re.search(
            rf"(?m)^(?:export\s+)?(?P<kind>interface|class)\s+{escaped_type}\b[^\n]*{{",
            content,
        )
        if not declaration_match:
            continue
        declaration_end = content.find("\n}", declaration_match.end())
        if declaration_end < 0:
            continue
        body = content[declaration_match.end() : declaration_end]
        member_match = re.search(
            rf"(?m)^(?P<indent>\s*)(?P<prefix>(?:(?:public|private|protected|readonly|static|abstract)\s+)*)"
            rf"{escaped_member}\s*:\s*unknown\s*;",
            body,
        )
        if not member_match:
            continue
        prefix = str(member_match.group("prefix") or "")
        replacement = f"{member_match.group('indent')}{prefix}{member}: {replacement_type};"
        span_start = declaration_match.end() + member_match.start()
        span_end = declaration_match.end() + member_match.end()
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=span_start,
            span_end=span_end,
            expected=str(member_match.group(0) or ""),
            replacement=replacement,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_unknown_member_access",
                "type": type_name,
                "member": member,
                "replacement_type": replacement_type,
            },
        )
    return None


def _add_typescript_member_operation(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    member: str,
    member_is_call: bool,
) -> RepairOperation | None:
    escaped = re.escape(type_name)
    for path, content in base_files.items():
        match = re.search(rf"(interface\s+{escaped}\b[^{{]*{{|class\s+{escaped}\b[^{{]*{{)", content)
        if not match:
            continue
        insert_at = content.find("\n}", match.end())
        if insert_at < 0:
            continue
        declaration = f"\n  {member}(..._args: unknown[]): unknown;" if member_is_call else f"\n  {member}: unknown;"
        context_start = max(0, match.start())
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=insert_at,
            span_end=insert_at,
            expected="",
            replacement=declaration,
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_missing_member",
                "type": type_name,
                "member": member,
                "expected_context_before": content[context_start:insert_at],
                "expected_context_after": content[insert_at : insert_at + 2],
            },
        )
    return None


def _add_typescript_members_operation(
    *,
    base_files: Mapping[str, str],
    type_name: str,
    members: Sequence[tuple[str, bool, str] | tuple[str, bool, str, bool]],
) -> RepairOperation | None:
    escaped = re.escape(type_name)
    for path, content in base_files.items():
        match = re.search(rf"(?P<kind>interface|class)\s+{escaped}\b[^{{]*{{", content)
        if not match:
            continue
        insert_at = content.find("\n}", match.end())
        if insert_at < 0:
            insert_at = _typescript_matching_brace_index(content, match.end() - 1)
        if insert_at < 0:
            continue
        existing = _typescript_existing_member_names_for_type(base_files={path: content}, type_name=type_name)
        declarations: list[str] = []
        is_class = str(match.group("kind") or "") == "class"
        class_text = content[match.start() : insert_at]
        for member_spec in members:
            member, member_is_call, declared_type = member_spec[:3]
            static_context = len(member_spec) > 3 and bool(member_spec[3])
            if member in existing or not _TS_IDENTIFIER_RE.fullmatch(member):
                continue
            value_type = declared_type if _typescript_safe_structural_member_type(declared_type) else "unknown"
            if is_class and static_context and member_is_call:
                constructor_args = _typescript_constructor_default_arguments(class_text)
                declarations.append(
                    f"\n  public static {member}(..._args: unknown[]): {type_name} {{"
                    f"\n    return new {type_name}({constructor_args});\n  }}"
                )
            elif is_class and static_context:
                constructor_args = _typescript_constructor_default_arguments(class_text)
                declarations.append(
                    f"\n  public static readonly {member}: {type_name} = new {type_name}({constructor_args});"
                )
            elif is_class and member_is_call:
                return_type = value_type if value_type not in {"unknown", "any"} else "number"
                declarations.append(
                    f"\n  public {member}(..._args: unknown[]): {return_type} {{"
                    f"\n    return {_typescript_default_value_for_required_property_type(return_type)};\n  }}"
                )
            elif is_class:
                declarations.append(
                    f"\n  public {member}: {value_type} = {_typescript_default_value_for_required_property_type(value_type)};"
                )
            elif member_is_call:
                return_type = value_type if value_type not in {"unknown", "any"} else "number"
                declarations.append(f"\n  {member}(..._args: unknown[]): {return_type};")
            else:
                declarations.append(f"\n  {member}: {value_type};")
        if not declarations:
            return None
        context_start = max(0, match.start())
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=insert_at,
            span_end=insert_at,
            expected="",
            replacement="".join(declarations),
            before_hash=sha256_text(content),
            metadata={
                "repair_kind": "typescript_missing_member",
                "type": type_name,
                "members": tuple(member_spec[0] for member_spec in members),
                "batched_same_type_members": True,
                "expected_context_before": content[context_start:insert_at],
                "expected_context_after": content[insert_at : insert_at + 2],
            },
        )
    return None


def _typescript_constructor_default_arguments(class_text: str) -> str:
    match = re.search(r"\bconstructor\s*\((?P<params>[^)]*)\)", str(class_text or ""), re.DOTALL)
    if not match:
        return ""
    defaults: list[str] = []
    for raw_param in str(match.group("params") or "").split(","):
        param = raw_param.strip()
        if not param:
            continue
        type_match = re.search(r":\s*(?P<type>[^=,]+)", param)
        param_type = str(type_match.group("type") or "unknown").strip() if type_match else "unknown"
        default_value = _typescript_default_value_for_required_property_type(param_type)
        defaults.append(default_value if default_value else "undefined")
    return ", ".join(defaults)


def _resolve_relative_ts_module_path(importer_path: str, module_ref: str, base_files: Mapping[str, str]) -> str:
    if not module_ref.startswith("."):
        return ""
    base_dir = posixpath.dirname(importer_path)
    raw = posixpath.normpath(posixpath.join(base_dir, module_ref))
    raw_root, raw_ext = posixpath.splitext(raw)
    candidates = [raw, f"{raw}.ts", f"{raw}.tsx", posixpath.join(raw, "index.ts"), posixpath.join(raw, "index.tsx")]
    if raw_ext.lower() in {".js", ".jsx", ".mjs", ".cjs"}:
        candidates.extend(
            (
                f"{raw_root}.ts",
                f"{raw_root}.tsx",
                posixpath.join(raw_root, "index.ts"),
                posixpath.join(raw_root, "index.tsx"),
            )
        )
    for candidate in candidates:
        normalized = _normalize_repair_path(candidate)
        if normalized in base_files:
            return normalized
    return ""


def _parse_named_import_symbols(symbols: str) -> list[str]:
    parsed: list[str] = []
    for raw in str(symbols or "").split(","):
        token = raw.strip().split(" as ", 1)[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(token):
            parsed.append(token)
    return _dedupe_preserve_order(parsed)


def _typescript_imported_const_class_alias_available(
    *,
    base_files: Mapping[str, str],
    importer_path: str,
    importer_text: str,
    symbol: str,
) -> bool:
    for match in _TS_NAMED_IMPORT_RE.finditer(importer_text):
        imported_symbols = _parse_named_import_symbols(str(match.group("symbols") or ""))
        if symbol not in imported_symbols:
            continue
        module_path = _resolve_relative_ts_module_path(importer_path, str(match.group("module") or ""), base_files)
        if not module_path:
            continue
        if _typescript_module_exports_const_class_alias(str(base_files.get(module_path) or ""), symbol):
            return True
    return False


def _typescript_module_exports_const_class_alias(module_text: str, symbol: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return False
    alias_re = re.compile(
        rf"\bexport\s+const\s+{re.escape(symbol)}\s*=\s*(?P<class_name>[A-Za-z_$][A-Za-z0-9_$]*)\s*;",
        re.MULTILINE,
    )
    alias = alias_re.search(module_text)
    if not alias:
        return False
    class_name = str(alias.group("class_name") or "")
    if not _TS_IDENTIFIER_RE.fullmatch(class_name):
        return False
    class_re = re.compile(rf"\b(?:export\s+)?class\s+{re.escape(class_name)}\b", re.MULTILINE)
    return bool(class_re.search(module_text))


def _replace_typescript_value_used_as_type_reference(
    text: str,
    *,
    line_number: int,
    column_number: int,
    symbol: str,
) -> tuple[str, bool]:
    if line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return text, False
    lines = str(text or "").splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return text, False
    original_line = lines[line_index]
    if re.search(r"\bimport\b|\bexport\s+(?:const|class|function)\b", original_line):
        return text, False
    replacement = f"InstanceType<typeof {symbol}>"
    if replacement in original_line:
        return text, False
    symbol_re = re.compile(rf"(?<![\w$]){re.escape(symbol)}(?![\w$])")
    matches = list(symbol_re.finditer(original_line))
    if not matches:
        return text, False
    column_index = max(0, column_number - 1)
    selected = min(
        matches,
        key=lambda match: (
            0
            if match.start() <= column_index <= match.end()
            else min(abs(match.start() - column_index), abs(match.end() - column_index))
        ),
    )
    prefix = original_line[max(0, selected.start() - 16) : selected.start()]
    if re.search(r"typeof\s+$", prefix):
        return text, False
    lines[line_index] = original_line[: selected.start()] + replacement + original_line[selected.end() :]
    return "".join(lines), True


def _typescript_duplicate_identifier_targets(diagnostics: Sequence[RepairDiagnostic]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for diagnostic in diagnostics:
        if diagnostic.code != "typescript_ts2300":
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        match = _TS_DUPLICATE_IDENTIFIER_MESSAGE_RE.search(str(diagnostic.message or diagnostic.raw or ""))
        name = str(match.group("name") or "").strip() if match else ""
        if _TS_IDENTIFIER_RE.fullmatch(name):
            targets.setdefault(path, set()).add(name)
    return targets


def _typescript_export_ambiguity_targets(diagnostics: Sequence[RepairDiagnostic]) -> tuple[dict[str, object], ...]:
    targets: list[dict[str, object]] = []
    for diagnostic in diagnostics:
        if diagnostic.code != "typescript_ts2308":
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        match = _TS_EXPORT_AMBIGUITY_MESSAGE_RE.search(str(diagnostic.message or diagnostic.raw or ""))
        if not match:
            continue
        module = str(match.group("module") or "").strip()
        symbol = str(match.group("symbol") or "").strip()
        if not module.startswith(".") or not _TS_IDENTIFIER_RE.fullmatch(symbol):
            continue
        targets.append({"path": path, "module": module, "symbol": symbol, "diagnostic": diagnostic})
    return tuple(targets)


def _typescript_duplicate_export_import_operations(
    *,
    path: str,
    content: str,
    duplicate_names: set[str],
) -> tuple[RepairOperation, ...]:
    imported_by_module = _typescript_named_imports_by_module(content)
    locally_exported_names = _typescript_local_named_export_names(content)
    locally_type_exported_names = _typescript_local_type_named_export_names(content)
    value_reexports_by_module = _typescript_named_reexports_by_module(content, type_only=False)
    type_reexports_by_module = _typescript_named_reexports_by_module(content, type_only=True)
    type_reexported_names = set().union(*type_reexports_by_module.values()) if type_reexports_by_module else set()
    if not (
        (imported_by_module and locally_exported_names)
        or (value_reexports_by_module and type_reexports_by_module)
        or (locally_type_exported_names and type_reexported_names)
    ):
        return ()

    operations: list[RepairOperation] = []
    before_hash = sha256_text(content)
    for match in _TS_NAMED_REEXPORT_RE.finditer(content):
        module = str(match.group("module") or "")
        symbols = str(match.group("symbols") or "")
        is_type_reexport = str(match.group(0) or "").lstrip().startswith("export type")
        if is_type_reexport:
            removable = (
                duplicate_names
                & (value_reexports_by_module.get(module, set()) | locally_exported_names)
                & _typescript_named_value_specifier_names(symbols)
            )
        else:
            removable = duplicate_names & imported_by_module.get(module, set()) & locally_exported_names
        if not removable:
            continue
        replacement_symbols, removed = _remove_typescript_named_export_symbols(symbols, removable)
        if not removed:
            continue
        if replacement_symbols.strip():
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start("symbols"),
                    span_end=match.end("symbols"),
                    expected=str(match.group("symbols") or ""),
                    replacement=replacement_symbols,
                    before_hash=before_hash,
                    metadata={
                        "repair_kind": "typescript_duplicate_export_import_binding",
                        "module": module,
                        "removed_symbols": tuple(sorted(removed)),
                    },
                )
            )
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=str(match.group(0) or ""),
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_duplicate_export_import_binding",
                    "module": module,
                    "removed_symbols": tuple(sorted(removed)),
                    "removed_empty_export_statement": True,
                },
            )
        )
    for match in _TS_LOCAL_TYPE_NAMED_EXPORT_RE.finditer(content):
        symbols = str(match.group("symbols") or "")
        removable = duplicate_names & type_reexported_names & _typescript_named_export_specifier_names(symbols)
        if not removable:
            continue
        replacement_symbols, removed = _remove_typescript_named_export_symbols(symbols, removable)
        if not removed:
            continue
        if replacement_symbols.strip():
            operations.append(
                RepairOperation(
                    kind="text_replace",
                    path=path,
                    span_start=match.start("symbols"),
                    span_end=match.end("symbols"),
                    expected=symbols,
                    replacement=replacement_symbols,
                    before_hash=before_hash,
                    metadata={
                        "repair_kind": "typescript_duplicate_type_reexport_binding",
                        "removed_symbols": tuple(sorted(removed)),
                    },
                )
            )
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=match.start(),
                span_end=match.end(),
                expected=str(match.group(0) or ""),
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_duplicate_type_reexport_binding",
                    "removed_symbols": tuple(sorted(removed)),
                    "removed_empty_export_statement": True,
                },
            )
        )
    return tuple(operations)


def _typescript_branded_literal_target_type(diagnostic: RepairDiagnostic) -> str:
    text = f"{diagnostic.message}\n{diagnostic.raw}"
    match = _TS_BRANDED_STRING_ASSIGNMENT_MESSAGE_RE.search(text)
    if not match:
        return ""
    candidate = str(match.group("type") or "").strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""


def _typescript_string_brand_type_sources(base_files: Mapping[str, str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for path, content in base_files.items():
        normalized = _normalize_repair_path(path)
        if not normalized.endswith((".ts", ".tsx")):
            continue
        for match in _TS_STRING_BRAND_TYPE_ALIAS_RE.finditer(str(content or "")):
            name = str(match.group("name") or "").strip()
            if name:
                sources.setdefault(name, normalized)
    return sources


def _typescript_branded_literal_cast_operation(
    *,
    path: str,
    content: str,
    diagnostic: RepairDiagnostic,
    target_type: str,
) -> RepairOperation | None:
    line_number = int(diagnostic.line or 0)
    column_number = int(diagnostic.column or 0)
    if line_number <= 0:
        return None
    lines = content.splitlines(keepends=True)
    if line_number > len(lines):
        return None
    line_start = sum(len(line) for line in lines[: line_number - 1])
    line = lines[line_number - 1]
    search_start = max(0, min(len(line), column_number - 1 if column_number > 0 else 0))
    literal_match = _find_string_literal_after_column(line, search_start)
    if literal_match is None:
        return None
    literal_end = literal_match.end()
    trailing = line[literal_end : literal_end + 40]
    if re.match(r"\s+as\s+[A-Za-z_$][\w$]*", trailing):
        return None
    before_hash = sha256_text(content)
    literal = literal_match.group(0)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=line_start + literal_match.start(),
        span_end=line_start + literal_match.end(),
        expected=literal,
        replacement=f"{literal} as {target_type}",
        before_hash=before_hash,
        metadata={
            "repair_kind": "typescript_branded_literal_cast",
            "target_type": target_type,
            "line": line_number,
            "column": column_number,
        },
    )


def _find_string_literal_after_column(line: str, column_index: int) -> re.Match[str] | None:
    for match in re.finditer(r"(['\"])(?:\\.|(?!\1).)*\1", line):
        if match.end() <= column_index:
            continue
        return match
    return None


def _typescript_type_only_value_usage_symbol(diagnostic: RepairDiagnostic) -> str:
    text = f"{diagnostic.message}\n{diagnostic.raw}"
    match = _TS_TYPE_ONLY_VALUE_USAGE_MESSAGE_RE.search(text)
    if not match:
        return ""
    candidate = str(match.group("name") or "").strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""


def _typescript_string_literal_union_type_aliases(
    base_files: Mapping[str, str],
) -> dict[str, tuple[str, int, int, str, tuple[str, ...], bool]]:
    aliases: dict[str, tuple[str, int, int, str, tuple[str, ...], bool]] = {}
    for path, content in base_files.items():
        normalized = _normalize_repair_path(path)
        if not normalized.endswith((".ts", ".tsx")):
            continue
        text = str(content or "")
        for match in _TS_STRING_LITERAL_UNION_TYPE_ALIAS_RE.finditer(text):
            name = str(match.group("name") or "").strip()
            if not name or name in aliases:
                continue
            literals = _typescript_identifier_string_literal_union_values(str(match.group("body") or ""))
            if len(literals) < 2:
                continue
            aliases[name] = (
                normalized,
                match.start(),
                match.end(),
                str(match.group(0) or ""),
                literals,
                bool(match.group("export")),
            )
    return aliases


def _typescript_identifier_string_literal_union_values(body: str) -> tuple[str, ...]:
    literals: list[str] = []
    seen: set[str] = set()
    for part in str(body or "").split("|"):
        token = part.strip()
        match = re.fullmatch(r"(['\"])(?P<literal>[A-Za-z_$][\w$]*)\1", token)
        if not match:
            return ()
        literal = str(match.group("literal") or "")
        if literal in seen:
            continue
        seen.add(literal)
        literals.append(literal)
    return tuple(literals)


def _typescript_type_value_dot_member(
    *,
    base_files: Mapping[str, str],
    diagnostic: RepairDiagnostic,
    symbol: str,
) -> str:
    path = _normalize_repair_path(str(diagnostic.path or ""))
    content = str(base_files.get(path) or "")
    line_number = int(diagnostic.line or 0)
    if not path or not content:
        return ""
    fallback_match = re.search(rf"\b{re.escape(symbol)}\.(?P<member>[A-Za-z_$][\w$]*)\b", content)
    if line_number <= 0:
        return str(fallback_match.group("member") or "") if fallback_match else ""
    lines = content.splitlines()
    if line_number > len(lines):
        return ""
    line = lines[line_number - 1]
    match = re.search(rf"\b{re.escape(symbol)}\.(?P<member>[A-Za-z_$][\w$]*)\b", line)
    if not match and fallback_match is not None:
        match = fallback_match
    if match is None:
        return ""
    return str(match.group("member") or "")


def _typescript_literal_union_value_facade_operation(
    *,
    path: str,
    content: str,
    span_start: int,
    span_end: int,
    expected: str,
    type_name: str,
    literals: Sequence[str],
    exported: bool,
) -> RepairOperation | None:
    if re.search(rf"\bconst\s+{re.escape(type_name)}\s*=", content):
        return None
    export_prefix = "export " if exported else ""
    entries = "\n".join(f'  {literal}: "{literal}",' for literal in literals)
    replacement = (
        f"{export_prefix}const {type_name} = {{\n"
        f"{entries}\n"
        f"}} as const;\n"
        f"{export_prefix}type {type_name} = (typeof {type_name})[keyof typeof {type_name}];"
    )
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_literal_union_value_facade",
            "type_name": type_name,
            "literal_count": len(tuple(literals)),
        },
    )


def _typescript_file_has_type_name_import(content: str, type_name: str) -> bool:
    escaped = re.escape(type_name)
    return bool(
        re.search(rf"\bimport\s+type\s*\{{[^}}]*\b{escaped}\b[^}}]*\}}\s+from\b", content, re.DOTALL)
        or re.search(rf"\bimport\s*\{{[^}}]*\btype\s+{escaped}\b[^}}]*\}}\s+from\b", content, re.DOTALL)
    )


def _typescript_insert_type_import_operation(
    *,
    path: str,
    content: str,
    type_name: str,
    source_path: str,
) -> RepairOperation | None:
    module_specifier = _relative_import_specifier_for_actual_path(
        importer_rel=path,
        original_specifier="",
        actual_target_rel=source_path,
    )
    import_line = f'import type {{ {type_name} }} from "{module_specifier}";\n'
    insert_at = _typescript_import_insert_offset(content)
    before_hash = sha256_text(content)
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=insert_at,
        span_end=insert_at,
        expected="",
        replacement=import_line,
        before_hash=before_hash,
        metadata={
            "repair_kind": "typescript_branded_literal_type_import",
            "target_type": type_name,
            "module_specifier": module_specifier,
            "expected_context_before": content[max(0, insert_at - 240) : insert_at],
            "expected_context_after": content[insert_at : insert_at + 120],
        },
    )


def _typescript_import_insert_offset(content: str) -> int:
    matches = list(re.finditer(r"^import\b[^\n]*(?:\n|$)", content, re.MULTILINE))
    if matches:
        return matches[-1].end()
    header_match = re.match(r"^(?:/\*.*?\*/\s*)", content, re.DOTALL)
    return header_match.end() if header_match else 0


def _typescript_named_imports_by_module(content: str) -> dict[str, set[str]]:
    imported: dict[str, set[str]] = {}
    for match in _TS_NAMED_IMPORT_RE.finditer(content):
        module = str(match.group("module") or "")
        symbols = set(_parse_named_import_symbols(str(match.group("symbols") or "")))
        if module and symbols:
            imported.setdefault(module, set()).update(symbols)
    return imported


def _typescript_named_reexports_by_module(content: str, *, type_only: bool) -> dict[str, set[str]]:
    exported: dict[str, set[str]] = {}
    for match in _TS_NAMED_REEXPORT_RE.finditer(content):
        raw = str(match.group(0) or "")
        symbols = str(match.group("symbols") or "")
        is_type_reexport = raw.lstrip().startswith("export type")
        module = str(match.group("module") or "")
        if type_only:
            names = (
                _typescript_named_export_specifier_names(symbols)
                if is_type_reexport
                else _typescript_named_type_specifier_names(symbols)
            )
        else:
            if is_type_reexport:
                continue
            names = _typescript_named_value_specifier_names(symbols)
        if module and names:
            exported.setdefault(module, set()).update(names)
    return exported


def _typescript_local_named_export_names(content: str) -> set[str]:
    names: set[str] = set()
    for match in _TS_LOCAL_NAMED_EXPORT_RE.finditer(content):
        names.update(_typescript_named_value_specifier_names(str(match.group("symbols") or "")))
    return names


def _typescript_local_type_named_export_names(content: str) -> set[str]:
    names: set[str] = set()
    for match in _TS_LOCAL_TYPE_NAMED_EXPORT_RE.finditer(content):
        names.update(_typescript_named_export_specifier_names(str(match.group("symbols") or "")))
    return names


def _typescript_named_export_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        name = _typescript_named_export_specifier_name(raw)
        if name:
            names.add(name)
    return names


def _typescript_named_type_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        token = str(raw or "").strip()
        if not token.lower().startswith("type "):
            continue
        name = _typescript_named_export_specifier_name(token[5:].strip())
        if name:
            names.add(name)
    return names


def _typescript_named_value_specifier_names(symbols: str) -> set[str]:
    names: set[str] = set()
    for raw in str(symbols or "").split(","):
        name = _typescript_named_value_specifier_name(raw)
        if name:
            names.add(name)
    return names


def _remove_typescript_named_export_symbols(symbols: str, removable: set[str]) -> tuple[str, set[str]]:
    parts = [part.strip() for part in str(symbols or "").split(",")]
    kept: list[str] = []
    removed: set[str] = set()
    for part in parts:
        if not part:
            continue
        name = _typescript_named_value_specifier_name(part)
        if name and name in removable:
            removed.add(name)
            continue
        kept.append(part)
    if not removed:
        return symbols, set()
    if "\n" not in symbols:
        return f"{', '.join(kept)} " if kept else "", removed
    indent = _typescript_named_specifier_indent(symbols)
    return "".join(f"{part},\n{indent}" for part in kept).removesuffix(indent), removed


def _typescript_named_value_specifier_name(raw: str) -> str:
    token = str(raw or "").strip()
    if not token or token.startswith("type "):
        return ""
    return _typescript_named_export_specifier_name(token)


def _typescript_named_export_specifier_name(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    candidate = re.split(r"\s+as\s+", token, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    return candidate if _TS_IDENTIFIER_RE.fullmatch(candidate) else ""


def _typescript_named_specifier_indent(symbols: str) -> str:
    for line in str(symbols or "").splitlines():
        if line.strip():
            match = re.match(r"^\s*", line)
            indent = match.group(0) if match else ""
            return indent or "  "
    return "  "


def _typescript_module_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    if re.search(rf"\bexport\s+(?:type|interface|enum|class|const|let|var|function)\s+{escaped}\b", module_text):
        return True
    for match in re.finditer(r"\bexport\s+(?:type\s+)?\{(?P<symbols>[^}]+)\}", module_text):
        for token in str(match.group("symbols") or "").split(","):
            parts = re.split(r"\s+as\s+", token.strip(), maxsplit=1)
            exported = parts[-1].strip()
            if exported == symbol:
                return True
    return False


def _typescript_exported_symbol_is_type_only(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    return bool(re.search(rf"\bexport\s+(?:interface|type)\s+{escaped}\b", module_text))


def _typescript_file_has_named_reexport(content: str, *, module: str, symbol: str) -> bool:
    value_reexports = _typescript_named_reexports_by_module(content, type_only=False)
    type_reexports = _typescript_named_reexports_by_module(content, type_only=True)
    return symbol in value_reexports.get(module, set()) or symbol in type_reexports.get(module, set())


def _find_unique_runtime_export_source(base_files: Mapping[str, str], module_path: str, symbol: str) -> str:
    matches = [
        path
        for path, text in base_files.items()
        if path != module_path and path.endswith((".ts", ".tsx")) and _typescript_module_exports_symbol(text, symbol)
    ]
    return matches[0] if len(matches) == 1 else ""


def _build_typescript_reexport_line(*, module_path: str, source_path: str, symbol: str) -> str:
    module_dir = posixpath.dirname(module_path)
    rel = posixpath.relpath(source_path.removesuffix(".ts").removesuffix(".tsx"), module_dir or ".")
    if not rel.startswith("."):
        rel = f"./{rel}"
    return f"export {{ {symbol} }} from '{rel}';"


def _looks_like_typescript_reexport_signal(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    if not any(hint in text for hint in ("typescript", ".ts", ".tsx", "vitest", "npm test")):
        return False
    return any(
        hint in text
        for hint in (
            "cannot read properties of undefined",
            "undefined",
            "missing export",
            "re-export",
            "reexport",
            "import/export",
            "export/import",
            "contract fix",
        )
    )


def _parse_typescript_cannot_find_name_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_CANNOT_FIND_NAME_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "col": str(match.group("col") or ""),
                    "symbol": str(match.group("symbol") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["symbol"]]


def _typescript_missing_identifier_usage_is_type_position(text: str, item: Mapping[str, str]) -> bool:
    line_number = _to_positive_int(item.get("line"))
    lines = str(text or "").splitlines()
    if line_number <= 0 or line_number > len(lines):
        return False
    symbol = re.escape(str(item.get("symbol") or ""))
    return bool(re.search(rf"[:<,|&([]\s*{symbol}\b|\bas\s+{symbol}\b", lines[line_number - 1]))


def _add_typescript_reexported_type_binding(text: str, *, missing_symbol: str) -> tuple[str, dict[str, str]]:
    symbol = str(missing_symbol or "").strip()
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return text, {}
    for match in _TS_NAMED_REEXPORT_RE.finditer(str(text or "")):
        module = str(match.group("module") or "")
        if symbol not in _parse_named_import_symbols(str(match.group("symbols") or "")):
            continue
        import_line = f'import type {{ {symbol} }} from "{module}";\n'
        if import_line in text:
            return text, {}
        return import_line + text, {"symbol": symbol, "module": module}
    return text, {}


def _build_relative_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
    source_tool: str,
    rule_id: str,
    mode_filter: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    repairs: list[dict[str, str]] = []
    updated = dict(base_files)
    for item in _parse_unresolved_relative_import_errors(diagnostics):
        importer = item["file"]
        specifier = item["specifier"]
        content = str(updated.get(importer) or "")
        if not specifier.startswith(".") or not content:
            continue
        operation: RepairOperation | None = None
        metadata: dict[str, str] = {"specifier": specifier}
        actual_target = _resolve_relative_import_target(
            base_files=updated,
            importer_rel=importer,
            specifier=specifier,
            allow_case_variant=True,
        )
        if mode_filter == "case":
            if not actual_target:
                continue
            corrected = _relative_import_specifier_for_actual_path(
                importer_rel=importer,
                original_specifier=specifier,
                actual_target_rel=actual_target,
            )
            if corrected == specifier:
                continue
            operation = _replace_import_specifier_operation(
                path=importer,
                content=content,
                specifier=specifier,
                replacement=corrected,
                metadata={
                    "repair_kind": "typescript_relative_import_case",
                    "specifier": specifier,
                    "corrected_specifier": corrected,
                    "target_file": actual_target,
                },
            )
            metadata = {"specifier": specifier, "corrected_specifier": corrected, "target_file": actual_target}
        elif mode_filter == "unused":
            if actual_target:
                continue
            operation = _remove_unused_typescript_import_operation(path=importer, content=content, specifier=specifier)
        elif mode_filter == "unique_export":
            if actual_target:
                continue
            actual_target = _find_unique_typescript_export_for_import(
                base_files=updated,
                importer_path=importer,
                content=content,
                specifier=specifier,
            )
            if not actual_target:
                continue
            corrected = _relative_import_specifier_for_actual_path(
                importer_rel=importer,
                original_specifier=specifier,
                actual_target_rel=actual_target,
            )
            if corrected == specifier:
                continue
            operation = _replace_import_specifier_operation(
                path=importer,
                content=content,
                specifier=specifier,
                replacement=corrected,
                metadata={
                    "repair_kind": "typescript_unique_export_import",
                    "specifier": specifier,
                    "corrected_specifier": corrected,
                    "target_file": actual_target,
                },
            )
            metadata = {"specifier": specifier, "corrected_specifier": corrected, "target_file": actual_target}
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(content, operation)
        operations.append(operation)
        repairs.append({"file": importer, **metadata})
    return _repair_plan_or_none(
        rule_id=rule_id,
        source_tool=source_tool,
        operations=operations,
        diagnostics=diagnostics,
        mode=mode,
        metadata={"imports": repairs},
    )


def _parse_unresolved_relative_import_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for match in _UNRESOLVED_RELATIVE_IMPORT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "specifier": str(match.group("specifier") or "").strip(),
            }
            key = (item["file"], item["specifier"])
            if item["file"] and item["specifier"].startswith(".") and key not in seen:
                seen.add(key)
                parsed.append(item)
    return parsed


def _resolve_relative_import_target(
    *,
    base_files: Mapping[str, str],
    importer_rel: str,
    specifier: str,
    allow_case_variant: bool,
) -> str:
    for candidate in _relative_import_repair_target_candidates(importer_rel=importer_rel, specifier=specifier):
        if candidate in base_files:
            return candidate
        if allow_case_variant:
            case_variant = _resolve_case_variant_base_file(base_files=base_files, relative_path=candidate)
            if case_variant:
                return case_variant
    return ""


def _relative_import_repair_target_candidates(*, importer_rel: str, specifier: str) -> list[str]:
    base_dir = posixpath.dirname(importer_rel)
    raw = _normalize_repair_path(posixpath.normpath(posixpath.join(base_dir, specifier)))
    if not raw:
        return []
    suffix = posixpath.splitext(raw)[1]
    if suffix:
        return [raw]
    return [
        raw,
        f"{raw}.ts",
        f"{raw}.tsx",
        f"{raw}.js",
        f"{raw}.jsx",
        posixpath.join(raw, "index.ts"),
        posixpath.join(raw, "index.tsx"),
        posixpath.join(raw, "index.js"),
        posixpath.join(raw, "index.jsx"),
    ]


def _resolve_case_variant_base_file(*, base_files: Mapping[str, str], relative_path: str) -> str:
    normalized = _normalize_repair_path(relative_path)
    if not normalized:
        return ""
    lowered = normalized.lower()
    matches = [path for path in base_files if path.lower() == lowered]
    return matches[0] if len(matches) == 1 else ""


def _relative_import_specifier_for_actual_path(
    *,
    importer_rel: str,
    original_specifier: str,
    actual_target_rel: str,
) -> str:
    relative = posixpath.relpath(actual_target_rel, posixpath.dirname(importer_rel) or ".")
    if not relative.startswith("."):
        relative = f"./{relative}"
    if not posixpath.splitext(original_specifier)[1]:
        for suffix in _relative_import_suffix_order(importer_rel):
            if relative.endswith(suffix):
                relative = relative[: -len(suffix)]
                break
    return relative


def _relative_import_suffix_order(importer_rel: str) -> tuple[str, ...]:
    if importer_rel.endswith(".tsx"):
        return (".tsx", ".ts", ".jsx", ".js")
    if importer_rel.endswith(".jsx"):
        return (".jsx", ".js", ".tsx", ".ts")
    return (".ts", ".tsx", ".js", ".jsx")


def _replace_import_specifier_operation(
    *,
    path: str,
    content: str,
    specifier: str,
    replacement: str,
    metadata: Mapping[str, object],
) -> RepairOperation | None:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=match.start("specifier"),
        span_end=match.end("specifier"),
        expected=specifier,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata=dict(metadata),
    )


def _typescript_import_statement_for_specifier(content: str, specifier: str) -> re.Match[str] | None:
    pattern = re.compile(_TS_IMPORT_FROM_SPECIFIER_TEMPLATE.format(specifier=re.escape(specifier)))
    return pattern.search(content)


def _typescript_import_pairs_for_specifier(content: str, specifier: str) -> list[tuple[str, str]]:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return []
    return _typescript_import_pairs_from_clause(str(match.group("clause") or ""))


def _typescript_import_pairs_from_clause(clause: str) -> list[tuple[str, str]]:
    clause = str(clause or "").strip()
    if clause.startswith("type "):
        clause = clause[5:].strip()
    pairs: list[tuple[str, str]] = []
    namespace_match = re.fullmatch(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if namespace_match:
        return []
    compact_clause = clause.replace(" ", "")
    if clause.startswith("{") and clause.endswith("}"):
        default_clause = ""
        named_clause = clause[1:-1]
    elif ",{" in compact_clause:
        default_clause, named_clause = clause.split(",", 1)
        named_clause = named_clause.strip()
        named_clause = named_clause[1:-1] if named_clause.startswith("{") and named_clause.endswith("}") else ""
    else:
        default_clause = clause
        named_clause = ""
    default_name = default_clause.strip()
    if _TS_IDENTIFIER_RE.fullmatch(default_name):
        pairs.append(("default", default_name))
    for raw_part in named_clause.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("type "):
            part = part[5:].strip()
        alias_parts = re.split(r"\s+as\s+", part, maxsplit=1, flags=re.IGNORECASE)
        imported = alias_parts[0].strip()
        local = alias_parts[-1].strip()
        if _TS_IDENTIFIER_RE.fullmatch(imported) and _TS_IDENTIFIER_RE.fullmatch(local):
            pairs.append((imported, local))
    return pairs


def _typescript_identifier_used_outside_span(content: str, name: str, span: tuple[int, int]) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(name):
        return False
    outside = content[: span[0]] + content[span[1] :]
    return re.search(rf"\b{re.escape(name)}\b", outside) is not None


def _remove_unused_typescript_import_operation(
    *,
    path: str,
    content: str,
    specifier: str,
) -> RepairOperation | None:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return None
    pairs = _typescript_import_pairs_for_specifier(content, specifier)
    if not pairs:
        return None
    span = match.span()
    if any(_typescript_identifier_used_outside_span(content, local, span) for _, local in pairs):
        return None
    start, end = span
    if end < len(content) and content[end : end + 1] == "\n":
        end += 1
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=end,
        expected=content[start:end],
        replacement="",
        before_hash=sha256_text(content),
        metadata={"repair_kind": "typescript_unused_import", "specifier": specifier},
    )


def _find_unique_typescript_export_for_import(
    *,
    base_files: Mapping[str, str],
    importer_path: str,
    content: str,
    specifier: str,
) -> str:
    match = _typescript_import_statement_for_specifier(content, specifier)
    if match is None:
        return ""
    needed_symbols = [
        imported
        for imported, local in _typescript_import_pairs_for_specifier(content, specifier)
        if imported != "default" and _typescript_identifier_used_outside_span(content, local, match.span())
    ]
    if not needed_symbols:
        return ""
    candidates = [
        path
        for path, text in base_files.items()
        if path != importer_path
        and path.endswith((".ts", ".tsx"))
        and not path.endswith(".d.ts")
        and all(_typescript_module_exports_symbol(text, symbol) for symbol in needed_symbols)
    ]
    return candidates[0] if len(candidates) == 1 else ""


def _typescript_scaffold_package_payload() -> dict[str, object]:
    return {
        "name": "typescript-application",
        "version": "1.0.0",
        "main": "dist/index.js",
        "scripts": {"build": "tsc", "test": "npm run build", "start": "node dist/index.js"},
        "devDependencies": {"typescript": "^5.0.0"},
    }


def _typescript_scaffold_tsconfig_payload() -> dict[str, object]:
    return {
        "compilerOptions": {
            "target": "ES2020",
            "module": "ESNext",
            "moduleResolution": "node",
            "outDir": "dist",
            "rootDir": "src",
            "strict": True,
            "esModuleInterop": True,
            "skipLibCheck": True,
        },
        "include": ["src/**/*.ts"],
        "exclude": ["node_modules", "dist"],
    }


def _parse_typescript_sourcefile_diagnostics_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        for match in _TS_SOURCEFILE_DIAGNOSTICS_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("file") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)


def _repair_typescript_sourcefile_diagnostics_usage(text: str) -> str:
    source = str(text or "")
    if "ts.createSourceFile" not in source:
        return source
    create_match = re.search(
        r"ts\.createSourceFile\(\s*(?P<file>[A-Za-z_$][A-Za-z0-9_$]*)\s*,\s*"
        r"(?P<source>[A-Za-z_$][A-Za-z0-9_$]*)",
        source,
        re.DOTALL,
    )
    source_var = str(create_match.group("source") if create_match else "text")
    file_var = str(create_match.group("file") if create_match else "file")
    diagnostics_re = re.compile(
        r"(?m)^(?P<indent>\s*)const\s+diagnostics(?:\s*:[^=]+)?\s*=\s*"
        r"(?P<expr>[^\n;]*(?:parseDiagnostics|undefined\s+as\s+unknown|unknown\s*\?\?\s*\[\])[^\n;]*);?\s*$"
    )

    def _replace(match: re.Match[str]) -> str:
        indent = str(match.group("indent") or "")
        inner = indent + "  "
        return (
            f"{indent}const diagnostics: readonly ts.Diagnostic[] =\n"
            f"{inner}ts.transpileModule({source_var}, {{\n"
            f"{inner}  compilerOptions: {{\n"
            f"{inner}    module: ts.ModuleKind.ES2020,\n"
            f"{inner}    target: ts.ScriptTarget.ES2020,\n"
            f"{inner}  }},\n"
            f"{inner}  fileName: {file_var},\n"
            f"{inner}  reportDiagnostics: true,\n"
            f"{inner}}}).diagnostics ?? [];"
        )

    repaired, replacements = diagnostics_re.subn(_replace, source, count=1)
    if replacements == 0:
        return source
    return re.sub(r"if\s*\(\s*(?:0\s*>\s*0|false)\s*\)", "if (diagnostics.length > 0)", repaired)


def _parse_typescript_too_few_arguments_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_TOO_FEW_ARGUMENTS_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append({key: str(match.group(key) or "") for key in ("file", "line", "col", "expected", "got")})
    return parsed


def _too_few_arguments_operation(base_files: Mapping[str, str], item: Mapping[str, str]) -> RepairOperation | None:
    path = _normalize_repair_path(str(item.get("file") or ""))
    content = str(base_files.get(path) or "")
    line_number = _to_positive_int(item.get("line"))
    column = _to_positive_int(item.get("col"))
    expected_count = _to_positive_int(item.get("expected"))
    got_count = _to_positive_int(item.get("got"))
    if not path or not content or line_number <= 0 or column <= 0 or expected_count == got_count:
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    usage_line = lines[line_index].rstrip("\r\n")
    method_name = _typescript_call_name_from_usage_line(usage_line, column)
    if not method_name:
        return None
    if expected_count < got_count:
        return _too_many_arguments_declaration_operation(
            base_files=base_files,
            method_name=method_name,
            expected_count=expected_count,
        )
    callsite_operation = _too_few_arguments_callsite_operation(
        path=path,
        content=content,
        line_index=line_index,
        method_name=method_name,
        expected_count=expected_count,
        got_count=got_count,
        column=column,
    )
    if callsite_operation is not None:
        return callsite_operation
    declaration = _find_unique_typescript_method_declaration(
        base_files=base_files,
        method_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_line = declaration
    repaired_line = _add_defaults_to_typescript_method_params(
        declaration_line.rstrip("\r\n"),
        got_count=got_count,
        expected_count=expected_count,
    )
    if repaired_line == declaration_line.rstrip("\r\n"):
        return None
    newline = declaration_line[len(declaration_line.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=str(base_files[declaration_path]),
        line_index=declaration_line_index,
        replacement=f"{repaired_line}{newline}",
        metadata={
            "repair_kind": "typescript_too_few_arguments",
            "method": method_name,
            "repair": "declaration_default_parameters",
        },
    )


def _too_many_arguments_declaration_operation(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
) -> RepairOperation | None:
    if expected_count != 0:
        return None
    declaration = _find_unique_typescript_function_declaration(
        base_files=base_files,
        function_name=method_name,
        expected_count=expected_count,
    )
    if declaration is None:
        return None
    declaration_path, declaration_line_index, declaration_line = declaration
    repaired_line = _add_rest_param_to_typescript_callable(declaration_line.rstrip("\r\n"))
    if repaired_line == declaration_line.rstrip("\r\n"):
        return None
    newline = declaration_line[len(declaration_line.rstrip("\r\n")) :]
    return _line_text_replace_operation(
        path=declaration_path,
        content=str(base_files[declaration_path]),
        line_index=declaration_line_index,
        replacement=f"{repaired_line}{newline}",
        metadata={
            "repair_kind": "typescript_too_many_arguments",
            "method": method_name,
            "repair": "declaration_rest_parameter",
        },
    )


def _typescript_call_name_from_usage_line(usage_line: str, column: int) -> str:
    prefix = usage_line[: max(0, min(len(usage_line), int(column)))]
    matches = list(re.finditer(r"(?:\.|\b)(?P<name>[A-Za-z_$][\w$]*)\s*\(", prefix))
    if not matches:
        matches = list(re.finditer(r"\b(?P<name>[A-Za-z_$][\w$]*)\s*\(", usage_line))
    return str(matches[-1].group("name") if matches else "").strip()


def _too_few_arguments_callsite_operation(
    *,
    path: str,
    content: str,
    line_index: int,
    method_name: str,
    expected_count: int,
    got_count: int,
    column: int,
) -> RepairOperation | None:
    if method_name != "clamp" or expected_count != 3 or got_count != 2:
        return None
    lines = content.splitlines(keepends=True)
    line = lines[line_index]
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    column_index = max(0, int(column) - 1)
    for match in re.finditer(r"\bclamp\s*\(", line_body):
        open_index = line_body.find("(", match.start())
        close_index = _find_matching_paren(line_body, open_index)
        if close_index < 0 or not (match.start() <= column_index <= close_index):
            continue
        spans = _split_typescript_argument_spans(line_body, open_index + 1, close_index)
        if len(spans) != 2:
            continue
        first_arg = line_body[spans[0][0] : spans[0][1]].strip()
        second_arg = line_body[spans[1][0] : spans[1][1]].strip()
        if not first_arg or not second_arg:
            continue
        repaired_line = f"{line_body[: open_index + 1]}{first_arg}, 0, {second_arg}{line_body[close_index:]}{newline}"
        return _line_text_replace_operation(
            path=path,
            content=content,
            line_index=line_index,
            replacement=repaired_line,
            metadata={
                "repair_kind": "typescript_too_few_arguments",
                "method": method_name,
                "repair": "callsite_insert_default_min_bound",
            },
        )
    return None


def _find_unique_typescript_method_declaration(
    *,
    base_files: Mapping[str, str],
    method_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(method_name):
        return None
    method_re = re.compile(
        rf"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?{re.escape(method_name)}\s*\((?P<params>[^)]*)\)",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        for line_index, line in enumerate(str(text or "").splitlines(keepends=True)):
            match = method_re.search(line.rstrip("\r\n"))
            if not match:
                continue
            params = _split_typescript_params(str(match.group("params") or ""))
            if len(params) >= expected_count:
                matches.append((path, line_index, line))
    return matches[0] if len(matches) == 1 else None


def _find_unique_typescript_function_declaration(
    *,
    base_files: Mapping[str, str],
    function_name: str,
    expected_count: int,
) -> tuple[str, int, str] | None:
    if not _TS_IDENTIFIER_RE.fullmatch(function_name):
        return None
    function_re = re.compile(
        rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(function_name)}\s*"
        r"\((?P<params>[^)]*)\)",
    )
    matches: list[tuple[str, int, str]] = []
    for path, text in base_files.items():
        if not path.endswith((".ts", ".tsx")) or path.endswith(".d.ts"):
            continue
        for line_index, line in enumerate(str(text or "").splitlines(keepends=True)):
            match = function_re.search(line.rstrip("\r\n"))
            if not match:
                continue
            params = _split_typescript_params(str(match.group("params") or ""))
            if len(params) == expected_count:
                matches.append((path, line_index, line))
    return matches[0] if len(matches) == 1 else None


def _add_rest_param_to_typescript_callable(line: str) -> str:
    open_index = line.find("(")
    close_index = _find_matching_paren(line, open_index)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index].strip()
    if "..._args" in params_text:
        return line
    separator = ", " if params_text else ""
    repaired_params = f"{params_text}{separator}..._args: unknown[]"
    return line[: open_index + 1] + repaired_params + line[close_index:]


def _add_defaults_to_typescript_method_params(line: str, *, got_count: int, expected_count: int) -> str:
    open_index = line.find("(")
    close_index = _find_matching_paren(line, open_index)
    if open_index < 0 or close_index < 0:
        return line
    params_text = line[open_index + 1 : close_index]
    params = _split_typescript_params(params_text)
    if len(params) < expected_count or got_count >= expected_count:
        return line
    changed = False
    for index in range(got_count, min(expected_count, len(params))):
        repaired = _typescript_param_with_default(params[index])
        if repaired != params[index]:
            params[index] = repaired
            changed = True
    if not changed:
        return line
    return line[: open_index + 1] + ", ".join(params) + line[close_index:]


def _split_typescript_params(params_text: str) -> list[str]:
    spans = _split_typescript_argument_spans(params_text, 0, len(params_text))
    return [params_text[start:end].strip() for start, end in spans if params_text[start:end].strip()]


def _typescript_param_with_default(param: str) -> str:
    if "=" in param:
        return param
    if ":" not in param:
        return f"{param} = undefined"
    name, annotation = param.split(":", 1)
    ts_type = annotation.strip()
    return f"{name.strip()}: {ts_type} = {_typescript_default_value_for_type(ts_type)}"


def _typescript_errors_require_dom_lib(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    dom_global_names = ("console", "window", "document", "navigator", "location")
    dom_type_names = ("htmlelement", "htmlelementtagnamemap")
    return ("include 'dom'" in text and any(f"cannot find name '{name}'" in text for name in dom_global_names)) or any(
        f"cannot find name '{name}'" in text for name in dom_type_names
    )


def _typescript_errors_require_import_meta_module(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return "ts1343" in text and "import.meta" in text and "module" in text


def _typescript_errors_require_es2021_lib(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return (
        ("ts2550" in text or "property 'replaceall' does not exist" in text)
        and "replaceall" in text
        and ("es2021" in text or "target library" in text or "lib" in text)
    )


def _typescript_libs_allow_es2021(libs: Sequence[str]) -> bool:
    allowed = {"es2021", "es2022", "es2023", "es2024", "esnext"}
    return any(str(item or "").strip().lower() in allowed for item in libs)


def _typescript_promote_libs_to_es2021(libs: Sequence[str], target: object) -> list[str]:
    promoted: list[str] = []
    replaced = False
    for item in libs:
        raw = str(item or "").strip()
        lowered = raw.lower()
        if lowered in {"es5", "es6", "es2015", "es2016", "es2017", "es2018", "es2019", "es2020"}:
            if not replaced:
                promoted.append("ES2021")
                replaced = True
            continue
        if raw:
            promoted.append(raw)
    if not replaced and not _typescript_libs_allow_es2021(promoted):
        target_text = str(target or "").strip()
        if target_text and target_text.lower() not in {"es2021", "es2022", "es2023", "es2024", "esnext"}:
            promoted.insert(0, "ES2021")
        elif target_text:
            promoted.insert(0, target_text)
        else:
            promoted.insert(0, "ES2021")
    return list(dict.fromkeys(promoted))


def _typescript_module_allows_import_meta(raw_module: object) -> bool:
    return str(raw_module or "").strip().lower() in {
        "es2020",
        "es2022",
        "esnext",
        "system",
        "node16",
        "node18",
        "node20",
        "nodenext",
    }


def _parse_typescript_uninitialized_property_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_UNINITIALIZED_PROPERTY_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            parsed.append(
                {
                    "file": _normalize_repair_path(str(match.group("file") or "")),
                    "line": str(match.group("line") or ""),
                    "member": str(match.group("member") or ""),
                }
            )
    return [item for item in parsed if item["file"] and item["line"] and item["member"]]


def _typescript_property_line_with_default(line: str, member: str) -> str:
    match = re.match(
        rf"^(?P<prefix>\s*(?:(?:public|private|protected)\s+)?(?:readonly\s+)?{re.escape(member)}\s*:\s*)(?P<type>[^;=]+)(?P<suffix>;?\s*)$",
        line,
    )
    if not match or "=" in line or "!" in line:
        return line
    ts_type = str(match.group("type") or "unknown").strip()
    return f"{match.group('prefix')}{ts_type} = {_typescript_default_value_for_type(ts_type)}{match.group('suffix')}"


def _typescript_default_value_for_type(ts_type: str) -> str:
    lowered = str(ts_type or "").strip().lower()
    if lowered == "string":
        return '""'
    if lowered == "number":
        return "0"
    if lowered == "boolean":
        return "false"
    if "[]" in lowered:
        return "[]"
    if lowered == "date":
        return "new Date(0)"
    return "undefined"


def _repair_typescript_unresolved_identifier_lines(
    text: str,
    *,
    target_line_number: int,
    missing_symbol: str,
) -> tuple[str, str]:
    if target_line_number <= 0 or not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return str(text or ""), ""
    lines = str(text or "").splitlines(keepends=True)
    target_index = target_line_number - 1
    if target_index < 0 or target_index >= len(lines):
        return str(text or ""), ""
    replacement = _select_typescript_unresolved_identifier_replacement(lines, target_index, missing_symbol)
    if not replacement:
        return str(text or ""), ""
    line = lines[target_index]
    repaired_line = re.sub(rf"\b{re.escape(missing_symbol)}\b", replacement, line)
    if repaired_line == line:
        return str(text or ""), ""
    lines[target_index] = repaired_line
    return "".join(lines), replacement


def _select_typescript_unresolved_identifier_replacement(
    lines: Sequence[str],
    target_index: int,
    missing_symbol: str,
) -> str:
    line = str(lines[target_index] or "") if 0 <= target_index < len(lines) else ""
    if _typescript_unresolved_identifier_is_array_length_assertion(line, missing_symbol):
        return "unknown"
    for param in _typescript_function_param_names_for_line(lines, target_index):
        if _typescript_identifier_alias_matches(missing_symbol, param):
            return param
    return ""


def _typescript_unresolved_identifier_is_array_length_assertion(line: str, missing_symbol: str) -> bool:
    if not _TS_IDENTIFIER_RE.fullmatch(missing_symbol):
        return False
    pattern = _TS_UNRESOLVED_ARRAY_ASSERTION_LENGTH_ASSIGNMENT_TEMPLATE.format(symbol=re.escape(missing_symbol))
    return bool(re.search(pattern, str(line or "")))


def _typescript_function_param_names_for_line(lines: Sequence[str], target_index: int) -> list[str]:
    for start_index in range(target_index, -1, -1):
        line_body = lines[start_index].rstrip("\r\n")
        match = _TS_FUNCTION_DECLARATION_LINE_RE.match(line_body) or _TS_ARROW_FUNCTION_DECLARATION_LINE_RE.match(
            line_body
        )
        if not match:
            continue
        if not _typescript_line_is_inside_scope(lines, start_index, target_index):
            continue
        return _parse_typescript_param_names(str(match.group("params") or ""))
    return []


def _typescript_line_is_inside_scope(lines: Sequence[str], start_index: int, target_index: int) -> bool:
    depth = 0
    for index in range(start_index, target_index + 1):
        line_body = lines[index].rstrip("\r\n")
        depth += line_body.count("{")
        depth -= line_body.count("}")
        if index < target_index and depth <= 0:
            return False
    return depth > 0


def _parse_typescript_param_names(params_text: str) -> list[str]:
    names: list[str] = []
    for raw_param in _split_typescript_params(params_text):
        param = raw_param.split("=", 1)[0].split(":", 1)[0].strip().removeprefix("...").strip()
        if _TS_IDENTIFIER_RE.fullmatch(param):
            names.append(param)
    return names


def _typescript_identifier_alias_matches(missing_symbol: str, candidate: str) -> bool:
    missing_lower = missing_symbol.lower()
    candidate_lower = candidate.lower()
    if not candidate_lower or missing_lower == candidate_lower:
        return False
    prefixes = ("new", "next", "updated", "current", "previous", "prev")
    return any(missing_lower == f"{prefix}{candidate_lower}" for prefix in prefixes)


def _parse_typescript_missing_test_global_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        for match in _TS_CANNOT_FIND_TEST_GLOBAL_RAW_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            path = _normalize_repair_path(str(match.group("file") or ""))
            symbol = str(match.group("symbol") or "")
            if path.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")) and symbol in _TS_TEST_GLOBAL_NAMES:
                parsed.append({"file": path, "symbol": symbol})
    return parsed


def _add_vitest_import_to_typescript_test(text: str, symbols: set[str]) -> str:
    requested = sorted(symbol for symbol in symbols if symbol in _TS_TEST_GLOBAL_NAMES)
    if not requested:
        return text
    match = _TS_VITEST_IMPORT_RE.search(text)
    if match:
        existing = {token.strip() for token in str(match.group("symbols") or "").split(",") if token.strip()}
        replacement = f"import {{ {', '.join(sorted(existing | set(requested)))} }} from 'vitest';"
        return text[: match.start()] + replacement + text[match.end() :]
    return f"import {{ {', '.join(requested)} }} from 'vitest';\n{text}"


def _prepend_typescript_vitest_import_operation(
    *,
    path: str,
    original: str,
    symbols: set[str],
) -> RepairOperation | None:
    requested = sorted(symbol for symbol in symbols if symbol in _TS_TEST_GLOBAL_NAMES)
    if not requested or not original:
        return None
    first_line_end = original.find("\n")
    if first_line_end < 0:
        span_start = 0
        span_end = len(original)
        expected = original
        replacement = f"import {{ {', '.join(requested)} }} from 'vitest';\n{original}"
    else:
        span_start = 0
        span_end = first_line_end + 1
        expected = original[:span_end]
        replacement = f"import {{ {', '.join(requested)} }} from 'vitest';\n{expected}"
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=span_start,
        span_end=span_end,
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(original),
        metadata={
            "repair_kind": "typescript_vitest_global_import",
            "symbols": tuple(requested),
            "prepend_import": True,
        },
    )


def _typescript_vitest_manifest_operations(package_text: str) -> tuple[RepairOperation, ...]:
    payload = _json_object(package_text)
    operations: list[RepairOperation] = []
    scripts_raw = payload.get("scripts")
    scripts = dict(scripts_raw) if isinstance(scripts_raw, Mapping) else {}
    if "vitest" not in str(scripts.get("test") or ""):
        scripts["test"] = "vitest run"
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("scripts",),
                value=dict(sorted(scripts.items())),
                before_hash=sha256_text(package_text),
                metadata={"repair_kind": "typescript_vitest_test_script"},
            )
        )
    dev_deps_raw = payload.get("devDependencies")
    dev_deps = dict(dev_deps_raw) if isinstance(dev_deps_raw, Mapping) else {}
    dependencies_raw = payload.get("dependencies")
    dependencies = dict(dependencies_raw) if isinstance(dependencies_raw, Mapping) else {}
    if "vitest" not in dev_deps and "vitest" not in dependencies:
        dev_deps["vitest"] = "^2.1.8"
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("devDependencies",),
                value=dict(sorted(dev_deps.items())),
                before_hash=sha256_text(package_text),
                metadata={"repair_kind": "typescript_vitest_dev_dependency"},
            )
        )
    return tuple(operations)


def _parse_typescript_zod_type_class_collision_paths(diagnostics: Sequence[RepairDiagnostic]) -> list[str]:
    paths: list[str] = []
    for diagnostic in diagnostics:
        match = _TS_ZOD_TYPE_CLASS_COLLISION_ERROR_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)


def _repair_typescript_zod_type_class_collision(text: str) -> str:
    token = str(text or "")
    changed = False

    def class_exists(name: str) -> bool:
        return bool(re.search(rf"(?:^|\n)\s*(?:export\s+)?class\s+{re.escape(name)}\b", token))

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        name = str(match.group("name") or "")
        if not class_exists(name):
            return match.group(0)
        changed = True
        return f"{match.group('indent')}{match.group('export') or ''}type {name}Data = {match.group('infer')};"

    repaired = _TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE.sub(replace, token)
    if not changed:
        return token

    for match in _TS_ZOD_INFERRED_TYPE_ALIAS_LINE_RE.finditer(token):
        name = str(match.group("name") or "").strip()
        if not name or not class_exists(name):
            continue
        new_name = f"{name}Data"
        repaired = re.sub(
            rf"(\bconstructor\s*\([^)]*\bdata\s*:\s*){re.escape(name)}\b",
            rf"\g<1>{new_name}",
            repaired,
        )
        repaired = re.sub(
            rf"(\b(?:public|private|protected|readonly\s+)*data\s*:\s*){re.escape(name)}\b",
            rf"\g<1>{new_name}",
            repaired,
        )
    return repaired


def _export_existing_typescript_declaration(text: str, symbol: str) -> str:
    escaped = re.escape(symbol)
    declaration_re = re.compile(
        rf"(?m)^(?P<indent>\s*)(?P<declare>declare\s+)?(?P<kind>(?:abstract\s+)?class|function|interface|type|const|let|var|enum)\s+{escaped}\b"
    )

    def replace(match: re.Match[str]) -> str:
        return f"{match.group('indent')}export {match.group('declare') or ''}{match.group('kind')} {symbol}"

    return declaration_re.sub(replace, text, count=1)


def _repair_object_property_semicolon_line(line_body: str) -> str:
    match = _TS_OBJECT_PROPERTY_SEMICOLON_LINE_RE.match(line_body)
    if match:
        return f"{match.group('indent')}{match.group('name')},"
    value_match = _TS_OBJECT_PROPERTY_VALUE_SEMICOLON_LINE_RE.match(line_body)
    if value_match:
        return f"{value_match.group('indent')}{value_match.group('property').rstrip()},"
    return line_body


def _object_property_line_needs_previous_comma(
    line_body: str,
    repaired_lines: list[str],
) -> bool:
    if not repaired_lines or not _TS_OBJECT_PROPERTY_KEY_LINE_RE.match(line_body):
        return False
    previous = repaired_lines[-1].rstrip("\r\n")
    previous_stripped = previous.rstrip()
    if not previous_stripped or previous_stripped.endswith((",", "{", "[", "(", ":", ";")):
        return False
    return not previous_stripped.lstrip().startswith(("//", "*", "/*"))


def _append_object_property_comma(line: str) -> str:
    line_body = line.rstrip("\r\n")
    newline = line[len(line_body) :]
    return f"{line_body.rstrip()},{newline}"


def _repair_typescript_multiline_dom_handle_declarations(
    text: str,
    symbols: set[str],
) -> tuple[str, list[str]]:
    guarded: list[str] = []
    declaration_re = re.compile(
        r"(?ms)^(?P<indent>\s*)(?P<kind>const|let|var)\s+"
        r"(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
        r"(?P<source>(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\(.*?\)\s+as\s+(?P<type>[^;\n]*\bnull\b[^;\n]*)\s*;)"
    )

    def _replace(match: re.Match[str]) -> str:
        symbol = str(match.group("symbol") or "").strip()
        if symbols and symbol not in symbols:
            return match.group(0)
        source = str(match.group("source") or "")
        narrowed_source = re.sub(r"\s*\|\s*null\b", "", source)
        narrowed_source = re.sub(r"\bnull\s*\|\s*", "", narrowed_source)
        if narrowed_source == source:
            return match.group(0)
        guarded.append(symbol)
        declaration = f"{match.group('indent')}{match.group('kind')} {symbol} = {narrowed_source}"
        following = text[match.end() : match.end() + 240]
        if _typescript_nullable_guard_in_text_window(following, symbol):
            return declaration
        indent = str(match.group("indent") or "")
        return (
            f"{declaration}\n"
            f"{indent}if (!{symbol}) {{\n"
            f'{indent}  throw new Error("DOM element unavailable: {symbol}");\n'
            f"{indent}}}"
        )

    repaired = declaration_re.sub(_replace, text)
    return repaired, _dedupe_preserve_order(guarded)


def _typescript_canvas_context_non_null_assertion_line(line: str) -> str:
    if re.search(r"\.getContext\(\s*['\"]2d['\"]\s*\)!", line):
        return line
    return re.sub(r"(\.getContext\(\s*['\"]2d['\"]\s*\))(\s*;?\s*)$", r"\1!\2", line)


def _typescript_dom_handle_non_null_assertion_line(line: str) -> str:
    if "| null" in line or "null |" in line:
        narrowed = re.sub(r"\s*\|\s*null\b", "", line)
        narrowed = re.sub(r"\bnull\s*\|\s*", "", narrowed)
        return narrowed
    if re.search(
        r"(?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\([^;\n]*\)!",
        line,
    ):
        return line
    return re.sub(
        r"((?:document\.(?:getElementById|querySelector)|"
        r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
        r"\s*\([^;\n]*\))",
        r"\1!",
        line,
        count=1,
    )


def _typescript_nullable_guard_follows(lines: list[str], index: int, symbol: str) -> bool:
    window = "\n".join(lines[index + 1 : index + 7])
    return _typescript_nullable_guard_in_text_window(window, symbol)


def _typescript_nullable_guard_in_text_window(window: str, symbol: str) -> bool:
    compact = re.sub(r"\s+", "", window)
    return (
        f"if(!{symbol})" in compact
        or f"if({symbol}===null)" in compact
        or f"if({symbol}==null)" in compact
        or f"if(null==={symbol})" in compact
        or f"if(null=={symbol})" in compact
    )


def _parse_nullable_canvas_context_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[str] | None]:
    by_path: dict[str, set[str] | None] = {}
    for diagnostic in diagnostics:
        _add_nullable_targets_from_raw(by_path, diagnostic.raw or diagnostic.message)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path:
            continue
        code = diagnostic.code.lower()
        message = diagnostic.message or diagnostic.raw
        if code in {"typescript_ts18047", "typescript_ts18048"}:
            match = (
                _TS_POSSIBLY_UNDEFINED_MESSAGE_RE.search(message)
                if code == "typescript_ts18048"
                else _TS_POSSIBLY_NULL_MESSAGE_RE.search(message)
            )
            symbol = str(match.group("symbol") or "").strip() if match else ""
            if _typescript_nullable_target_is_safe(symbol):
                _add_nullable_target(by_path, path, symbol)
        elif code == "typescript_ts2345" and "null" in message.lower() and "not assignable" in message.lower():
            _add_nullable_target(by_path, path, "")
    return by_path


def _parse_duplicate_object_property_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[int]]:
    by_path: dict[str, set[int]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_DUPLICATE_OBJECT_PROPERTY_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            if path and line > 0:
                by_path.setdefault(path, set()).add(line)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts1117" and path and diagnostic.line:
            by_path.setdefault(path, set()).add(int(diagnostic.line))
    return by_path


def _parse_enum_member_separator_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[int]]:
    by_path: dict[str, set[int]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_ENUM_MEMBER_SEPARATOR_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            if path and line > 0:
                by_path.setdefault(path, set()).add(line)
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts1357" and path and diagnostic.line:
            by_path.setdefault(path, set()).add(int(diagnostic.line))
    return by_path


def _parse_missing_closing_brace_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_MISSING_CLOSING_BRACE_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            if path and line > 0 and column > 0:
                by_path.setdefault(path, set()).add((line, column))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_missing_closing_brace_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column)))
    return by_path


def _parse_number_to_string_argument_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            if path and line > 0 and column > 0:
                by_path.setdefault(path, set()).add((line, column))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_number_to_string_argument(diagnostic) and path and diagnostic.line and diagnostic.column:
            by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column)))
    return by_path


def _parse_number_property_call_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int]]]:
    by_path: dict[str, set[tuple[int, int]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_NUMBER_PROPERTY_CALL_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            if path and line > 0 and column > 0:
                by_path.setdefault(path, set()).add((line, column))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_number_property_call_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column)))
    return by_path


def _parse_readonly_assignment_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int, str]]]:
    by_path: dict[str, set[tuple[int, int, str]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_READONLY_ASSIGNMENT_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            prop = str(match.group("property") or "").strip()
            if path and line > 0 and column > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((line, column, prop))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_readonly_assignment_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            prop_match = re.search(r"Cannot assign to ['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]", text)
            prop = str(prop_match.group("property") or "").strip() if prop_match else ""
            if _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column), prop))
    return by_path


def _parse_shorthand_property_scope_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int, str]]]:
    by_path: dict[str, set[tuple[int, int, str]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            prop = str(match.group("property") or "").strip()
            if path and line > 0 and column > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((line, column, prop))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_shorthand_property_scope_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            prop_match = re.search(
                r"shorthand property ['\"](?P<property>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
                text,
                re.IGNORECASE,
            )
            prop = str(prop_match.group("property") or "").strip() if prop_match else ""
            if _TS_IDENTIFIER_RE.fullmatch(prop):
                by_path.setdefault(path, set()).add((int(diagnostic.line), int(diagnostic.column), prop))
    return by_path


def _parse_unknown_member_access_targets(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _append_target(*, path: str, line: object, column: object, receiver: str, member: str) -> None:
        normalized_path = _normalize_repair_path(path)
        normalized_line = str(line or "")
        normalized_receiver = str(receiver or "")
        normalized_member = str(member or "")
        key = (normalized_path, normalized_line, normalized_receiver, normalized_member)
        if key in seen:
            return
        seen.add(key)
        parsed.append(
            {
                "file": normalized_path,
                "line": normalized_line,
                "column": str(column or ""),
                "receiver": normalized_receiver,
                "member": normalized_member,
            }
        )

    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_UNKNOWN_MEMBER_ACCESS_RAW_RE.finditer(text):
            _append_target(
                path=str(match.group("file") or ""),
                line=match.group("line"),
                column=match.group("col"),
                receiver=str(match.group("receiver") or ""),
                member=str(match.group("member") or ""),
            )
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if diagnostic.code.lower() == "typescript_ts18046" and path and diagnostic.line:
            inline_match = re.search(
                r"['\"](?P<receiver>[A-Za-z_$][A-Za-z0-9_$]*)\.(?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]"
                r"\s+is\s+of\s+type\s+['\"]unknown['\"]",
                text,
                re.IGNORECASE,
            )
            if inline_match:
                _append_target(
                    path=path,
                    line=diagnostic.line,
                    column=diagnostic.column,
                    receiver=str(inline_match.group("receiver") or ""),
                    member=str(inline_match.group("member") or ""),
                )
    return [
        item
        for item in parsed
        if item["file"]
        and _to_positive_int(item.get("line")) > 0
        and _TS_IDENTIFIER_RE.fullmatch(item["receiver"])
        and _TS_IDENTIFIER_RE.fullmatch(item["member"])
    ]


def _parse_string_literal_suggestion_targets(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, set[tuple[int, int, str, str]]]:
    by_path: dict[str, set[tuple[int, int, str, str]]] = {}
    for diagnostic in diagnostics:
        text = str(diagnostic.raw or diagnostic.message or "")
        for match in _TS_STRING_LITERAL_SUGGESTION_RAW_RE.finditer(text):
            path = _normalize_repair_path(str(match.group("file") or ""))
            line = _to_positive_int(match.group("line"))
            column = _to_positive_int(match.group("col"))
            actual = _strip_typescript_literal_type(str(match.group("actual") or "").strip())
            suggestion = _strip_typescript_literal_type(str(match.group("suggestion") or "").strip())
            if _valid_string_literal_suggestion_target(path, line, column, actual, suggestion):
                by_path.setdefault(path, set()).add((line, column, actual, suggestion))
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if _is_string_literal_suggestion_diagnostic(diagnostic) and path and diagnostic.line and diagnostic.column:
            inline_match = re.search(
                r"Type (?P<actual_quote>['\"])(?P<actual>.*?)(?P=actual_quote) is not assignable to type "
                r"(?P<target_quote>['\"]).*?(?P=target_quote)\.\s+Did you mean "
                r"(?P<suggestion_quote>['\"])(?P<suggestion>.*?)(?P=suggestion_quote)\?",
                text,
            )
            actual = (
                _strip_typescript_literal_type(str(inline_match.group("actual") or "").strip()) if inline_match else ""
            )
            suggestion = (
                _strip_typescript_literal_type(str(inline_match.group("suggestion") or "").strip())
                if inline_match
                else ""
            )
            line = int(diagnostic.line)
            column = int(diagnostic.column)
            if _valid_string_literal_suggestion_target(path, line, column, actual, suggestion):
                by_path.setdefault(path, set()).add((line, column, actual, suggestion))
    return by_path


def _strip_typescript_literal_type(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        return normalized[1:-1]
    return normalized


def _valid_string_literal_suggestion_target(
    path: str,
    line: int,
    column: int,
    actual: str,
    suggestion: str,
) -> bool:
    return (
        bool(path)
        and line > 0
        and column > 0
        and bool(actual)
        and bool(suggestion)
        and actual != suggestion
        and "\n" not in actual
        and "\r" not in actual
        and "\n" not in suggestion
        and "\r" not in suggestion
        and len(actual) <= 160
        and len(suggestion) <= 160
    )


def _is_missing_closing_brace_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    return diagnostic.code.lower() == "typescript_ts1005" and "expected" in message and "}" in message


def _is_number_to_string_argument(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    return (
        diagnostic.code.lower() == "typescript_ts2345"
        and "argument of type" in message
        and "number" in message
        and "not assignable" in message
        and "string" in message
    )


def _is_number_property_call_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    return diagnostic.code.lower() == "typescript_ts2349" and "not callable" in message


def _is_readonly_assignment_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code.lower() == "typescript_ts2540"
        and "cannot assign to" in message
        and "read-only property" in message
    )


def _is_shorthand_property_scope_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code.lower() == "typescript_ts18004"
        and "no value exists in scope" in message
        and "shorthand property" in message
    )


def _is_string_literal_suggestion_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return (
        diagnostic.code.lower() == "typescript_ts2820"
        and "not assignable to type" in message
        and "did you mean" in message
    )


def _is_number_to_function_argument(diagnostic: RepairDiagnostic) -> bool:
    message = str(diagnostic.message or diagnostic.raw or "").lower()
    if diagnostic.code.lower() == "typescript_ts2345" and "number" in message and "(n: number) => number" in message:
        return True
    return bool(_TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE.search(str(diagnostic.raw or diagnostic.message or "")))


def _has_number_to_function_argument_diagnostic(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    return any(_is_number_to_function_argument(diagnostic) for diagnostic in diagnostics)


def _diagnostic_targets_path(diagnostic: RepairDiagnostic, path: str) -> bool:
    normalized_path = _normalize_repair_path(str(diagnostic.path or ""))
    if normalized_path == path:
        return True
    return path in {
        _normalize_repair_path(str(match.group("file") or ""))
        for pattern in (
            _TS_MISSING_CLOSING_BRACE_RAW_RE,
            _TS_NUMBER_PROPERTY_CALL_RAW_RE,
            _TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE,
            _TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE,
            _TS_READONLY_ASSIGNMENT_RAW_RE,
            _TS_SHORTHAND_PROPERTY_SCOPE_RAW_RE,
            _TS_STRING_LITERAL_SUGGESTION_RAW_RE,
            _TS_UNKNOWN_MEMBER_ACCESS_RAW_RE,
        )
        for match in pattern.finditer(str(diagnostic.raw or diagnostic.message or ""))
    }


def _missing_closing_brace_operation(*, path: str, content: str) -> RepairOperation | None:
    missing_count = _typescript_brace_balance_delta(content)
    if missing_count <= 0 or missing_count > 8:
        return None
    repaired = repair_typescript_missing_closing_braces(content)
    if repaired == content:
        return None
    start = len(content.rstrip())
    unique_context = content[max(0, start - 160) : len(content)]
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=start,
        span_end=len(content),
        expected=content[start:],
        replacement=repaired[start:],
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_missing_closing_brace",
            "missing_count": missing_count,
            "unique_context": unique_context,
        },
    )


def _number_to_string_argument_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    columns_by_line: dict[int, list[int]] = {}
    for line, column in targets:
        if line > 0 and column > 0:
            columns_by_line.setdefault(line, []).append(column)
    operations: list[RepairOperation] = []
    for line_number in sorted(columns_by_line):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original_line = lines[line_index]
        line_body = original_line.rstrip("\r\n")
        newline = original_line[len(line_body) :]
        repaired_line_body = line_body
        for column in sorted(set(columns_by_line[line_number]), reverse=True):
            repaired_line_body = _wrap_typescript_argument_at_column_as_string(repaired_line_body, column)
        if repaired_line_body == line_body:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index],
                span_end=offsets[line_index + 1],
                expected=original_line,
                replacement=f"{repaired_line_body}{newline}",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_number_to_string_argument",
                    "line": line_number,
                    "columns": tuple(sorted(set(columns_by_line[line_number]))),
                },
            )
        )
    return tuple(operations)


def _number_property_call_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    columns_by_line: dict[int, set[int]] = {}
    for line, column in targets:
        if line > 0 and column > 0:
            columns_by_line.setdefault(line, set()).add(column)
    operations: list[RepairOperation] = []
    for line_number in sorted(columns_by_line):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        line_body = lines[line_index].rstrip("\r\n")
        candidate = _number_property_call_candidate(line_body, columns_by_line[line_number])
        if candidate is None:
            continue
        call_start, call_end, paren_start, paren_end = candidate
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index] + paren_start,
                span_end=offsets[line_index] + paren_end,
                expected=line_body[paren_start:paren_end],
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_number_property_call",
                    "line": line_number,
                    "columns": tuple(sorted(columns_by_line[line_number])),
                    "call_expression": line_body[call_start:call_end],
                    "unique_context": lines[line_index],
                },
            )
        )
    return tuple(operations)


def _number_property_call_candidate(
    line: str,
    columns: set[int],
) -> tuple[int, int, int, int] | None:
    matches = [
        match
        for match in _TS_ZERO_ARG_PROPERTY_CALL_RE.finditer(line)
        if _property_call_is_near_columns(match.start(), match.end(), columns)
    ]
    if len(matches) != 1:
        return None
    match = matches[0]
    paren_start = str(line).rfind("(", match.start(), match.end())
    if paren_start < 0:
        return None
    paren_end = str(line).find(")", paren_start)
    if paren_end < 0:
        return None
    return match.start(), match.end(), paren_start, paren_end + 1


def _property_call_is_near_columns(start: int, end: int, columns: set[int]) -> bool:
    if not columns:
        return True
    for column in columns:
        zero_based = max(0, column - 1)
        if start <= zero_based <= end:
            return True
        if zero_based < start and start - zero_based <= 120:
            return True
    return False


def _string_literal_suggestion_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int, str, str]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    operations: list[RepairOperation] = []
    seen_lines: set[int] = set()
    for line_number, column, actual, suggestion in sorted(targets):
        if line_number in seen_lines:
            continue
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original_line = lines[line_index]
        line_body = original_line.rstrip("\r\n")
        newline = original_line[len(line_body) :]
        matches = list(_string_literal_matches(line_body, actual))
        if len(matches) != 1:
            continue
        match = matches[0]
        if not _column_is_near_span(column, match.start(), match.end()):
            continue
        quote = str(match.group("quote") or '"')
        replacement_literal = f"{quote}{_escape_typescript_string_literal(suggestion, quote)}{quote}"
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index] + match.start(),
                span_end=offsets[line_index] + match.end(),
                expected=match.group(0),
                replacement=replacement_literal,
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_string_literal_suggestion",
                    "actual": actual,
                    "suggestion": suggestion,
                    "line": line_number,
                    "column": column,
                    "unique_context": f"{line_body}{newline}",
                },
            )
        )
        seen_lines.add(line_number)
    return tuple(operations)


def _shorthand_property_scope_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int, str]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    targets_by_line: dict[int, set[str]] = {}
    for line, _column, prop in targets:
        if line > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
            targets_by_line.setdefault(line, set()).add(prop)
    operations: list[RepairOperation] = []
    for line_number in sorted(targets_by_line):
        line_index = line_number - 1
        if line_index < 0 or line_index >= len(lines):
            continue
        original_line = lines[line_index]
        line_body = original_line.rstrip("\r\n")
        newline = original_line[len(line_body) :]
        repaired_line_body, removed = _remove_shorthand_properties(line_body, targets_by_line[line_number])
        if repaired_line_body == line_body or not removed:
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=offsets[line_index],
                span_end=offsets[line_index + 1],
                expected=original_line,
                replacement=f"{repaired_line_body}{newline}",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_shorthand_property_scope",
                    "line": line_number,
                    "property": ",".join(sorted(removed)),
                    "unique_context": original_line,
                },
            )
        )
    return tuple(operations)


def _remove_shorthand_properties(line: str, properties: set[str]) -> tuple[str, tuple[str, ...]]:
    if not properties or "{" not in line or "}" not in line:
        return line, ()
    open_index = line.find("{")
    close_index = line.rfind("}")
    if close_index <= open_index:
        return line, ()
    inner = line[open_index + 1 : close_index]
    if "{" in inner or "}" in inner:
        return line, ()
    parts = [part.strip() for part in inner.split(",")]
    kept: list[str] = []
    removed: list[str] = []
    for part in parts:
        if not part:
            continue
        if part in properties and _TS_IDENTIFIER_RE.fullmatch(part):
            removed.append(part)
            continue
        kept.append(part)
    if not removed:
        return line, ()
    replacement_inner = f" {', '.join(kept)} " if kept else ""
    return f"{line[: open_index + 1]}{replacement_inner}{line[close_index:]}", tuple(sorted(removed))


def _string_literal_matches(line: str, actual: str) -> tuple[re.Match[str], ...]:
    if not actual:
        return ()
    double_escaped = _escape_typescript_string_literal(actual, '"')
    pattern = re.compile(rf"(?P<quote>['\"]){re.escape(double_escaped)}(?P=quote)")
    matches = list(pattern.finditer(line))
    if matches:
        return tuple(matches)
    single_escaped = _escape_typescript_string_literal(actual, "'")
    pattern = re.compile(rf"(?P<quote>['\"]){re.escape(single_escaped)}(?P=quote)")
    return tuple(pattern.finditer(line))


def _column_is_near_span(column: int, span_start: int, span_end: int) -> bool:
    if column <= 0:
        return True
    zero_based = column - 1
    if span_start <= zero_based <= span_end:
        return True
    return zero_based < span_start and span_start - zero_based <= 120


def _escape_typescript_string_literal(value: str, quote: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\")
    if quote == "'":
        return escaped.replace("'", "\\'")
    return escaped.replace('"', '\\"')


def _readonly_assignment_operations(
    *,
    path: str,
    content: str,
    targets: set[tuple[int, int, str]],
) -> tuple[RepairOperation, ...]:
    if not targets:
        return ()
    by_property: dict[str, set[int]] = {}
    for line, _column, prop in targets:
        if line > 0 and _TS_IDENTIFIER_RE.fullmatch(prop):
            by_property.setdefault(prop, set()).add(line)
    if not by_property:
        return ()
    before_hash = sha256_text(content)
    lines = str(content or "").splitlines(keepends=True)
    operations: list[RepairOperation] = []
    for prop in sorted(by_property):
        if not all(_line_mentions_assignment_property(lines, line, prop) for line in by_property[prop]):
            continue
        declaration_spans = _readonly_property_declaration_spans(content, prop)
        if len(declaration_spans) != 1:
            continue
        line_index, readonly_start, readonly_end = declaration_spans[0]
        original_line = lines[line_index]
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=readonly_start,
                span_end=readonly_end,
                expected=content[readonly_start:readonly_end],
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_readonly_assignment",
                    "property": prop,
                    "diagnostic_lines": tuple(sorted(by_property[prop])),
                    "declaration_line": line_index + 1,
                    "unique_context": original_line,
                },
            )
        )
    return tuple(operations)


def _line_mentions_assignment_property(lines: Sequence[str], line_number: int, prop: str) -> bool:
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return False
    line = str(lines[line_index] or "")
    escaped = re.escape(prop)
    return bool(re.search(rf"(?:\.{escaped}\b|\[['\"]{escaped}['\"]\])\s*(?:[+\-*/%]?=|\+\+|--)", line))


def _readonly_property_declaration_spans(content: str, prop: str) -> list[tuple[int, int, int]]:
    if not _TS_IDENTIFIER_RE.fullmatch(prop):
        return []
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    pattern = re.compile(
        rf"^(?P<prefix>\s*(?:(?:public|private|protected)\s+)?)"
        rf"(?P<readonly>readonly\s+)(?P<property>{re.escape(prop)})(?=\s*[?:!:])"
    )
    spans: list[tuple[int, int, int]] = []
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if not match:
            continue
        spans.append((index, offsets[index] + match.start("readonly"), offsets[index] + match.end("readonly")))
    return spans


def _canvas_scale_return_type_operation(*, path: str, content: str) -> RepairOperation | None:
    if "scaleToCanvas" not in content or "sx:" not in content or "sy:" not in content:
        return None
    if not re.search(r"sx\s*:\s*\([^)]*number[^)]*\)\s*=>", content):
        return None
    if not re.search(r"sy\s*:\s*\([^)]*number[^)]*\)\s*=>", content):
        return None
    match = _TS_CANVAS_SCALE_RETURN_TYPE_RE.search(content)
    if not match:
        return None
    replacement = "{ sx: (n: number) => number; sy: (n: number) => number; scale: number }"
    expected = str(match.group("return_type") or "")
    if expected == replacement:
        return None
    return RepairOperation(
        kind="text_replace",
        path=path,
        span_start=match.start("return_type"),
        span_end=match.end("return_type"),
        expected=expected,
        replacement=replacement,
        before_hash=sha256_text(content),
        metadata={
            "repair_kind": "typescript_canvas_scale_return_type",
            "symbol": "scaleToCanvas",
        },
    )


def _wrap_typescript_argument_at_column_as_string(line: str, column: int) -> str:
    span = _find_typescript_argument_span_at_column(line, column)
    if span is None:
        return line
    start, end = span
    argument = line[start:end]
    stripped = argument.strip()
    if not stripped or stripped.startswith(("String(", '"', "'", "`")):
        return line
    leading = argument[: len(argument) - len(argument.lstrip())]
    trailing = argument[len(argument.rstrip()) :]
    replacement = f"{leading}String({stripped}){trailing}"
    return line[:start] + replacement + line[end:]


def _find_typescript_argument_span_at_column(line: str, column: int) -> tuple[int, int] | None:
    index = max(0, min(len(line), int(column) - 1))
    open_index = line.rfind("(", 0, index + 1)
    close_index = line.find(")", index)
    if open_index < 0 or close_index < 0 or close_index <= open_index:
        return None
    spans = _split_typescript_argument_spans(line, open_index + 1, close_index)
    for start, end in spans:
        if start <= index <= end:
            if "=>" in line[start:end]:
                return None
            return start, end
    return None


def _find_matching_paren(text: str, open_paren: int) -> int:
    if open_paren < 0 or open_paren >= len(text) or text[open_paren] != "(":
        return -1
    depth = 0
    quote = ""
    escaped = False
    for index in range(open_paren, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_typescript_argument_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    arg_start = start
    quote = ""
    index = start
    while index < end:
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            spans.append((arg_start, index))
            arg_start = index + 1
        index += 1
    if arg_start <= end:
        spans.append((arg_start, end))
    return spans


def _typescript_brace_balance_delta(source: str) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


def _enum_member_separator_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
) -> tuple[RepairOperation, ...]:
    if not line_numbers:
        return ()
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    brace_depth = 0
    enum_depth: int | None = None
    offset = 0
    for line_number, line in enumerate(str(content or "").splitlines(keepends=True), start=1):
        line_start = offset
        line_end = offset + len(line)
        offset = line_end
        line_body = line.rstrip("\r\n")
        newline = line[len(line_body) :]
        if enum_depth is not None and line_number in line_numbers:
            repaired_line = _repair_typescript_enum_member_line(line_body)
            if repaired_line != line_body:
                operations.append(
                    RepairOperation(
                        kind="text_replace",
                        path=path,
                        span_start=line_start,
                        span_end=line_end,
                        expected=line,
                        replacement=f"{repaired_line}{newline}",
                        before_hash=before_hash,
                        metadata={
                            "repair_kind": "typescript_enum_member_separator",
                            "line": line_number,
                        },
                    )
                )

        opens = line_body.count("{")
        closes = line_body.count("}")
        if enum_depth is None and _TS_ENUM_DECLARATION_LINE_RE.search(line_body):
            enum_depth = brace_depth + max(opens, 1)
        brace_depth += opens - closes
        if enum_depth is not None and brace_depth < enum_depth:
            enum_depth = None
    return tuple(operations)


def _repair_typescript_enum_member_line(line_body: str) -> str:
    match = _TS_ENUM_MEMBER_LINE_RE.match(line_body)
    if not match:
        return line_body
    if match.group("separator") == ",":
        return line_body
    prefix = str(match.group("prefix") or "").rstrip()
    if not prefix or prefix.lstrip().startswith(("enum ", "export ")):
        return line_body
    comment = str(match.group("comment") or "")
    space = " " if comment else str(match.group("space") or "")
    return f"{prefix},{space}{comment}"


def _duplicate_object_property_delete_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
) -> tuple[RepairOperation, ...]:
    if not line_numbers:
        return ()
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    offset = 0
    lines = str(content or "").splitlines(keepends=True)
    for line_no, line in enumerate(lines, start=1):
        line_start = offset
        line_end = offset + len(line)
        offset = line_end
        if line_no not in line_numbers:
            continue
        if not _looks_like_single_line_typescript_object_property(line.rstrip("\r\n")):
            continue
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=line_start,
                span_end=line_end,
                expected=line,
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_duplicate_object_property",
                    "line": line_no,
                },
            )
        )
    return tuple(operations)


def _looks_like_single_line_typescript_object_property(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped or ":" not in stripped:
        return False
    if stripped.startswith(("case ", "default", "return ", "if ", "for ", "while ", "//", "/*", "*")):
        return False
    property_re = re.compile(r"^(?:[A-Za-z_$][A-Za-z0-9_$]*|['\"][^'\"]+['\"]|\[[^\]]+\])\s*:\s*.+,?\s*(?://.*)?$")
    return bool(property_re.match(stripped))


def _add_nullable_targets_from_raw(targets: dict[str, set[str] | None], raw: str) -> None:
    text = str(raw or "")
    for match in _TS_POSSIBLY_NULL_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        symbol = str(match.group("symbol") or "").strip()
        if path and _typescript_nullable_target_is_safe(symbol):
            _add_nullable_target(targets, path, symbol)
    for match in _TS_POSSIBLY_UNDEFINED_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        symbol = str(match.group("symbol") or "").strip()
        if path and _typescript_nullable_target_is_safe(symbol):
            _add_nullable_target(targets, path, symbol)
    for match in _TS_NULLABLE_ARGUMENT_RAW_RE.finditer(text):
        path = _normalize_repair_path(str(match.group("file") or ""))
        if path:
            _add_nullable_target(targets, path, "")


def _add_nullable_target(targets: dict[str, set[str] | None], path: str, symbol: str) -> None:
    if not symbol:
        targets[path] = None
        return
    existing = targets.get(path)
    if existing is None and path in targets:
        return
    if existing is None:
        existing = set()
        targets[path] = existing
    existing.add(symbol)


def _text_replace_operations_from_repair(
    *,
    path: str,
    original: str,
    repaired: str,
    metadata: Mapping[str, object],
) -> tuple[RepairOperation, ...]:
    before_hash = sha256_text(original)
    operations: list[RepairOperation] = []
    original_lines = original.splitlines(keepends=True)
    repaired_lines = repaired.splitlines(keepends=True)
    original_offsets = _line_start_offsets(original_lines)
    matcher = SequenceMatcher(a=original_lines, b=repaired_lines, autojunk=False)
    for tag, start_line, end_line, replacement_start, replacement_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        start = original_offsets[start_line]
        end = original_offsets[end_line]
        expected = "".join(original_lines[start_line:end_line])
        operation_metadata = dict(metadata)
        if not expected:
            operation_metadata["expected_context_before"] = "".join(original_lines[max(0, start_line - 2) : start_line])
            operation_metadata["expected_context_after"] = "".join(
                original_lines[start_line : min(len(original_lines), start_line + 2)]
            )
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="".join(repaired_lines[replacement_start:replacement_end]),
                before_hash=before_hash,
                metadata=operation_metadata,
            )
        )
    return tuple(operations)


def _typescript_config_key_split_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
    seen_spans: set[tuple[str, int, int]],
) -> tuple[RepairOperation, ...]:
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    candidate_indexes = (
        {line_number - 1 for line_number in line_numbers if 0 < line_number <= len(lines)}
        if line_numbers
        else set(range(len(lines)))
    )
    for index in sorted(candidate_indexes):
        line = lines[index]
        line_without_newline = line.rstrip("\r\n")
        match = _TS_CONFIG_SPLIT_KEY_LINE_RE.match(line_without_newline)
        if match is None:
            continue
        left = str(match.group("left") or "")
        right = str(match.group("right") or "")
        replacement_key = f"{left}{right}"
        if replacement_key not in _TS_CONFIG_JOINABLE_KEYS:
            continue
        start = offsets[index] + match.start("left")
        end = offsets[index] + match.end("right")
        span_key = (path, start, end)
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        expected = content[start:end]
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement=replacement_key,
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_config_key_split",
                    "edit_strategy": "text_replace",
                    "line": index + 1,
                    "original_key": expected,
                    "replacement_key": replacement_key,
                },
            )
        )
    return tuple(operations)


def _is_typescript_dom_local_shim_diagnostic(
    diagnostic: RepairDiagnostic,
    *,
    base_files: Mapping[str, str],
) -> bool:
    code = str(diagnostic.code or "").lower()
    if code not in {"typescript_ts2339", "typescript_ts2739", "typescript_ts2740", "typescript_ts2741"}:
        return False
    path = _normalize_repair_path(str(diagnostic.path or ""))
    if not path.endswith((".ts", ".tsx")):
        return False
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if _is_typescript_dom_local_shim_type_conflict(code=code, text=text):
        return True
    return _is_typescript_dom_local_shim_member_gap(code=code, text=text) and _typescript_base_files_have_dom_lib(
        base_files
    )


def _is_typescript_dom_local_shim_type_conflict(*, code: str, text: str) -> bool:
    if code not in {"typescript_ts2739", "typescript_ts2740", "typescript_ts2741"}:
        return False
    return "missing" in text and any(name.lower() in text for name in _TS_LOCAL_DOM_SHIM_NAMES)


def _is_typescript_dom_local_shim_member_gap(*, code: str, text: str) -> bool:
    if code != "typescript_ts2339":
        return False
    return ("property 'createelement'" in text and ("getelementbyid" in text or "document" in text)) or (
        "property 'queryselector'" in text and "htmlelement" in text
    )


def _typescript_base_files_have_dom_lib(base_files: Mapping[str, str]) -> bool:
    for path, content in base_files.items():
        basename = _normalize_repair_path(str(path or "")).rsplit("/", maxsplit=1)[-1].lower()
        if not basename.startswith("tsconfig") or not basename.endswith(".json"):
            continue
        payload = _json_object(str(content or ""))
        compiler_options = payload.get("compilerOptions")
        if not isinstance(compiler_options, dict):
            continue
        libs = compiler_options.get("lib")
        if isinstance(libs, list) and any(str(lib).strip().lower() == "dom" for lib in libs):
            return True
    return False


def _remove_typescript_local_dom_shims(text: str) -> tuple[str, tuple[str, ...]]:
    lines = str(text or "").splitlines(keepends=True)
    kept: list[str] = []
    removed: list[str] = []
    index = 0
    while index < len(lines):
        symbol = _typescript_local_dom_shim_start_symbol(lines[index])
        if symbol:
            end_index = _typescript_block_end(lines, index)
            removed.append(symbol)
            index = end_index
            continue
        kept.append(lines[index])
        index += 1
    if not removed:
        return str(text or ""), ()
    repaired = "".join(kept)
    repaired = re.sub(r"\n{3,}", "\n\n", repaired)
    return repaired, tuple(dict.fromkeys(removed))


def _typescript_local_dom_shim_start_symbol(line: str) -> str:
    declare_match = _TS_LOCAL_DOM_DECLARE_CONST_START_RE.match(line)
    if declare_match:
        return str(declare_match.group("name") or "").strip()
    interface_match = _TS_LOCAL_DOM_INTERFACE_START_RE.match(line)
    if interface_match:
        return str(interface_match.group("name") or "").strip()
    return ""


def _typescript_block_end(lines: Sequence[str], start_index: int) -> int:
    depth = 0
    saw_open = False
    for index in range(start_index, len(lines)):
        line = str(lines[index] or "")
        open_count = line.count("{")
        close_count = line.count("}")
        saw_open = saw_open or open_count > 0
        depth += open_count - close_count
        if saw_open and depth <= 0:
            return index + 1
    return start_index + 1


def _typescript_expect_error_diagnostics_by_path(
    diagnostics: Sequence[RepairDiagnostic],
) -> dict[str, list[RepairDiagnostic]]:
    grouped: dict[str, list[RepairDiagnostic]] = {}
    for diagnostic in diagnostics:
        if not _is_typescript_expect_error_placement_diagnostic(diagnostic):
            continue
        path = _normalize_repair_path(str(diagnostic.path or ""))
        if not path.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx")):
            continue
        grouped.setdefault(path, []).append(diagnostic)
    return grouped


def _is_typescript_expect_error_placement_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    code = str(diagnostic.code or "").lower()
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    if code == "typescript_ts2578":
        return "@ts-expect-error" in text and "unused" in text
    if code == "typescript_ts2345":
        return "argument of type" in text and "not assignable to parameter of type" in text
    return False


def _repair_typescript_expect_error_placement(
    text: str,
    *,
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[str, tuple[dict[str, int | str], ...]]:
    lines = str(text or "").splitlines(keepends=True)
    unused_lines = sorted(
        {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if str(diagnostic.code or "").lower() == "typescript_ts2578" and int(diagnostic.line or 0) > 0
        }
    )
    assignability_lines = sorted(
        {
            int(diagnostic.line or 0)
            for diagnostic in diagnostics
            if str(diagnostic.code or "").lower() == "typescript_ts2345" and int(diagnostic.line or 0) > 0
        }
    )
    if not unused_lines or not assignability_lines:
        return str(text or ""), ()

    remove_indices: set[int] = set()
    insertions: dict[int, list[str]] = {}
    moved: list[dict[str, int | str]] = []
    for unused_line in unused_lines:
        unused_index = unused_line - 1
        if unused_index < 0 or unused_index >= len(lines):
            continue
        comment_line = lines[unused_index]
        if "@ts-expect-error" not in comment_line:
            continue
        target_line = next((line for line in assignability_lines if unused_line < line <= unused_line + 5), 0)
        target_index = target_line - 1
        if target_index < 0 or target_index >= len(lines):
            continue
        if _typescript_previous_line_is_expect_error(lines, target_index):
            continue
        target_indent = re.match(r"^\s*", lines[target_index])
        indent = target_indent.group(0) if target_indent else ""
        comment_text = comment_line.strip()
        newline = "\n" if comment_line.endswith("\n") else ""
        insertions.setdefault(target_index, []).append(f"{indent}{comment_text}{newline}")
        remove_indices.add(unused_index)
        moved.append({"from_line": unused_line, "to_line": target_line, "comment": comment_text})

    if not moved:
        return str(text or ""), ()

    repaired_lines: list[str] = []
    for index, line in enumerate(lines):
        repaired_lines.extend(insertions.get(index, ()))
        if index not in remove_indices:
            repaired_lines.append(line)
    return "".join(repaired_lines), tuple(moved)


def _typescript_previous_line_is_expect_error(lines: Sequence[str], target_index: int) -> bool:
    previous_index = target_index - 1
    while previous_index >= 0:
        previous = str(lines[previous_index] or "").strip()
        if not previous:
            previous_index -= 1
            continue
        return "@ts-expect-error" in previous
    return False


def _is_typescript_config_key_split_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code.lower() == "typescript_config_key_syntax":
        return True
    text = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return bool(
        _is_typescript_config_file(str(diagnostic.path or ""))
        and "expected" in text
        and "found" in text
        and "config" in text
    )


def _is_typescript_config_file(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return basename.endswith((".config.ts", ".config.tsx"))


def _is_typescript_test_block_residue_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    message = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    return diagnostic.code.lower() == "typescript_ts1128" and "declaration or statement expected" in message


def _is_typescript_test_file(path: str) -> bool:
    normalized = _normalize_repair_path(path).lower()
    if not normalized:
        return False
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return "/__tests__/" in f"/{normalized}" or basename.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))


def _typescript_test_block_residue_operations(
    *,
    path: str,
    content: str,
    line_numbers: set[int],
    seen_spans: set[tuple[str, int, int]],
) -> tuple[RepairOperation, ...]:
    if not line_numbers:
        return ()
    lines = str(content or "").splitlines(keepends=True)
    offsets = _line_start_offsets(lines)
    before_hash = sha256_text(content)
    operations: list[RepairOperation] = []
    for line_number in sorted(line_numbers):
        diagnostic_index = line_number - 1
        if diagnostic_index < 0 or diagnostic_index >= len(lines):
            continue
        residue_start = _find_typescript_test_block_residue_suffix_start(lines, diagnostic_index)
        if residue_start is None:
            continue
        start = offsets[residue_start]
        end = offsets[len(lines)]
        span_key = (path, start, end)
        if span_key in seen_spans:
            continue
        expected = content[start:end]
        if not expected.strip():
            continue
        seen_spans.add(span_key)
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected=expected,
                replacement="",
                before_hash=before_hash,
                metadata={
                    "repair_kind": "typescript_test_block_residue",
                    "edit_strategy": "text_replace",
                    "start_line": residue_start + 1,
                    "end_line": len(lines),
                    "diagnostic_line": line_number,
                },
            )
        )
    return tuple(operations)


def _find_typescript_test_block_residue_suffix_start(lines: Sequence[str], diagnostic_index: int) -> int | None:
    diagnostic_line = lines[diagnostic_index].rstrip("\r\n")
    if not _TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(diagnostic_line):
        return None
    search_start = max(0, diagnostic_index - 24)
    for index in range(search_start, diagnostic_index + 1):
        line_body = lines[index].rstrip("\r\n")
        if not re.match(r"^\s{2,}(?:assert\.|expect\()", line_body):
            continue
        if _typescript_test_block_residue_suffix_is_safe(lines, index, diagnostic_index):
            return index
    return None


def _typescript_test_block_residue_suffix_is_safe(
    lines: Sequence[str],
    start_index: int,
    diagnostic_index: int,
) -> bool:
    prefix = "".join(lines[:start_index])
    suffix = "".join(lines[start_index:])
    if _typescript_brace_balance_delta(prefix) != 0:
        return False
    if _typescript_brace_balance_delta(suffix) >= 0:
        return False
    residue_lines = [line.rstrip("\r\n") for line in lines[start_index:]]
    nonblank_lines = [line for line in residue_lines if line.strip()]
    if not nonblank_lines:
        return False
    if not any(_TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(line) for line in nonblank_lines):
        return False
    if not any(
        index >= diagnostic_index and _TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(lines[index].rstrip("\r\n"))
        for index in range(start_index, len(lines))
    ):
        return False
    return all(
        _TS_TEST_BLOCK_RESIDUE_STATEMENT_RE.match(line) or _TS_TEST_BLOCK_RESIDUE_CLOSER_RE.match(line)
        for line in nonblank_lines
    )


def _line_start_offsets(lines: Sequence[str]) -> list[int]:
    offsets: list[int] = [0]
    current = 0
    for line in lines:
        current += len(line)
        offsets.append(current)
    return offsets


def _to_positive_int(value: object) -> int:
    try:
        parsed = int(str(value or "0"))
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def _dedupe_preserve_order(items: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _normalize_repair_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return ""
    return normalized


def _is_typescript_comma_expected_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code == "typescript_return_object_property_semicolon":
        return True
    if diagnostic.code.lower() != "typescript_ts1005":
        return False
    message = diagnostic.message.lower()
    raw = diagnostic.raw.lower()
    return "expected" in message and "," in (message + raw)


__all__ = [
    "HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL",
    "JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL",
    "TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL",
    "TYPESCRIPT_BRANDED_LITERAL_CAST_SOURCE_TOOL",
    "TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_CONFIG_KEY_SPLIT_SOURCE_TOOL",
    "TYPESCRIPT_DOM_LOCAL_SHIM_CLEANUP_SOURCE_TOOL",
    "TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL",
    "TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL",
    "TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL",
    "TYPESCRIPT_EXPECT_ERROR_PLACEMENT_SOURCE_TOOL",
    "TYPESCRIPT_EXPORT_AMBIGUITY_SOURCE_TOOL",
    "TYPESCRIPT_HTML_CONTAINER_SELECTOR_SOURCE_TOOL",
    "TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_LITERAL_UNION_VALUE_FACADE_SOURCE_TOOL",
    "TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL",
    "TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_PROPERTY_CALL_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL",
    "TYPESCRIPT_PRIVATE_CONSTRUCTOR_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORT_SOURCE_TOOL",
    "TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL",
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "TYPESCRIPT_SCAFFOLD_SOURCE_TOOL",
    "TYPESCRIPT_SHORTHAND_PROPERTY_SCOPE_SOURCE_TOOL",
    "TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL",
    "TYPESCRIPT_STRING_LITERAL_SUGGESTION_SOURCE_TOOL",
    "TYPESCRIPT_TEST_BLOCK_RESIDUE_SOURCE_TOOL",
    "TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL",
    "TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL",
    "TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL",
    "TYPESCRIPT_UNKNOWN_MEMBER_ACCESS_SOURCE_TOOL",
    "TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL",
    "TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL",
    "TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL",
    "build_typescript_canvas_scale_return_type_plan",
    "build_typescript_duplicate_object_property_plan",
    "build_typescript_enum_member_separator_plan",
    "build_typescript_hyphenated_identifier_plan",
    "build_typescript_missing_closing_brace_plan",
    "build_typescript_nullable_canvas_context_plan",
    "build_typescript_number_property_call_plan",
    "build_typescript_number_to_string_argument_plan",
    "build_typescript_object_literal_comma_plan",
    "build_typescript_runtime_plan_for_source_tool",
    "build_typescript_shorthand_property_scope_plan",
    "build_typescript_string_literal_suggestion_plan",
    "build_typescript_unknown_member_access_plan",
    "repair_typescript_nullable_canvas_context_guards",
    "repair_typescript_object_literal_commas",
]
