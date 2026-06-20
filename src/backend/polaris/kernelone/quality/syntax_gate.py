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
import re
import shutil
import subprocess
import sys
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
}

_DEFAULT_TIMEOUT_SECONDS = 20
_TS_SYNTAX_DIAGNOSTIC_RE = re.compile(r"\bTS1\d{3}\b")


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
    if (
        not ok
        and os.path.splitext(path)[1].lower() in {".ts", ".tsx"}
        and not _TS_SYNTAX_DIAGNOSTIC_RE.search(raw_error)
    ):
        return SyntaxCheckResult(
            path=path,
            checked=True,
            ok=True,
            error="",
            reason="tsc reported non-syntax diagnostics only",
        )
    error = "" if ok else raw_error[:500]
    return SyntaxCheckResult(path=path, checked=True, ok=ok, error=error, reason="")


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
