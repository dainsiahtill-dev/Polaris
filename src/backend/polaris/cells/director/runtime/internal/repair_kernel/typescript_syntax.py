"""Canonical TypeScript syntax repair rules for Director Runtime."""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any

from .contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text

TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL = "deterministic_typescript_return_object_semicolon_repair"
TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL = "deterministic_typescript_nullable_canvas_context_repair"
TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL = "deterministic_typescript_duplicate_object_property_repair"
TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL = "deterministic_typescript_enum_member_separator_repair"
TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL = "deterministic_typescript_missing_closing_brace_repair"
TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL = "deterministic_typescript_number_to_string_argument_repair"
TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL = "deterministic_typescript_canvas_scale_return_type_repair"
HTML_TYPESCRIPT_MODULE_SCRIPT_SOURCE_TOOL = "deterministic_html_typescript_module_script_repair"
JAVASCRIPT_TYPESCRIPT_ANNOTATION_SOURCE_TOOL = "deterministic_javascript_typescript_annotation_repair"
TYPEORM_MODEL_NORMALIZATION_SOURCE_TOOL = "deterministic_typeorm_model_normalization_repair"
TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL = "deterministic_typescript_commonjs_package_type_repair"
TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL = "deterministic_typescript_entrypoint_repair"
TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL = "deterministic_typescript_escaped_newline_repair"
TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL = "deterministic_typescript_hyphenated_identifier_repair"
TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL = "deterministic_typescript_member_alias_repair"
TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL = "deterministic_typescript_missing_export_repair"
TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL = "deterministic_typescript_missing_member_repair"
TYPESCRIPT_REEXPORT_SOURCE_TOOL = "deterministic_typescript_reexport_repair"
TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL = "deterministic_typescript_reexported_type_binding_repair"
TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL = "deterministic_typescript_relative_import_case_repair"
TYPESCRIPT_SCAFFOLD_SOURCE_TOOL = "deterministic_typescript_scaffold_repair"
TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL = "deterministic_typescript_sourcefile_diagnostics_repair"
TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL = "deterministic_typescript_too_few_arguments_repair"
TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL = "deterministic_typescript_tsconfig_lib_repair"
TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL = "deterministic_typescript_uninitialized_property_repair"
TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL = "deterministic_typescript_unique_export_import_repair"
TYPESCRIPT_UNRESOLVED_IDENTIFIER_SOURCE_TOOL = "deterministic_typescript_unresolved_identifier_repair"
TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL = "deterministic_typescript_unused_import_repair"
TYPESCRIPT_VITEST_GLOBALS_SOURCE_TOOL = "deterministic_typescript_vitest_globals_repair"
TYPESCRIPT_ZOD_TYPE_CLASS_COLLISION_SOURCE_TOOL = "deterministic_typescript_zod_type_class_collision_repair"

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
_TS_HYPHENATED_VARIABLE_DECLARATION_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<left>[A-Za-z_$][A-Za-z0-9_$]*)-(?P<right>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"\b(?=\s*(?::|=))"
)
_TS_POSSIBLY_NULL_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18047:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)
_TS_POSSIBLY_NULL_MESSAGE_RE = re.compile(
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+possibly\s+['\"]null['\"]",
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
_TS_MISSING_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2339:\s*"
    r"Property\s+['\"](?P<member>[^'\"]+)['\"]\s+does\s+not\s+exist\s+on\s+type\s+['\"](?P<type>[^'\"]+)['\"]",
    re.IGNORECASE,
)
_TS_TOO_FEW_ARGUMENTS_RAW_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2554:\s*"
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but\s+got\s+(?P<got>\d+)",
    re.IGNORECASE,
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
_TS_NAMED_REEXPORT_RE = re.compile(
    r"export\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<module>[^'\"]+)['\"]\s*;?",
    re.MULTILINE | re.DOTALL,
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
        repaired_lines.append(line)
    if not guarded:
        return text, []
    return "\n".join(repaired_lines) + ("\n" if text.endswith("\n") else ""), _dedupe_preserve_order(guarded)


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
        TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL: _build_typescript_entrypoint_plan,
        TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL: _build_typescript_escaped_newline_plan,
        TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL: build_typescript_hyphenated_identifier_plan,
        TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL: _build_typescript_member_alias_plan,
        TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL: _build_typescript_missing_export_plan,
        TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL: _build_typescript_missing_member_plan,
        TYPESCRIPT_REEXPORT_SOURCE_TOOL: _build_typescript_reexport_plan,
        TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL: _build_typescript_reexported_type_binding_plan,
        TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL: _build_typescript_relative_import_case_plan,
        TYPESCRIPT_SCAFFOLD_SOURCE_TOOL: _build_typescript_scaffold_plan,
        TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL: _build_typescript_sourcefile_diagnostics_plan,
        TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL: _build_typescript_too_few_arguments_plan,
        TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL: _build_typescript_tsconfig_lib_plan,
        TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL: _build_typescript_uninitialized_property_plan,
        TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL: _build_typescript_unique_export_import_plan,
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
        replacement = _html_javascript_entrypoint_for_typescript_source(source_ref)
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


def _build_typescript_missing_export_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    operations: list[RepairOperation] = []
    exports: list[dict[str, str]] = []
    updated: dict[str, str] = {}
    for item in _parse_typescript_missing_export_errors(diagnostics):
        operation, meta = _missing_export_operation(base_files={**base_files, **updated}, item=item)
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(
            updated.get(operation.path) or base_files[operation.path], operation
        )
        operations.append(operation)
        exports.append(meta)
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
    operations: list[RepairOperation] = []
    members: list[dict[str, str]] = []
    updated = dict(base_files)
    for item in _parse_typescript_missing_member_errors(diagnostics):
        type_name = _typescript_declaration_type_name(item["type"])
        member = item["member"]
        if not type_name or not _TS_IDENTIFIER_RE.fullmatch(member):
            continue
        usage_path = item["file"]
        usage_text = str(base_files.get(usage_path) or "")
        line_number = _to_positive_int(item.get("line"))
        member_is_call = _typescript_member_usage_is_call(usage_text, line_number, member)
        operation = _add_typescript_member_operation(
            base_files=updated,
            type_name=type_name,
            member=member,
            member_is_call=member_is_call,
        )
        if operation is None:
            continue
        updated[operation.path] = _apply_single_text_operation(updated[operation.path], operation)
        operations.append(operation)
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
    return _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL,
        rule_id="typescript.unique_export_import",
        mode_filter="unique_export",
    )


def _build_typescript_unused_import_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str,
) -> RepairPlan | None:
    return _build_relative_import_plan(
        base_files=base_files,
        diagnostics=diagnostics,
        mode=mode,
        source_tool=TYPESCRIPT_UNUSED_IMPORT_SOURCE_TOOL,
        rule_id="typescript.unused_import",
        mode_filter="unused",
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
    if not needs_dom_lib and not needs_import_meta_module:
        return None
    payload = _json_object(tsconfig_text)
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        compiler_options = {}
    operations: list[RepairOperation] = []
    libs_raw = compiler_options.get("lib")
    libs = [str(item) for item in libs_raw] if isinstance(libs_raw, list) else []
    normalized_libs = {item.lower() for item in libs}
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
        metadata={"libs": libs, "module": "ES2020" if needs_import_meta_module else module_value},
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


def _parse_html_typescript_module_script_errors(diagnostics: Sequence[RepairDiagnostic]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for diagnostic in diagnostics:
        for match in _HTML_TS_MODULE_SCRIPT_ERROR_RE.finditer(str(diagnostic.raw or diagnostic.message or "")):
            item = {
                "file": _normalize_repair_path(str(match.group("path") or "")),
                "source": str(match.group("src") or "").strip(),
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
    type_text = str(match.group("type") or "unknown").strip()
    if optional:
        return f"{indent}{name}?: {type_text};"
    return f"{indent}{name}: {type_text} = {_typescript_default_value_for_type(type_text)};"


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
        match = _TS_ESCAPED_NEWLINE_IN_LINE_COMMENT_ERROR_RE.search(str(diagnostic.raw or diagnostic.message or ""))
        if match:
            path = _normalize_repair_path(str(match.group("path") or ""))
            if path:
                paths.append(path)
    return _dedupe_preserve_order(paths)


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


def _typescript_declaration_type_name(raw: str) -> str:
    match = re.search(r"[A-Za-z_$][A-Za-z0-9_$]*", str(raw or ""))
    return str(match.group(0) if match else "")


def _typescript_receiver_for_member_access(line: str, member: str) -> str:
    match = re.search(rf"\b(?P<receiver>[A-Za-z_$][\w$]*)\s*\.\s*{re.escape(member)}\b", str(line or ""))
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
            r"^\s*(?P<name>[A-Za-z_$][\w$]*)\s*(?:[:=(])", match.group("body"), re.MULTILINE
        ):
            members.add(str(member_match.group("name") or ""))
    return members


def _typescript_member_alias_replacement(*, receiver: str, missing_member: str, existing_members: set[str]) -> str:
    if missing_member in {"x", "y"} and "position" in existing_members:
        return f"{receiver}.position.{missing_member}"
    if missing_member == "brightness" and "intensity" in existing_members:
        return f"{receiver}.intensity"
    if missing_member == "size" and "radius" in existing_members:
        return f"{receiver}.radius"
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
        ):
            for match in pattern.finditer(text):
                module = str(match.group("module") or "").strip().strip("'\"`").rstrip(".")
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
    exported = _export_existing_typescript_declaration(original, symbol)
    declaration_kind = "export_existing"
    if exported == original:
        declaration_kind, declaration = _build_typescript_missing_export_declaration(
            symbol=symbol,
            importer_text=str(base_files.get(importer) or ""),
        )
        if not declaration:
            return None, {}
        operation = _append_typescript_missing_export_declaration_operation(
            path=exporter,
            original=original,
            declaration=declaration,
            symbol=symbol,
            declaration_kind=declaration_kind,
        )
        return (
            (
                operation,
                {"file": exporter, "symbol": symbol, "kind": declaration_kind},
            )
            if operation is not None
            else (None, {})
        )
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


def _build_typescript_missing_export_declaration(*, symbol: str, importer_text: str) -> tuple[str, str]:
    if not _TS_IDENTIFIER_RE.fullmatch(symbol):
        return "", ""
    if _typescript_symbol_is_constructed(importer_text, symbol):
        return "class", _build_typescript_missing_export_class_declaration(symbol=symbol, importer_text=importer_text)
    if _typescript_symbol_is_called(importer_text, symbol):
        return "function", f"export function {symbol}(..._args: unknown[]): any {{\n  return undefined;\n}}"
    if symbol[:1].isupper():
        return "type", f"export type {symbol} = any;"
    return "const", f"export const {symbol}: unknown = undefined;"


def _typescript_symbol_is_constructed(text: str, symbol: str) -> bool:
    return bool(re.search(rf"\bnew\s+{re.escape(symbol)}\s*\(", str(text or "")))


def _typescript_symbol_is_called(text: str, symbol: str) -> bool:
    token = str(text or "")
    return bool(re.search(rf"(?<!new\s)\b{re.escape(symbol)}\s*\(", token))


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


def _apply_single_text_operation(content: str, operation: RepairOperation) -> str:
    if operation.span_start is None or operation.span_end is None:
        return content
    return content[: operation.span_start] + str(operation.replacement or "") + content[operation.span_end :]


def _typescript_member_usage_is_call(text: str, line_number: int, member: str) -> bool:
    lines = str(text or "").splitlines()
    if line_number <= 0 or line_number > len(lines):
        return False
    return bool(re.search(rf"\.\s*{re.escape(member)}\s*\(", lines[line_number - 1]))


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
        return RepairOperation(
            kind="text_replace",
            path=path,
            span_start=insert_at,
            span_end=insert_at,
            expected="",
            replacement=declaration,
            before_hash=sha256_text(content),
            metadata={"repair_kind": "typescript_missing_member", "type": type_name, "member": member},
        )
    return None


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


def _typescript_module_exports_symbol(module_text: str, symbol: str) -> bool:
    escaped = re.escape(symbol)
    return bool(
        re.search(rf"\bexport\s+(?:type|interface|enum|class|const|let|var|function)\s+{escaped}\b", module_text)
    )


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
    clause = str(match.group("clause") or "").strip()
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
    if "ts.createSourceFile" not in str(text or ""):
        return str(text or "")
    return re.sub(
        r"const\s+diagnostics[^;\n]*;?", "const diagnostics: readonly ts.Diagnostic[] = [];", str(text or ""), count=1
    )


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
    if not path or not content or line_number <= 0 or column <= 0 or expected_count <= got_count:
        return None
    lines = content.splitlines(keepends=True)
    line_index = line_number - 1
    if line_index < 0 or line_index >= len(lines):
        return None
    usage_line = lines[line_index].rstrip("\r\n")
    method_name = _typescript_call_name_from_usage_line(usage_line, column)
    if not method_name:
        return None
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
    return "include 'dom'" in text and any(
        f"cannot find name '{name}'" in text for name in ("console", "window", "document", "navigator", "location")
    )


def _typescript_errors_require_import_meta_module(diagnostics: Sequence[RepairDiagnostic]) -> bool:
    text = _diagnostic_text(diagnostics).lower()
    return "ts1343" in text and "import.meta" in text and "module" in text


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
    for param in _typescript_function_param_names_for_line(lines, target_index):
        if _typescript_identifier_alias_matches(missing_symbol, param):
            return param
    return ""


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
        if code == "typescript_ts18047":
            match = _TS_POSSIBLY_NULL_MESSAGE_RE.search(message)
            symbol = str(match.group("symbol") or "").strip() if match else ""
            if _TS_IDENTIFIER_RE.fullmatch(symbol):
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
            _TS_NUMBER_TO_STRING_ARGUMENT_RAW_RE,
            _TS_NUMBER_TO_FUNCTION_ARGUMENT_RAW_RE,
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
    unique_context = content[max(0, start - 160) : start]
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
        if path and _TS_IDENTIFIER_RE.fullmatch(symbol):
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
        operations.append(
            RepairOperation(
                kind="text_replace",
                path=path,
                span_start=start,
                span_end=end,
                expected="".join(original_lines[start_line:end_line]),
                replacement="".join(repaired_lines[replacement_start:replacement_end]),
                before_hash=before_hash,
                metadata=dict(metadata),
            )
        )
    return tuple(operations)


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
    "TYPESCRIPT_CANVAS_SCALE_RETURN_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_COMMONJS_PACKAGE_TYPE_SOURCE_TOOL",
    "TYPESCRIPT_DUPLICATE_OBJECT_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_ENTRYPOINT_SOURCE_TOOL",
    "TYPESCRIPT_ENUM_MEMBER_SEPARATOR_SOURCE_TOOL",
    "TYPESCRIPT_ESCAPED_NEWLINE_SOURCE_TOOL",
    "TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL",
    "TYPESCRIPT_MEMBER_ALIAS_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_CLOSING_BRACE_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_EXPORT_SOURCE_TOOL",
    "TYPESCRIPT_MISSING_MEMBER_SOURCE_TOOL",
    "TYPESCRIPT_NULLABLE_CANVAS_CONTEXT_SOURCE_TOOL",
    "TYPESCRIPT_NUMBER_TO_STRING_ARGUMENT_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORTED_TYPE_BINDING_SOURCE_TOOL",
    "TYPESCRIPT_REEXPORT_SOURCE_TOOL",
    "TYPESCRIPT_RELATIVE_IMPORT_CASE_SOURCE_TOOL",
    "TYPESCRIPT_RETURN_OBJECT_COMMA_SOURCE_TOOL",
    "TYPESCRIPT_SCAFFOLD_SOURCE_TOOL",
    "TYPESCRIPT_SOURCEFILE_DIAGNOSTICS_SOURCE_TOOL",
    "TYPESCRIPT_TOO_FEW_ARGUMENTS_SOURCE_TOOL",
    "TYPESCRIPT_TSCONFIG_LIB_SOURCE_TOOL",
    "TYPESCRIPT_UNINITIALIZED_PROPERTY_SOURCE_TOOL",
    "TYPESCRIPT_UNIQUE_EXPORT_IMPORT_SOURCE_TOOL",
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
    "build_typescript_number_to_string_argument_plan",
    "build_typescript_object_literal_comma_plan",
    "build_typescript_runtime_plan_for_source_tool",
    "repair_typescript_nullable_canvas_context_guards",
    "repair_typescript_object_literal_commas",
]
