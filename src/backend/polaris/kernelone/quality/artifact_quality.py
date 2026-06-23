"""Artifact quality checks shared by Director and integration QA."""

from __future__ import annotations

import ast
import contextlib
import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
_NPM_NODE_OPTION_VALUE_FLAGS = {"--loader", "--require", "-r", "--import"}
_NPM_SCRIPT_SEPARATORS = {"&&", "||", ";", "|"}
_NPM_PLACEHOLDER_SCRIPT_COMMANDS = {"echo", "printf"}
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

    try:
        root_full = Path(workspace_full).resolve()
    except (OSError, RuntimeError, ValueError):
        return ["Artifact quality scan failed: workspace path cannot be resolved"]
    if not root_full.exists() or not root_full.is_dir():
        return ["Artifact quality scan failed: workspace path does not exist"]

    errors: list[str] = []
    try:
        paths = (
            _iter_target_files(root_full, relative_paths)
            if relative_paths is not None
            else _iter_workspace_source_files(root_full)
        )
        scanned_relative_paths: list[str] = []
        for full_path in paths:
            if len(errors) >= 50:
                return errors
            relative_path = full_path.relative_to(root_full).as_posix()
            scanned_relative_paths.append(relative_path)
            errors.extend(_scan_file(root_full, full_path, relative_path))
        if len(errors) < 50:
            errors.extend(_scan_typescript_project_typecheck(root_full, scanned_relative_paths))
    except (OSError, RuntimeError, ValueError) as exc:
        return [f"Artifact quality scan failed: {exc}"]
    return errors


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


def _scan_file(root_full: Path, full_path: Path, relative_path: str) -> list[str]:
    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")[:1_000_000]
    except (OSError, RuntimeError, ValueError):
        return []

    receipt_error = _tool_receipt_contamination_error(relative_path, text)
    if receipt_error:
        return [receipt_error]

    narration_error = _source_narration_contamination_error(relative_path, text)
    if narration_error:
        return [narration_error]

    errors: list[str] = []
    syntax = check_source_file_syntax(str(full_path))
    if syntax is not None and syntax.get("ok") is False:
        errors.append(
            f"Artifact quality scan failed: syntax error in {relative_path}: {str(syntax.get('error'))[:200]}"
        )
    if os.path.basename(relative_path).lower() == "package.json":
        errors.extend(_scan_package_manifest(root_full, text, relative_path))
    errors.extend(_scan_typescript_imports(root_full, full_path, text, relative_path))
    errors.extend(_scan_python_imports(root_full, full_path, text, relative_path))
    errors.extend(_scan_typescript_syntax_red_flags(root_full, full_path, text, relative_path))
    errors.extend(_scan_html_typescript_module_scripts(full_path, text, relative_path))
    for marker in _DETERMINISTIC_SCAFFOLD_MARKERS:
        if marker in text:
            errors.append(f"Artifact quality scan failed: deterministic scaffold marker {marker!r} in {relative_path}")
            break
    helper_count = len(_NUMERIC_HELPER_FILLER_RE.findall(text))
    if helper_count >= 5:
        errors.append(
            f"Artifact quality scan failed: repeated numeric helper filler in {relative_path} (count={helper_count})"
        )
    if helper_count >= 3 and _GENERIC_STORE_RECORD_RE.search(text) and _GENERIC_STORE_MAP_RE.search(text):
        errors.append(f"Artifact quality scan failed: generic payload/index store scaffold in {relative_path}")
    if _PATCH_RESIDUE_RE.search(text):
        errors.append(f"Artifact quality scan failed: patch residue marker in {relative_path}")
    if _is_test_like_artifact_path(relative_path):
        trivial_count = len(_TRIVIAL_ARITHMETIC_EXPECT_RE.findall(text))
        if trivial_count >= 3:
            errors.append(
                "Artifact quality scan failed: repeated trivial arithmetic placeholder "
                f"tests in {relative_path} (count={trivial_count})"
            )
    return errors


def _scan_typescript_syntax_red_flags(root_full: Path, full_path: Path, text: str, relative_path: str) -> list[str]:
    if full_path.suffix.lower() not in _TS_JS_SOURCE_EXTS:
        return []
    if _typescript_line_comment_contains_escaped_newline_code(text):
        return [
            f"Artifact quality scan failed: TypeScript escaped newline in line comment before code in {relative_path}"
        ]
    collision_name = _typescript_zod_inferred_type_class_collision_name(text)
    if collision_name:
        return [
            "Artifact quality scan failed: TypeScript zod inferred type collides "
            f"with class {collision_name} in {relative_path}"
        ]
    for match in _TS_RETURN_OBJECT_BLOCK_RE.finditer(text):
        if _TS_OBJECT_PROPERTY_SEMICOLON_RE.search(match.group("body")):
            return [
                "Artifact quality scan failed: TypeScript return object contains "
                f"semicolon-terminated property in {relative_path}"
            ]
    type_export_error = _typescript_isolated_modules_type_reexport_error(root_full, text)
    if type_export_error:
        return [
            "Artifact quality scan failed: TypeScript isolatedModules requires "
            f"`export type` for {type_export_error} in {relative_path}"
        ]
    return []


def _scan_html_typescript_module_scripts(full_path: Path, text: str, relative_path: str) -> list[str]:
    if full_path.suffix.lower() not in {".html", ".htm"}:
        return []
    errors: list[str] = []
    for match in _HTML_TYPESCRIPT_MODULE_SCRIPT_RE.finditer(text):
        src = str(match.group("src") or "").strip()
        if src:
            errors.append(
                "Artifact quality scan failed: HTML module script references TypeScript source "
                f"{src!r} in {relative_path}; static entrypoints must load JavaScript"
            )
    return errors


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


def _scan_package_manifest(root_full: Path, text: str, relative_path: str) -> list[str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    errors: list[str] = []
    scripts = payload.get("scripts")
    if isinstance(scripts, dict):
        test_script = str(scripts.get("test") or "")
        lowered = test_script.lower()
        if "no test specified" in lowered or "no tests specified" in lowered:
            errors.append(f"Artifact quality scan failed: npm default failing test script in {relative_path}")
        if _NPM_PLACEHOLDER_TEST_SCRIPT_RE.search(test_script):
            errors.append(f"Artifact quality scan failed: npm placeholder test script in {relative_path}")
        if _NPM_MANIFEST_ONLY_TEST_SCRIPT_RE.search(test_script):
            errors.append(f"Artifact quality scan failed: npm manifest-only test script in {relative_path}")
        if (
            _NPM_TEST_RUNNER_SCRIPT_RE.search(test_script)
            and _workspace_has_node_source_files(root_full)
            and not _workspace_has_node_test_files(root_full)
        ):
            errors.append(
                "Artifact quality scan failed: npm package manifest has test runner script "
                f"but no test/spec files exist in {relative_path}"
            )
        for script_name, script_value in scripts.items():
            script_text = str(script_value or "")
            try:
                tokens = shlex.split(script_text, posix=(os.name != "nt"))
            except ValueError as exc:
                errors.append(
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} has invalid shell syntax in {relative_path}: {exc}"
                )
                continue
            placeholder_reason = _placeholder_package_script_reason(str(script_name), script_text, tokens)
            if placeholder_reason:
                errors.append(f"Artifact quality scan failed: {placeholder_reason} in {relative_path}")
                continue
            if _NPM_SCRIPT_FAILURE_SWALLOW_RE.search(script_text):
                errors.append(
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} swallows command failures in {relative_path}"
                )
                continue
            if _NPM_SCRIPT_SHELL_SUBSTITUTION_RE.search(script_text):
                errors.append(
                    "Artifact quality scan failed: npm package manifest script "
                    f"{str(script_name)!r} uses shell command substitution in {relative_path}"
                )
                continue
            if _PYTHON_COMMAND_IN_NPM_SCRIPT_RE.search(script_text):
                errors.append(
                    "Artifact quality scan failed: npm package manifest contains "
                    f"Python command in script {str(script_name)!r} in {relative_path}"
                )
                break
            node_eval_error = _scan_npm_script_node_eval_syntax(tokens, str(script_name), relative_path)
            if node_eval_error:
                errors.append(node_eval_error)
                continue
            errors.extend(
                _scan_npm_script_missing_local_entrypoints(root_full, script_text, str(script_name), relative_path)
            )
            errors.extend(_scan_npm_script_missing_local_configs(root_full, tokens, str(script_name), relative_path))
        if _package_manifest_requires_typescript(root_full, payload) and not _package_declares_dependency(
            payload, "typescript"
        ):
            errors.append(
                "Artifact quality scan failed: TypeScript project requires 'typescript' "
                f"devDependency in {relative_path}"
            )
    main_entry = str(payload.get("main") or "").strip().replace("\\", "/").lower()
    if main_entry.endswith(".py"):
        errors.append(
            f"Artifact quality scan failed: npm package manifest contains Python runtime entrypoint in {relative_path}"
        )
    for section_name in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        section = payload.get(section_name)
        if not isinstance(section, dict):
            continue
        for package_name in section:
            normalized = str(package_name or "").strip().lower()
            if normalized in _PYTHON_PACKAGE_MANIFEST_DEPENDENCIES:
                errors.append(
                    "Artifact quality scan failed: npm package manifest declares "
                    f"Python package dependency {package_name!r} in {relative_path}"
                )
                return errors
    return errors


def _scan_npm_script_node_eval_syntax(tokens: list[str], script_name: str, relative_path: str) -> str:
    for source in _iter_node_eval_sources(tokens):
        detail = _check_javascript_snippet_syntax(source)
        if detail:
            return (
                "Artifact quality scan failed: npm package manifest script "
                f"{script_name!r} has invalid node eval syntax in {relative_path}: {detail[:200]}"
            )
    return ""


def _iter_node_eval_sources(tokens: list[str]) -> Iterable[str]:
    for index, token in enumerate(tokens):
        normalized = os.path.basename(str(token or "").strip().lower())
        if normalized not in {"node", "node.exe"}:
            continue
        next_index = index + 1
        if next_index >= len(tokens):
            continue
        eval_flag = str(tokens[next_index] or "").strip()
        if eval_flag in {"-e", "--eval"}:
            source_index = next_index + 1
            if source_index < len(tokens):
                source = str(tokens[source_index] or "")
                if source.strip():
                    yield source
            continue
        for prefix in ("-e=", "--eval="):
            if eval_flag.startswith(prefix):
                source = eval_flag[len(prefix) :]
                if source.strip():
                    yield source


def _check_javascript_snippet_syntax(source: str) -> str:
    node = shutil.which("node")
    if not node:
        return ""
    temp_path = ""
    try:
        fd, temp_path = tempfile.mkstemp(prefix="polaris-node-eval-", suffix=".js")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(source)
            if not source.endswith("\n"):
                handle.write("\n")
        proc = subprocess.run([node, "--check", temp_path], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"syntax check could not run: {exc}"
    finally:
        if temp_path:
            with contextlib.suppress(OSError):
                os.unlink(temp_path)
    if proc.returncode == 0:
        return ""
    return _compress_node_syntax_error(proc.stderr or proc.stdout, temp_path)


def _placeholder_package_script_reason(script_name: str, command: str, tokens: list[str]) -> str:
    if not tokens:
        return f"npm package manifest script {script_name!r} is empty"
    first_command = os.path.basename(str(tokens[0] or ""))
    if first_command not in _NPM_PLACEHOLDER_SCRIPT_COMMANDS:
        return ""
    for index, token in enumerate(tokens):
        if token not in _NPM_SCRIPT_SEPARATORS or index + 1 >= len(tokens):
            continue
        next_command = os.path.basename(str(tokens[index + 1] or ""))
        if next_command not in {*_NPM_PLACEHOLDER_SCRIPT_COMMANDS, "exit", "true"}:
            return ""
    return f"npm package manifest script {script_name!r} is a placeholder command: {command}"


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


def _scan_npm_script_missing_local_entrypoints(
    root_full: Path, script_text: str, script_name: str, relative_path: str
) -> list[str]:
    if _NPM_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(script_text):
        return []
    try:
        tokens = shlex.split(script_text, posix=(os.name != "nt"))
    except ValueError:
        return []
    errors: list[str] = []
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
        if Path(normalized).suffix.lower() not in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            continue
        if not (root_full / normalized).is_file():
            errors.append(
                "Artifact quality scan failed: npm package manifest script "
                f"{script_name!r} references missing local entrypoint {normalized!r} in {relative_path}"
            )
    return errors


def _scan_npm_script_missing_local_configs(
    root_full: Path,
    tokens: list[str],
    script_name: str,
    relative_path: str,
) -> list[str]:
    errors: list[str] = []
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
            errors.append(
                "Artifact quality scan failed: npm package manifest script "
                f"{script_name!r} references missing config file {normalized!r} in {relative_path}"
            )
    return errors


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


def _scan_typescript_project_typecheck(root_full: Path, relative_paths: list[str]) -> list[str]:
    if os.environ.get(_TSC_PROJECT_CHECK_FLAG, "1").strip().lower() in {"0", "false", "no", "off"}:
        return []
    if not (root_full / "tsconfig.json").is_file():
        return []
    if not any(
        Path(path).suffix.lower() in {".ts", ".tsx"} or Path(path).name == "tsconfig.json" for path in relative_paths
    ):
        return []
    tsc = _typescript_project_typecheck_command(root_full)
    if not tsc:
        return []
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
        return []
    if proc.returncode == 0:
        return []
    detail = _first_nonempty_line(f"{proc.stdout}\n{proc.stderr}")
    if not detail:
        detail = f"tsc --noEmit exited with code {proc.returncode}"
    return [f"Artifact quality scan failed: TypeScript project typecheck failed: {detail[:400]}"]


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


def _resolve_python_package_dir(root_full: Path, importer_full: Path, module: str, level: int) -> Path | None:
    """Directory a dotted import path points at *as a package dir*, or None.

    Relative imports (``level >= 1``) start from the importer's own directory and
    climb ``level - 1`` parents; absolute imports resolve under the workspace root.
    """
    if level and level > 0:
        base = importer_full.parent
        for _ in range(level - 1):
            base = base.parent
        return base / module.replace(".", "/") if module else base
    if module:
        return root_full / module.replace(".", "/")
    return None


def _resolve_python_module_file(root_full: Path, importer_full: Path, module: str, level: int) -> Path | None:
    """Resolve an import target to an in-workspace ``.py`` file, or None when it
    points outside the workspace (stdlib / third-party / unresolvable)."""
    pkg_target = _resolve_python_package_dir(root_full, importer_full, module, level)
    if pkg_target is None:
        return None
    for candidate in (pkg_target.with_suffix(".py"), pkg_target / "__init__.py"):
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root_full)
        except (ValueError, OSError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _python_submodule_exists(root_full: Path, importer_full: Path, module: str, level: int, name: str) -> bool:
    """True when ``name`` is a submodule file of the import's package (e.g.
    ``from . import board`` where ``board.py`` exists), not a module attribute."""
    pkg_dir = _resolve_python_package_dir(root_full, importer_full, module, level)
    if pkg_dir is None:
        return False
    for candidate in (pkg_dir / f"{name}.py", pkg_dir / name / "__init__.py"):
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root_full)
        except (ValueError, OSError):
            continue
        if resolved.is_file():
            return True
    return False


def _python_module_exports(module_text: str) -> set[str] | None:
    """Top-level names a module provides, or None when the surface cannot be
    determined safely (wildcard re-export / dynamic ``__getattr__``).

    None is the fail-open signal: callers must NOT report any symbol as missing
    when the target's export surface is unknown — a false 'missing' would burn
    Director turns exactly like the readme.md/README.md case.
    """
    try:
        tree = ast.parse(module_text)
    except (SyntaxError, ValueError):
        return None
    exports: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            exports.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exports.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    exports.update(elt.id for elt in target.elts if isinstance(elt, ast.Name))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                exports.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                exports.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return None  # wildcard re-export — unknown surface, do not check against it
            for alias in node.names:
                exports.add(alias.asname or alias.name)
    if "__getattr__" in exports:
        return None  # module-level __getattr__ resolves any attribute dynamically
    return exports


def _scan_python_imports(root_full: Path, full_path: Path, text: str, relative_path: str) -> list[str]:
    """Cross-file symbol coherence for in-workspace Python imports.

    Catches the attrition-drift failure where one file imports a symbol a sibling
    never defines (live factory-bench L3-16: ``tetris/__init__.py`` imported
    ``SRS_ROTATION_STATES`` from ``tetris/constants.py`` which never defined it →
    ``import tetris`` raised ImportError → the product did not run). Conservative
    by design: only flags imports that resolve to a workspace ``.py`` file whose
    full export surface is known; anything ambiguous fails open.
    """
    if full_path.suffix.lower() != ".py" or _is_test_like_artifact_path(relative_path):
        return []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []  # syntax errors are reported by the syntax check, not here
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if any(alias.name == "*" for alias in node.names):
            continue
        module = node.module or ""
        level = node.level or 0
        target_file = _resolve_python_module_file(root_full, full_path, module, level)
        if target_file is None:
            continue  # external / stdlib / unresolved → fail open
        try:
            module_text = target_file.read_text(encoding="utf-8", errors="replace")[:1_000_000]
        except (OSError, RuntimeError, ValueError):
            continue
        exports = _python_module_exports(module_text)
        if exports is None:
            continue  # unknown surface → fail open
        prefix = "." * level + module
        for alias in node.names:
            name = alias.name
            if name in exports or _python_submodule_exists(root_full, full_path, module, level, name):
                continue
            errors.append(
                f"Artifact quality scan failed: unresolved import symbol {name!r} from "
                f"{prefix!r} in {relative_path} (sibling module does not define it)"
            )
    return errors


def _scan_typescript_imports(root_full: Path, full_path: Path, text: str, relative_path: str) -> list[str]:
    if full_path.suffix.lower() not in _TS_JS_SOURCE_EXTS:
        return []
    declared_dependencies = _declared_package_dependencies(root_full)
    errors: list[str] = []
    is_typescript = full_path.suffix.lower() in _TS_SOURCE_EXTS
    has_package_manifest = (root_full / "package.json").is_file()
    node_types_error_added = False
    for match in _IMPORT_SPECIFIER_RE.finditer(text):
        specifier = str(match.group(1) or "").strip()
        if not specifier:
            continue
        if specifier.startswith((".", "/")):
            if not _relative_import_exists(root_full, full_path, specifier):
                errors.append(
                    f"Artifact quality scan failed: unresolved relative import {specifier!r} in {relative_path}"
                )
            continue
        if _is_test_like_artifact_path(relative_path) and _package_root_name(specifier) in _TEST_FRAMEWORK_IMPORTS:
            continue
        root_name = _package_root_name(specifier)
        builtin_name = _node_builtin_root_name(specifier)
        if builtin_name in _NODE_BUILTIN_IMPORTS:
            if (
                is_typescript
                and has_package_manifest
                and not node_types_error_added
                and not _node_types_declared(declared_dependencies)
            ):
                errors.append(
                    "Artifact quality scan failed: TypeScript node builtin import "
                    f"{specifier!r} requires '@types/node' in {relative_path}"
                )
                node_types_error_added = True
            continue
        if root_name in declared_dependencies:
            continue
        if not _is_test_like_artifact_path(relative_path):
            errors.append(f"Artifact quality scan failed: undeclared runtime import {specifier!r} in {relative_path}")
    if _ts_symbol_coherence_enabled():
        errors.extend(_scan_typescript_symbol_coherence(root_full, full_path, text, relative_path))
    return errors


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


def _parse_ts_clause_names(inner: str, *, for_export: bool) -> set[str]:
    """Parse the identifiers in an `import {…}` or `export {…}` clause.

    For exports the bound name is the alias (`A as B` exports ``B``); for imports
    the name that must exist in the sibling is the original (`A as B` imports
    ``A``). Inline type-only members (`type X`) are skipped — they are erased at
    runtime and carry ambient/declaration-merging risk we will not adjudicate.
    """
    names: set[str] = set()
    for raw in str(inner or "").split(","):
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


def _scan_typescript_symbol_coherence(root_full: Path, full_path: Path, text: str, relative_path: str) -> list[str]:
    """Flag named imports of a resolvable relative sibling that the sibling never
    exports — the TS/JS analogue of the Python symbol-coherence check. Conservative
    by construction: only plain named imports of relative specifiers are checked,
    and any ambiguity (type-only import, unresolved specifier, unknowable export
    surface) is skipped, never flagged.
    """
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    exports_cache: dict[Path, set[str] | None] = {}
    for match in _TS_NAMED_IMPORT_RE.finditer(text):
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
            errors.append(
                f"Artifact quality scan failed: unresolved import symbol {name!r} "
                f"from {specifier!r} in {relative_path} (sibling module does not define it)"
            )
    return errors


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
