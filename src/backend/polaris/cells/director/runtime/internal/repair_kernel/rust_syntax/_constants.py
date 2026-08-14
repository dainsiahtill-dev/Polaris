"""Rust repair source-tool constants and compiled diagnostic patterns."""

from __future__ import annotations

import re

RUST_CRATE_IMPORT_SOURCE_TOOL = "deterministic_rust_crate_import_repair"

RUST_CRATE_IMPORT_REWRITE_SOURCE_TOOL = "deterministic_rust_crate_import_rewrite_repair"

RUST_DEPENDENCY_SOURCE_TOOL = "deterministic_rust_dependency_repair"

RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL = "deterministic_rust_duplicate_module_file_repair"

RUST_FIELD_RENAME_SUGGESTION_SOURCE_TOOL = "deterministic_rust_field_rename_suggestion_repair"

RUST_INCOMPATIBLE_COPY_DERIVE_SOURCE_TOOL = "deterministic_rust_incompatible_copy_derive_repair"

RUST_LINE_SUGGESTION_SOURCE_TOOL = "deterministic_rust_line_suggestion_repair"

RUST_METHOD_SELF_SIGNATURE_SOURCE_TOOL = "deterministic_rust_method_self_signature_repair"

RUST_MISSING_BINARY_ENTRYPOINT_SOURCE_TOOL = "deterministic_rust_missing_binary_entrypoint_repair"

RUST_MISSING_MODULE_FILE_SOURCE_TOOL = "deterministic_rust_missing_module_file_repair"

RUST_MISSING_TRAIT_DERIVE_SOURCE_TOOL = "deterministic_rust_derive_repair"

RUST_POST_SOURCE_TOOL = "deterministic_rust_post_repair"

RUST_SERDE_DERIVE_SOURCE_TOOL = "deterministic_rust_serde_derive_repair"

RUST_TRAIT_IMPORT_SOURCE_TOOL = "deterministic_rust_trait_import_repair"

RUST_UNUSED_IMPORT_SOURCE_TOOL = "deterministic_rust_unused_import_repair"

RUST_UNRESOLVED_PUB_USE_SOURCE_TOOL = "deterministic_rust_unresolved_pub_use_repair"

RUST_WRONG_CRATE_PATH_SOURCE_TOOL = "deterministic_rust_wrong_crate_path_repair"

RUST_MISSING_MODULE_FILE_STUB = (
    "// Polaris marker: rust.missing_module_file\n// Created from rustc E0583 as an empty module topology stub.\n"
)

_RUST_UNRESOLVED_IMPORT_RE = re.compile(
    r"unresolved import [`'\"](?P<import>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)

_RUST_UNRESOLVED_CRATE_RE = re.compile(
    r"(?:cannot find (?:module or )?crate|use of unresolved module or unlinked crate) "
    r"[`'\"](?P<crate>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)

_RUST_SERDE_DERIVE_SUGGESTION_RE = re.compile(
    r"consider adding [`'\"]#\[derive\(serde::(?P<trait>Serialize|Deserialize)\)\][`'\"] "
    r"to your [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)::(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] type",
    re.IGNORECASE,
)

_RUST_MISSING_TRAIT_BOUND_RE = re.compile(
    r"the trait bound [`'\"](?:[A-Za-z_][A-Za-z0-9_]*::)*(?P<symbol>[A-Za-z_][A-Za-z0-9_]*):\s*"
    r"(?P<trait>(?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*)[`'\"] is not satisfied",
    re.IGNORECASE,
)

_RUST_DERIVABLE_TRAIT_NAMES = frozenset(
    {
        "Clone",
        "Copy",
        "Debug",
        "Default",
        "Eq",
        "Hash",
        "Ord",
        "PartialEq",
        "PartialOrd",
    }
)

_RUST_DERIVE_PREREQUISITES: dict[str, frozenset[str]] = {
    "Copy": frozenset({"Clone"}),
    "Eq": frozenset({"PartialEq"}),
    "Ord": frozenset({"PartialOrd", "Eq", "PartialEq"}),
    "PartialOrd": frozenset({"PartialEq"}),
}

_KNOWN_RUST_DEPENDENCIES: dict[str, str] = {
    "serde": 'serde = { version = "1.0", features = ["derive"] }',
    "serde_json": 'serde_json = "1.0"',
}

_RUST_METHOD_SELF_LOCATION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE,
)

_RUST_LOCATION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE,
)

_RUST_MISSING_MODULE_FILE_RE = re.compile(
    r"file not found for module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)

_RUST_E0583_HELP_LINE_RE = re.compile(
    r"to create the module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
    r"create file (?P<candidates>[^\n]+)",
    re.IGNORECASE,
)

_RUST_QUOTED_RS_PATH_RE = re.compile(r'"(?P<path>[^"\n]+\.rs)"', re.IGNORECASE)

_RUST_DUPLICATE_MODULE_FILE_RE = re.compile(
    r"(?:error\[E0761\]:\s*)?file for module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"]\s+"
    r"found at both [`'\"](?P<first>[^`'\"\n]+\.rs)[`'\"]\s+and\s+"
    r"[`'\"](?P<second>[^`'\"\n]+\.rs)[`'\"]",
    re.IGNORECASE,
)

_RUST_INCOMPATIBLE_COPY_LOCATION_RE = re.compile(
    r"the trait [`'\"]Copy[`'\"] cannot be implemented.*?"
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

_RUST_DERIVE_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)#\[derive\((?P<items>[^)\r\n]*)\)\](?P<trailing>[^\r\n]*)(?P<newline>\r\n|\n|\r)?$"
)

_RUST_COPY_DERIVE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])Copy(?![A-Za-z0-9_])")

_RUST_NO_SYMBOL_RE = re.compile(
    r"no [`'\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"] in [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_:]*)[`'\"]",
    re.IGNORECASE,
)

_RUST_PUB_USE_STATEMENT_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)pub\s+use\s+(?P<path>[A-Za-z_][A-Za-z0-9_:]*)::"
    r"(?P<tail>[^;\n]+);[ \t]*(?P<newline>\n?)"
)

_RUST_METHOD_SELF_SIGNATURE_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\(\s*&mut\s*\)"), "(&mut self)", "mut_self"),
    (re.compile(r"\(\s*&\s*\)"), "(&self)", "self"),
)

_RUST_FIELD_METHOD_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+one of the expressions' fields has a method of the same name.*?"
    r"^\s*(?P=line)\s+\|\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

_RUST_FULL_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+(?:consider borrowing here|try dereferencing|consider removing the borrow).*?"
    r"^\s*(?P=line)\s+\|\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

# rustc machine-diff help: "132 +     new line"
_RUST_PLUS_LINE_SUGGESTION_RE = re.compile(
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):\d+.*?"
    r"help:\s+(?P<help>[^\n]+).*?"
    r"^\s*(?P=line)\s+\+\s(?P<code>[^\n]+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

_RUST_XML_GENERIC_CLOSE_RE = re.compile(r"(?:\n(?:</[A-Za-z_][A-Za-z0-9_]*>)+)+\s*$")

_RUST_VEC_BARE_GENERIC_RE = re.compile(r"\bVec(?P<inner>[A-Z][A-Za-z0-9_]*)\b(?P<tail>\s*=\s*Vec::new\(\))")

_RUST_INTEGER_IS_FINITE_RE = re.compile(
    r"\n(?P<indent>[ \t]*)if\s*!(?P<name>[A-Za-z_][A-Za-z0-9_]*)\.is_finite\(\)\s*\{"
    r"(?:[^{}]|\{[^{}]*\})*\}[ \t]*\n",
    re.MULTILINE,
)

_RUST_TWO_TUPLE_LET_RE = re.compile(
    r"(?P<prefix>let\s+)\((?P<a>[A-Za-z_][A-Za-z0-9_]*),\s*(?P<b>[A-Za-z_][A-Za-z0-9_]*)\)"
    r"(?P<rest>\s*=\s*[^;]+;)"
)

_RUST_ENUM_VARIANT_MISSING_FIELD_RE = re.compile(
    r"missing field [`'\"](?P<field>[A-Za-z_][A-Za-z0-9_]*)[`'\"]",
    re.IGNORECASE,
)

_RUST_FIELD_RENAME_ERROR_RE = re.compile(
    r"error\[E0609\]:\s*no field [`'\"](?P<wrong>[A-Za-z_][A-Za-z0-9_]*)[`'\"]"
    r".*?^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

_RUST_FIELD_RENAME_PLUS_LINE_RE = re.compile(
    r"^\s*(?P<line>\d+)\s+\+\s(?P<code>[^\n]+)",
    re.MULTILINE,
)

_RUST_FIELD_ACCESS_RE = re.compile(r"\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\b")

_RUST_USE_IMPORT_LINE_RE = re.compile(r"^use\s+[^;\r\n]+;$")

_RUST_USE_IMPORT_IN_TEXT_RE = re.compile(r"\b(?P<import>use\s+[^;\r\n]+;)")

_RUST_UNUSED_IMPORT_RE = re.compile(
    r"warning:\s*unused\s+import:\s*[`'\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
    r"^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

_RUST_REAL_ITEM_RE = re.compile(
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+|unsafe\s+|extern\s+)*"
    r"(?:struct|enum|trait|impl|fn|mod|use|const|static|type|macro_rules!)\b"
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

_RUST_CANT_FIND_BIN_PATH_RE = re.compile(
    r"can't find bin\s+`[^`]+`\s+at path\s+`(?P<path>[^`]+)`",
    re.IGNORECASE,
)
