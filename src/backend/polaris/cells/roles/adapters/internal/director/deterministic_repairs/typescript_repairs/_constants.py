"""TypeScript repair regexes, templates, and name sets.

Lossless extract from the former ``typescript_repairs`` module.
"""

from __future__ import annotations

import re

_TS_MISSING_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2339:\s*"
    r"Property\s+'(?P<member>[^']+)'\s+does\s+not\s+exist\s+on\s+type\s+'(?P<type>[^']+)'",
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
_TS_COMMA_EXPECTED_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1005:\s*"
    r"['\"`],['\"`]\s+expected",
    re.IGNORECASE,
)
_TS_NUMBER_TO_STRING_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+'number'\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+'string'",
    re.IGNORECASE,
)
_TS_NUMBER_TO_FUNCTION_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+'number'\s+is\s+not\s+assignable\s+to\s+parameter\s+of\s+type\s+"
    r"['\"]\(\s*n\s*:\s*number\s*\)\s*=>\s*number['\"]",
    re.IGNORECASE,
)
_TS_CANVAS_SCALE_RETURN_TYPE_RE = re.compile(
    r"(export\s+function\s+scaleToCanvas\s*\([\s\S]*?\)\s*:\s*)"
    r"\{\s*sx\s*:\s*number\s*;\s*sy\s*:\s*number\s*;\s*scale\s*:\s*number\s*;?\s*\}",
    re.MULTILINE,
)
_TS_TOO_FEW_ARGUMENTS_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2554:\s*"
    r"Expected\s+(?P<expected>\d+)\s+arguments?,\s+but\s+got\s+(?P<got>\d+)",
    re.IGNORECASE,
)
_TS_POSSIBLY_NULL_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18047:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+possibly\s+['\"]null['\"]",
    re.IGNORECASE,
)
_TS_DUPLICATE_OBJECT_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1117:\s*"
    r"An\s+object\s+literal\s+cannot\s+have\s+multiple\s+properties\s+with\s+the\s+same\s+name",
    re.IGNORECASE,
)
_TS_SOURCEFILE_DIAGNOSTICS_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2339|2871|7006):\s*"
    r"(?P<message>[^\n]*(?:parseDiagnostics|diagnostics|always\s+nullish|implicitly\s+has\s+an\s+['\"]any['\"]\s+type)[^\n]*)",
    re.IGNORECASE,
)
_TS_NULLABLE_ARGUMENT_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2345:\s*"
    r"Argument\s+of\s+type\s+['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\s*\|\s*null['\"]\s+is\s+not\s+assignable\s+"
    r"to\s+parameter\s+of\s+type\s+['\"](?P=type)['\"]",
    re.IGNORECASE,
)
_TS_CANNOT_FIND_TEST_GLOBAL_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS(?:2304|2582):\s*"
    r"Cannot\s+find\s+name\s+['\"](?P<symbol>describe|it|test|expect|beforeEach|afterEach|beforeAll|afterAll)['\"]",
    re.IGNORECASE,
)
_TS_UNINITIALIZED_PROPERTY_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2564:\s*"
    r"Property\s+['\"](?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+has\s+no\s+initializer",
    re.IGNORECASE,
)
_TS_UNKNOWN_VALUE_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS18046:\s*"
    r"['\"](?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+of\s+type\s+['\"]unknown['\"]",
    re.IGNORECASE,
)
_TS_REQUIRED_PROPERTY_MISSING_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2741:\s*"
    r"Property\s+['\"](?P<member>[A-Za-z_$][A-Za-z0-9_$]*)['\"]\s+is\s+missing\s+in\s+type\s+"
    r".+?\s+but\s+required\s+in\s+type\s+['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)['\"]",
    re.IGNORECASE | re.DOTALL,
)
_TS_REQUIRED_PROPERTIES_MISSING_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS2739:\s*"
    r"Type\s+.+?\s+is\s+missing\s+the\s+following\s+properties\s+from\s+type\s+"
    r"['\"](?P<type>[A-Za-z_$][A-Za-z0-9_$]*)['\"]:\s*(?P<members>[^\n]+)",
    re.IGNORECASE | re.DOTALL,
)
_TS_ENUM_MEMBER_SEPARATOR_ERROR_RE = re.compile(
    r"(?P<file>[^:\n]+\.tsx?)\((?P<line>\d+),(?P<col>\d+)\):\s*error\s+TS1357:\s*"
    r"An\s+enum\s+member\s+name\s+must\s+be\s+followed\s+by\s+a\s+',',\s*'=',\s*or\s*'}'",
    re.IGNORECASE,
)
_TS_ENUM_DECLARATION_LINE_RE = re.compile(r"\benum\s+[A-Za-z_$][A-Za-z0-9_$]*\b[^{}]*{")
_TS_ENUM_MEMBER_LINE_RE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z_$][A-Za-z0-9_$]*(?:\s*=\s*[^,;{}]+?)?)(?P<separator>[;,]?)(?P<space>\s*)(?P<comment>//.*)?$"
)
_TS_CANVAS_CONTEXT_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*[^;\n]*\.getContext\(\s*['\"]2d['\"]\s*\)\s*;?\s*$"
)
# Generalized: match any function call declaration that may return nullable type
_TS_NULLABLE_FUNCTION_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<rhs>[^;\n]*\([^;\n]*\))\s*;?\s*$"
)
_TS_NAMED_REEXPORT_RE = re.compile(
    r"export\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<module>[^'\"]+)['\"]\s*;?",
    re.MULTILINE | re.DOTALL,
)
_TS_NULLABLE_DOM_HANDLE_DECLARATION_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=]+)?\s*=\s*(?P<source>[^;\n]*"
    r"(?:document\.(?:getElementById|querySelector)|"
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*\.querySelector)"
    r"\s*\([^;\n]*\)[^;\n]*)\s*;?\s*$"
)
_TS_EXPORTED_CLASS_RE_TEMPLATE = r"export\s+(?:abstract\s+)?class\s+{type_name}\b[^{{]*{{"
_TS_STRUCTURAL_TYPE_RE_TEMPLATE = r"(?:export\s+)?(?:interface\s+{type_name}\b[^{{]*{{|type\s+{type_name}\b\s*=\s*{{)"
_TS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_TS_STRINGISH_MEMBER_NAMES = {"color", "colour", "id", "key", "label", "name", "title"}
_TS_NUMERIC_MEMBER_NAMES = {
    "amplitude",
    "basex",
    "basey",
    "brightness",
    "count",
    "duration",
    "height",
    "humidity",
    "index",
    "intensity",
    "hue",
    "moonphase",
    "phase",
    "phaseangle",
    "petalcount",
    "r",
    "g",
    "b",
    "alpha",
    "radius",
    "size",
    "speed",
    "time",
    "width",
    "x",
    "y",
    "z",
}
