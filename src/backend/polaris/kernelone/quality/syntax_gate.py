"""Generic, language-agnostic syntax gate (single source of truth).

A grep/clause-based step verify can pass even when the produced code does not
parse: live I3-r15/r18, ``main.js`` satisfied every grep clause but
``node --check`` failed on a stray ``;`` inside an object literal
(``alive: true;``), so a broken, non-running product shipped. This module runs
the language's own syntax checker so a non-parsing file is caught at the LIVE
verify/QA gate, not only in post-hoc forensics.

Fail-closed semantics: ``ok`` is False ONLY on a DEFINITE parse failure
(``checked is True`` and the checker returned non-zero). Absence of a checker, a
missing tool, a missing file, or a timeout yields ``checked=False, ok=True`` —
the gate must not block on "could not prove it's broken", only on "proven
broken". Generic: extension → checker mapping carries no project specifics.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

# Extension groups → the checker command (the file path is appended).
_SYNTAX_CHECKERS: dict[tuple[str, ...], list[str]] = {
    (".js", ".mjs", ".cjs"): ["node", "--check"],
    (".ts", ".tsx"): [
        "tsc",
        "--noEmit",
        "--pretty",
        "false",
        "--skipLibCheck",
        "--target",
        "ES2020",
        "--module",
        "commonjs",
        "--lib",
        "ES2020,DOM",
    ],
    (".py",): [sys.executable, "-m", "py_compile"],
    (".go",): ["gofmt", "-e"],
    (".rs",): ["rustc", "--crate-type", "lib", "--emit", "metadata"],
    (".cpp", ".cc", ".cxx"): ["g++", "-fsyntax-only"],
    (".java",): ["javac", "-Xlint:none", "-proc:none"],
}

_DEFAULT_TIMEOUT_SECONDS = 20
_CONTENT_PRECOMMIT_EXTENSIONS = frozenset({".go", ".js", ".mjs", ".cjs", ".py", ".ts", ".tsx"})
_TS_PARSE_DIAGNOSTICS_SCRIPT = r"""
const ts = require(process.argv[1]);
const fs = require("fs");
const filename = process.argv[2];
const source = fs.readFileSync(filename, "utf8");
const kind = filename.toLowerCase().endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
const sourceFile = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, kind);
for (const diagnostic of sourceFile.parseDiagnostics) {
  const position = diagnostic.start === undefined
    ? { line: 0, character: 0 }
    : sourceFile.getLineAndCharacterOfPosition(diagnostic.start);
  const message = ts.flattenDiagnosticMessageText(diagnostic.messageText, "\n");
  process.stderr.write(
    `${filename}(${position.line + 1},${position.character + 1}): error TS${diagnostic.code}: ${message}\n`
  );
}
process.exit(sourceFile.parseDiagnostics.length > 0 ? 1 : 0);
"""


def _typescript_compiler_api_path(tsc_tool: str) -> str | None:
    """Resolve the compiler API shipped beside the available ``tsc`` binary."""

    executable = shutil.which(tsc_tool)
    if not executable:
        return None
    real_executable = os.path.realpath(executable)
    candidate = os.path.normpath(os.path.join(os.path.dirname(real_executable), "..", "lib", "typescript.js"))
    return candidate if os.path.isfile(candidate) else None


@dataclass(frozen=True)
class SyntaxCheckResult:
    """Outcome of a single-file syntax check.

    ``checked`` — an actual checker ran. ``ok`` — no definite parse failure (True
    whenever ``checked`` is False, so a naive ``if not result.ok`` gate rejects
    only proven-broken files). ``error`` — the checker's message (truncated) on a
    real failure; ``reason`` — why no check ran (tool unavailable, etc.).
    """

    path: str
    checked: bool
    ok: bool
    error: str
    reason: str


def syntax_checker_for(filename: str) -> list[str] | None:
    """Return the checker command for ``filename``'s extension, or None."""
    ext = os.path.splitext(filename)[1].lower()
    return next((list(base) for exts, base in _SYNTAX_CHECKERS.items() if ext in exts), None)


def check_file_syntax(path: str, *, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> SyntaxCheckResult:
    """Run the language's own syntax checker on ``path`` (UTF-8 paths)."""
    cmd = syntax_checker_for(path)
    if cmd is None:
        return SyntaxCheckResult(path=path, checked=False, ok=True, error="", reason="no checker for extension")
    tool = cmd[0]
    if tool != sys.executable and shutil.which(tool) is None:
        return SyntaxCheckResult(path=path, checked=False, ok=True, error="", reason=f"{tool} unavailable")
    if not os.path.isfile(path):
        return SyntaxCheckResult(path=path, checked=False, ok=True, error="", reason="file not found")
    try:
        cwd = os.path.dirname(os.path.abspath(path)) or None
        ext = os.path.splitext(path)[1].lower()
        if ext in {".ts", ".tsx"}:
            compiler_api = _typescript_compiler_api_path(tool)
            node = shutil.which("node")
            if compiler_api is None or node is None:
                return SyntaxCheckResult(
                    path=path,
                    checked=False,
                    ok=True,
                    error="",
                    reason="TypeScript compiler API unavailable",
                )
            # A syntax gate must inspect only this source file's parser output.
            # Running ``tsc`` as a project compiler pulls in imports/node_modules
            # and applies an arbitrary module mode; both produced live false
            # failures (dependency TS1xxx and valid ``import.meta`` under
            # CommonJS), causing Director to rewrite correct product code.
            proc = subprocess.run(
                [node, "-e", _TS_PARSE_DIAGNOSTICS_SCRIPT, compiler_api, path],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        elif ext in {".rs", ".java"}:
            with tempfile.TemporaryDirectory(prefix="polaris-syntax-") as temp_dir:
                cmdline = [*cmd, "--out-dir" if ext == ".rs" else "-d", temp_dir, path]
                proc = subprocess.run(
                    cmdline,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
        else:
            proc = subprocess.run(
                [*cmd, path],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SyntaxCheckResult(path=path, checked=False, ok=True, error="", reason=str(exc))
    ok = proc.returncode == 0
    raw_error = (proc.stderr or proc.stdout or "").strip()
    error = "" if ok else raw_error[:500]
    return SyntaxCheckResult(path=path, checked=True, ok=ok, error=error, reason="")


def check_content_syntax(
    filename: str,
    content: str,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> SyntaxCheckResult:
    """Parse candidate UTF-8 source bytes without changing the workspace.

    This is intentionally limited to checkers whose single-file invocation is
    parse-only (or effectively parse-only).  C/C++/Rust/Java are excluded here:
    compiling an isolated temporary file can report missing project imports or
    modules and would reject valid edits for semantic, not syntax, reasons.
    """

    suffix = os.path.splitext(str(filename or ""))[1].lower()
    if suffix not in _CONTENT_PRECOMMIT_EXTENSIONS:
        return SyntaxCheckResult(
            path=str(filename),
            checked=False,
            ok=True,
            error="",
            reason="no safe precommit checker for extension",
        )
    try:
        with tempfile.TemporaryDirectory(prefix="polaris-syntax-candidate-") as temp_dir:
            candidate = os.path.join(temp_dir, f"candidate{suffix}")
            with open(candidate, "w", encoding="utf-8", newline="") as handle:
                handle.write(str(content))
            result = check_file_syntax(candidate, timeout_seconds=timeout_seconds)
    except (OSError, UnicodeError) as exc:
        return SyntaxCheckResult(
            path=str(filename),
            checked=False,
            ok=True,
            error="",
            reason=str(exc),
        )
    return SyntaxCheckResult(
        path=str(filename),
        checked=result.checked,
        ok=result.ok,
        error=result.error,
        reason=result.reason,
    )


def first_syntax_failure(
    workspace: str,
    files: list[str] | tuple[str, ...] | set[str],
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> SyntaxCheckResult | None:
    """First DEFINITE syntax failure among ``files`` (workspace-relative), or None.

    Returns the failing :class:`SyntaxCheckResult` so a gate can reject and quote
    a precise, weak-model-fixable message; returns None when every checkable file
    parses (or no file could be checked) — never blocks on un-checkable files.
    """
    for rel in sorted(files):
        result = check_file_syntax(os.path.join(workspace, rel), timeout_seconds=timeout_seconds)
        if result.checked and not result.ok:
            return result
    return None
