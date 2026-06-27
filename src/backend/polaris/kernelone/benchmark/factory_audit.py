"""Deterministic audit primitives for factory-bench full-chain project runs.

Zero-LLM: inspects a workspace that the Polaris role chain (PM→Architect/CE→
Director→QA) just generated a project into, and produces a schema-stamped
audit record: which planning/blueprint documents exist, what code was
produced, and whether deterministic runnability checks pass (Python compiles,
HTML present, JS syntax, minimum file counts).

Check vocabulary (mirrors ``scripts/factory_bench/projects_v1.json``):
``py_compile`` / ``html`` / ``js_syntax`` / ``min_files:N`` /
``package_scripts`` / ``ts_syntax`` / ``go_compile`` / ``rust_compile`` /
``cpp_compile`` / ``java_compile``.
"""

from __future__ import annotations

import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from polaris.kernelone.benchmark.factory_depth_contract import (
    build_factory_bench_level_contract,
    extract_level_contract_minimums,
)

FACTORY_AUDIT_SCHEMA_VERSION = "factory-audit/1"

_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".html",
    ".css",
    ".c",
    ".h",
    ".cpp",
    ".rs",
    ".go",
    ".java",
    ".json",
    ".sql",
    ".sh",
}
_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".rs",
    ".go",
    ".java",
}
_SCAFFOLD_EXTENSIONS = {".json", ".html", ".css", ".sh", ".sql"}
_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
# "runtime" excluded: the chain mirrors traceability json INTO the workspace,
# which inflated code_file_count (L1-02 min_files passed on a matrix json).
# Build-output dirs excluded (mirrors _DEPTH_EXCLUDED_DIRS): CMake/cargo/webpack
# write generated files into build/target/dist that pollute the inventory and its
# language detection — a C++ project's CMake build dir leaked
# build/CMakeFiles/.../compiler_depend.ts, counted as a TypeScript file and falsely
# flagging the C++ project as TS missing package.json/tsconfig.json (L1-06).
_SKIP_DIRS = {
    ".git",
    ".polaris",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "runtime",
    "build",
    "cmake-build",
    "target",
    "dist",
    "out",
}
_MAX_SCAN_FILES = 20000
_SCRIPT_INTERPRETERS = {"node", "python", "python3", "bash", "sh"}
_SCRIPT_PATH_EXTENSIONS = {".cjs", ".js", ".mjs", ".py", ".sh", ".ts", ".tsx"}
_SHELL_OPERATORS = {"&&", "||", ";", "|"}
_BUILD_OUTPUT_DIR_NAMES = {"dist", "build", "out", "bin"}
_PLACEHOLDER_SCRIPT_COMMANDS = {"echo", "printf"}
_DEPTH_EXCLUDED_DIRS = {
    ".git",
    ".polaris",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "runtime",
    "target",
    "dist",
    "build",
    "out",
}
_BEHAVIOR_SYMBOL_RE = re.compile(
    r"\b(function|func|def|fn|class|struct|enum|interface|record)\b|"
    r"\b(public|private|protected)\s+[\w<>, ?\[\]]+\s+\w+\s*\(|"
    r"\b(?!if\b|for\b|while\b|switch\b|catch\b)(?:[A-Za-z_]\w*::)?[A-Za-z_]\w+"
    r"\s*\([^;{}]*\)\s*(?:const\s*)?\{",
    re.IGNORECASE,
)
_BRANCH_RE = re.compile(r"\b(if|else\s+if|switch|match|case|for|while|try|catch|except)\b", re.IGNORECASE)
_TEST_ASSERTION_RE = re.compile(
    r"\b(assert|assertEqual|assertIn|expect|t\.Run|testing\.|@Test|TEST_F?)\b|#\s*\[\s*test\s*\]",
    re.IGNORECASE,
)
_PLACEHOLDER_SOURCE_RE = re.compile(
    r"\b(todo|fixme|notimplemented|not implemented|placeholder|stub)\b|^\s*pass\s*(?:#.*)?$",
    re.IGNORECASE | re.MULTILINE,
)
# Pure-comment line prefixes across bench languages (Go/JS/TS ``//``, Python/shell
# ``#``, block-comment continuations ``*`` / ``/*``, SQL ``--``, HTML ``<!--``).
# Documentation comments legitimately mention words like "stub"/"todo" (e.g. a Go
# doc comment ``// pass a stub from tests instead of``); those are NOT unfinished-
# code markers and must not be flagged as placeholder hits (factory_bench L1-04
# museum.go false positive). Bare ``pass`` bodies and code-level markers stay
# flagged because their lines are not pure comments.
_COMMENT_LINE_PREFIXES: tuple[str, ...] = ("//", "#", "*", "/*", "--", "<!--")
# String/docstring literals also legitimately contain placeholder WORDS as prose
# (e.g. a module docstring "...never falls back to static placeholder text...", or an
# anti-placeholder test naming forbidden tokens). Strip string literals so their words
# don't false-trigger; bare ``pass`` (never inside a string) and code-level markers stay
# flagged. Triple-quoted (docstring) literals are matched before single-line ones.
_STRING_LITERAL_RE = re.compile(
    r'"""[\s\S]*?"""'
    r"|'''[\s\S]*?'''"
    r'|"(?:[^"\\]|\\.)*"'
    r"|'(?:[^'\\]|\\.)*'"
)


def _has_unfinished_placeholder(text: str) -> bool:
    """Return True if a placeholder/stub marker appears in real code (not comments/strings).

    Suppresses placeholder WORDS occurring only inside string literals, whole-line
    comments, OR inline comments (e.g. a C++ line ``"...",  // ...placeholder...``
    where the comment, not the code, names the word). Bare ``pass`` bodies stay
    flagged. String literals are stripped first so ``//`` inside a URL string is not
    mistaken for a comment; a leading-``#`` line (Python comment or C preprocessor)
    is dropped whole, while an inline ``#`` after code is treated as a comment.
    """
    without_strings = _STRING_LITERAL_RE.sub('""', text)
    scan_lines: list[str] = []
    for line in without_strings.splitlines():
        if line.lstrip().startswith(_COMMENT_LINE_PREFIXES):
            continue
        slash = line.find("//")
        if slash != -1:
            line = line[:slash]
        hash_idx = line.find("#")
        if hash_idx > 0 and line[:hash_idx].strip():
            line = line[:hash_idx]
        scan_lines.append(line)
    return bool(_PLACEHOLDER_SOURCE_RE.search("\n".join(scan_lines)))


_STRUCTURAL_SYMBOL_LINE_RE = re.compile(
    r"\b(function|func|def|fn|class|struct|enum|interface|record|mod)\b|"
    r"\b(public|private|protected)\s+[\w<>, ?\[\]]+\s+\w+\s*\(",
    re.IGNORECASE,
)
_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE = re.compile(
    r"(?:npm\s+run\s+(?:build|compile)|pnpm\s+(?:build|compile)|yarn\s+(?:build|compile)|\btsc\b)",
    re.IGNORECASE,
)
_TS_SYNTAX_CHECKER_JS = r"""
const fs = require('fs');
let ts;
try {
  ts = require('typescript');
} catch (error) {
  console.log(JSON.stringify({
    ok: false,
    unavailable: true,
    detail: `typescript module unavailable: ${error && error.message ? error.message : String(error)}`
  }));
  process.exit(2);
}

const payload = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
const files = Array.isArray(payload.files) ? payload.files : [];
const failures = [];
for (const rel of files) {
  const text = fs.readFileSync(rel, 'utf8');
  const sourceFile = ts.createSourceFile(rel, text, ts.ScriptTarget.Latest, true);
  const parseDiagnostic = (sourceFile.parseDiagnostics || []).find(
    (item) => item.category === ts.DiagnosticCategory.Error
  );
  if (parseDiagnostic) {
    const message = ts.flattenDiagnosticMessageText(parseDiagnostic.messageText, ' ');
    let location = rel;
    if (typeof parseDiagnostic.start === 'number') {
      const pos = sourceFile.getLineAndCharacterOfPosition(parseDiagnostic.start);
      location = `${rel}(${pos.line + 1},${pos.character + 1})`;
    }
    failures.push(`${location}: TS${parseDiagnostic.code}: ${message}`);
    break;
  }
  const result = ts.transpileModule(text, {
    fileName: rel,
    reportDiagnostics: true,
    compilerOptions: {
      target: ts.ScriptTarget.ES2020,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.ReactJSX,
      isolatedModules: true
    }
  });
  const diagnostic = (result.diagnostics || []).find((item) => item.category === ts.DiagnosticCategory.Error);
  if (diagnostic) {
    const message = ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ');
    let location = rel;
    if (typeof diagnostic.start === 'number') {
      const pos = sourceFile.getLineAndCharacterOfPosition(diagnostic.start);
      location = `${rel}(${pos.line + 1},${pos.character + 1})`;
    }
    failures.push(`${location}: TS${diagnostic.code}: ${message}`);
    break;
  }
}

if (failures.length) {
  console.log(JSON.stringify({ok: false, detail: failures[0]}));
  process.exit(1);
}
console.log(JSON.stringify({ok: true, checked: files.length}));
"""


def collect_workspace_inventory(workspace: str) -> dict[str, Any]:
    """Enumerate generated code/doc files (workspace-relative, sorted).

    Returns dict with keys:
    - ``code_files``: all files matching _CODE_EXTENSIONS (backward compatible)
    - ``source_files``: subset of code_files that are real source code (not scaffold)
    - ``doc_files``: documentation files
    """
    code_files: list[str] = []
    source_files: list[str] = []
    doc_files: list[str] = []
    scanned = 0
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            scanned += 1
            if scanned > _MAX_SCAN_FILES:
                dirnames[:] = []
                break
            rel = os.path.relpath(os.path.join(current_root, filename), workspace).replace("\\", "/")
            _, ext = os.path.splitext(filename)
            ext_lower = ext.lower()
            if ext_lower in _CODE_EXTENSIONS:
                code_files.append(rel)
                if ext_lower in _SOURCE_EXTENSIONS:
                    source_files.append(rel)
            elif ext_lower in _DOC_EXTENSIONS:
                doc_files.append(rel)
    return {
        "code_files": sorted(code_files),
        "source_files": sorted(source_files),
        "doc_files": sorted(doc_files),
    }


def _is_depth_excluded_path(rel: str) -> bool:
    parts = {part.lower() for part in Path(rel).parts}
    return bool(parts & _DEPTH_EXCLUDED_DIRS)


def _is_test_source_path(rel: str) -> bool:
    parts = {part.lower() for part in Path(rel).parts}
    filename = Path(rel).name.lower()
    return (
        "tests" in parts
        or "test" in parts
        or filename.startswith("test_")
        or ".test." in filename
        or ".spec." in filename
        or filename.endswith(
            (
                "_test.go",
                "_test.rs",
                "_test.py",
                "_test.js",
                "_test.ts",
                "_test.tsx",
                ".test.tsx",
                ".spec.tsx",
                "test.java",
                "test.kt",
                "test.cpp",
                "test.cc",
                "test.cxx",
                "test.hpp",
            )
        )
    )


def _read_workspace_file(workspace: str, rel: str) -> str:
    with open(os.path.join(workspace, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _nonempty_source_line_count(text: str) -> int:
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("#", "//", "/*", "*", "*/")):
            continue
        count += 1
    return count


def _implementation_depth_metrics(workspace: str, inventory: dict[str, Any]) -> dict[str, Any]:
    production_files: list[str] = []
    test_files: list[str] = []
    production_lines = 0
    test_lines = 0
    behavior_symbols = 0
    branches = 0
    test_assertions = 0
    placeholder_hits: list[str] = []

    for rel in inventory.get("source_files", []):
        if not isinstance(rel, str) or _is_depth_excluded_path(rel):
            continue
        try:
            text = _read_workspace_file(workspace, rel)
        except OSError:
            continue
        line_count = _nonempty_source_line_count(text)
        if _is_test_source_path(rel):
            test_files.append(rel)
            test_lines += line_count
            test_assertions += len(_TEST_ASSERTION_RE.findall(text))
            continue
        production_files.append(rel)
        production_lines += line_count
        behavior_symbols += len(_BEHAVIOR_SYMBOL_RE.findall(text))
        branches += len(_BRANCH_RE.findall(text))
        if _has_unfinished_placeholder(text):
            placeholder_hits.append(rel)

    return {
        "production_source_files": len(production_files),
        "production_source_lines": production_lines,
        "test_source_files": len(test_files),
        "test_source_lines": test_lines,
        "behavior_symbol_count": behavior_symbols,
        "branch_count": branches,
        "test_assertion_count": test_assertions,
        "placeholder_hits": placeholder_hits[:10],
        "production_files": production_files[:40],
        "test_files": test_files[:30],
    }


def _load_workspace_catalog_contract(workspace: str) -> dict[str, Any]:
    path = Path(workspace) / ".polaris" / "catalog_contract.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_level_contract(
    *,
    workspace: str,
    project: dict[str, Any] | None = None,
    level_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_payload = project or {}
    catalog_contract = _load_workspace_catalog_contract(workspace)
    level = project_payload.get("level") or catalog_contract.get("level") or 1

    for candidate in (
        level_contract,
        project_payload.get("level_contract"),
        project_payload.get("factory_bench_level_contract"),
        catalog_contract.get("level_contract"),
    ):
        if isinstance(candidate, dict) and candidate.get("minimums"):
            return candidate

    return build_factory_bench_level_contract(level, project=project_payload or catalog_contract)


def _check_implementation_depth(
    workspace: str,
    inventory: dict[str, Any],
    *,
    level_contract: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    metrics = _implementation_depth_metrics(workspace, inventory)
    resolved_contract = _resolve_level_contract(workspace=workspace, level_contract=level_contract)
    minimums = extract_level_contract_minimums(resolved_contract, level=resolved_contract.get("level"))
    failures: list[str] = []

    if int(metrics["production_source_files"]) < minimums["min_prod_files"]:
        failures.append(f"production_source_files={metrics['production_source_files']} < {minimums['min_prod_files']}")
    if int(metrics["production_source_lines"]) < minimums["min_prod_lines"]:
        failures.append(f"production_source_lines={metrics['production_source_lines']} < {minimums['min_prod_lines']}")
    if int(metrics["behavior_symbol_count"]) < minimums["min_behavior_symbols"]:
        failures.append(
            f"behavior_symbol_count={metrics['behavior_symbol_count']} < {minimums['min_behavior_symbols']}"
        )
    if int(metrics["branch_count"]) < minimums["min_branch_count"]:
        failures.append(f"branch_count={metrics['branch_count']} < {minimums['min_branch_count']}")
    if int(metrics["test_source_files"]) < minimums["min_test_files"]:
        failures.append(f"test_source_files={metrics['test_source_files']} < {minimums['min_test_files']}")
    if int(metrics["test_assertion_count"]) < minimums["min_test_assertions"]:
        failures.append(f"test_assertion_count={metrics['test_assertion_count']} < {minimums['min_test_assertions']}")
    placeholder_hits = metrics["placeholder_hits"]
    if placeholder_hits:
        failures.append("placeholder_or_stub_markers=" + ",".join(str(item) for item in placeholder_hits[:3]))

    plan = _read_plan_json(workspace)
    declared_source_targets = _extract_declared_source_targets(workspace, plan)
    _, missing_targets = compute_declared_source_target_coverage(workspace, declared_source_targets)
    if missing_targets:
        failures.append(f"missing_declared_source_targets={len(missing_targets)}")

    detail = (
        "implementation depth metrics: "
        f"prod_files={metrics['production_source_files']}, "
        f"prod_lines={metrics['production_source_lines']}, "
        f"test_files={metrics['test_source_files']}, "
        f"test_assertions={metrics['test_assertion_count']}, "
        f"behavior_symbols={metrics['behavior_symbol_count']}, "
        f"branches={metrics['branch_count']}, "
        f"level={resolved_contract.get('level')}, "
        f"minimums={minimums}"
    )
    if failures:
        return False, detail + "; failures: " + "; ".join(failures[:8])
    return True, detail


def _should_add_implementation_depth_check(configured_checks: list[str]) -> bool:
    normalized = [str(item or "").strip().lower() for item in configured_checks]
    if "implementation_depth" in normalized:
        return False
    return any(item.startswith(("source_target_coverage:", "content_any:")) for item in normalized)


def _content_keywords_from_checks(configured_checks: list[str]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for check in configured_checks:
        normalized = str(check or "").strip()
        if not normalized.lower().startswith("content_any:"):
            continue
        for raw in normalized.split(":", 1)[1].split("|"):
            token = re.sub(r"[^a-z0-9_]+", "_", raw.strip().lower()).strip("_")
            if token and token not in seen:
                keywords.append(token)
                seen.add(token)
    return keywords


def _check_feature_keyword_structure(
    workspace: str,
    inventory: dict[str, Any],
    *,
    configured_checks: list[str],
    level_contract: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    keywords = _content_keywords_from_checks(configured_checks)
    if not keywords:
        return True, "feature keyword structure skipped: no content_any keywords"

    resolved_contract = _resolve_level_contract(workspace=workspace, level_contract=level_contract)
    minimums = extract_level_contract_minimums(resolved_contract, level=resolved_contract.get("level"))
    required = min(len(keywords), max(1, min(3, int(minimums.get("min_primary_entities", 1)))))
    structural_texts: list[str] = []
    production_files = []
    for rel in inventory.get("source_files", []):
        if not isinstance(rel, str) or _is_depth_excluded_path(rel) or _is_test_source_path(rel):
            continue
        production_files.append(rel)
        structural_texts.append(rel.lower())
        try:
            text = _read_workspace_file(workspace, rel)
        except OSError:
            continue
        for line in text.splitlines():
            if _STRUCTURAL_SYMBOL_LINE_RE.search(line):
                structural_texts.append(line.lower())

    structural_haystack = "\n".join(structural_texts)
    matched = [keyword for keyword in keywords if keyword in structural_haystack]
    ok = len(matched) >= required
    detail = (
        "feature keyword structure: "
        f"matched={matched}, required>={required}, keywords={keywords}, prod_files={len(production_files)}"
    )
    if ok:
        return True, detail
    return False, detail + "; feature keywords must appear in production file/module/type/function names"


def _should_add_feature_keyword_structure_check(configured_checks: list[str]) -> bool:
    normalized = [str(item or "").strip().lower() for item in configured_checks]
    if "feature_keyword_structure" in normalized:
        return False
    return bool(_content_keywords_from_checks(configured_checks))


def _iter_files(workspace: str, suffix: str) -> list[str]:
    matches: list[str] = []
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.lower().endswith(suffix):
                matches.append(os.path.join(current_root, filename))
    return sorted(matches)


def _iter_files_any(workspace: str, suffixes: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    lowered = tuple(suffix.lower() for suffix in suffixes)
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if filename.lower().endswith(lowered):
                matches.append(os.path.join(current_root, filename))
    return sorted(matches)


def _rel_paths(workspace: str, paths: list[str]) -> list[str]:
    return [os.path.relpath(path, workspace).replace("\\", "/") for path in paths]


def _tool_unavailable_detail(tool: str, language: str, count: int) -> str:
    return f"{tool} unavailable — {count} {language} file(s) present but compile check could not run"


def _typescript_compiler(workspace: str) -> str | None:
    bin_name = "tsc.cmd" if os.name == "nt" else "tsc"
    local_tsc = os.path.join(workspace, "node_modules", ".bin", bin_name)
    if os.path.exists(local_tsc):
        return local_tsc
    return shutil.which("tsc")


def _typescript_syntax_node_env(workspace: str) -> dict[str, str]:
    env = os.environ.copy()
    node_paths: list[str] = []
    local_node_modules = os.path.join(workspace, "node_modules")
    if os.path.isdir(local_node_modules):
        node_paths.append(local_node_modules)
    repo_node_modules = os.path.join(Path(__file__).resolve().parents[5], "node_modules")
    if os.path.isdir(repo_node_modules):
        node_paths.append(repo_node_modules)
    existing = env.get("NODE_PATH", "").strip()
    if existing:
        node_paths.append(existing)
    if node_paths:
        env["NODE_PATH"] = os.pathsep.join(node_paths)
    return env


def _check_py_compile(workspace: str) -> tuple[bool, str]:
    failures: list[str] = []
    py_files = _iter_files(workspace, ".py")
    if not py_files:
        return False, "no .py files found"
    for path in py_files:
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{os.path.relpath(path, workspace)}: {exc.msg.splitlines()[-1] if exc.msg else exc}")
        except (OSError, UnicodeDecodeError) as exc:
            failures.append(f"{os.path.relpath(path, workspace)}: {exc}")
    if failures:
        return False, f"{len(failures)}/{len(py_files)} files fail to compile: " + "; ".join(failures[:3])
    return True, f"{len(py_files)} python files compile"


def _check_html(workspace: str) -> tuple[bool, str]:
    html_files = _iter_files(workspace, ".html")
    for path in html_files:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "<html" in fh.read().lower():
                    return True, f"html page present: {os.path.relpath(path, workspace)}"
        except OSError:
            continue
    return False, "no .html file containing <html> found"


def _check_js_syntax(workspace: str) -> tuple[bool, str]:
    js_files = [p for p in _iter_files(workspace, ".js") if not p.endswith(".min.js")]
    if not js_files:
        # Single-file HTML apps with inline <script> are a legitimate
        # implementation shape (live L2-10 r4: a complete one-file Markdown
        # previewer); judge the inline script presence instead of failing.
        for html_path in _iter_files(workspace, ".html"):
            try:
                with open(html_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            inline_match = re.search(
                r"<script\b[^>]*>(?P<body>.*?)</script>",
                content,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if inline_match and inline_match.group("body").strip():
                rel = os.path.relpath(html_path, workspace)
                return True, f"no standalone .js; inline <script> present in {rel}"
        return False, "no .js files found"
    node = shutil.which("node")
    if not node:
        return True, f"node unavailable — {len(js_files)} js files present (syntax unchecked)"
    failures: list[str] = []
    for path in js_files:
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=30, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            failures.append(f"{os.path.relpath(path, workspace)}: {detail[0] if detail else 'syntax error'}")
    if failures:
        return False, f"{len(failures)}/{len(js_files)} js files fail --check: " + "; ".join(failures[:3])
    return True, f"{len(js_files)} js files pass node --check"


def _check_ts_syntax(workspace: str) -> tuple[bool, str]:
    ts_files = [path for path in _iter_files_any(workspace, (".ts", ".tsx")) if not path.endswith(".d.ts")]
    if not ts_files:
        return False, "no .ts/.tsx files found"
    node = shutil.which("node")
    if not node:
        return False, _tool_unavailable_detail("node", "TypeScript", len(ts_files))
    rel_files = _rel_paths(workspace, ts_files[:120])
    proc = subprocess.run(
        [node, "-e", _TS_SYNTAX_CHECKER_JS],
        cwd=workspace,
        input=json.dumps({"files": rel_files}, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=_typescript_syntax_node_env(workspace),
    )
    detail_payload: dict[str, Any] = {}
    stdout = (proc.stdout or "").strip()
    if stdout:
        try:
            detail_payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            detail_payload = {}
    if proc.returncode != 0:
        if detail_payload.get("unavailable"):
            return False, str(
                detail_payload.get("detail") or _tool_unavailable_detail("typescript", "TypeScript", len(ts_files))
            )
        detail = str(detail_payload.get("detail") or "").strip()
        if not detail:
            lines = (proc.stderr or proc.stdout).strip().splitlines()
            detail = lines[0] if lines else "unknown TypeScript syntax error"
        return False, "TypeScript syntax check failed: " + detail
    return True, f"{len(ts_files)} TypeScript files pass TypeScript syntax parser"


def _check_go_compile(workspace: str) -> tuple[bool, str]:
    go_files = _iter_files(workspace, ".go")
    if not go_files:
        return False, "no .go files found"
    go = shutil.which("go")
    if not go:
        return False, _tool_unavailable_detail("go", "Go", len(go_files))
    if os.path.exists(os.path.join(workspace, "go.mod")):
        cmd = [go, "test", "./..."]
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            return False, "go test compile failed: " + (detail[0] if detail else "unknown Go error")
        return True, f"{len(go_files)} Go files compile via go test"

    go_dirs = sorted({os.path.dirname(path) for path in go_files})
    env = os.environ.copy()
    env["GO111MODULE"] = "off"
    for go_dir in go_dirs:
        rel_dir = os.path.relpath(go_dir, workspace).replace("\\", "/")
        proc = subprocess.run(
            [go, "test"],
            cwd=go_dir,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            return False, f"go test compile failed in {rel_dir}: " + (detail[0] if detail else "unknown Go error")
    return True, f"{len(go_files)} Go files compile via per-directory go test"


def _check_rust_compile(workspace: str) -> tuple[bool, str]:
    rust_files = _iter_files(workspace, ".rs")
    if not rust_files:
        return False, "no .rs files found"
    cargo = shutil.which("cargo")
    if os.path.exists(os.path.join(workspace, "Cargo.toml")) and cargo:
        proc = subprocess.run(
            [cargo, "check", "--quiet"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            return False, "cargo check failed: " + (detail[0] if detail else "unknown Rust error")
        return True, f"{len(rust_files)} Rust files compile via cargo check"
    rustc = shutil.which("rustc")
    if not rustc:
        return False, _tool_unavailable_detail("rustc/cargo", "Rust", len(rust_files))
    rels = _rel_paths(workspace, rust_files)
    root = next(
        (rel for rel in ("src/main.rs", "main.rs", "src/lib.rs", "lib.rs") if rel in rels),
        rels[0],
    )
    proc = subprocess.run(
        [rustc, "--edition=2021", "--emit=metadata", root],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "rustc metadata compile failed: " + (detail[0] if detail else "unknown Rust error")
    return True, f"{len(rust_files)} Rust files compile via rustc metadata"


def _check_cpp_compile(workspace: str) -> tuple[bool, str]:
    cpp_files = _iter_files_any(workspace, (".cc", ".cpp", ".cxx"))
    if not cpp_files:
        return False, "no C++ source files found"
    compiler = shutil.which("g++") or shutil.which("c++")
    if not compiler:
        return False, _tool_unavailable_detail("g++/c++", "C++", len(cpp_files))
    failures: list[str] = []
    for rel in _rel_paths(workspace, cpp_files[:80]):
        proc = subprocess.run(
            [compiler, "-std=c++17", "-fsyntax-only", rel],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            failures.append(f"{rel}: {detail[0] if detail else 'syntax error'}")
    if failures:
        return False, f"{len(failures)}/{len(cpp_files)} C++ files fail syntax check: " + "; ".join(failures[:3])
    return True, f"{len(cpp_files)} C++ files pass g++ -fsyntax-only"


def _check_java_compile(workspace: str) -> tuple[bool, str]:
    java_files = _iter_files(workspace, ".java")
    if not java_files:
        return False, "no .java files found"
    javac = shutil.which("javac")
    if not javac:
        return False, _tool_unavailable_detail("javac", "Java", len(java_files))
    with tempfile.TemporaryDirectory(prefix="polaris-factory-javac-") as out_dir:
        proc = subprocess.run(
            [javac, "-encoding", "UTF-8", "-d", out_dir, *_rel_paths(workspace, java_files[:120])],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, "javac compile failed: " + (detail[0] if detail else "unknown Java error")
    return True, f"{len(java_files)} Java files compile via javac"


def _is_local_script_reference(token: str) -> bool:
    if not token or token.startswith("-"):
        return False
    normalized = token.replace("\\", "/")
    if "://" in normalized:
        return False
    _, ext = os.path.splitext(normalized)
    return "/" in normalized or ext.lower() in _SCRIPT_PATH_EXTENSIONS


def _is_local_script_option_reference(token: str) -> bool:
    normalized = token.replace("\\", "/")
    _, ext = os.path.splitext(normalized)
    return normalized.startswith(("./", "../", "/")) or ext.lower() in _SCRIPT_PATH_EXTENSIONS


def _script_reference_exists(workspace: str, token: str) -> bool:
    normalized = token.replace("\\", "/")
    if os.path.isabs(normalized):
        return os.path.exists(normalized)
    exact = os.path.join(workspace, normalized)
    if os.path.exists(exact):
        return True
    base, ext = os.path.splitext(exact)
    if ext:
        return False
    return any(os.path.exists(base + suffix) for suffix in _SCRIPT_PATH_EXTENSIONS)


def _script_builds_before_interpreter(tokens: list[str], interpreter_index: int) -> bool:
    return bool(_SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(" ".join(tokens[:interpreter_index])))


def _is_build_output_reference(candidate: str) -> bool:
    normalized = candidate.replace("\\", "/").lstrip("./")
    first_segment = normalized.split("/", 1)[0]
    return first_segment in _BUILD_OUTPUT_DIR_NAMES


def _script_lifecycle_can_build_output(scripts: dict[str, Any], script_name: str, tokens: list[str]) -> bool:
    pre_script = scripts.get(f"pre{script_name}")
    command = " ".join(tokens)
    if isinstance(pre_script, str) and _SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(pre_script):
        return True
    if _SCRIPT_BUILDS_BEFORE_ENTRYPOINT_RE.search(command):
        return True
    return script_name in {"start", "serve", "preview"} and isinstance(scripts.get("build"), str)


def _missing_package_script_entrypoints(
    workspace: str,
    script_name: str,
    command: str,
    *,
    scripts: dict[str, Any] | None = None,
) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return [f"script {script_name!r} has invalid shell syntax: {exc}"]
    all_scripts = scripts or {}
    missing: list[str] = []
    index = 0
    while index < len(tokens):
        interpreter_index = index
        token = os.path.basename(tokens[index])
        if token not in _SCRIPT_INTERPRETERS:
            index += 1
            continue
        index += 1
        while index < len(tokens):
            candidate = tokens[index]
            if candidate in _SHELL_OPERATORS:
                break
            if token == "node" and candidate in {"-e", "--eval", "-p", "--print"}:
                index += 2
                continue
            if token == "node" and candidate in {"-r", "--require", "--import", "--loader"}:
                if index + 1 < len(tokens):
                    option_value = tokens[index + 1]
                    if _is_local_script_option_reference(option_value) and not _script_reference_exists(
                        workspace, option_value
                    ):
                        missing.append(f"script {script_name!r} references missing local entrypoint: {option_value}")
                index += 2
                continue
            if candidate.startswith("-"):
                index += 1
                continue
            if _is_local_script_reference(candidate) and not _script_reference_exists(workspace, candidate):
                if _script_builds_before_interpreter(tokens, interpreter_index):
                    break
                if _is_build_output_reference(candidate) and _script_lifecycle_can_build_output(
                    all_scripts,
                    script_name,
                    tokens,
                ):
                    break
                missing.append(f"script {script_name!r} references missing local entrypoint: {candidate}")
            break
    return missing


def _placeholder_package_script_reason(script_name: str, command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens:
        return f"script {script_name!r} is empty"
    first_command = os.path.basename(tokens[0])
    if first_command not in _PLACEHOLDER_SCRIPT_COMMANDS:
        return ""
    for index, token in enumerate(tokens):
        if token not in _SHELL_OPERATORS or index + 1 >= len(tokens):
            continue
        next_command = os.path.basename(tokens[index + 1])
        if next_command not in {*_PLACEHOLDER_SCRIPT_COMMANDS, "exit", "true"}:
            return ""
    return f"script {script_name!r} is a placeholder command: {command}"


def _check_package_scripts(workspace: str) -> tuple[bool, str]:
    from polaris.kernelone.quality import check_package_scripts

    result = check_package_scripts(workspace)
    return result.ok, result.detail


def run_checks(
    workspace: str,
    checks: list[str],
    *,
    level_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the project's deterministic check list; unknown kinds fail closed."""
    inventory = collect_workspace_inventory(workspace)
    results: list[dict[str, Any]] = []
    for check in checks:
        kind = check.strip()
        ok = False
        detail = ""
        if kind == "py_compile":
            ok, detail = _check_py_compile(workspace)
        elif kind == "html":
            ok, detail = _check_html(workspace)
        elif kind == "js_syntax":
            ok, detail = _check_js_syntax(workspace)
        elif kind == "ts_syntax":
            ok, detail = _check_ts_syntax(workspace)
        elif kind == "go_compile":
            ok, detail = _check_go_compile(workspace)
        elif kind == "rust_compile":
            ok, detail = _check_rust_compile(workspace)
        elif kind == "cpp_compile":
            ok, detail = _check_cpp_compile(workspace)
        elif kind == "java_compile":
            ok, detail = _check_java_compile(workspace)
        elif kind == "package_scripts":
            ok, detail = _check_package_scripts(workspace)
        elif kind == "implementation_depth":
            ok, detail = _check_implementation_depth(workspace, inventory, level_contract=level_contract)
        elif kind == "feature_keyword_structure":
            ok, detail = _check_feature_keyword_structure(
                workspace,
                inventory,
                configured_checks=checks,
                level_contract=level_contract,
            )
        elif kind == "runnable_any":
            # Shape-neutral runnability: briefs like "typing tester with live
            # highlighting" are legitimately delivered as either a Python
            # program or a web app (live L2-11 r1: PM chose web, the fixture
            # demanded py_compile). Pass when either shape is fully runnable.
            py_ok, py_detail = _check_py_compile(workspace)
            if py_ok:
                ok, detail = True, f"python shape: {py_detail}"
            else:
                html_ok, html_detail = _check_html(workspace)
                js_ok, js_detail = _check_js_syntax(workspace)
                if html_ok and js_ok:
                    ok, detail = True, f"web shape: {html_detail}; {js_detail}"
                else:
                    ok = False
                    detail = f"no runnable shape: py({py_detail}); web({html_detail}; {js_detail})"
        elif kind.startswith("content_any:"):
            # Feature probe: at least one SOURCE file must mention the pattern.
            # Scaffold files (package.json, tsconfig.json, etc.) are excluded
            # to prevent hollow-scaffold deliveries from passing on metadata
            # keywords alone (e.g. "firefly" in package.json description).
            pattern_text = kind.split(":", 1)[1]
            try:
                probe = re.compile(pattern_text, re.IGNORECASE)
            except re.error as exc:
                results.append({"check": kind, "ok": False, "detail": f"bad pattern: {exc}"})
                continue
            matched_file = ""
            for rel in inventory["source_files"]:
                try:
                    with open(os.path.join(workspace, rel), encoding="utf-8", errors="replace") as fh:
                        if probe.search(fh.read()):
                            matched_file = rel
                            break
                except OSError:
                    continue
            ok = bool(matched_file)
            detail = (
                f"feature pattern {pattern_text!r} found in {matched_file}"
                if ok
                else f"feature pattern {pattern_text!r} not found in any source file"
            )
        elif kind.startswith("source_target_coverage:"):
            # Source target coverage gate: verifies that source files exist under
            # expected directories (e.g. src/**/*.ts). Prevents projects from
            # passing with only scaffold files (package.json, tsconfig.json, README).
            # Format: source_target_coverage:<glob_pattern>
            # Example: source_target_coverage:src/**/*.ts
            target_pattern = kind.split(":", 1)[1].strip()
            if not target_pattern:
                results.append({"check": kind, "ok": False, "detail": "empty source target pattern"})
                continue
            import glob as globmod

            abs_pattern = os.path.join(workspace, target_pattern)
            matched_paths = sorted(globmod.glob(abs_pattern, recursive=True))
            # Filter to actual source files only
            matched_source = [
                os.path.relpath(p, workspace).replace("\\", "/") for p in matched_paths if os.path.isfile(p)
            ]
            ok = bool(matched_source)
            detail = (
                f"source target {target_pattern!r}: {len(matched_source)} file(s) found"
                if ok
                else f"source target {target_pattern!r}: no source files found — "
                "Director only produced scaffold files, not core source code"
            )
        elif kind.startswith("min_files:"):
            try:
                minimum = int(kind.split(":", 1)[1])
            except ValueError:
                minimum = 1
            count = len(inventory["source_files"])
            ok = count >= minimum
            detail = f"{count} source files (need >= {minimum})"
        else:
            detail = f"unknown check kind: {kind}"
        results.append({"check": kind, "ok": ok, "detail": detail})
    return results


def build_factory_audit_record(
    *,
    project: dict[str, Any],
    workspace: str,
    artifact_globs: dict[str, list[str]] | None = None,
    chain_terminal: bool = True,
    chain_status: str = "",
    chain_phase: str = "",
) -> dict[str, Any]:
    """Assemble the full per-project audit record.

    ``artifact_globs`` maps artifact kinds (plan/blueprint/verdict) to lists of
    workspace-relative paths discovered by the runner (the chain's artifact
    layout is runner-configured, not hardcoded here).

    ``chain_terminal`` indicates whether the chain reached a terminal state
    (completed/failed/cancelled) before this audit was taken.  When *False* the
    snapshot is provisional and must NOT be treated as a final project verdict.

    ``chain_status`` and ``chain_phase`` capture the terminal status/phase for
    traceability.
    """
    inventory = collect_workspace_inventory(workspace)
    configured_checks = list(project.get("checks") or [])
    level_contract = _resolve_level_contract(workspace=workspace, project=project)
    supplemental_checks = []
    if os.path.exists(os.path.join(workspace, "package.json")) and "package_scripts" not in configured_checks:
        supplemental_checks.append("package_scripts")
    if _should_add_implementation_depth_check(configured_checks):
        supplemental_checks.append("implementation_depth")
    if _should_add_feature_keyword_structure_check(configured_checks):
        supplemental_checks.append("feature_keyword_structure")
    checks = run_checks(workspace, configured_checks + supplemental_checks, level_contract=level_contract)
    artifacts = artifact_globs or {}

    # Read PM plan and extract declared source targets
    plan = _read_plan_json(workspace)
    declared_source_targets = _extract_declared_source_targets(workspace, plan)
    _, missing_targets = compute_declared_source_target_coverage(workspace, declared_source_targets)
    implementation_depth_check = next((item for item in checks if item.get("check") == "implementation_depth"), None)

    # Snapshot kind: "terminal" when chain reached a final state, "non_terminal"
    # when the chain was still running / errored before a definitive outcome.
    snapshot_kind = "terminal" if chain_terminal else "non_terminal"

    return {
        "schema_version": FACTORY_AUDIT_SCHEMA_VERSION,
        "project_id": str(project.get("id") or ""),
        "level": int(project.get("level") or 0),
        "level_contract": level_contract,
        "domain": str(project.get("domain") or ""),
        "title": str(project.get("title") or ""),
        "code_file_count": len(inventory["code_files"]),
        "source_file_count": len(inventory["source_files"]),
        "code_files": inventory["code_files"][:60],
        "source_files": inventory["source_files"][:60],
        "doc_files": inventory["doc_files"][:30],
        "artifacts": {kind: paths[:10] for kind, paths in artifacts.items()},
        "has_plan_doc": bool(artifacts.get("plan")),
        "has_blueprint_doc": bool(artifacts.get("blueprint")),
        "has_qa_verdict": bool(artifacts.get("verdict")),
        "checks": checks,
        "all_checks_passed": bool(configured_checks) and all(c["ok"] for c in checks),
        "implementation_depth": implementation_depth_check or {},
        "declared_source_targets": declared_source_targets,
        "declared_source_target_count": len(declared_source_targets),
        "missing_declared_source_targets": missing_targets,
        "missing_declared_source_target_count": len(missing_targets),
        "pm_plan_missing_source_targets": plan is not None and not declared_source_targets,
        "audit_snapshot_kind": snapshot_kind,
        "audit_terminal": bool(chain_terminal),
        "terminal_status": chain_status,
        "terminal_phase": chain_phase,
    }


def _read_plan_json(workspace: str) -> dict[str, Any] | None:
    """Read plan.json from workspace, trying multiple candidate paths.

    Returns the parsed JSON dict or None if not found/invalid.
    """
    candidates = [
        os.path.join(workspace, ".polaris", "docs", "product", "plan.json"),
        os.path.join(workspace, ".polaris", "runtime", "tasks", "plan.json"),
        os.path.join(workspace, "tasks", "plan.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
    return None


def _extract_declared_source_targets(workspace: str, plan: dict[str, Any] | None) -> list[str]:
    """Extract declared source file targets from PM plan.

    Scans tasks[].target_files for entries that look like source code paths
    (containing src/ or having a source file extension).
    """
    if not plan:
        return []

    source_extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".c", ".h", ".cpp", ".rs", ".go", ".java"}
    declared: list[str] = []

    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return []

    for task in tasks:
        if not isinstance(task, dict):
            continue
        target_files = task.get("target_files") or task.get("files") or task.get("paths") or []
        if not isinstance(target_files, list):
            continue
        for tf in target_files:
            if not isinstance(tf, str) or not tf.strip():
                continue
            tf_clean = tf.strip().replace("\\", "/")
            _, ext = os.path.splitext(tf_clean)
            ext_lower = ext.lower()
            # Include if it has a source extension or is under src/ directory
            if (
                ext_lower in source_extensions or "/src/" in tf_clean or tf_clean.startswith("src/")
            ) and tf_clean not in declared:
                declared.append(tf_clean)

    return sorted(declared)


def compute_declared_source_target_coverage(
    workspace: str,
    declared_targets: list[str],
) -> tuple[list[str], list[str]]:
    """Check which declared source targets exist in workspace.

    Returns (present_targets, missing_targets).
    """
    if not declared_targets:
        return [], []

    present: list[str] = []
    missing: list[str] = []

    for target in declared_targets:
        # Normalize path separators
        normalized = target.replace("\\", "/")
        full_path = os.path.join(workspace, normalized)
        if os.path.isfile(full_path):
            present.append(normalized)
        else:
            missing.append(normalized)

    return present, missing


def aggregate_factory_audits(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Run-level aggregate over per-project audit records (per-level breakdown)."""
    total = len(records)
    by_level: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = by_level.setdefault(f"L{record.get('level')}", {"total": 0, "passed": 0})
        bucket["total"] += 1
        if record.get("all_checks_passed"):
            bucket["passed"] += 1
    depth_records = [
        depth
        for record in records
        for depth in (record.get("implementation_depth"),)
        if isinstance(depth, dict) and (depth.get("check") == "implementation_depth" or "ok" in depth)
    ]
    return {
        "schema_version": FACTORY_AUDIT_SCHEMA_VERSION,
        "total": total,
        "all_checks_passed": sum(1 for r in records if r.get("all_checks_passed")),
        "with_plan_doc": sum(1 for r in records if r.get("has_plan_doc")),
        "with_blueprint_doc": sum(1 for r in records if r.get("has_blueprint_doc")),
        "with_qa_verdict": sum(1 for r in records if r.get("has_qa_verdict")),
        "with_source_files": sum(1 for r in records if r.get("source_file_count", 0) > 0),
        "zero_source_files": sum(1 for r in records if r.get("source_file_count", 0) == 0),
        "implementation_depth_checked": len(depth_records),
        "implementation_depth_passed": sum(1 for depth in depth_records if depth.get("ok") is True),
        "implementation_depth_failed": sum(1 for depth in depth_records if depth.get("ok") is not True),
        "by_level": dict(sorted(by_level.items())),
    }
