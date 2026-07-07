"""Artifact quality checks shared by Director and integration QA."""

from __future__ import annotations

import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from polaris.kernelone.quality.cross_artifact_interfaces import (
    ContractAmendmentRequest,
    CrossArtifactConsistencyIssue,
    CrossArtifactInterfaceContract,
    CrossArtifactRepairPlan,
    build_contract_amendment_request,
    plan_cross_artifact_repairs,
    scan_cross_artifact_consistency,
)
from polaris.kernelone.quality.interface_ledger import (
    read_all_declared_interfaces,
    read_declared_interfaces,
    validate_declared_interface_issues_against_snapshot,
)
from polaris.kernelone.quality.package_scripts import (
    PackageScriptIssue,
    check_package_scripts,
)

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
_TS_RETURN_OBJECT_BLOCK_RE = re.compile(r"return\s*\{(?P<body>.*?)^\s*\};", re.DOTALL | re.MULTILINE)
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

# Cross-file symbol-coherence detection for TS/JS named imports. The regex
# export-surface intentionally fails open on ambiguous modules; keep an env kill
# switch for emergency false-positive triage.
_TS_SYMBOL_COHERENCE_FLAG = "KERNELONE_TS_SYMBOL_COHERENCE"

# Surface-unknowable constructs: ANY occurrence (even in a comment/string) forces
# fail-open, which only ever causes a SKIPPED check (false negative = safe), never
# a false positive. Detected on RAW text so comments cannot hide them.
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
# Generous export-name capture: missing a real export form would be a FALSE
# POSITIVE, so capture every plausible declaration form. `enum`/`from` are kept
# out of the const/let/var capture by the explicit keyword forms below.
_TS_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:async\s+)?function\s*\*?\s*(?P<fn>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:abstract\s+)?class\s+(?P<cls>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:declare\s+)?(?:interface|type|enum|namespace|module)\s+(?P<ty>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+const\s+enum\s+(?P<cenum>[A-Za-z_$][\w$]*)"
    r"|\bexport\s+(?:declare\s+)?(?:const|let|var)\s+(?!enum\b)(?P<var>[A-Za-z_$][\w$]*)",
)
# `export { A, B as C }` and `export { A } from './y'` — the EXPORTED name is the
# alias when `as` is present. `export default {…}` is excluded (no `{` directly
# after `export`); `export type { … }` handled by the optional `type`.
_TS_EXPORT_CLAUSE_RE = re.compile(r"\bexport\s+(?:type\s+)?\{(?P<inner>[^{}]*)\}")
_TS_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\b")
# `import [type] [Default,] { names } from '<spec>'` — only this shape is
# symbol-checked. Default-only / `* as NS` / side-effect imports have no `{` and
# never match, so they are skipped (no named symbol to verify).
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
_NPM_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE = re.compile(
    r"(?:npm\s+run\s+(?:build|compile)|pnpm\s+(?:build|compile)|yarn\s+(?:build|compile))"
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


def _check_html_completeness(absolute_path: str) -> dict[str, Any]:
    """Detect structurally truncated HTML.

    An output-budget-truncated write produces a file that simply STOPS —
    missing ``</html>`` / unbalanced ``<script>`` tags (live factory-bench
    L2-11 r4: typing_test.html ended mid-function at line 198, no closing
    tags, and nothing in the chain noticed). Not a validator — only the
    truncation signature is checked.
    """
    with open(absolute_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    lowered = text.lower()
    problems: list[str] = []
    if "<html" in lowered and "</html>" not in lowered:
        problems.append("missing </html> closing tag")
    open_scripts = len(re.findall(r"<script\b", lowered))
    close_scripts = lowered.count("</script>")
    if open_scripts > close_scripts:
        problems.append(f"{open_scripts - close_scripts} unclosed <script> tag(s)")
    if problems:
        return {"ok": False, "error": "truncated/incomplete HTML: " + "; ".join(problems)}
    return {"ok": True}


def _compress_node_syntax_error(raw_output: str, absolute_path: str) -> str:
    """Reduce `node --check` output to its actionable core.

    Keeps "<file>:<line>", the offending code line, the caret, and the
    SyntaxError message; drops the node stack frames and replaces the absolute
    path with the file name. A weak model repairing from this text needs the
    quoted line for a narrow edit_blocks match — the "at wrapSafe (node:...)"
    frames and absolute paths are pure distraction (live factory-bench L2-11
    r2: the repair turn failed an edit_blocks match against the noisy form).
    """
    text = str(raw_output or "").strip()
    if not text:
        return "syntax error"
    file_name = os.path.basename(absolute_path)
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("at ") or stripped.startswith("Node.js v"):
            continue
        lines.append(line.replace(absolute_path, file_name))
        if stripped.startswith(("SyntaxError", "Error")) and len(lines) > 1:
            break
    return "\n".join(lines).strip() or text[:200]


def check_source_file_syntax(absolute_path: str) -> dict[str, Any] | None:
    """Best-effort syntax validation for a materialized source file.

    Returns ``{'ok': False, 'error': ...}`` on syntax failure, ``{'ok': True}``
    on pass, ``None`` when no checker applies (unknown extension or checker
    tool unavailable). Single source of truth shared by the post-write tool
    diagnostic (A5) and the materialization artifact-quality scan, so a
    syntax-broken artifact that survives the turn deterministically enters the
    repair ladder (live factory-bench L2-10 r5: ``gfm: true;`` in app.js had
    its write-time diagnostic ignored and nothing downstream re-checked).
    """
    suffix = os.path.splitext(absolute_path)[1].lower()
    try:
        if suffix == ".py":
            try:
                py_compile.compile(absolute_path, doraise=True)
                return {"ok": True}
            except py_compile.PyCompileError as exc:
                message = str(exc.msg or exc).strip().splitlines()[-1]
                return {"ok": False, "error": message}
        if suffix in (".js", ".mjs", ".cjs"):
            node = shutil.which("node")
            if not node:
                return None
            proc = subprocess.run(
                [node, "--check", absolute_path], capture_output=True, text=True, timeout=20, check=False
            )
            if proc.returncode == 0:
                return {"ok": True}
            detail = _compress_node_syntax_error(proc.stderr or proc.stdout, absolute_path)
            return {"ok": False, "error": detail[:400]}
        if suffix == ".json":
            with open(absolute_path, encoding="utf-8") as fh:
                json.load(fh)
            return {"ok": True}
        if suffix in (".html", ".htm"):
            return _check_html_completeness(absolute_path)
    except (OSError, UnicodeDecodeError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"syntax check could not run: {exc}"}
    except ValueError as exc:  # json.JSONDecodeError
        return {"ok": False, "error": f"invalid JSON: {exc}"}
    return None


@dataclass(frozen=True, slots=True)
class ArtifactQualityIssue:
    """Typed projection for one artifact-quality finding.

    This is evidence, not a repair authorization. String-compatible callers
    still consume ``ArtifactQualityEvidence.errors`` while typed gates can rely
    on ``issues`` instead of reparsing human-readable strings.
    """

    code: str
    message: str
    path: str | None = None
    severity: str = "error"
    source: str = "artifact_quality"
    line: int | None = None
    column: int | None = None
    metadata: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
            "source": self.source,
            "metadata": dict(self.metadata or {}),
        }
        if self.line is not None:
            payload["line"] = self.line
        if self.column is not None:
            payload["column"] = self.column
        return payload


@dataclass(frozen=True, slots=True)
class ArtifactQualityEvidence:
    """Structured quality evidence used by audit, AGI, and repair planning."""

    errors: tuple[str, ...] = ()
    issues: tuple[ArtifactQualityIssue, ...] = ()
    scanned_relative_paths: tuple[str, ...] = ()
    cross_artifact_issues: tuple[CrossArtifactConsistencyIssue, ...] = ()
    cross_artifact_repair_plans: tuple[CrossArtifactRepairPlan, ...] = ()
    contract_amendment_request: ContractAmendmentRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors": list(self.errors),
            "issues": [issue.to_dict() for issue in self.issues],
            "scanned_relative_paths": list(self.scanned_relative_paths),
            "cross_artifact_issues": [issue.to_dict() for issue in self.cross_artifact_issues],
            "cross_artifact_repair_plans": [plan.to_dict() for plan in self.cross_artifact_repair_plans],
            "contract_amendment_request": self.contract_amendment_request.to_dict()
            if self.contract_amendment_request is not None
            else None,
        }


@dataclass(frozen=True, slots=True)
class _FileArtifactQualityEvidence:
    """Internal per-file scanner output with legacy strings and typed issues."""

    errors: tuple[str, ...] = ()
    issues: tuple[ArtifactQualityIssue, ...] = ()


def _artifact_quality_scan_failure_issue(
    message: str,
    *,
    exc: BaseException | None = None,
) -> ArtifactQualityIssue:
    """Return typed evidence for scanner infrastructure failures."""

    metadata: dict[str, Any] = {"raw": message}
    if exc is not None:
        metadata["exception_type"] = type(exc).__name__
    return ArtifactQualityIssue(
        code="artifact_quality_scan_failed",
        message=message,
        source="artifact_quality_scanner",
        metadata=metadata,
    )


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
    r"\bundefined:\s*(?P<identifier>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


def _legacy_artifact_quality_issue_code_from_message(message: str) -> str:
    """Classify legacy display-string artifact quality diagnostics."""

    normalized = message.lower()
    for classifier in _LEGACY_ARTIFACT_QUALITY_ISSUE_CODE_CLASSIFIERS:
        issue_code = classifier(message, normalized)
        if issue_code:
            return issue_code
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug[:80] or "artifact_quality_error"


def _artifact_quality_issue_code_from_typed_metadata(
    metadata: Mapping[str, Any],
    *,
    source: str,
) -> str:
    """Classify structured issue metadata before legacy message parsing.

    Only stable scanner-owned metadata fields are mapped here. Display strings
    remain a compatibility fallback rather than the primary source of issue
    identity.
    """

    source_token = str(source or "").strip()
    script_issue = str(metadata.get("script_issue") or "").strip()
    script_issue_source = str(metadata.get("script_issue_source") or "").strip()
    package_script_issue_code = str(metadata.get("package_script_issue_code") or "").strip()
    if package_script_issue_code or (
        script_issue
        and source_token in {"package_manifest_scanner", "package_scripts"}
        and script_issue_source in {"", "package_manifest_scanner", "package_scripts"}
    ):
        return "npm_manifest_invalid"
    if (
        script_issue in {"missing_compiled_entrypoint", "typescript_source_loader_require_cycle"}
        and source_token == "runtime_smoke"
    ):
        return "javascript_module_error"
    if (
        script_issue == "missing_local_config"
        and source_token == "npm_script_config_scanner"
        and script_issue_source == "npm_script_config_scanner"
    ):
        return "npm_script_missing_local_config"
    if (
        script_issue == "missing_local_entrypoint"
        and source_token == "npm_script_entrypoint_scanner"
        and script_issue_source == "npm_script_entrypoint_scanner"
    ):
        return "npm_script_missing_local_entrypoint"

    diagnostic_kind = str(metadata.get("diagnostic_kind") or "").strip()
    language = str(metadata.get("language") or "").strip().lower()
    if diagnostic_kind == "workspace_path_missing" and source_token == "artifact_quality_scanner":
        return "workspace_path_missing"
    if diagnostic_kind == "syntax_error" and source_token == "source_syntax_checker":
        return "syntax_error"
    if diagnostic_kind == "undefined_identifier" and language == "go":
        return "go_compile_error"
    if diagnostic_kind == "package_module_type_commonjs_mismatch":
        return "package_module_type_commonjs_mismatch"
    if diagnostic_kind == "html_module_script_typescript_source":
        return "html_module_script_typescript_source"
    if diagnostic_kind == "unresolved_relative_import" and source_token == "typescript_import_scanner":
        return "unresolved_relative_import"
    if (
        diagnostic_kind == "undeclared_runtime_import"
        and source_token == "typescript_import_scanner"
    ):
        return "undeclared_runtime_import"
    if (
        diagnostic_kind == "typescript_node_types_missing"
        and source_token == "typescript_import_scanner"
    ):
        return "typescript_node_types_missing"
    if (
        diagnostic_kind == "typescript_escaped_newline_line_comment"
        and source_token == "typescript_syntax_red_flag_scanner"
    ):
        return "typescript_escaped_newline_line_comment"
    if (
        diagnostic_kind == "typescript_return_object_semicolon_property"
        and source_token == "typescript_syntax_red_flag_scanner"
    ):
        return "typescript_return_object_semicolon_property"
    if (
        diagnostic_kind == "typescript_isolated_modules_type_reexport"
        and source_token == "typescript_syntax_red_flag_scanner"
    ):
        return "typescript_isolated_modules_type_reexport"
    if (
        diagnostic_kind == "typescript_zod_type_class_collision"
        and source_token == "typescript_syntax_red_flag_scanner"
    ):
        return "typescript_zod_type_class_collision"
    if (
        diagnostic_kind == "typescript_import_unresolved_symbol"
        and source_token == "typescript_symbol_coherence_scanner"
    ):
        return "typescript_import_unresolved_symbol"
    if (
        diagnostic_kind == "typescript_project_typecheck_failed"
        and source_token == "typescript_project_typecheck"
    ):
        return "typescript_project_typecheck_failed"
    if (
        diagnostic_kind == "npm_script_node_test_directory_target"
        and source_token == "npm_script_test_target_scanner"
    ):
        return "npm_script_node_test_directory_target"
    return ""


def _legacy_target_or_import_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy target-contract and import-topology diagnostics."""

    if "declared target file" in normalized_message and "missing" in normalized_message:
        return "declared_target_missing"
    if "unresolved import symbol" in normalized_message:
        return "unresolved_import_symbol"
    if "unresolved relative import" in normalized_message:
        return "unresolved_relative_import"
    if "undeclared runtime import" in normalized_message:
        return "undeclared_runtime_import"
    return ""


def _legacy_npm_manifest_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy npm manifest display diagnostics."""

    if (
        "npm default failing test script" in normalized_message
        or "npm placeholder test script" in normalized_message
        or "npm manifest-only test script" in normalized_message
    ):
        return "npm_manifest_invalid"
    runtime_script_invoked = (
        "npm run start" in normalized_message
        or "npm start" in normalized_message
        or "npm run serve" in normalized_message
        or "npm run dev" in normalized_message
        or "npm run preview" in normalized_message
    )
    runtime_port_conflict = "eaddrinuse" in normalized_message or "address already in use" in normalized_message
    if runtime_script_invoked and runtime_port_conflict:
        return "npm_manifest_invalid"
    if "test script must use node --test" in normalized_message:
        return "npm_manifest_invalid"
    if "npm package manifest" in normalized_message:
        return "npm_manifest_invalid"
    return ""


def _legacy_language_or_syntax_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy broad language and syntax diagnostics."""

    if "typescript project typecheck failed" in normalized_message:
        return "typescript_project_typecheck_failed"
    if "syntax error" in normalized_message or "invalid json" in normalized_message:
        return "syntax_error"
    return ""


def _legacy_hygiene_issue_code(_message: str, normalized_message: str) -> str:
    """Classify legacy hygiene and contamination diagnostics."""

    if "patch residue" in normalized_message:
        return "patch_residue"
    if "tool execution receipt contamination" in normalized_message:
        return "tool_receipt_contamination"
    if "source narration contamination" in normalized_message:
        return "source_narration_contamination"
    return ""


def _legacy_compiler_issue_code_from_explicit_code(message: str, _normalized_message: str) -> str:
    """Classify legacy compiler diagnostics with explicit TS/Rust error codes."""

    typescript_match = _ARTIFACT_QUALITY_TYPESCRIPT_ERROR_RE.search(message)
    if typescript_match:
        return f"typescript_{str(typescript_match.group('code') or '').lower()}"
    rust_match = _ARTIFACT_QUALITY_RUST_ERROR_RE.search(message)
    if rust_match:
        return f"rust_{str(rust_match.group('code') or '').lower()}"
    return ""


def _legacy_compiler_issue_code_from_path(message: str, normalized_message: str) -> str:
    """Classify legacy compiler diagnostics that only expose a source path."""

    compiler_path = _artifact_quality_issue_path(message)
    if not compiler_path:
        return ""
    compiler_suffix = Path(compiler_path).suffix.lower()
    if compiler_suffix == ".go":
        return "go_compile_error"
    if compiler_suffix == ".java" and "error:" in normalized_message:
        return "java_compile_error"
    if compiler_suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}:
        return "cpp_compile_error"
    return ""


_LEGACY_ARTIFACT_QUALITY_ISSUE_CODE_CLASSIFIERS: tuple[_LegacyIssueCodeClassifier, ...] = (
    _legacy_target_or_import_issue_code,
    _legacy_compiler_issue_code_from_explicit_code,
    _legacy_compiler_issue_code_from_path,
    _legacy_language_or_syntax_issue_code,
    _legacy_npm_manifest_issue_code,
    _legacy_hygiene_issue_code,
)


def _artifact_quality_issue_path(message: str) -> str | None:
    rust_location = _ARTIFACT_QUALITY_RUST_LOCATION_RE.search(message)
    if rust_location:
        return str(rust_location.group("path") or "").strip().replace("\\", "/")
    for regex in (
        _ARTIFACT_QUALITY_COMPILER_PATH_RE,
        _ARTIFACT_QUALITY_QUOTED_PATH_RE,
        _ARTIFACT_QUALITY_IN_PATH_RE,
    ):
        match = regex.search(message)
        if not match:
            continue
        path = str(match.group("path") or "").strip().replace("\\", "/")
        if path.endswith(_ARTIFACT_QUALITY_PATH_EXTENSIONS):
            return path
    return None


def _artifact_quality_optional_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _artifact_quality_issue_location(message: str) -> tuple[int | None, int | None]:
    rust_location = _ARTIFACT_QUALITY_RUST_LOCATION_RE.search(message)
    if rust_location:
        return (
            _artifact_quality_optional_int(rust_location.group("line")),
            _artifact_quality_optional_int(rust_location.group("column")),
        )
    match = _ARTIFACT_QUALITY_COMPILER_PATH_RE.search(message)
    if not match:
        return None, None
    raw_line = match.group("line_paren") or match.group("line_colon")
    raw_column = match.group("column_paren") or match.group("column_colon")
    try:
        line = int(raw_line) if raw_line else None
    except ValueError:
        line = None
    try:
        column = int(raw_column) if raw_column else None
    except ValueError:
        column = None
    return line, column


def _artifact_quality_issue_metadata(text: str, message: str, code: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"raw": text}
    if code == "declared_target_missing":
        metadata.update(_legacy_declared_target_missing_metadata(message))
    elif code == "npm_manifest_invalid":
        metadata["manifest_path"] = "package.json"
        metadata.update(_legacy_npm_manifest_issue_metadata(message))
    elif code == "unresolved_import_symbol":
        metadata.update(_legacy_unresolved_import_symbol_metadata(message))
    elif code == "unresolved_relative_import":
        metadata.update(_legacy_unresolved_relative_import_metadata(message))
    elif code == "undeclared_runtime_import":
        metadata.update(_legacy_undeclared_runtime_import_metadata(message))
    elif code.startswith(("typescript_ts", "rust_e")) or code in {
        "go_compile_error",
        "java_compile_error",
        "cpp_compile_error",
    }:
        metadata.update(_legacy_compiler_diagnostic_metadata(message, code))
    return {key: value for key, value in metadata.items() if value}


def _legacy_declared_target_missing_metadata(message: str) -> dict[str, str]:
    """Project old declared-target display errors into typed metadata.

    Target contract scanners should emit structured metadata directly. This
    helper isolates the legacy display-string path while callers migrate.
    """

    path = _artifact_quality_issue_path(message)
    if not path:
        return {}
    return {"target_file": path}


def _legacy_npm_script_metadata(script_name: str, script_issue: str, *, entrypoint: str = "") -> dict[str, str]:
    """Project old display-only npm script errors into typed metadata.

    New package-script scanner paths should construct ArtifactQualityIssue rows
    directly from PackageScriptIssue. This compatibility helper is only for
    legacy diagnostic strings that still reach _artifact_quality_issue_metadata.
    """

    metadata = {
        "script_name": script_name.strip(),
        "script_issue": script_issue.strip(),
        "script_issue_source": "legacy_error_text",
    }
    if entrypoint:
        metadata["entrypoint"] = entrypoint.strip()
    return {key: value for key, value in metadata.items() if value}


def _legacy_npm_manifest_issue_metadata(message: str) -> dict[str, str]:
    script_match = _ARTIFACT_QUALITY_NPM_SCRIPT_RE.search(message)
    if script_match:
        detail = str(script_match.group("detail") or "").strip()
        entrypoint = ""
        entrypoint_match = _ARTIFACT_QUALITY_NPM_MISSING_ENTRYPOINT_RE.search(detail)
        if entrypoint_match:
            entrypoint = str(entrypoint_match.group("entrypoint") or "").strip()
        return _legacy_npm_script_metadata(
            str(script_match.group("script") or ""),
            _npm_manifest_script_issue(detail),
            entrypoint=entrypoint,
        )

    python_command_match = _ARTIFACT_QUALITY_NPM_PYTHON_COMMAND_RE.search(message)
    if python_command_match:
        return _legacy_npm_script_metadata(str(python_command_match.group("script") or ""), "python_command")

    normalized_message = message.lower()
    if "test script must use node --test" in normalized_message:
        return _legacy_npm_script_metadata("test", "node_test_runner_contract")

    script_name = ""
    for candidate in ("start", "serve", "dev", "preview"):
        if f"npm run {candidate}" in normalized_message:
            script_name = candidate
            break
    if not script_name and "npm start" in normalized_message:
        script_name = "start"
    port_conflict = "eaddrinuse" in normalized_message or "address already in use" in normalized_message
    if script_name and port_conflict:
        return _legacy_npm_script_metadata(script_name, "fixed_port_conflict")
    if "npm default failing test script" in normalized_message:
        return _legacy_npm_script_metadata("test", "default_failing_test_script")
    if "npm placeholder test script" in normalized_message:
        return _legacy_npm_script_metadata("test", "placeholder_test_script")
    if "npm manifest-only test script" in normalized_message:
        return _legacy_npm_script_metadata("test", "manifest_only_test_script")
    return {}


def _legacy_unresolved_import_symbol_metadata(message: str) -> dict[str, str]:
    """Project old unresolved-import-symbol display text into metadata.

    Cross-file interface scanners should prefer typed import/export evidence.
    This compatibility helper keeps legacy diagnostic parsing in one place until
    all callers emit structured import issues directly.
    """

    match = _ARTIFACT_QUALITY_UNRESOLVED_IMPORT_SYMBOL_RE.search(message)
    if not match:
        return {}
    return {
        key: value
        for key, value in {
            "symbol": str(match.group("symbol") or "").strip(),
            "module": str(match.group("module") or "").strip(),
            "importer_path": str(match.group("path") or "").strip(),
        }.items()
        if value
    }


def _legacy_unresolved_relative_import_metadata(message: str) -> dict[str, str]:
    """Project old unresolved-relative-import display text into metadata."""

    match = _ARTIFACT_QUALITY_UNRESOLVED_RELATIVE_IMPORT_RE.search(message)
    if not match:
        return {}
    return {
        key: value
        for key, value in {
            "specifier": str(match.group("specifier") or "").strip(),
            "importer_path": str(match.group("path") or "").strip(),
        }.items()
        if value
    }


def _legacy_undeclared_runtime_import_metadata(message: str) -> dict[str, str]:
    """Project old undeclared-runtime-import display text into metadata."""

    match = _ARTIFACT_QUALITY_UNDECLARED_RUNTIME_IMPORT_RE.search(message)
    if not match:
        return {}
    specifier = str(match.group("specifier") or "").strip()
    package_root = _package_root_name(specifier) if specifier else ""
    return {
        key: value
        for key, value in {
            "specifier": specifier,
            "package_root": package_root,
            "path": str(match.group("path") or "").strip(),
            "diagnostic_kind": "undeclared_runtime_import",
            "archetype": "missing_dependency",
        }.items()
        if value
    }


def _legacy_compiler_diagnostic_metadata(message: str, code: str) -> dict[str, str]:
    """Project legacy compiler diagnostic text into metadata.

    Parser-backed scanners should emit these fields directly. This helper keeps
    compatibility parsing centralized while typed compiler issue rows replace
    display-string diagnostics one language family at a time.
    """

    metadata: dict[str, str] = {}
    if code.startswith("typescript_ts"):
        typescript_match = _ARTIFACT_QUALITY_TYPESCRIPT_ERROR_RE.search(message)
        if typescript_match:
            metadata["diagnostic_code"] = str(typescript_match.group("code") or "").strip()
    elif code.startswith("rust_e"):
        rust_match = _ARTIFACT_QUALITY_RUST_ERROR_RE.search(message)
        if rust_match:
            metadata["diagnostic_code"] = str(rust_match.group("code") or "").strip()
    elif code in {"go_compile_error", "java_compile_error", "cpp_compile_error"}:
        metadata["language"] = code.removesuffix("_compile_error")
        if code == "go_compile_error":
            go_undefined_match = _ARTIFACT_QUALITY_GO_UNDEFINED_RE.search(message)
            if go_undefined_match:
                metadata["identifier"] = str(go_undefined_match.group("identifier") or "").strip()
                metadata["diagnostic_kind"] = "undefined_identifier"
    return {key: value for key, value in metadata.items() if value}


def _npm_manifest_script_issue(detail: str) -> str:
    normalized = detail.lower()
    if "placeholder command" in normalized:
        return "placeholder_command"
    if "references missing local entrypoint" in normalized:
        return "missing_local_entrypoint"
    if "recursively invokes itself" in normalized:
        return "recursive_script"
    if "invalid shell syntax" in normalized:
        return "invalid_shell_syntax"
    if "invalid node eval syntax" in normalized:
        return "invalid_node_eval_syntax"
    if "shell command substitution" in normalized:
        return "shell_command_substitution"
    if "swallows command failures" in normalized:
        return "swallows_command_failures"
    return "manifest_script_error"


def _javascript_module_error_metadata(text: str, message: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"raw": text}
    normalized = f"{text}\n{message}".lower()
    start_invoked = "npm run start" in normalized or "npm start" in normalized
    source_loader = "ts-node" in normalized or "node --loader" in normalized or ".ts" in normalized
    require_cycle = "err_require_cycle_module" in normalized or "cannot require() es module" in normalized
    if start_invoked and source_loader and require_cycle:
        metadata["script_name"] = "start"
        metadata["script_issue"] = "typescript_source_loader_require_cycle"
    missing_compiled_entrypoint = _compiled_entrypoint_from_node_module_error(message)
    if missing_compiled_entrypoint:
        metadata["script_issue"] = "missing_compiled_entrypoint"
        metadata["script_issue_source"] = "node_module_not_found"
        metadata["entrypoint"] = missing_compiled_entrypoint
        script_name = _script_name_from_npm_invocation(normalized)
        if script_name:
            metadata["script_name"] = script_name
    return metadata


def _compiled_entrypoint_from_node_module_error(message: str) -> str:
    match = _ARTIFACT_QUALITY_NODE_CANNOT_FIND_MODULE_RE.search(message)
    if not match:
        return ""
    raw_path = str(match.group("path") or "").strip().replace("\\", "/")
    normalized_path = raw_path.removeprefix("./")
    for segment in ("dist/", "build/", "out/"):
        if normalized_path.startswith(segment):
            return normalized_path
        marker = f"/{segment}"
        marker_index = normalized_path.rfind(marker)
        if marker_index >= 0:
            return normalized_path[marker_index + 1 :]
    return ""


def _script_name_from_npm_invocation(normalized_text: str) -> str:
    for script_name in ("start", "serve", "dev", "preview", "build", "test", "verify"):
        if f"npm run {script_name}" in normalized_text:
            return script_name
    if "npm start" in normalized_text:
        return "start"
    return ""


def _javascript_module_error_issue(
    *,
    text: str,
    message: str,
    match: re.Match[str],
    line: int | None,
    column: int | None,
) -> ArtifactQualityIssue:
    """Project old JavaScript module-loader output into a typed issue row."""

    module_message = str(match.group("message") or message).strip()
    return ArtifactQualityIssue(
        code="javascript_module_error",
        message=module_message,
        path=_artifact_quality_issue_path(message),
        source="runtime_smoke",
        line=line,
        column=column,
        metadata=_javascript_module_error_metadata(text, module_message),
    )


def _artifact_quality_issue_from_error(error: str) -> ArtifactQualityIssue:
    text = str(error or "").strip()
    message = text
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    line, column = _artifact_quality_issue_location(message)
    javascript_module_error = _ARTIFACT_QUALITY_JAVASCRIPT_MODULE_ERROR_RE.search(message)
    if javascript_module_error:
        return _javascript_module_error_issue(
            text=text,
            message=message,
            match=javascript_module_error,
            line=line,
            column=column,
        )
    code = _legacy_artifact_quality_issue_code_from_message(message)
    path = "package.json" if code == "npm_manifest_invalid" else _artifact_quality_issue_path(message)
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=path,
        line=line,
        column=column,
        metadata=_artifact_quality_issue_metadata(text, message, code),
    )


def _artifact_quality_issue_from_mapping(payload: Mapping[str, Any]) -> ArtifactQualityIssue | None:
    code = str(payload.get("code") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not code and not message:
        return None
    metadata_raw = payload.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    for key, value in payload.items():
        if key in {"code", "message", "path", "severity", "source", "line", "column", "metadata"}:
            continue
        if key not in metadata:
            metadata[str(key)] = value
    path_raw = payload.get("path")
    path = str(path_raw).strip().replace("\\", "/") if path_raw is not None else None
    source = str(payload.get("source") or "artifact_quality").strip() or "artifact_quality"
    return ArtifactQualityIssue(
        code=code
        or _artifact_quality_issue_code_from_typed_metadata(metadata, source=source)
        or _legacy_artifact_quality_issue_code_from_message(message),
        message=message or code,
        path=path or None,
        severity=str(payload.get("severity") or "error").strip() or "error",
        source=source,
        line=_artifact_quality_optional_int(payload.get("line")),
        column=_artifact_quality_optional_int(payload.get("column")),
        metadata=metadata,
    )


def _artifact_quality_issue_from_value(value: Any) -> ArtifactQualityIssue | None:
    if isinstance(value, ArtifactQualityIssue):
        return value
    if isinstance(value, Mapping):
        return _artifact_quality_issue_from_mapping(value)
    text = str(value or "").strip()
    if not text:
        return None
    return _artifact_quality_issue_from_error(text)


def _artifact_quality_issues_from_errors(errors: Iterable[Any]) -> tuple[ArtifactQualityIssue, ...]:
    return tuple(issue for value in errors if (issue := _artifact_quality_issue_from_value(value)) is not None)


def _artifact_quality_issue_from_cross_artifact_issue(
    issue: CrossArtifactConsistencyIssue,
) -> ArtifactQualityIssue:
    """Project cross-file interface evidence without reparsing its message."""

    raw_message = issue.to_error_message()
    return ArtifactQualityIssue(
        code=issue.code,
        message=issue.message,
        path=issue.importer_path or issue.owner_path or None,
        severity=issue.severity,
        source="cross_artifact_consistency",
        metadata={
            "raw": raw_message,
            "importer_path": issue.importer_path,
            "owner_path": issue.owner_path,
            "symbol": issue.symbol,
            "details": dict(issue.details),
        },
    )


def artifact_quality_issues_from_errors(errors: Iterable[Any]) -> tuple[dict[str, Any], ...]:
    """Project artifact-quality findings into typed issue payloads."""

    return tuple(issue.to_dict() for issue in _artifact_quality_issues_from_errors(errors))


def artifact_quality_issues_for_errors(
    errors: Iterable[Any],
    issue_payloads: Iterable[Any],
) -> tuple[dict[str, Any], ...]:
    """Return issue payloads matching a filtered artifact-quality error list.

    Scanners can emit both display errors and structured issues. Downstream
    gates often filter the display errors by task scope, then need the matching
    typed issues without reparsing message prose in the adapter layer. This
    helper keeps that matching and residual projection inside KernelOne
    artifact quality.

    Complexity:
        O(e + i) average time for ``e`` errors and ``i`` issue payloads,
        excluding the small tuple keys built for each row; O(e + i) memory.
    """

    error_rows = [str(error or "").strip() for error in errors if str(error or "").strip()]
    allowed_raw = set(error_rows)
    allowed_structural_keys = {key for error in error_rows if (key := artifact_quality_issue_structural_key(error))}
    merged: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, ...]] = set()
    seen_raw: set[str] = set()
    seen_structural_keys: set[tuple[str, ...]] = set()

    for payload in issue_payloads:
        issue = _artifact_quality_issue_from_value(payload)
        if issue is None:
            continue
        issue_payload = dict(payload) if isinstance(payload, Mapping) else issue.to_dict()
        raw = artifact_quality_issue_raw(issue_payload)
        key = artifact_quality_issue_key(issue_payload)
        structural_key = artifact_quality_issue_structural_key(issue_payload)
        if key in seen_keys:
            continue
        if raw not in allowed_raw and (not structural_key or structural_key not in allowed_structural_keys):
            continue
        merged.append(issue_payload)
        seen_keys.add(key)
        if raw:
            seen_raw.add(raw)
        if structural_key:
            seen_structural_keys.add(structural_key)

    residual_errors: list[str] = []
    for raw in error_rows:
        if raw in seen_raw:
            continue
        parsed_structural_key = artifact_quality_issue_structural_key(raw)
        if parsed_structural_key and parsed_structural_key in seen_structural_keys:
            continue
        residual_errors.append(raw)
    for residual_issue in artifact_quality_issues_from_errors(residual_errors):
        raw = artifact_quality_issue_raw(residual_issue)
        key = artifact_quality_issue_key(residual_issue)
        if key in seen_keys or (raw and raw in seen_raw):
            continue
        merged.append(dict(residual_issue))
        seen_keys.add(key)
        if raw:
            seen_raw.add(raw)
    return tuple(merged)


def artifact_quality_issue_raw(value: Any) -> str:
    """Return the canonical raw diagnostic text for an artifact-quality issue."""

    issue = _artifact_quality_issue_from_value(value)
    if issue is None:
        return ""
    metadata = issue.metadata
    if isinstance(metadata, Mapping):
        raw = str(metadata.get("raw") or "").strip()
        if raw:
            return raw
    return str(issue.message or "").strip()


def artifact_quality_issue_key(value: Any) -> tuple[str, ...]:
    """Return the canonical identity key for artifact-quality issue de-duplication."""

    issue = _artifact_quality_issue_from_value(value)
    if issue is None:
        return ("legacy_raw", "")
    code = str(issue.code or "").strip()
    path = str(issue.path or "").strip().replace("\\", "/")
    line = str(issue.line or "").strip() if issue.line is not None else ""
    column = str(issue.column or "").strip() if issue.column is not None else ""
    message = str(issue.message or "").strip()
    if code or path or line or column:
        return ("structured", code, path, line, column, message)
    raw = artifact_quality_issue_raw(issue)
    return ("legacy_raw", raw or message)


def artifact_quality_issue_structural_key(value: Any) -> tuple[str, ...]:
    """Return a message-independent structured key for issue matching.

    This key is intentionally coarser than :func:`artifact_quality_issue_key`.
    It lets downstream gates match a typed issue to its source diagnostic without
    reparsing the diagnostic message, while still requiring code and path facts.

    Complexity:
        O(1) time and memory for one issue payload.
    """

    issue = _artifact_quality_issue_from_value(value)
    if issue is None:
        return ()
    code = str(issue.code or "").strip()
    path = str(issue.path or "").strip().replace("\\", "/")
    if not code or not path:
        return ()
    line = str(issue.line or "").strip() if issue.line is not None else ""
    column = str(issue.column or "").strip() if issue.column is not None else ""
    return code, path, line, column


def _artifact_quality_evidence(
    *,
    errors: Iterable[str] = (),
    issues: Iterable[Any] = (),
    scanned_relative_paths: Iterable[str] = (),
    cross_artifact_issues: Iterable[CrossArtifactConsistencyIssue] = (),
    cross_artifact_repair_plans: Iterable[CrossArtifactRepairPlan] = (),
    contract_amendment_request: ContractAmendmentRequest | None = None,
) -> ArtifactQualityEvidence:
    deduped_errors = tuple(dict.fromkeys(str(error).strip() for error in errors if str(error or "").strip()))
    direct_issues = _artifact_quality_issues_from_errors(issues)
    deduped_cross_artifact_issues = tuple(cross_artifact_issues)
    cross_artifact_error_messages = {
        issue.to_error_message() for issue in deduped_cross_artifact_issues if not issue.code.startswith("contract_")
    }
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in direct_issues}
    residual_errors = tuple(
        error
        for error in deduped_errors
        if str(error or "").strip() not in (*cross_artifact_error_messages, *direct_issue_messages)
    )
    string_projected_issues = _artifact_quality_issues_from_errors(residual_errors)
    projected_cross_artifact_issues = tuple(
        _artifact_quality_issue_from_cross_artifact_issue(issue)
        for issue in deduped_cross_artifact_issues
        if not issue.code.startswith("contract_")
    )
    return ArtifactQualityEvidence(
        errors=deduped_errors,
        issues=(*direct_issues, *string_projected_issues, *projected_cross_artifact_issues),
        scanned_relative_paths=tuple(scanned_relative_paths),
        cross_artifact_issues=deduped_cross_artifact_issues,
        cross_artifact_repair_plans=tuple(cross_artifact_repair_plans),
        contract_amendment_request=contract_amendment_request,
    )


def scan_workspace_artifact_quality(
    workspace_full: str,
    *,
    relative_paths: Iterable[str] | None = None,
) -> list[str]:
    """Reject known worthless generated artifacts.

    When ``relative_paths`` is provided, only those workspace-relative files are
    scanned. This lets Director validate the files it just changed without
    failing unrelated seed files that later tasks are expected to repair. QA
    calls this without ``relative_paths`` to scan the complete final workspace.
    """

    return list(scan_workspace_artifact_quality_evidence(workspace_full, relative_paths=relative_paths).errors)


def scan_workspace_artifact_quality_evidence(
    workspace_full: str,
    *,
    relative_paths: Iterable[str] | None = None,
    interface_contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None = None,
    task_id: str = "",
) -> ArtifactQualityEvidence:
    """Scan artifacts and return structured evidence without changing old callers."""

    try:
        root_full = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        message = "Artifact quality scan failed: workspace path cannot be resolved"
        return _artifact_quality_evidence(
            errors=(message,),
            issues=(
                ArtifactQualityIssue(
                    code="workspace_path_unresolved",
                    message=message,
                    source="artifact_quality_scanner",
                ),
            ),
        )
    if not root_full.exists() or not root_full.is_dir():
        message = "Artifact quality scan failed: workspace path does not exist"
        return _artifact_quality_evidence(
            errors=(message,),
            issues=(
                ArtifactQualityIssue(
                    code="workspace_path_missing",
                    message=message,
                    source="artifact_quality_scanner",
                    metadata={
                        "raw": message,
                        "diagnostic_kind": "workspace_path_missing",
                    },
                ),
            ),
        )

    errors: list[str] = []
    typed_issues: list[Any] = []
    scanned_relative_paths: list[str] = []
    cross_artifact_issues: tuple[CrossArtifactConsistencyIssue, ...] = ()
    try:
        interface_contract = interface_contract or _declared_interface_contract(
            root_full=root_full,
            relative_paths=relative_paths,
            task_id=task_id,
        )
        paths = (
            _iter_target_files(root_full, relative_paths)
            if relative_paths is not None
            else _iter_workspace_source_files(root_full)
        )
        for full_path in paths:
            if len(errors) >= 50:
                return _artifact_quality_evidence(
                    errors=errors,
                    issues=typed_issues,
                    scanned_relative_paths=tuple(scanned_relative_paths),
                )
            relative_path = full_path.relative_to(root_full).as_posix()
            scanned_relative_paths.append(relative_path)
            file_evidence = _scan_file_evidence(root_full, full_path, relative_path)
            errors.extend(file_evidence.errors)
            typed_issues.extend(file_evidence.issues)
        if len(errors) < 50:
            typecheck_evidence = _scan_typescript_project_typecheck_evidence(root_full, scanned_relative_paths)
            errors.extend(typecheck_evidence.errors)
            typed_issues.extend(typecheck_evidence.issues)
        if len(errors) < 50:
            cross_artifact_issues = tuple(
                scan_cross_artifact_consistency(
                    root_full,
                    relative_paths=scanned_relative_paths if relative_paths is not None else None,
                    contract=interface_contract,
                )
            )
            errors.extend(
                issue.to_error_message() for issue in cross_artifact_issues if not issue.code.startswith("contract_")
            )
        if len(errors) < 50:
            declared_interface_issues = _scan_declared_interface_ledger_issues(
                root_full,
                scanned_relative_paths if relative_paths is not None else None,
            )
            typed_issues.extend(declared_interface_issues)
            errors.extend(
                issue["metadata"]["raw"]
                for issue in declared_interface_issues
                if isinstance(issue.get("metadata"), Mapping) and str(issue["metadata"].get("raw") or "").strip()
            )
    except (OSError, RuntimeError, ValueError) as exc:
        message = f"Artifact quality scan failed: {exc}"
        return _artifact_quality_evidence(
            errors=(message,),
            issues=(_artifact_quality_scan_failure_issue(message, exc=exc),),
        )
    return _artifact_quality_evidence(
        errors=errors,
        issues=typed_issues,
        scanned_relative_paths=scanned_relative_paths,
        cross_artifact_issues=cross_artifact_issues,
        cross_artifact_repair_plans=plan_cross_artifact_repairs(cross_artifact_issues),
        contract_amendment_request=build_contract_amendment_request(
            task_id=_artifact_quality_task_id(task_id=task_id, interface_contract=interface_contract),
            issues=cross_artifact_issues,
        ),
    )


def _declared_interface_contract(
    *,
    root_full: Path,
    relative_paths: Iterable[str] | None,
    task_id: str,
) -> CrossArtifactInterfaceContract | None:
    declared: dict[str, dict[str, Any]] = {}
    target_files = list(relative_paths) if relative_paths is not None else None
    for cache_root in ("", root_full.as_posix()):
        try:
            entries = (
                read_declared_interfaces(root_full.as_posix(), cache_root, target_files)
                if target_files is not None
                else read_all_declared_interfaces(root_full.as_posix(), cache_root)
            )
        except (OSError, RuntimeError, ValueError):
            continue
        for target, entry in entries.items():
            current = declared.setdefault(target, {"identifiers": [], "public_symbols": [], "signatures": []})
            current["identifiers"] = _merge_quality_names(current.get("identifiers"), entry.get("identifiers"))
            current["public_symbols"] = _merge_quality_names(current.get("public_symbols"), entry.get("public_symbols"))
            current["signatures"] = _merge_quality_names(current.get("signatures"), entry.get("signatures"))
    if not declared:
        return None
    interfaces = []
    for owner_path, entry in sorted(declared.items()):
        code_symbols = _quality_string_list(entry.get("public_symbols")) or [
            identifier
            for identifier in _quality_string_list(entry.get("identifiers"))
            if _looks_like_code_symbol(identifier)
        ]
        for identifier in code_symbols:
            interfaces.append(
                {
                    "domain": "declared_interface_ledger",
                    "owner_path": owner_path,
                    "name": identifier,
                    "kind": "code_symbol",
                }
            )
    if not interfaces:
        return None
    return CrossArtifactInterfaceContract.from_mapping(
        {
            "task_id": str(task_id or "").strip(),
            "language": "",
            "interfaces": interfaces,
        }
    )


def _quality_string_list(value: Any) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _merge_quality_names(left: Any, right: Any) -> list[str]:
    merged: list[str] = []
    for item in [*_quality_string_list(left), *_quality_string_list(right)]:
        if item not in merged:
            merged.append(item)
    return merged


def _looks_like_code_symbol(value: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(value or "")) is not None


def _artifact_quality_task_id(
    *,
    task_id: str,
    interface_contract: CrossArtifactInterfaceContract | Mapping[str, Any] | None,
) -> str:
    explicit = str(task_id or "").strip()
    if explicit:
        return explicit
    if isinstance(interface_contract, CrossArtifactInterfaceContract):
        return interface_contract.task_id
    if isinstance(interface_contract, Mapping):
        return str(interface_contract.get("task_id") or "").strip()
    return ""


def _scan_declared_interface_ledger_issues(
    root_full: Path,
    relative_paths: Iterable[str] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    target_files = list(relative_paths) if relative_paths is not None else None
    for cache_root in ("", root_full.as_posix()):
        try:
            issues.extend(
                issue.to_artifact_quality_issue()
                for issue in validate_declared_interface_issues_against_snapshot(
                    root_full.as_posix(),
                    cache_root,
                    target_files,
                )
            )
        except (OSError, RuntimeError, ValueError):
            continue
    deduped: dict[str, dict[str, Any]] = {}
    for issue in issues:
        metadata_raw = issue.get("metadata")
        metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
        raw = str(metadata.get("raw") or issue.get("message") or "").strip()
        if raw:
            deduped.setdefault(raw, issue)
    return list(deduped.values())


def _iter_workspace_source_files(root_full: Path) -> Iterable[Path]:
    for current_root, dir_names, file_names in os.walk(root_full):
        dir_names[:] = [name for name in dir_names if name not in _ARTIFACT_QUALITY_SKIP_DIRS]
        current = Path(current_root)
        for name in file_names:
            full_path = current / name
            if _is_source_artifact(full_path):
                yield full_path


def _iter_target_files(root_full: Path, relative_paths: Iterable[str] | None) -> Iterable[Path]:
    seen: set[Path] = set()
    for raw_path in relative_paths or ():
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized:
            continue
        full_path = (root_full / normalized).resolve()
        try:
            full_path.relative_to(root_full)
        except ValueError:
            continue
        if full_path in seen:
            continue
        if any(part in _ARTIFACT_QUALITY_SKIP_DIRS for part in full_path.relative_to(root_full).parts):
            continue
        if full_path.is_dir():
            for nested_path in _iter_workspace_source_files(full_path):
                nested_resolved = nested_path.resolve()
                try:
                    nested_relative_parts = nested_resolved.relative_to(root_full).parts
                except ValueError:
                    continue
                if (
                    nested_resolved in seen
                    or any(part in _ARTIFACT_QUALITY_SKIP_DIRS for part in nested_relative_parts)
                    or not _is_source_artifact(nested_resolved)
                ):
                    continue
                seen.add(nested_resolved)
                yield nested_resolved
            continue
        if not full_path.is_file() or not _is_source_artifact(full_path):
            continue
        seen.add(full_path)
        yield full_path


def _is_source_artifact(path: Path) -> bool:
    return path.name.lower() == "package.json" or path.suffix.lower() in _ARTIFACT_QUALITY_SOURCE_EXTS


def _tool_receipt_contamination_error(relative_path: str, text: str) -> str:
    lowered = str(text or "").lower()
    if not any(token in lowered for token in _TOOL_RECEIPT_CONTAMINATION_TOKENS):
        return ""
    return (
        "Artifact quality scan failed: tool execution receipt contamination in "
        f"{relative_path}; file contains a Polaris tool failure receipt instead of source code. "
        "Rewrite this artifact with real UTF-8 project code and do not copy tool error text."
    )


def _file_artifact_quality_issue(
    error: str,
    relative_path: str,
    *,
    code: str,
    source: str = "file_artifact_scanner",
    metadata: Mapping[str, Any] | None = None,
) -> ArtifactQualityIssue:
    normalized_error = str(error or "").strip()
    message = normalized_error
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    issue_metadata: dict[str, Any] = {
        "raw": normalized_error,
        "artifact_path": relative_path,
    }
    if isinstance(metadata, Mapping):
        for key, value in metadata.items():
            if value is None:
                continue
            issue_metadata[str(key)] = value
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=relative_path,
        source=source,
        metadata=issue_metadata,
    )


def _source_narration_contamination_error(relative_path: str, text: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix not in _ARTIFACT_QUALITY_SOURCE_EXTS:
        return ""
    stripped = str(text or "").lstrip()
    if not stripped:
        return ""
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return ""
    if not _SOURCE_NARRATION_LEAK_RE.search(stripped[:500]):
        return ""
    return (
        "Artifact quality scan failed: source narration contamination in "
        f"{relative_path}; file starts with assistant prose instead of project source code. "
        "Rewrite this artifact with real UTF-8 source only."
    )


def _scan_file_evidence(root_full: Path, full_path: Path, relative_path: str) -> _FileArtifactQualityEvidence:
    """Return legacy and typed artifact-quality findings for one file."""

    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except (OSError, RuntimeError, ValueError):
        return _FileArtifactQualityEvidence()

    receipt_error = _tool_receipt_contamination_error(relative_path, text)
    if receipt_error:
        return _FileArtifactQualityEvidence(
            errors=(receipt_error,),
            issues=(
                _file_artifact_quality_issue(
                    receipt_error,
                    relative_path,
                    code="tool_receipt_contamination",
                ),
            ),
        )

    narration_error = _source_narration_contamination_error(relative_path, text)
    if narration_error:
        return _FileArtifactQualityEvidence(
            errors=(narration_error,),
            issues=(
                _file_artifact_quality_issue(
                    narration_error,
                    relative_path,
                    code="source_narration_contamination",
                ),
            ),
        )

    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []

    def append_file_issue(
        error: str,
        *,
        code: str,
        source: str = "file_artifact_scanner",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_error = str(error or "").strip()
        if not normalized_error:
            return
        errors.append(normalized_error)
        issues.append(
            _file_artifact_quality_issue(
                normalized_error,
                relative_path,
                code=code,
                source=source,
                metadata=metadata,
            )
        )

    syntax = check_source_file_syntax(str(full_path))
    if syntax is not None and syntax.get("ok") is False:
        syntax_detail = str(syntax.get("error"))[:200]
        syntax_error = f"Artifact quality scan failed: syntax error in {relative_path}: {syntax_detail}"
        errors.append(syntax_error)
        issues.append(
            ArtifactQualityIssue(
                code="syntax_error",
                message=f"syntax error in {relative_path}: {syntax_detail}",
                path=relative_path,
                source="source_syntax_checker",
                metadata={
                    "raw": syntax_error,
                    "syntax_error": syntax_detail,
                    "diagnostic_kind": "syntax_error",
                },
            )
        )
    if os.path.basename(relative_path).lower() == "package.json":
        manifest_evidence = _scan_package_manifest_evidence(root_full, text, relative_path)
        errors.extend(manifest_evidence.errors)
        issues.extend(manifest_evidence.issues)
    typescript_import_evidence = _scan_typescript_import_evidence(root_full, full_path, text, relative_path)
    errors.extend(typescript_import_evidence.errors)
    issues.extend(typescript_import_evidence.issues)
    typescript_red_flag_evidence = _scan_typescript_syntax_red_flag_evidence(root_full, full_path, text, relative_path)
    errors.extend(typescript_red_flag_evidence.errors)
    issues.extend(typescript_red_flag_evidence.issues)
    html_module_script_evidence = _scan_html_typescript_module_script_evidence(full_path, text, relative_path)
    errors.extend(html_module_script_evidence.errors)
    issues.extend(html_module_script_evidence.issues)
    for marker in _DETERMINISTIC_SCAFFOLD_MARKERS:
        if marker in text:
            append_file_issue(
                f"Artifact quality scan failed: deterministic scaffold marker {marker!r} in {relative_path}",
                code="deterministic_scaffold_marker",
                metadata={
                    "marker_kind": "deterministic_scaffold",
                    "marker_value": marker,
                },
            )
            break
    helper_count = len(_NUMERIC_HELPER_FILLER_RE.findall(text))
    if helper_count >= 5:
        append_file_issue(
            f"Artifact quality scan failed: repeated numeric helper filler in {relative_path} (count={helper_count})",
            code="repeated_numeric_helper_filler",
            metadata={"helper_count": helper_count},
        )
    if helper_count >= 3 and _GENERIC_STORE_RECORD_RE.search(text) and _GENERIC_STORE_MAP_RE.search(text):
        append_file_issue(
            f"Artifact quality scan failed: generic payload/index store scaffold in {relative_path}",
            code="generic_payload_index_store_scaffold",
            metadata={
                "helper_count": helper_count,
                "scaffold_kind": "generic_payload_index_store",
            },
        )
    patch_residue_match = _PATCH_RESIDUE_RE.search(text)
    if patch_residue_match:
        append_file_issue(
            f"Artifact quality scan failed: patch residue marker in {relative_path}",
            code="patch_residue_marker",
            metadata={
                "marker_kind": "patch_residue",
                "marker_value": patch_residue_match.group(0).strip(),
            },
        )
    if _is_test_like_artifact_path(relative_path):
        trivial_count = len(_TRIVIAL_ARITHMETIC_EXPECT_RE.findall(text))
        if trivial_count >= 3:
            append_file_issue(
                "Artifact quality scan failed: repeated trivial arithmetic placeholder "
                f"tests in {relative_path} (count={trivial_count})",
                code="repeated_trivial_arithmetic_tests",
                metadata={
                    "assertion_count": trivial_count,
                },
            )
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in issues}
    residual_errors = tuple(error for error in errors if str(error or "").strip() not in direct_issue_messages)
    string_projected_issues = _artifact_quality_issues_from_errors(residual_errors)
    return _FileArtifactQualityEvidence(
        errors=tuple(errors),
        issues=(*issues, *string_projected_issues),
    )


def _typescript_syntax_red_flag_issue(
    *,
    error: str,
    code: str,
    relative_path: str,
    metadata: Mapping[str, Any] | None = None,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=relative_path,
        source="typescript_syntax_red_flag_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "path": relative_path,
            **dict(metadata or {}),
        },
    )


def _scan_typescript_syntax_red_flag_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return TypeScript syntax red flags as strings and direct typed issues."""

    suffix = full_path.suffix.lower()
    if suffix not in _TS_JS_SOURCE_EXTS:
        return _FileArtifactQualityEvidence()
    if _typescript_line_comment_contains_escaped_newline_code(text):
        error = (
            f"Artifact quality scan failed: TypeScript escaped newline in line comment before code in {relative_path}"
        )
        return _FileArtifactQualityEvidence(
            errors=(error,),
            issues=(
                _typescript_syntax_red_flag_issue(
                    error=error,
                    code="typescript_escaped_newline_line_comment",
                    relative_path=relative_path,
                    metadata={
                        "diagnostic_kind": "typescript_escaped_newline_line_comment",
                    },
                ),
            ),
        )
    if suffix not in _TS_SOURCE_EXTS:
        return _FileArtifactQualityEvidence()
    collision_name = _typescript_zod_inferred_type_class_collision_name(text)
    if collision_name:
        error = (
            "Artifact quality scan failed: TypeScript zod inferred type collides "
            f"with class {collision_name} in {relative_path}"
        )
        return _FileArtifactQualityEvidence(
            errors=(error,),
            issues=(
                _typescript_syntax_red_flag_issue(
                    error=error,
                    code="typescript_zod_type_class_collision",
                    relative_path=relative_path,
                    metadata={
                        "collision_name": collision_name,
                        "diagnostic_kind": "typescript_zod_type_class_collision",
                    },
                ),
            ),
        )
    for match in _TS_RETURN_OBJECT_BLOCK_RE.finditer(text):
        if _TS_OBJECT_PROPERTY_SEMICOLON_RE.search(match.group("body")):
            error = (
                "Artifact quality scan failed: TypeScript return object contains "
                f"semicolon-terminated property in {relative_path}"
            )
            return _FileArtifactQualityEvidence(
                errors=(error,),
                issues=(
                    _typescript_syntax_red_flag_issue(
                        error=error,
                        code="typescript_return_object_semicolon_property",
                        relative_path=relative_path,
                        metadata={
                            "diagnostic_kind": "typescript_return_object_semicolon_property",
                        },
                    ),
                ),
            )
    type_export_error = _typescript_isolated_modules_type_reexport_error(root_full, text)
    if type_export_error:
        error = (
            "Artifact quality scan failed: TypeScript isolatedModules requires "
            f"`export type` for {type_export_error} in {relative_path}"
        )
        return _FileArtifactQualityEvidence(
            errors=(error,),
            issues=(
                _typescript_syntax_red_flag_issue(
                    error=error,
                    code="typescript_isolated_modules_type_reexport",
                    relative_path=relative_path,
                    metadata={
                        "export_name": type_export_error,
                        "diagnostic_kind": "typescript_isolated_modules_type_reexport",
                    },
                ),
            ),
        )
    return _FileArtifactQualityEvidence()


def _html_module_script_quality_issue(error: str, relative_path: str, *, src: str) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="html_module_script_typescript_source",
        message=message,
        path=relative_path,
        source="html_module_script_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "html_path": relative_path,
            "script_src": src,
            "diagnostic_kind": "html_module_script_typescript_source",
        },
    )


def _scan_html_typescript_module_script_evidence(
    full_path: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return HTML module-script findings as strings and direct typed issues."""

    if full_path.suffix.lower() not in {".html", ".htm"}:
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for match in _HTML_TYPESCRIPT_MODULE_SCRIPT_RE.finditer(text):
        src = str(match.group("src") or "").strip()
        if src:
            error = (
                "Artifact quality scan failed: HTML module script references TypeScript source "
                f"{src!r} in {relative_path}; static entrypoints must load JavaScript"
            )
            errors.append(error)
            issues.append(_html_module_script_quality_issue(error, relative_path, src=src))
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _typescript_isolated_modules_type_reexport_error(root_full: Path, text: str) -> str:
    if not _typescript_project_uses_isolated_modules(root_full):
        return ""
    type_names = {str(match.group("name") or "") for match in _TS_TYPE_DECL_RE.finditer(text)}
    if not type_names:
        return ""
    for match in _TS_EXPORT_CLAUSE_RE.finditer(text):
        inner = str(match.group("inner") or "")
        for raw in inner.split(","):
            token = raw.strip()
            if not token or token.startswith("type "):
                continue
            exported_name = re.split(r"\s+as\s+", token)[0].strip()
            if exported_name in type_names:
                return exported_name
    return ""


def _typescript_project_uses_isolated_modules(root_full: Path) -> bool:
    try:
        payload = json.loads((root_full / "tsconfig.json").read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    compiler_options = payload.get("compilerOptions")
    return isinstance(compiler_options, dict) and compiler_options.get("isolatedModules") is True


def _typescript_zod_inferred_type_class_collision_name(text: str) -> str:
    for match in _TS_ZOD_INFERRED_TYPE_RE.finditer(str(text or "")):
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        class_re = re.compile(rf"(?:^|\n)\s*(?:export\s+)?class\s+{re.escape(name)}\b", re.MULTILINE)
        if class_re.search(text):
            return name
    return ""


def _typescript_line_comment_contains_escaped_newline_code(text: str) -> bool:
    for raw_line in str(text or "").splitlines():
        if "//" not in raw_line or "\\n" not in raw_line:
            continue
        comment_index = raw_line.find("//")
        if comment_index < 0:
            continue
        if _TS_LINE_COMMENT_ESCAPED_NEWLINE_CODE_RE.search(raw_line[comment_index:]):
            return True
    return False


def _package_manifest_quality_issue(
    error: str,
    relative_path: str,
    metadata_override: Mapping[str, Any] | None = None,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    metadata = _artifact_quality_issue_metadata(str(error or "").strip(), message, "npm_manifest_invalid")
    if isinstance(metadata_override, Mapping):
        metadata.update({str(key): value for key, value in metadata_override.items() if value})
    metadata["manifest_path"] = relative_path
    return ArtifactQualityIssue(
        code="npm_manifest_invalid",
        message=message,
        path=relative_path,
        source="package_manifest_scanner",
        metadata=metadata,
    )


def _append_package_manifest_issue(
    errors: list[str],
    issues: list[ArtifactQualityIssue],
    error: str,
    relative_path: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    normalized_error = str(error or "").strip()
    if not normalized_error:
        return
    errors.append(normalized_error)
    issues.append(_package_manifest_quality_issue(normalized_error, relative_path, metadata))


def _package_script_gate_artifact_error(issue: PackageScriptIssue, relative_path: str) -> str:
    if issue.code == "npm_placeholder_script" and issue.script_name and issue.command:
        return (
            "Artifact quality scan failed: npm package manifest script "
            f"{issue.script_name!r} is a placeholder command: {issue.command} in {relative_path}"
        )
    if issue.code == "npm_script_empty" and issue.script_name:
        return f"Artifact quality scan failed: npm package manifest script {issue.script_name!r} is empty in {relative_path}"
    return f"Artifact quality scan failed: {issue.message} in {relative_path}"


def _package_script_gate_artifact_issue(
    issue: PackageScriptIssue,
    relative_path: str,
    display_error: str,
) -> ArtifactQualityIssue:
    metadata = {
        "raw": display_error,
        "manifest_path": relative_path,
        **dict(issue.metadata or {}),
        "script_issue_source": "package_scripts",
        "package_script_issue_code": issue.code,
    }
    for key, value in issue.to_dict().items():
        if key in {"code", "message", "path", "severity", "source", "metadata"} or not value:
            continue
        metadata[str(key)] = value
    message = display_error
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="npm_manifest_invalid",
        message=message,
        path=relative_path,
        source="package_manifest_scanner",
        metadata=metadata,
    )


def _append_package_script_gate_issue(
    errors: list[str],
    issues: list[ArtifactQualityIssue],
    issue: PackageScriptIssue,
    relative_path: str,
) -> None:
    display_error = _package_script_gate_artifact_error(issue, relative_path)
    errors.append(display_error)
    issues.append(_package_script_gate_artifact_issue(issue, relative_path, display_error))


def _package_script_gate_issues_for_code(
    root_full: Path,
    *codes: str,
) -> tuple[PackageScriptIssue, ...]:
    allowed_codes = {str(code or "").strip() for code in codes if str(code or "").strip()}
    if not allowed_codes:
        return ()
    result = check_package_scripts(str(root_full))
    return tuple(issue for issue in result.issues if issue.code in allowed_codes)


def _first_package_script_gate_issue_for_script(
    issues: Iterable[PackageScriptIssue],
    *,
    script_name: str,
    codes: set[str],
) -> PackageScriptIssue | None:
    normalized_script = str(script_name or "").strip()
    for issue in issues:
        if issue.code not in codes:
            continue
        if str(issue.script_name or "").strip() == normalized_script:
            return issue
    return None


def _package_manifest_evidence_from_errors(
    errors: list[str],
    relative_path: str,
    direct_issues: list[ArtifactQualityIssue] | None = None,
) -> _FileArtifactQualityEvidence:
    issues = list(direct_issues or [])
    direct_issue_messages = {str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in issues}
    issues.extend(
        _package_manifest_quality_issue(error, relative_path)
        for error in errors
        if str(error or "").strip() not in direct_issue_messages
    )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _scan_package_manifest_evidence(root_full: Path, text: str, relative_path: str) -> _FileArtifactQualityEvidence:
    """Return package-manifest findings as legacy strings and direct typed issues."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _FileArtifactQualityEvidence()
    if not isinstance(payload, dict):
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        package_script_gate_issues = _package_script_gate_issues_for_code(
            root_full,
            "npm_script_cycle",
            "npm_placeholder_script",
            "npm_script_empty",
        )
        for issue in package_script_gate_issues:
            if issue.code != "npm_script_cycle":
                continue
            _append_package_script_gate_issue(
                errors,
                issues,
                issue,
                relative_path,
            )
        test_script = str(scripts.get("test") or "")
        lowered = test_script.lower()
        if "no test specified" in lowered or "no tests specified" in lowered:
            _append_package_manifest_issue(
                errors,
                issues,
                f"Artifact quality scan failed: npm default failing test script in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "default_failing_test_script",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        if _NPM_PLACEHOLDER_TEST_SCRIPT_RE.search(test_script):
            _append_package_manifest_issue(
                errors,
                issues,
                f"Artifact quality scan failed: npm placeholder test script in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "placeholder_test_script",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        if _NPM_MANIFEST_ONLY_TEST_SCRIPT_RE.search(test_script):
            _append_package_manifest_issue(
                errors,
                issues,
                f"Artifact quality scan failed: npm manifest-only test script in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "manifest_only_test_script",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        if (
            _NPM_TEST_RUNNER_SCRIPT_RE.search(test_script)
            and _workspace_has_node_source_files(root_full)
            and not _workspace_has_node_test_files(root_full)
        ):
            _append_package_manifest_issue(
                errors,
                issues,
                "Artifact quality scan failed: npm package manifest has test runner script "
                f"but no test/spec files exist in {relative_path}",
                relative_path,
                {
                    "script_name": "test",
                    "script_issue": "missing_node_test_files",
                    "script_issue_source": "package_manifest_scanner",
                },
            )
        for script_name, script_value in scripts.items():
            script_text = str(script_value or "")
            try:
                tokens = shlex.split(script_text, posix=(os.name != "nt"))
            except ValueError as exc:
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} has invalid shell syntax in {relative_path}: {exc}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "invalid_shell_syntax",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                continue
            placeholder_issue = _first_package_script_gate_issue_for_script(
                package_script_gate_issues,
                script_name=str(script_name),
                codes={"npm_placeholder_script", "npm_script_empty"},
            )
            if placeholder_issue is not None:
                _append_package_script_gate_issue(
                    errors,
                    issues,
                    placeholder_issue,
                    relative_path,
                )
                continue
            if _NPM_SCRIPT_FAILURE_SWALLOW_RE.search(script_text):
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} swallows command failures in {relative_path}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "swallows_command_failures",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                continue
            if _NPM_SCRIPT_SHELL_SUBSTITUTION_RE.search(script_text):
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} uses shell command substitution in {relative_path}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "shell_command_substitution",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                continue
            if _PYTHON_COMMAND_IN_NPM_SCRIPT_RE.search(script_text):
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest contains "
                    f"Python command in script {str(script_name)!r} in {relative_path}",
                    relative_path,
                    {
                        "script_name": str(script_name),
                        "script_issue": "python_command",
                        "script_issue_source": "package_manifest_scanner",
                    },
                )
                break
            node_eval_issue = _scan_npm_script_node_eval_syntax(tokens, str(script_name), relative_path)
            if node_eval_issue is not None:
                _append_package_manifest_issue(
                    errors,
                    issues,
                    node_eval_issue.display_error,
                    relative_path,
                    {
                        "script_name": node_eval_issue.script_name,
                        "script_issue": "invalid_node_eval_syntax",
                        "script_issue_source": "package_manifest_scanner",
                        "diagnostic_detail": node_eval_issue.diagnostic_detail,
                        "diagnostic_kind": "node_eval_syntax",
                    },
                )
                continue
            test_directory_evidence = _scan_npm_script_node_test_directory_target_evidence(
                root_full,
                tokens,
                str(script_name),
                relative_path,
            )
            errors.extend(test_directory_evidence.errors)
            issues.extend(test_directory_evidence.issues)
            entrypoint_evidence = _scan_npm_script_missing_local_entrypoint_evidence(
                root_full,
                script_text,
                str(script_name),
                relative_path,
            )
            errors.extend(entrypoint_evidence.errors)
            issues.extend(entrypoint_evidence.issues)
            config_evidence = _scan_npm_script_missing_local_config_evidence(
                root_full,
                tokens,
                str(script_name),
                relative_path,
            )
            errors.extend(config_evidence.errors)
            issues.extend(config_evidence.issues)
        if _package_manifest_requires_typescript(root_full, payload) and not _package_declares_dependency(
            payload, "typescript"
        ):
            _append_package_manifest_issue(
                errors,
                issues,
                "Artifact quality scan failed: TypeScript project requires 'typescript' "
                f"devDependency in {relative_path}",
                relative_path,
                {
                    "manifest_issue": "typescript_dependency_missing",
                    "manifest_issue_source": "package_manifest_scanner",
                    "package_name": "typescript",
                    "dependency_section": "devDependencies",
                },
            )
    main_entry = str(payload.get("main") or "").strip().replace("\\", "/").lower()
    if main_entry.endswith(".py"):
        _append_package_manifest_issue(
            errors,
            issues,
            f"Artifact quality scan failed: npm package manifest contains Python runtime entrypoint in {relative_path}",
            relative_path,
            {
                "manifest_issue": "python_runtime_entrypoint",
                "manifest_issue_source": "package_manifest_scanner",
                "entrypoint": main_entry,
            },
        )
    module_type_evidence = _scan_package_module_type_mismatch_evidence(root_full, payload, relative_path)
    errors.extend(module_type_evidence.errors)
    issues.extend(module_type_evidence.issues)
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        for package_name in section:
            normalized = str(package_name or "").strip().lower()
            if normalized in _PYTHON_PACKAGE_MANIFEST_DEPENDENCIES:
                _append_package_manifest_issue(
                    errors,
                    issues,
                    "Artifact quality scan failed: npm package manifest declares "
                    f"Python package dependency {package_name!r} in {relative_path}",
                    relative_path,
                    {
                        "manifest_issue": "python_package_dependency",
                        "manifest_issue_source": "package_manifest_scanner",
                        "package_name": str(package_name),
                        "dependency_section": section_name,
                    },
                )
                return _package_manifest_evidence_from_errors(errors, relative_path, issues)
    return _package_manifest_evidence_from_errors(errors, relative_path, issues)


@dataclass(frozen=True, slots=True)
class _NodeEvalSyntaxIssue:
    """Structured npm script `node --eval` syntax finding."""

    display_error: str
    diagnostic_detail: str
    script_name: str
    relative_path: str


def _scan_npm_script_node_eval_syntax(
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> _NodeEvalSyntaxIssue | None:
    for source in _iter_node_eval_sources(tokens):
        detail = _check_javascript_snippet_syntax(source)
        if detail:
            diagnostic_detail = detail[:200]
            return _NodeEvalSyntaxIssue(
                display_error=(
                    "Artifact quality scan failed: npm package manifest script "
                    f"{script_name!r} has invalid node eval syntax in {relative_path}: {diagnostic_detail}"
                ),
                diagnostic_detail=diagnostic_detail,
                script_name=script_name,
                relative_path=relative_path,
            )
    return None


def _iter_node_eval_sources(tokens: list[str]) -> Iterable[str]:
    """Yield JavaScript source snippets passed to ``node --eval`` / ``-e``.

    Scans one ``node`` invocation at a time, skipping safe Node options such as
    ``--no-warnings`` or ``--enable-source-maps`` that may appear between
    ``node`` and the eval flag. It fails closed on shell operators and on a
    positional script path / command, so snippets are never inferred from a
    later clause or from code meant to run from a file.
    """

    length = len(tokens)
    index = 0
    while index < length:
        if os.path.basename(str(tokens[index] or "").strip().lower()) not in {"node", "node.exe"}:
            index += 1
            continue
        index += 1
        while index < length:
            token = str(tokens[index] or "").strip()
            if token in _NPM_SCRIPT_SEPARATORS:
                break
            lowered = token.lower()
            if lowered in {"-e", "--eval"}:
                index += 1
                if index < length:
                    source = str(tokens[index] or "")
                    if source.strip():
                        yield source
                    index += 1
                continue
            if lowered.startswith("-e=") or lowered.startswith("--eval="):
                source = token.split("=", 1)[1]
                if source.strip():
                    yield source
                index += 1
                continue
            if lowered in _NPM_NODE_OPTION_VALUE_FLAGS:
                index += 2
                continue
            if lowered.startswith(("--loader=", "--require=", "--import=")):
                index += 1
                continue
            if lowered.startswith("-"):
                # Safe boolean option such as --no-warnings or --enable-source-maps.
                index += 1
                continue
            # Positional script path / command: stop scanning this node invocation.
            break


def _check_javascript_snippet_syntax(source: str) -> str:
    node = shutil.which("node")
    if not node:
        return ""
    input_source = source if source.endswith("\n") else f"{source}\n"
    try:
        proc = subprocess.run(
            [node, "--check", "-"],
            input=input_source,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"syntax check could not run: {exc}"
    if proc.returncode == 0:
        return ""
    return _compress_node_syntax_error(proc.stderr or proc.stdout, "[stdin]")


def _package_declares_dependency(payload: dict[str, Any], package_name: str) -> bool:
    target = str(package_name or "").strip()
    if not target:
        return False
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, dict) and target in {str(name).strip() for name in section}:
            return True
    return False


def _package_manifest_requires_typescript(root_full: Path, payload: dict[str, Any]) -> bool:
    if not (root_full / "tsconfig.json").is_file():
        return False
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        for script_value in scripts.values():
            if _NPM_SCRIPT_TSC_RE.search(str(script_value or "")):
                return True
    for relative_path in _iter_workspace_relative_files(root_full):
        if Path(relative_path).suffix.lower() in _TS_SOURCE_EXTS:
            return True
    return False


def _workspace_has_node_source_files(root_full: Path) -> bool:
    for relative_path in _iter_workspace_relative_files(root_full):
        if _is_test_like_artifact_path(relative_path):
            continue
        if Path(relative_path).suffix.lower() in _TS_JS_SOURCE_EXTS:
            return True
    return False


def _workspace_has_node_test_files(root_full: Path) -> bool:
    return any(
        _is_test_like_artifact_path(relative_path) for relative_path in _iter_workspace_relative_files(root_full)
    )


_NPM_SCRIPT_ENTRYPOINT_PATTERN_CHARS = frozenset("*?[]{}")


def _is_concrete_npm_script_entrypoint_path(value: str) -> bool:
    return not any(char in value for char in _NPM_SCRIPT_ENTRYPOINT_PATTERN_CHARS)


def _npm_script_missing_local_entrypoint_issue(
    error: str,
    relative_path: str,
    *,
    script_name: str,
    entrypoint: str,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="npm_script_missing_local_entrypoint",
        message=message,
        path=relative_path,
        source="npm_script_entrypoint_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "script_issue": "missing_local_entrypoint",
            "script_issue_source": "npm_script_entrypoint_scanner",
            "script_name": script_name,
            "entrypoint": entrypoint,
        },
    )


def _scan_npm_script_missing_local_entrypoint_evidence(
    root_full: Path,
    script_text: str,
    script_name: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return npm script missing-entrypoint findings as strings and typed issues."""

    if _NPM_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(script_text):
        return _FileArtifactQualityEvidence()
    try:
        tokens = shlex.split(script_text, posix=(os.name != "nt"))
    except ValueError:
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for index, token in enumerate(tokens[:-1]):
        command = token.strip().lower()
        if command not in _NPM_SCRIPT_ENTRYPOINT_COMMANDS:
            continue
        entrypoint = _npm_script_entrypoint_after_command(tokens, index)
        if not entrypoint:
            continue
        normalized = entrypoint.replace("\\", "/")
        if normalized.startswith(("/", "http://", "https://")) or ".." in normalized.split("/"):
            continue
        if not _is_concrete_npm_script_entrypoint_path(normalized):
            continue
        if Path(normalized).suffix.lower() not in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            continue
        if not (root_full / normalized).is_file():
            error = (
                "Artifact quality scan failed: npm package manifest script "
                f"{script_name!r} references missing local entrypoint {normalized!r} in {relative_path}"
            )
            errors.append(error)
            issues.append(
                _npm_script_missing_local_entrypoint_issue(
                    error,
                    relative_path,
                    script_name=script_name,
                    entrypoint=normalized,
                )
            )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _npm_script_node_test_directory_target_issue(
    error: str,
    relative_path: str,
    *,
    script_name: str,
    target_directory: str,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="npm_script_node_test_directory_target",
        message=message,
        path=relative_path,
        source="npm_script_test_target_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "script_name": script_name,
            "target_directory": target_directory,
            "script_issue": "node_test_directory_target",
            "script_issue_source": "npm_script_test_target_scanner",
            "diagnostic_kind": "npm_script_node_test_directory_target",
        },
    )


def _scan_npm_script_node_test_directory_target_evidence(
    root_full: Path,
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return npm script node-test directory target findings as strings and typed issues."""

    if str(script_name or "").strip().lower() != "test":
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for index, token in enumerate(tokens[:-1]):
        command = os.path.basename(str(token or "").strip().lower())
        if command not in {"node", "node.exe"}:
            continue
        if not any(_node_token_enables_test_runner(item) for item in tokens[index + 1 :]):
            continue
        entrypoint = _npm_script_entrypoint_after_command(tokens, index)
        normalized = entrypoint.replace("\\", "/").strip().strip("'\"")
        if not normalized or normalized.startswith(("/", "http://", "https://")) or ".." in normalized.split("/"):
            continue
        if Path(normalized).suffix:
            continue
        target_dir = root_full / normalized
        if not target_dir.is_dir():
            continue
        if not _directory_has_node_test_files(target_dir):
            continue
        error = (
            "Artifact quality scan failed: npm package manifest script "
            f"{script_name!r} references test directory {normalized!r} instead of concrete test files in "
            f"{relative_path}"
        )
        errors.append(error)
        issues.append(
            _npm_script_node_test_directory_target_issue(
                error,
                relative_path,
                script_name=script_name,
                target_directory=normalized,
            )
        )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _node_token_enables_test_runner(token: str) -> bool:
    normalized = str(token or "").strip().lower()
    return normalized == "--test" or normalized.startswith("--test=")


def _directory_has_node_test_files(directory: Path) -> bool:
    try:
        for path in directory.rglob("*"):
            if path.is_file() and _is_test_like_artifact_path(path.as_posix()):
                return True
    except OSError:
        return False
    return False


def _package_module_type_mismatch_issue(error: str, relative_path: str, *, source_path: str) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="package_module_type_commonjs_mismatch",
        message=message,
        path=relative_path,
        source="package_module_type_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "source_path": source_path,
            "declared_type": "module",
            "runtime_syntax": "commonjs",
            "diagnostic_kind": "package_module_type_commonjs_mismatch",
        },
    )


def _scan_package_module_type_mismatch_evidence(
    root_full: Path,
    payload: dict[str, Any],
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return package module-type mismatch findings as strings and direct typed issues."""

    if str(payload.get("type") or "").strip().lower() != "module":
        return _FileArtifactQualityEvidence()
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for candidate in _iter_workspace_relative_files(root_full):
        if Path(candidate).suffix.lower() not in {".js", ".jsx"}:
            continue
        full_path = root_full / candidate
        try:
            text = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _COMMONJS_RUNTIME_TOKEN_RE.search(text):
            error = (
                "Artifact quality scan failed: JavaScript source "
                f"{candidate} uses CommonJS runtime syntax; npm package manifest declares type=module but workspace "
                f"JavaScript uses CommonJS runtime syntax in {relative_path}"
            )
            errors.append(error)
            issues.append(_package_module_type_mismatch_issue(error, relative_path, source_path=candidate))
    return _FileArtifactQualityEvidence(errors=tuple(errors[:20]), issues=tuple(issues[:20]))


def _npm_script_missing_local_config_issue(
    error: str,
    relative_path: str,
    *,
    script_name: str,
    config_path: str,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="npm_script_missing_local_config",
        message=message,
        path=relative_path,
        source="npm_script_config_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "manifest_path": relative_path,
            "script_issue": "missing_local_config",
            "script_issue_source": "npm_script_config_scanner",
            "script_name": script_name,
            "config_path": config_path,
        },
    )


def _scan_npm_script_missing_local_config_evidence(
    root_full: Path,
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return npm script missing-config findings as strings and typed issues."""

    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    for index, token in enumerate(tokens):
        config_path = ""
        if token == "--config" and index + 1 < len(tokens):
            config_path = str(tokens[index + 1] or "")
        elif token.startswith("--config="):
            config_path = token.split("=", 1)[1]
        config_path = config_path.strip().strip("'\"")
        if not config_path:
            continue
        normalized = config_path.replace("\\", "/")
        if normalized.startswith(("/", "http://", "https://")) or ".." in normalized.split("/"):
            continue
        if Path(normalized).suffix.lower() not in {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}:
            continue
        if not (root_full / normalized).is_file():
            error = (
                "Artifact quality scan failed: npm package manifest script "
                f"{script_name!r} references missing config file {normalized!r} in {relative_path}"
            )
            errors.append(error)
            issues.append(
                _npm_script_missing_local_config_issue(
                    error,
                    relative_path,
                    script_name=script_name,
                    config_path=normalized,
                )
            )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _npm_script_entrypoint_after_command(tokens: list[str], command_index: int) -> str:
    command = str(tokens[command_index] or "").strip().lower()
    index = command_index + 1
    while index < len(tokens):
        token = str(tokens[index] or "").strip()
        if not token or token in _NPM_SCRIPT_SEPARATORS:
            return ""
        lowered = token.lower()
        if lowered in _NPM_SCRIPT_ENTRYPOINT_SUBCOMMANDS.get(command, set()):
            index += 1
            continue
        if lowered in _NPM_NODE_INLINE_CODE_FLAGS:
            return ""
        if lowered in _NPM_NODE_OPTION_VALUE_FLAGS:
            index += 2
            continue
        if lowered.startswith("--loader=") or lowered.startswith("--require=") or lowered.startswith("--import="):
            index += 1
            continue
        if lowered.startswith("-"):
            index += 1
            continue
        return token
    return ""


def _typescript_project_typecheck_issue(
    *,
    error: str,
    detail: str,
    exit_code: int,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code="typescript_project_typecheck_failed",
        message=message,
        source="typescript_project_typecheck",
        metadata={
            "raw": str(error or "").strip(),
            "command": "tsc --noEmit --pretty false",
            "exit_code": exit_code,
            "detail": detail,
            "diagnostic_kind": "typescript_project_typecheck_failed",
        },
    )


def _scan_typescript_project_typecheck_evidence(
    root_full: Path, relative_paths: list[str]
) -> _FileArtifactQualityEvidence:
    """Return TypeScript project typecheck findings as strings and typed issues."""

    if os.environ.get(_TSC_PROJECT_CHECK_FLAG, "1").strip().lower() in {"0", "false", "no", "off"}:
        return _FileArtifactQualityEvidence()
    if not (root_full / "tsconfig.json").is_file():
        return _FileArtifactQualityEvidence()
    if not any(
        Path(path).suffix.lower() in {".ts", ".tsx"} or Path(path).name == "tsconfig.json" for path in relative_paths
    ):
        return _FileArtifactQualityEvidence()
    tsc = _typescript_project_typecheck_command(root_full)
    if not tsc:
        return _FileArtifactQualityEvidence()
    try:
        proc = subprocess.run(
            [tsc, "--noEmit", "--pretty", "false"],
            cwd=str(root_full),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _FileArtifactQualityEvidence()
    if proc.returncode == 0:
        return _FileArtifactQualityEvidence()
    detail = _first_nonempty_line(f"{proc.stdout}\n{proc.stderr}")
    if not detail:
        detail = f"tsc --noEmit exited with code {proc.returncode}"
    trimmed_detail = detail[:400]
    error = f"Artifact quality scan failed: TypeScript project typecheck failed: {trimmed_detail}"
    return _FileArtifactQualityEvidence(
        errors=(error,),
        issues=(
            _typescript_project_typecheck_issue(
                error=error,
                detail=trimmed_detail,
                exit_code=proc.returncode,
            ),
        ),
    )


def _typescript_project_typecheck_command(root_full: Path) -> str:
    local_name = "tsc.cmd" if os.name == "nt" else "tsc"
    local_tsc = root_full / "node_modules" / ".bin" / local_name
    if local_tsc.is_file():
        return str(local_tsc)
    if (root_full / "package.json").is_file():
        return ""
    return shutil.which("tsc") or ""


def _typescript_project_requires_local_tsc(root_full: Path) -> bool:
    try:
        payload = json.loads((root_full / "tsconfig.json").read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    compiler_options = payload.get("compilerOptions")
    if not isinstance(compiler_options, dict):
        return False
    module_resolution = str(compiler_options.get("moduleResolution") or "").strip().lower()
    return module_resolution == "bundler"


def _first_nonempty_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _iter_workspace_relative_files(root_full: Path) -> Iterable[str]:
    for current_root, dir_names, file_names in os.walk(root_full):
        dir_names[:] = [name for name in dir_names if name not in _ARTIFACT_QUALITY_SKIP_DIRS]
        current = Path(current_root)
        for name in file_names:
            full_path = current / name
            try:
                relative_path = full_path.relative_to(root_full).as_posix()
            except ValueError:
                continue
            yield relative_path


def _typescript_import_quality_issue(
    *,
    error: str,
    code: str,
    relative_path: str,
    metadata: Mapping[str, Any],
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    return ArtifactQualityIssue(
        code=code,
        message=message,
        path=relative_path,
        source="typescript_import_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "importer_path": relative_path,
            **dict(metadata),
        },
    )


def _scan_typescript_import_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
) -> _FileArtifactQualityEvidence:
    """Return TypeScript/JavaScript import findings as strings and typed issues."""

    if full_path.suffix.lower() not in _TS_JS_SOURCE_EXTS:
        return _FileArtifactQualityEvidence()
    declared_dependencies = _declared_package_dependencies(root_full)
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    code_mask = _ts_js_code_mask(text)
    is_typescript = full_path.suffix.lower() in _TS_SOURCE_EXTS
    has_package_manifest = (root_full / "package.json").is_file()
    node_types_error_added = False
    for match in _IMPORT_SPECIFIER_RE.finditer(text):
        if not _match_starts_in_ts_js_code(code_mask, match.start()):
            continue
        specifier = str(match.group(1) or "").strip()
        if not specifier:
            continue
        if specifier.startswith((".", "/")):
            if not _relative_import_exists(root_full, full_path, specifier):
                error = f"Artifact quality scan failed: unresolved relative import {specifier!r} in {relative_path}"
                errors.append(error)
                issues.append(
                    _typescript_import_quality_issue(
                        error=error,
                        code="unresolved_relative_import",
                        relative_path=relative_path,
                        metadata={
                            "specifier": specifier,
                            "diagnostic_kind": "unresolved_relative_import",
                        },
                    )
                )
            continue
        if _is_test_like_artifact_path(relative_path) and _package_root_name(specifier) in _TEST_FRAMEWORK_IMPORTS:
            continue
        root_name = _package_root_name(specifier)
        builtin_name = _node_builtin_root_name(specifier)
        if specifier.startswith("node:") or builtin_name in _NODE_BUILTIN_IMPORTS:
            if (
                is_typescript
                and has_package_manifest
                and not node_types_error_added
                and not _node_types_declared(declared_dependencies)
            ):
                error = (
                    "Artifact quality scan failed: TypeScript node builtin import "
                    f"{specifier!r} requires '@types/node' in {relative_path}"
                )
                errors.append(error)
                issues.append(
                    _typescript_import_quality_issue(
                        error=error,
                        code="typescript_node_types_missing",
                        relative_path=relative_path,
                        metadata={
                            "specifier": specifier,
                            "required_dependency": "@types/node",
                            "diagnostic_kind": "typescript_node_types_missing",
                        },
                    )
                )
                node_types_error_added = True
            continue
        if root_name in declared_dependencies:
            continue
        if not _is_test_like_artifact_path(relative_path):
            error = f"Artifact quality scan failed: undeclared runtime import {specifier!r} in {relative_path}"
            errors.append(error)
            issues.append(
                _typescript_import_quality_issue(
                    error=error,
                    code="undeclared_runtime_import",
                    relative_path=relative_path,
                    metadata={
                        "specifier": specifier,
                        "package_root": root_name,
                        "diagnostic_kind": "undeclared_runtime_import",
                    },
                )
            )
    if _ts_symbol_coherence_enabled():
        symbol_coherence_evidence = _scan_typescript_symbol_coherence_evidence(
            root_full,
            full_path,
            text,
            relative_path,
            code_mask=code_mask,
        )
        errors.extend(symbol_coherence_evidence.errors)
        issues.extend(symbol_coherence_evidence.issues)
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _declared_package_dependencies(root_full: Path) -> set[str]:
    package_path = root_full / "package.json"
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, dict):
        return set()
    declared: set[str] = set()
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if isinstance(section, dict):
            declared.update(str(name).strip() for name in section if str(name).strip())
    return declared


def _package_root_name(specifier: str) -> str:
    token = str(specifier or "").strip()
    if token.startswith("@"):
        parts = token.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else token
    return token.split("/", 1)[0]


def _node_builtin_root_name(specifier: str) -> str:
    token = str(specifier or "").strip()
    if token.startswith("node:"):
        token = token.removeprefix("node:")
    return token.split("/", 1)[0]


def _node_types_declared(declared_dependencies: set[str]) -> bool:
    return "@types/node" in declared_dependencies


def _relative_import_exists(root_full: Path, importer_path: Path, specifier: str) -> bool:
    base = (
        (importer_path.parent / specifier).resolve() if specifier.startswith(".") else (root_full / specifier).resolve()
    )
    try:
        base.relative_to(root_full)
    except ValueError:
        return False
    for candidate in _relative_import_candidates(base):
        try:
            candidate.relative_to(root_full)
        except ValueError:
            continue
        if candidate.is_file():
            return True
    return False


def _relative_import_candidates(base: Path) -> list[Path]:
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")
    raw_candidates: list[Path] = [base]
    if base.suffix:
        if base.suffix.lower() in suffixes:
            raw_candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
        else:
            raw_candidates.extend(Path(f"{base}{suffix}") for suffix in suffixes)
            raw_candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
    else:
        raw_candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
        raw_candidates.extend(base / f"index{suffix}" for suffix in suffixes)

    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in raw_candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


_TS_MODULE_READ_CAP_BYTES = 512 * 1024


def _ts_symbol_coherence_enabled() -> bool:
    """TS/JS cross-file symbol coherence is ON unless explicitly disabled."""
    return os.environ.get(_TS_SYMBOL_COHERENCE_FLAG, "1").strip().lower() not in {"0", "false", "no", "off"}


def _ts_js_code_mask(text: str) -> list[bool]:
    """Mark TS/JS source positions that are executable code.

    The artifact scanner is intentionally regex-based and conservative. This
    mask prevents fixture strings, template literals, and comments from being
    interpreted as real imports. Template literal expressions are skipped too:
    that can miss a rare dynamic case, but it avoids false positives in tests
    that embed generated source snippets.
    """

    source = str(text or "")
    mask = [True] * len(source)
    i = 0
    n = len(source)
    while i < n:
        char = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if char == "/" and nxt == "/":
            start = i
            i += 2
            while i < n and source[i] not in "\r\n":
                i += 1
            for pos in range(start, i):
                mask[pos] = False
            continue
        if char == "/" and nxt == "*":
            start = i
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i = min(n, i + 2)
            for pos in range(start, i):
                mask[pos] = False
            continue
        if char in {"'", '"', "`"}:
            quote = char
            start = i
            i += 1
            escaped = False
            while i < n:
                current = source[i]
                if escaped:
                    escaped = False
                    i += 1
                    continue
                if current == "\\":
                    escaped = True
                    i += 1
                    continue
                i += 1
                if current == quote:
                    break
            for pos in range(start, i):
                mask[pos] = False
            continue
        i += 1
    return mask


def _match_starts_in_ts_js_code(mask: list[bool], start: int) -> bool:
    return 0 <= start < len(mask) and mask[start]


def _parse_ts_clause_names(inner: str, *, for_export: bool) -> set[str]:
    """Parse the identifiers in an `import {…}` or `export {…}` clause.

    For exports the bound name is the alias (`A as B` exports ``B``); for imports
    the name that must exist in the sibling is the original (`A as B` imports
    ``A``). Inline type-only members (`type X`) are skipped — they are erased at
    runtime and carry ambient/declaration-merging risk we will not adjudicate.
    """
    names: set[str] = set()
    clause = str(inner or "")
    mask = _ts_js_code_mask(clause)
    cleaned_clause = "".join(char if mask[index] else " " for index, char in enumerate(clause))
    for raw in cleaned_clause.split(","):
        token = raw.strip()
        if not token or token == "type" or token.startswith("type "):
            continue
        parts = re.split(r"\s+as\s+", token)
        chosen = (parts[-1] if for_export else parts[0]).strip()
        if re.fullmatch(r"[A-Za-z_$][\w$]*", chosen):
            names.add(chosen)
    return names


def _typescript_module_exports(text: str) -> set[str] | None:
    """Best-effort export surface of a TS/JS module, or ``None`` (fail-open).

    Returns ``None`` whenever the surface cannot be safely determined (any
    surface-unknowable construct: ``export *``, ``export =``, CommonJS
    ``module.exports``/``exports.x``, ambient ``declare module``, destructured
    export). Capture is otherwise generous — missing a real export form would be
    a FALSE POSITIVE (a runnable product wrongly failed), whereas over-capturing
    only yields a benign false negative — so every plausible declaration and
    clause form is collected.
    """
    if not text:
        return None
    if _TS_DYNAMIC_EXPORT_RE.search(text):
        return None
    exports: set[str] = set()
    for match in _TS_EXPORT_DECL_RE.finditer(text):
        name = (
            match.group("fn") or match.group("cls") or match.group("ty") or match.group("cenum") or match.group("var")
        )
        if name:
            exports.add(name)
    for match in _TS_EXPORT_CLAUSE_RE.finditer(text):
        exports.update(_parse_ts_clause_names(match.group("inner"), for_export=True))
    if _TS_EXPORT_DEFAULT_RE.search(text):
        exports.add("default")
    return exports


def _resolve_typescript_module_file(root_full: Path, importer_path: Path, specifier: str) -> Path | None:
    """Resolve a RELATIVE TS/JS import specifier to its single sibling file."""
    if not specifier.startswith("."):
        return None
    base = (importer_path.parent / specifier).resolve()
    try:
        base.relative_to(root_full)
    except ValueError:
        return None
    for candidate in _relative_import_candidates(base):
        try:
            candidate.relative_to(root_full)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _read_typescript_module_exports(module_file: Path) -> set[str] | None:
    try:
        if module_file.stat().st_size > _TS_MODULE_READ_CAP_BYTES:
            return None
        content = module_file.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return _typescript_module_exports(content)


def _typescript_symbol_coherence_quality_issue(
    *,
    error: str,
    relative_path: str,
    specifier: str,
    imported_symbol: str,
    module_file: Path,
    root_full: Path,
) -> ArtifactQualityIssue:
    message = str(error or "").strip()
    if message.lower().startswith(_ARTIFACT_QUALITY_ERROR_PREFIX.lower()):
        message = message[len(_ARTIFACT_QUALITY_ERROR_PREFIX) :].strip()
    try:
        exporter_path = module_file.relative_to(root_full).as_posix()
    except ValueError:
        exporter_path = module_file.as_posix()
    return ArtifactQualityIssue(
        code="typescript_import_unresolved_symbol",
        message=message,
        path=relative_path,
        source="typescript_symbol_coherence_scanner",
        metadata={
            "raw": str(error or "").strip(),
            "importer_path": relative_path,
            "exporter_path": exporter_path,
            "specifier": specifier,
            "imported_symbol": imported_symbol,
            "diagnostic_kind": "typescript_import_unresolved_symbol",
        },
    )


def _scan_typescript_symbol_coherence_evidence(
    root_full: Path,
    full_path: Path,
    text: str,
    relative_path: str,
    *,
    code_mask: list[bool] | None = None,
) -> _FileArtifactQualityEvidence:
    """Flag named imports of a resolvable relative sibling that the sibling never
    exports — the TS/JS analogue of the Python symbol-coherence check. Conservative
    by construction: only plain named imports of relative specifiers are checked,
    and any ambiguity (type-only import, unresolved specifier, unknowable export
    surface) is skipped, never flagged.
    """
    errors: list[str] = []
    issues: list[ArtifactQualityIssue] = []
    seen: set[tuple[str, str]] = set()
    exports_cache: dict[Path, set[str] | None] = {}
    mask = code_mask if code_mask is not None else _ts_js_code_mask(text)
    for match in _TS_NAMED_IMPORT_RE.finditer(text):
        if not _match_starts_in_ts_js_code(mask, match.start()):
            continue
        if match.group("typeonly"):
            continue
        specifier = str(match.group("spec") or "").strip()
        if not specifier.startswith("."):
            continue
        imported = _parse_ts_clause_names(match.group("names"), for_export=False)
        if not imported:
            continue
        module_file = _resolve_typescript_module_file(root_full, full_path, specifier)
        if module_file is None:
            continue
        if module_file not in exports_cache:
            exports_cache[module_file] = _read_typescript_module_exports(module_file)
        surface = exports_cache[module_file]
        if surface is None:
            continue
        for name in sorted(imported):
            if name in surface:
                continue
            key = (name, specifier)
            if key in seen:
                continue
            seen.add(key)
            error = (
                f"Artifact quality scan failed: unresolved import symbol {name!r} "
                f"from {specifier!r} in {relative_path} (sibling module does not define it)"
            )
            errors.append(error)
            issues.append(
                _typescript_symbol_coherence_quality_issue(
                    error=error,
                    relative_path=relative_path,
                    specifier=specifier,
                    imported_symbol=name,
                    module_file=module_file,
                    root_full=root_full,
                )
            )
    return _FileArtifactQualityEvidence(errors=tuple(errors), issues=tuple(issues))


def _is_test_like_artifact_path(relative_path: str) -> bool:
    normalized = str(relative_path or "").replace("\\", "/").lower()
    name = os.path.basename(normalized)
    return (
        "/test/" in normalized
        or "/tests/" in normalized
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.py")
    )
