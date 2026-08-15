"""Shared constants and compiled patterns for artifact quality scans."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Mapping

_LegacyIssueCodeClassifier = Callable[[str, str], str]

_ARTIFACT_QUALITY_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".polaris",
    ".pytest_cache",
    ".ruff_cache",
    ".vite",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}

_ARTIFACT_QUALITY_SOURCE_EXTS = {
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".ts",
    ".tsx",
}

_DETERMINISTIC_SCAFFOLD_MARKERS = (
    "audit-seed",
    "planning scenario",
    "deterministic-declared-scope-v1",
    "createGameViewScaffoldState",
    "createCombatSystemScaffoldState",
    "Created by Polaris",
    "Generated file for",
    "generated-project",
    "build verification completed",
    "test verification completed",
    "structural build passed",
    "structural tests passed",
    "Hello from TypeScript project",
    "polaris-typescript-scaffold",
    "typescript-bootstrap",
    "Bootstrap TypeScript project scaffold",
    "Polaris TypeScript scaffold",
    "TypeScript scaffold",
    "TypeScript project scaffold",
)

_REMOVED_TYPESCRIPT_COMPILER_OPTIONS = {
    "charset": "TS5102",
}

_NUMERIC_HELPER_FILLER_RE = re.compile(
    r"export\s+function\s+\w+Helper\d+\s*"
    r"\(\s*value\s*:\s*number\s*\)\s*:\s*number\s*"
    r"\{\s*return\s+value\s*\+\s*\d+\s*;\s*\}",
    re.IGNORECASE,
)

_GENERIC_STORE_RECORD_RE = re.compile(
    r"export\s+interface\s+\w+Record\s*\{[^}]*"
    r"payload\s*:\s*string\s*;[^}]*"
    r"index\s*:\s*number\s*;[^}]*\}",
    re.IGNORECASE | re.DOTALL,
)

_GENERIC_STORE_MAP_RE = re.compile(
    r"private\s+readonly\s+items\s*=\s*new\s+Map\s*<\s*string\s*,\s*\w+Record\s*>",
    re.IGNORECASE,
)

_TRIVIAL_ARITHMETIC_EXPECT_RE = re.compile(
    r"expect\s*\(\s*\d+\s*(?:[+\-*/])\s*\d+\s*\)\s*\.\s*to(?:Be|Equal)\s*\(\s*\d+\s*\)",
    re.IGNORECASE,
)

_PATCH_RESIDUE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b|>{4,7}\s*REPLACE\b|END\s+PATCH_FILE\b|PATCH_FILE(?::|\s+))",
    re.IGNORECASE,
)

_TOOL_RECEIPT_CONTAMINATION_TOKENS = (
    "**write_file**: error",
    "**edit_file**: error",
    "**append_to_file**: error",
    "destructive shrink rejected",
    "director_write_policy_denied",
    "handler_error_type",
)

_DIAGNOSTIC_KIND_SOURCE_RULES: Mapping[str, frozenset[str]] = {
    "npm_script_missing_local_config": frozenset(("npm_script_config_scanner",)),
    "npm_script_missing_local_entrypoint": frozenset(("npm_script_entrypoint_scanner",)),
    "artifact_quality_scan_failed": frozenset(("artifact_quality_scanner",)),
    "workspace_path_missing": frozenset(("artifact_quality_scanner",)),
    "workspace_path_unresolved": frozenset(("artifact_quality_scanner",)),
    "javascript_module_error": frozenset(("runtime_smoke",)),
    "syntax_error": frozenset(("source_syntax_checker",)),
    "package_module_type_commonjs_mismatch": frozenset(("package_module_type_scanner",)),
    "html_module_script_typescript_source": frozenset(("html_module_script_scanner",)),
    "html_module_script_compiled_javascript_missing": frozenset(("html_module_script_scanner",)),
    "unresolved_relative_import": frozenset(("typescript_import_scanner",)),
    "undeclared_runtime_import": frozenset(("typescript_import_scanner",)),
    "typescript_node_types_missing": frozenset(("typescript_import_scanner",)),
    "typescript_escaped_newline_line_comment": frozenset(("typescript_syntax_red_flag_scanner",)),
    "typescript_return_object_semicolon_property": frozenset(("typescript_syntax_red_flag_scanner",)),
    "tsconfig_removed_compiler_option": frozenset(("typescript_tsconfig_scanner",)),
    "typescript_isolated_modules_type_reexport": frozenset(("typescript_syntax_red_flag_scanner",)),
    "typescript_zod_type_class_collision": frozenset(("typescript_syntax_red_flag_scanner",)),
    "typescript_import_unresolved_symbol": frozenset(("typescript_symbol_coherence_scanner",)),
    "typescript_project_typecheck_failed": frozenset(("typescript_project_typecheck",)),
    "npm_script_node_test_directory_target": frozenset(("npm_script_test_target_scanner",)),
}

_FILE_ARTIFACT_SCANNER_DIAGNOSTIC_KINDS: frozenset[str] = frozenset(
    (
        "tool_receipt_contamination",
        "source_narration_contamination",
        "deterministic_scaffold_marker",
        "repeated_numeric_helper_filler",
        "generic_payload_index_store_scaffold",
        "patch_residue_marker",
        "repeated_trivial_arithmetic_tests",
    )
)

_CROSS_ARTIFACT_CONSISTENCY_DIAGNOSTIC_KINDS: frozenset[str] = frozenset(
    (
        "unresolved_import_symbol",
        "contract_export_missing",
        "contract_signature_mismatch",
    )
)

_SOURCE_NARRATION_LEAK_RE = re.compile(
    r"(?is)^\s*(?:"
    r"i(?:'|’)ll\s+|"
    r"i\s+will\s+|"
    r"let\s+me\s+|"
    r"here(?:'|’)s\s+|"
    r"here\s+is\s+|"
    r"below\s+is\s+|"
    r"(?:the\s+)?quality\s+repair\s+mode\s+requires\s+me\b|"
    r"the\s+(?:repair\s+)?directive\s+(?:is|says|said)\b|"
    r"the\s+override\s+(?:says|instruction)\b|"
    r"the\s+(?:task|instruction|requirement|requirements)\s+(?:is|are|says|said)\b|"
    r"the\s+(?:two\s+)?(?:problem|problems|issue|issues)\s+(?:are|is)\b|"
    r"i\s+(?:also\s+)?need\s+to\b|"
    r"for\s+[\w./-]+\.(?:py|js|ts|jsx|tsx|go|rs)\s+-\s+should\b|"
    r"this\s+file\s+(?:defines|contains|implements)\b|"
    r"我(?:会|将|来)|"
    r"让我|"
    r"下面(?:是|我)"
    r")"
)

_NPM_SCRIPT_SHELL_SUBSTITUTION_RE = re.compile(r"`|\$\(")

_NPM_SCRIPT_TSC_RE = re.compile(r"(?:^|[&|;\s])(?:npx\s+)?tsc(?:\s|$)", re.IGNORECASE)

_TS_RETURN_OBJECT_OPEN_RE = re.compile(r"return\s*\{")

_TS_OBJECT_PROPERTY_SEMICOLON_RE = re.compile(
    r"(?m)^\s*(?:[A-Za-z_$][\w$]*\s*|(?:\[[^\]]+\]|[A-Za-z_$][\w$]*|['\"][^'\"]+['\"])\s*:\s*[^;{}]+);\s*$"
)

_TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE = re.compile(
    r"//[^\r\n]*\\n\s*(?:export|import|const|let|var|class|function|interface|type|enum)\b",
    re.IGNORECASE,
)

_HTML_TYPESCRIPT_MODULE_SCRIPT_RE = re.compile(
    r"<script\b(?=[^>]*\btype\s*=\s*['\"]module['\"])[^>]*\bsrc\s*=\s*['\"](?P<src>[^'\"]+\.(?:ts|tsx))['\"][^>]*>",
    re.IGNORECASE,
)

_HTML_JAVASCRIPT_MODULE_SCRIPT_RE = re.compile(
    r"<script\b(?=[^>]*\btype\s*=\s*['\"]module['\"])[^>]*\bsrc\s*=\s*['\"](?P<src>[^'\"]+\.js)['\"][^>]*>",
    re.IGNORECASE,
)

_HTML_INLINE_MODULE_SCRIPT_RE = re.compile(
    r"<script\b(?=[^>]*\btype\s*=\s*['\"]module['\"])[^>]*>(?P<body>.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)

_HTML_INLINE_TYPESCRIPT_IMPORT_RE = re.compile(
    r"\b(?:from|import)\s*['\"](?P<src>[^'\"]+\.(?:ts|tsx))['\"]",
    re.IGNORECASE,
)

_TS_ZOD_INFERRED_TYPE_RE = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"z\.infer\s*<\s*typeof\s+[A-Za-z_$][\w$]*\s*>\s*;",
    re.MULTILINE,
)

_IMPORT_SPECIFIER_RE = re.compile(
    r"(?:^|\n)\s*(?:import\s+(?:type\s+)?(?:[^'\"\n]*?\s+from\s+)?|export\s+[^'\"\n]*?\s+from\s+)"
    r"[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

_TS_JS_SOURCE_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

_TS_SOURCE_EXTS = {".ts", ".tsx"}

_TS_TYPE_DECL_RE = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?(?:interface|type)\s+(?P<name>[A-Za-z_$][\w$]*)\b",
    re.MULTILINE,
)

_TS_SYMBOL_COHERENCE_FLAG = "KERNELONE_TS_SYMBOL_COHERENCE"

_TS_DYNAMIC_EXPORT_RE = re.compile(
    r"\bexport\s*\*"  # export * / export * from / export * as
    r"|\bexport\s*="  # TS export assignment
    r"|\bmodule\s*\.\s*exports\b"  # CommonJS module.exports
    r"|\bexports\s*\.\s*[A-Za-z_$]"  # CommonJS exports.x =
    r"|\bexports\s*\["  # CommonJS exports['x'] =
    r"|\bObject\s*\.\s*defineProperty\s*\(\s*exports\b"  # transpiled exports
    r"|\bdeclare\s+(?:module|global|namespace)\b"  # ambient declarations
    r"|\bexport\s+(?:declare\s+)?(?:const|let|var)\s+[\[{]",  # destructured export
)

_TS_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:async\s+)?function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:abstract\s+)?class\s+(?P<cls>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:declare\s+)?(?:interface|type|enum|namespace|module)\s+(?P<ty>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+const\s+enum\s+(?P<cenum>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:declare\s+)?(?:const|let|var)\s+(?!enum\b)(?P<var>[A-Za-z_$][\w$]*)",
)

_TS_EXPORT_CLAUSE_RE = re.compile(r"\bexport\s+(?:type\s+)?\{(?P<inner>[^{}]*)\}")

_TS_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\b")

_TS_NAMED_IMPORT_RE = re.compile(
    r"\bimport\s+(?P<typeonly>type\s+)?"
    r"(?:[A-Za-z_$][\w$]*\s*,\s*)?"  # optional default import before the brace
    r"\{(?P<names>[^{}]*)\}"
    r"\s*from\s*['\"](?P<spec>[^'\"]+)['\"]",
)

_NODE_BUILTIN_IMPORTS = {
    "assert",
    "async_hooks",
    "buffer",
    "child_process",
    "crypto",
    "events",
    "fs",
    "http",
    "https",
    "os",
    "path",
    "process",
    "stream",
    "timers",
    "url",
    "util",
    "zlib",
}

_TEST_FRAMEWORK_IMPORTS = {"@jest/globals", "jest", "vitest", "mocha"}

_NPM_TEST_RUNNER_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(vitest|jest|mocha|ava)(?:$|[\s;&|])", re.IGNORECASE)

_NPM_MANIFEST_ONLY_TEST_SCRIPT_RE = re.compile(
    r"(?:package\s+)?manifest\s+check\s+passed|invalid\s+package\s+manifest|readFileSync\s*\(\s*['\"]package\.json"
    r"|readFileSync\s*\(\s*['\"](?:tsconfig\.json|README\.md|src/main\.ts|index\.html)"
    r"|existsSync\s*\(\s*['\"]dist/"
    r"|missing\s+(?:build|start|test)\s+script"
    r"|tsconfig\s+missing\s+compilerOptions"
    r"|main\.ts\s+has\s+no\s+output"
    r"|dist/[^'\"]+\s+not\s+found",
    re.IGNORECASE,
)

_COMMONJS_RUNTIME_TOKEN_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"(?:const|let|var)\s+[\w${}\s,]+=\s*require\s*\("
    r"|module\s*\.\s*exports\b"
    r"|exports\s*\.\s*[A-Za-z_$]\w*\s*="
    r"|exports\s*\[\s*['\"][^'\"]+['\"]\s*\]\s*="
    r")",
    re.IGNORECASE,
)

_NPM_PLACEHOLDER_TEST_SCRIPT_RE = re.compile(
    r"\b(?:no\s+tests?\s+(?:specified|yet)|tests?\s+not\s+(?:implemented|available)|all\s+tests?\s+passed)\b",
    re.IGNORECASE,
)

_NPM_SCRIPT_ENTRYPOINT_COMMANDS = {"node", "tsx", "ts-node", "bun", "deno"}

_NPM_SCRIPT_ENTRYPOINT_SUBCOMMANDS = {
    "bun": {"run", "test"},
    "deno": {"run", "test", "bench"},
}

_NPM_NODE_INLINE_CODE_FLAGS = {"-e", "--eval", "-p", "--print", "-c", "--check"}

_NPM_NODE_OPTION_VALUE_FLAGS = {
    "--conditions",
    "--experimental-default-type",
    "--icu-data-dir",
    "--input-type",
    "--loader",
    "--openssl-config",
    "--require",
    "--title",
    "-C",
    "-r",
}

_NPM_SCRIPT_SEPARATORS = {"&&", "||", ";", "|"}

_NPM_SCRIPT_FAILURE_SWALLOW_RE = re.compile(
    r"(?:^|[\s;&|])\|\|\s*(?:echo|printf|true|exit\s+0)(?:$|[\s;&|])",
    re.IGNORECASE,
)

_TSC_PROJECT_CHECK_FLAG = "KERNELONE_TSC_PROJECT_CHECK"

_PYTHON_COMMAND_IN_NPM_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(python3?|pytest|pip3?)(?:$|[\s;&|])", re.IGNORECASE)

_PYTHON_PACKAGE_MANIFEST_DEPENDENCIES = {
    "django",
    "fastapi",
    "flask",
    "pandas",
    "pydantic",
    "pytest",
    "sqlalchemy",
    "uvicorn",
}

_ARTIFACT_QUALITY_ERROR_PREFIX = "Artifact quality scan failed:"

_ARTIFACT_QUALITY_PATH_EXTENSIONS = (
    ".c",
    ".cjs",
    ".cc",
    ".cpp",
    ".css",
    ".cxx",
    ".go",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".html",
    ".htm",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
)

_ARTIFACT_QUALITY_QUOTED_PATH_RE = re.compile(r"['\"](?P<path>[^'\"]+\.[A-Za-z0-9]+)['\"]")

_ARTIFACT_QUALITY_IN_PATH_RE = re.compile(r"\bin\s+(?P<path>[^\s:]+(?:\.[A-Za-z0-9]+))(?::|$|\s)")

_ARTIFACT_QUALITY_COMPILER_PATH_RE = re.compile(
    r"(?m)^(?P<path>[^\s:(]+(?:\.[A-Za-z0-9]+))"
    r"(?:(?:\((?P<line_paren>\d+)(?:,(?P<column_paren>\d+))?\))"
    r"|(?::(?P<line_colon>\d+)(?::(?P<column_colon>\d+))?))?"
    r"(?::|\s)"
)

_ARTIFACT_QUALITY_TYPESCRIPT_ERROR_RE = re.compile(r"\berror\s+(?P<code>TS\d+):", re.IGNORECASE)

_ARTIFACT_QUALITY_RUST_ERROR_RE = re.compile(r"\berror\[(?P<code>E\d+)\]:", re.IGNORECASE)

_ARTIFACT_QUALITY_RUST_LOCATION_RE = re.compile(r"(?m)^\s*-->\s*(?P<path>[^:\n]+\.rs):(?P<line>\d+):(?P<column>\d+)")

_ARTIFACT_QUALITY_RUST_MISSING_BIN_RE = re.compile(
    r"can'?t find bin\s+[`'\"](?P<bin>[^`'\"]+)[`'\"]\s+at path\s+[`'\"](?P<path>[^`'\"]+)[`'\"]",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_JAVASCRIPT_MODULE_ERROR_RE = re.compile(
    r"(?P<message>The requested module\s+['\"]?[^'\"\s]+['\"]?\s+"
    r"does not provide an export named\s+(?:['\"][^'\"]+['\"]|[A-Za-z_$][\w$]*)|"
    r"Cannot find module ['\"][^'\"]+['\"]|"
    r"does not provide an export named (?:['\"][^'\"]+['\"]|[A-Za-z_$][\w$]*)|"
    r"require is not defined in ES module scope|exports is not defined in ES module scope|"
    r"Cannot require\(\) ES Module [^\n]+|ERR_REQUIRE_CYCLE_MODULE|"
    r"Cannot use import statement outside a module|"
    r"[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\s+is not a function)",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_NODE_CANNOT_FIND_MODULE_RE = re.compile(
    r"Cannot find module ['\"](?P<path>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_UNRESOLVED_IMPORT_SYMBOL_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_UNRESOLVED_RELATIVE_IMPORT_RE = re.compile(
    r"unresolved relative import ['\"](?P<specifier>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_UNDECLARED_RUNTIME_IMPORT_RE = re.compile(
    r"undeclared runtime import ['\"](?P<specifier>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest script ['\"](?P<script>[^'\"]+)['\"] (?P<detail>.+)",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_NPM_MISSING_ENTRYPOINT_RE = re.compile(
    r"references missing local entrypoint ['\"](?P<entrypoint>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_NPM_PYTHON_COMMAND_RE = re.compile(
    r"npm package manifest contains Python command in script ['\"](?P<script>[^'\"]+)['\"]",
    re.IGNORECASE,
)

_ARTIFACT_QUALITY_GO_UNDEFINED_RE = re.compile(
    r"\bundefined:\s*(?P<identifier>(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
