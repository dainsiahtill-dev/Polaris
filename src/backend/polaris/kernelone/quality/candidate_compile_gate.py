"""Workspace-aware pre-commit compile checks for source edit candidates.

Single-file parsers cannot detect cross-line or cross-file semantic regressions
such as deleting a Go local declaration while retaining a later reference.  The
helpers here compare the current workspace with an isolated shadow containing
the candidate bytes.  They reject only a proven regression: the current
workspace compiles, while the candidate shadow does not.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_GO_COMPILE_COMMAND = ("go", "test", "-run", "^$", "./...")
_DEFAULT_TIMEOUT_SECONDS = 30
_GO_DIAGNOSTIC_RE = re.compile(r"^.+?:\d+(?::\d+)?:\s+\S")
_SHADOW_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".polaris",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
    }
)


@dataclass(frozen=True)
class CandidateCompileCheckResult:
    """Result of comparing the current workspace with a candidate shadow."""

    checked: bool
    before_ok: bool
    after_ok: bool
    regression: bool
    command: tuple[str, ...]
    error: str
    reason: str


def _run_compile(command: tuple[str, ...], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GOTOOLCHAIN", "local")
    env.setdefault("GOPROXY", "off")
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _shadow_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _SHADOW_IGNORED_NAMES}


def _diagnostic_signatures(result: subprocess.CompletedProcess[str]) -> tuple[str, ...]:
    """Extract stable Go compiler diagnostic lines from a verifier result."""

    output = "\n".join(part for part in (result.stderr, result.stdout) if part)
    return tuple(dict.fromkeys(line.strip() for line in output.splitlines() if _GO_DIAGNOSTIC_RE.match(line)))


def check_candidate_workspace_compile(
    workspace: str | Path,
    filename: str,
    content: str,
    *,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
) -> CandidateCompileCheckResult:
    """Reject a Go candidate when it provably worsens workspace compilation.

    The shadow copy keeps generated-project bytes untouched.  A baseline that
    already fails compilation remains repairable, but is not permission to
    expand its compiler diagnostics.  A candidate is accepted when it compiles
    or keeps/reduces the diagnostic count (bounded forward-unmask); increasing
    the count is a proven harmful mutation and is rejected before commit.
    """

    root = Path(workspace).resolve()
    rel = Path(str(filename).replace("\\", "/"))
    command = _GO_COMPILE_COMMAND
    if rel.suffix.lower() != ".go":
        return CandidateCompileCheckResult(False, False, False, False, command, "", "unsupported extension")
    if not (root / "go.mod").is_file():
        return CandidateCompileCheckResult(False, False, False, False, command, "", "go.mod not found")
    if shutil.which(command[0]) is None:
        return CandidateCompileCheckResult(False, False, False, False, command, "", "go unavailable")

    try:
        before = _run_compile(command, cwd=root, timeout_seconds=timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CandidateCompileCheckResult(False, False, False, False, command, "", str(exc))
    before_ok = before.returncode == 0
    before_diagnostics = _diagnostic_signatures(before)

    try:
        with tempfile.TemporaryDirectory(prefix="polaris-compile-candidate-", dir=str(root.parent)) as temp_dir:
            shadow = Path(temp_dir) / "workspace"
            shutil.copytree(root, shadow, ignore=_shadow_ignore)
            target = (shadow / rel).resolve()
            if shadow not in target.parents:
                return CandidateCompileCheckResult(False, before_ok, False, False, command, "", "unsafe candidate path")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8", newline="")
            after = _run_compile(command, cwd=shadow, timeout_seconds=timeout_seconds)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        return CandidateCompileCheckResult(False, before_ok, False, False, command, "", str(exc))

    after_ok = after.returncode == 0
    raw_error = (after.stderr or after.stdout or "").strip()
    after_diagnostics = _diagnostic_signatures(after)
    regression = (not after_ok) and (
        before_ok
        or (bool(before_diagnostics) and len(after_diagnostics) > len(before_diagnostics))
    )
    return CandidateCompileCheckResult(
        checked=True,
        before_ok=before_ok,
        after_ok=after_ok,
        regression=regression,
        command=command,
        error="" if after_ok else raw_error[:1000],
        reason=(
            ""
            if before_ok or after_ok
            else f"baseline diagnostics={len(before_diagnostics)}; candidate diagnostics={len(after_diagnostics)}"
        ),
    )


__all__ = ["CandidateCompileCheckResult", "check_candidate_workspace_compile"]
