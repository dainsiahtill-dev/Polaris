"""Constants and compiled patterns for JavaScript/Node syntax repairs."""

from __future__ import annotations

import re

JAVASCRIPT_ESM_COMMONJS_ENTRYPOINT_SOURCE_TOOL = "deterministic_javascript_esm_commonjs_entrypoint_repair"

JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL = "deterministic_javascript_missing_export_repair"

JAVASCRIPT_MISSING_METHOD_RUNTIME_SOURCE_TOOL = "deterministic_javascript_missing_method_runtime_repair"

JAVASCRIPT_DOM_GLOBAL_RUNTIME_SOURCE_TOOL = "deterministic_javascript_dom_global_runtime_guard_repair"

JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL = "deterministic_javascript_test_missing_target_repair"

NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL = "deterministic_node_test_script_contract_repair"

NPM_SCRIPT_CONTRACT_SOURCE_TOOL = "deterministic_npm_script_contract_repair"

TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL = "deterministic_typescript_local_js_import_repair"

_MISSING_NPM_SCRIPT_ENTRYPOINT_RE = re.compile(
    r"npm package manifest script '([^']+)' references missing local entrypoint '([^']+)'",
    re.IGNORECASE,
)

_MISSING_NPM_SCRIPT_ENTRYPOINT_GATE_RE = re.compile(
    r"script '([^']+)' references missing local entrypoint:\s*(\S+)",
    re.IGNORECASE,
)

_NODE_CANNOT_FIND_MODULE_DIST_RE = re.compile(
    r"Cannot find module ['\"](?P<path>[^'\"]*/dist/[^'\"]+\.js)['\"]",
    re.IGNORECASE,
)

_LOCAL_JS_MODULE_NOT_FOUND_RE = re.compile(
    r"Cannot find module ['\"](?P<specifier>\.{1,2}/[^'\"]+\.js)['\"]",
    re.IGNORECASE,
)

_LOCAL_JS_IMPORT_SPECIFIER_RE = re.compile(
    r"(?P<prefix>\b(?:from|import)\s*(?:\(\s*)?['\"])(?P<specifier>\.{1,2}/[^'\"]+\.js)(?P<suffix>['\"])",
)

_HTTP_SERVER_FIXED_PORT_RE = re.compile(
    r"(?P<flag>\s(?:-p|--port)\s+)(?P<port>\d{2,5})(?=$|\s)",
    re.IGNORECASE,
)

_RECURSIVE_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest script '([^']+)' recursively invokes itself",
    re.IGNORECASE,
)

_PLACEHOLDER_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest script '([^']+)' is a placeholder command",
    re.IGNORECASE,
)

_PYTHON_COMMAND_NPM_SCRIPT_RE = re.compile(
    r"npm package manifest contains Python command in script '([^']+)'",
    re.IGNORECASE,
)

_PYTHON_COMMAND_TOKEN_RE = re.compile(r"(?<![\w.-])python(?:3|[0-9]+(?:\.[0-9]+)?)?(?![\w.-])", re.IGNORECASE)

_REPAIRABLE_TEST_SCRIPT_ISSUES = frozenset(
    {
        "default_failing_test_script",
        "invalid_node_eval_syntax",
        "invalid_shell_syntax",
        "manifest_only_test_script",
        "missing_local_entrypoint",
        "placeholder_command",
        "placeholder_test_script",
        "shell_command_substitution",
        "swallows_command_failures",
    }
)

_UNRESOLVED_IMPORT_SYMBOL_RE = re.compile(
    r"unresolved (?:import )?symbol ['\"](?P<symbol>[^'\"]+)['\"] "
    r"from ['\"](?P<module>[^'\"]+)['\"] in (?P<path>\S+)",
    re.IGNORECASE,
)

_MISSING_NAMED_EXPORT_RE = re.compile(
    r"The requested module\s+['\"]?(?P<module>\.[^'\"\s]+)['\"]?\s+does not provide an export named\s+"
    r"['\"]?(?P<symbol>[A-Za-z_$][\w$]*)['\"]?",
)

_JS_NAMED_IMPORT_RE = re.compile(r"\bimport\s*\{\s*(?P<symbols>[^}]+)\s*\}\s*from\s*['\"](?P<specifier>\.[^'\"]+)['\"]")

_NODE_SCRIPT_SEGMENT_RE = re.compile(r"\s*(?:&&|\|\||[;|])\s*")

_NODE_FLAGS_WITH_VALUE = frozenset(
    {
        "--experimental-loader",
        "--import",
        "--loader",
        "--require",
        "--test-name-pattern",
        "--test-reporter",
        "--test-reporter-destination",
        "-r",
    }
)

_NODE_FLAGS_WITH_VALUE_PREFIXES = ("--experimental-loader=", "--import=", "--loader=", "--require=")

_JS_RUNTIME_FILE_RE = re.compile(r"(?:file://)?(?P<path>/[^\s:]+\.js):(?P<line>\d+)")

_JS_MISSING_METHOD_RUNTIME_RE = re.compile(
    r"(?P<file>(?:file://)?/[^\s:]+\.js):(?P<line>\d+).*?"
    r"TypeError:\s+(?P<object>[A-Za-z_$][\w$]*)\.(?P<member>[A-Za-z_$][\w$]*)\s+is not a function",
    re.DOTALL,
)

_JS_MISSING_METHOD_RUNTIME_STACK_RE = re.compile(
    r"TypeError:\s+(?P<object>[A-Za-z_$][\w$]*)\.(?P<member>[A-Za-z_$][\w$]*)\s+is not a function"
    r".*?\((?:file://)?(?P<file>/[^\s:]+\.js):(?P<line>\d+):\d+\)",
    re.DOTALL,
)

_JS_CONSTRUCTOR_STRING_CONTRACT_RE = re.compile(
    r"(?P<class_name>[A-Za-z_$][\w$]*)\.(?P<field>[A-Za-z_$][\w$]*)\s+must be a non-empty string"
    r".*?\bnew\s+(?P=class_name)\s*\((?:file://)?(?P<file>/[^\s:]+\.js):(?P<line>\d+)",
    re.DOTALL,
)

_JS_CONSTRUCTOR_REQUIRES_FIELD_RE = re.compile(
    r"(?P<class_name>[A-Za-z_$][\w$]*)\s+requires\s+(?:an?\s+)?(?P<field>[A-Za-z_$][\w$]*)"
    r".*?\bnew\s+(?P=class_name)\s*\((?:file://)?(?P<file>/[^\s:]+\.js):(?P<line>\d+)",
    re.DOTALL,
)

_JS_DOM_GLOBAL_RUNTIME_RE = re.compile(
    r"(?P<file>(?:file://)?/[^\s:]+\.js):(?P<line>\d+).*?"
    r"ReferenceError:\s+(?P<global>document|window)\s+is not defined",
    re.IGNORECASE | re.DOTALL,
)

_BROWSER_BOOTSTRAP_CALL_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?P<call>(?:whenReady|bootstrap|initApp|startApp)\s*\(\s*\)\s*;)\s*$"
)

_COMMONJS_REQUIRE_BINDING_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+(?P<binding>[A-Za-z_$][\w$]*)\s*=\s*"
    r"require\((?P<quote>['\"])(?P<specifier>[^'\"]+)(?P=quote)\)\s*;?\s*$"
)

_COMMONJS_REQUIRE_DESTRUCTURING_RE = re.compile(
    r"^(?P<indent>\s*)(?:const|let|var)\s+\{(?P<bindings>[^}]+)\}\s*=\s*"
    r"require\((?P<quote>['\"])(?P<specifier>[^'\"]+)(?P=quote)\)\s*;?\s*$"
)

_COMMONJS_MODULE_EXPORTS_DEFAULT_RE = re.compile(
    r"^(?P<indent>\s*)module\.exports\s*=\s*(?P<value>[A-Za-z_$][\w$]*)\s*;?\s*$"
)

_COMMONJS_MODULE_EXPORTS_OBJECT_RE = re.compile(
    r"^(?P<indent>\s*)module\.exports\s*=\s*\{(?P<bindings>[^}]+)\}\s*;?\s*$"
)

_COMMONJS_MODULE_EXPORTS_PROPERTY_RE = re.compile(
    r"^(?P<indent>\s*)module\.exports\.(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?P<value>.+?)\s*;?\s*$"
)

_COMMONJS_REQUIRE_MAIN_GUARD_RE = re.compile(
    r"^(?P<indent>\s*)if\s*\(\s*require\.main\s*===\s*module\s*\)\s*\{\s*"
    r"(?P<call>[A-Za-z_$][\w$]*\s*\(\s*\)\s*;?)\s*\}\s*$"
)

_COMMONJS_MODULE_EXPORTS_OBJECT_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)module\.exports\s*=\s*\{(?P<body>.*?)\}\s*;?\s*$",
    re.DOTALL,
)

_COMMONJS_MODULE_EXPORTS_VALUE_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)module\.exports\s*=\s*(?P<value>[A-Za-z_$][\w$]*)\s*;?\s*$"
)

_COMMONJS_MODULE_EXPORTS_PROPERTY_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)module\.exports\.(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?P<value>[A-Za-z_$][\w$]*|(?P<literal>['\"][^'\"]*['\"]|\d+(?:\.\d+)?|true|false|null))\s*;?\s*$"
)

_COMMONJS_REQUIRE_MAIN_GUARD_BLOCK_RE = re.compile(
    r"(?m)^(?P<indent>\s*)if\s*\(\s*require\.main\s*===\s*module\s*\)\s*\{\s*(?P<body>.*?)\s*\}\s*$",
    re.DOTALL,
)

_ORPHAN_COMMONJS_EXPORTS_LINE_RE = re.compile(r"(?m)^\s*(?:module)?\.exports\s*;\s*$")

_JS_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][\w$]*$")

_JS_STRING_LITERAL_RE = re.compile(r"(?P<quote>['\"])(?P<value>(?:\\.|(?!(?P=quote)).)*)(?P=quote)")

_JS_DECLARATION_RE_TEMPLATE = (
    r"(?m)^(?P<indent>\s*)(?P<decl>(?:async\s+)?(?:class|function)\s+{symbol}\b|(?:const|let|var)\s+{symbol}\b)"
)

_JS_EXPORTED_CLASS_RE = re.compile(r"(?m)^(?P<indent>\s*)export\s+class\s+(?P<name>[A-Za-z_$][\w$]*)\b[^\n]*\{")

_JS_CLASS_RE_TEMPLATE = r"(?m)^(?P<indent>\s*)(?:export\s+)?class\s+{class_name}\b[^\n]*\{{"

_JS_METHOD_RE = re.compile(r"(?m)^\s{2,}(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")

_JS_FUNCTION_START_RE_TEMPLATE = r"(?m)^(?P<prefix>\s*(?:export\s+)?(?:async\s+)?function\s+{symbol}\s*\([^)]*\)\s*)\{{"
