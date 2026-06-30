"""Deterministic Python smoke verifiers retained by the Director adapter.

File-mutating Python repairs live in ``director.runtime.repair_kernel``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..task_scope_paths import _normalize_declared_task_path
from ._common import (
    _PYTHON_MAIN_BLOCK_RE,
    _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS,
    _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE,
)


def _apply_deterministic_python_static_smoke(
    adapter: Any,
    *,
    all_affected_files: list[str],
) -> list[str]:
    """py_compile every Python artifact the model wrote, declared or not.

    Live factory-bench L2-07 (2026-06-17, after the runtime-smoke fix):
    the model wrote 13 .py files, 10 of which were in the declared
    target list and py_compile-checked by the existing quality gate.
    The remaining 3 (including ``src/ledger/ui/stats_view.py``)
    contained a ``SyntaxError: keyword argument repeated: columns`` —
    the model wrote ``columns=(...)`` twice in the same ``Treeview``
    constructor. The platform marked the run as PASS for that
    parent task because it never py_compile-checked the undeclared
    file. A rigid ruler must py_compile every Python artifact the
    model wrote, regardless of contract inclusion.

    The fix is intentionally narrow: ``py_compile`` is a cheap,
    language-server-grade syntax check. It does NOT execute the
    code, so it cannot catch call-time errors (that is the runtime
    smoke test's job). The two compose: static smoke catches
    ``SyntaxError`` across every file; runtime smoke catches
    call-time errors in ``__main__`` blocks.

    Returns a list of error strings suitable for
    ``artifact_quality_errors`` so the deterministic repair ladder
    and the LLM repair call see the syntax failure.
    """
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    errors: list[str] = []
    for rel in all_affected_files:
        if not isinstance(rel, str) or not rel.endswith(".py"):
            continue
        # Defense in depth: only check files inside the workspace.
        candidate = (workspace_path / rel).resolve()
        try:
            candidate.relative_to(workspace_path)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        # Use the `python3 -m py_compile` subprocess to enforce a real
        # syntax check. The in-process `py_compile.compile(..., doraise=True)`
        # API is more lenient than the CLI module entry point for some
        # edge cases (e.g. ``def f(x, x):`` is rejected by the CLI but
        # sometimes not by the API on newer Python releases), and
        # subprocess keeps each file isolated so one bad file does not
        # leak bytecode cache state into the next.
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(candidate)],
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(
                f"Artifact quality scan failed: python static smoke could not "
                f"check {rel!r}: {type(exc).__name__}: {exc}"
            )
            continue
        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            tail = "\n".join(line for line in stderr.splitlines()[-6:] if line)
            errors.append(
                f"Artifact quality scan failed: python static smoke found syntax error in {rel!r}; tail:\n{tail}"
            )
    return errors


def _apply_deterministic_python_runtime_smoke(
    adapter: Any,
    *,
    task_id: str,
    all_affected_files: list[str],
    timeout_seconds: float = _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS,
) -> list[str]:
    """Surface runtime errors that ``py_compile`` cannot catch.

    Live factory-bench L1-01 (2026-06-17, after the symbol-coherence
    fix): qwen3.6-27b-int4 wrote ``calculator.py`` that imports
    cleanly and ``py_compile``-passes, but the script's
    ``__main__`` block calls ``evaluate('1+2')`` which raises
    ``ValueError`` at call time — the model's tokenizer stores
    ``value=float(text)`` for operator tokens. The post-write
    materialization quality gate currently relies on ``py_compile`` +
    ``_em.scan_workspace_artifact_quality``; neither catches call-time
    failures. The materialization ladder must be told the code is
    broken so the LLM repair path (or a future deterministic fix)
    can take over.

    Strategy (fail-closed, conservative):
    1. For each ``.py`` file that has a top-level
       ``if __name__ == "__main__":`` block, run it in a subprocess
       with a hard timeout.
    2. If exit code != 0 or the process is killed, surface a
       materialization error string.
    3. Library files (no ``__main__`` block) are NOT executed —
       we do not know how to safely call their public API without
       project-specific knowledge, and ``py_compile`` + import-time
       static checks already cover the import surface.
    4. Timeout is enforced via ``subprocess.run``; the Director
       turn budget cannot be spent waiting for an infinite loop.

    Returns a list of error strings suitable for
    ``artifact_quality_errors`` so the deterministic repair ladder
    and the LLM repair call see the runtime failure.
    """
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    errors: list[str] = []
    for rel in all_affected_files:
        if not isinstance(rel, str) or not rel.endswith(".py"):
            continue
        # Defense in depth: only run files inside the workspace.
        candidate = (workspace_path / rel).resolve()
        try:
            candidate.relative_to(workspace_path)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _PYTHON_MAIN_BLOCK_RE.search(text):
            continue
        # Use Popen + communicate() so we keep a handle to the
        # child process after a timeout. ``subprocess.run`` raises
        # ``TimeoutExpired`` without exposing ``exc.process``; the
        # fix #3 boundary bug (L4-23) requires us to inspect the
        # child after timeout to distinguish a long-running server
        # (intentional) from a hung process (real failure).
        env = os.environ.copy()
        current_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = (
            str(workspace_path)
            if not current_pythonpath
            else os.pathsep.join([str(workspace_path), current_pythonpath])
        )
        proc = subprocess.Popen(
            [sys.executable, str(candidate)],
            cwd=str(workspace_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=max(0.5, float(timeout_seconds)))
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            # Live factory-bench L4-23 (2026-06-17, fix #3 boundary):
            # the model wrote ``gateway/server.py`` whose __main__
            # launches ``serve_forever()`` — the canonical pattern
            # for a Python web gateway. The 5s smoke timeout was a
            # false positive against a contract-compliant long-running
            # process. Distinguish "still alive" (intentional server
            # / daemon / game loop) from "exited during cleanup"
            # (real timeout failure) so the rigid ruler does not
            # penalize the model for a correct long-running script.
            if proc.poll() is None:
                # Process is still running — long-running, not a
                # quality failure. Kill it cleanly so it does not
                # outlive the smoke and leak as a zombie.
                try:
                    proc.kill()
                finally:
                    with contextlib.suppress(OSError):
                        proc.wait(timeout=2.0)
                # Long-running process is not a quality failure.
                # Do not append to errors; the model wrote a script
                # that intentionally runs forever.
                continue
            # Process exited during cleanup — real timeout failure.
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            tail = "\n".join(line for line in (stderr or "").strip().splitlines()[-8:] if line)
            errors.append(
                f"Artifact quality scan failed: python runtime smoke timed out for {rel!r} "
                f"after {timeout_seconds}s; tail:\n{tail}"
            )
            continue
        except (OSError, ValueError) as exc:
            errors.append(
                f"Artifact quality scan failed: python runtime smoke could not launch "
                f"{rel!r}: {type(exc).__name__}: {exc}"
            )
            continue

        if returncode == 0:
            continue
        stderr_tail = (stderr or stdout or "").strip().splitlines()
        tail = "\n".join(line for line in stderr_tail[-8:] if line)
        if returncode < 0:
            errors.append(
                f"Artifact quality scan failed: python runtime smoke was killed for {rel!r} "
                f"(returncode={returncode}, signal={-returncode}); tail:\n{tail}"
            )
        else:
            errors.append(
                f"Artifact quality scan failed: python runtime smoke crashed for {rel!r} "
                f"(returncode={returncode}); tail:\n{tail}"
            )
    errors.extend(
        _apply_deterministic_python_unittest_discover_smoke(
            adapter,
            all_affected_files=all_affected_files,
            timeout_seconds=timeout_seconds,
        )
    )
    return errors


def _apply_deterministic_python_unittest_discover_smoke(
    adapter: Any,
    *,
    all_affected_files: list[str],
    timeout_seconds: float = _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS,
) -> list[str]:
    """Run the real unittest discovery gate after Director writes Python tests.

    Per-file ``python tests/test_x.py`` smoke misses suite-level contract drift:
    tests and source can import cleanly in isolation while ``unittest discover``
    still proves the generated project is not runnable. Only trigger this gate
    when the current Director turn touched a Python unittest-style test file.
    """

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    touched_test_files = [
        _normalize_declared_task_path(str(item or ""))
        for item in all_affected_files
        if _looks_like_python_unittest_test_path(str(item or ""))
    ]
    if not touched_test_files:
        return []

    tests_dir = workspace_path / "tests"
    if not tests_dir.is_dir():
        return []
    try:
        has_discoverable_tests = any(path.is_file() for path in tests_dir.rglob("test_*.py"))
    except (OSError, RuntimeError):
        return []
    if not has_discoverable_tests:
        return []

    env = os.environ.copy()
    current_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        str(workspace_path) if not current_pythonpath else os.pathsep.join([str(workspace_path), current_pythonpath])
    )
    command = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*.py",
        "-v",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            env=env,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(str(part or "").strip() for part in (exc.stdout, exc.stderr) if part)
        tail = "\n".join(line for line in output.splitlines()[-40:] if line)
        return [
            "Artifact quality scan failed: workspace validation command timed out "
            "(python -m unittest discover -s tests -p test_*.py -v); "
            f"touched_tests={touched_test_files[:6]}; tail:\n{tail}"
        ]
    except (OSError, ValueError) as exc:
        return [
            "Artifact quality scan failed: workspace validation command could not launch "
            "(python -m unittest discover -s tests -p test_*.py -v): "
            f"{type(exc).__name__}: {exc}"
        ]

    output = (completed.stderr or completed.stdout or "").strip()
    if completed.returncode == 0 or _unittest_discover_only_found_no_tests(output):
        return []
    tail = "\n".join(line for line in output.splitlines()[-80:] if line)
    return [
        "Artifact quality scan failed: workspace validation command failed "
        "(python -m unittest discover -s tests -p test_*.py -v); "
        f"touched_tests={touched_test_files[:6]}; tail:\n{tail}"
    ]


def _looks_like_python_unittest_test_path(rel_path: str) -> bool:
    normalized = _normalize_declared_task_path(rel_path)
    name = Path(normalized).name
    return normalized.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py") or "/tests/" in normalized
    )


def _unittest_discover_only_found_no_tests(output: str) -> bool:
    token = str(output or "").lower()
    return "ran 0 tests" in token and "no tests ran" in token and "traceback" not in token


def _build_unresolved_import_symbol_repair_block(artifact_quality_errors: list[str]) -> str:
    symbol_errors: list[tuple[str, str, str]] = []
    for item in artifact_quality_errors:
        match = _UNRESOLVED_IMPORT_SYMBOL_ERROR_RE.search(str(item or ""))
        if not match:
            continue
        symbol = str(match.group("symbol") or "").strip()
        module = str(match.group("module") or "").strip()
        importer = _normalize_declared_task_path(match.group("path"))
        if symbol and module and importer:
            symbol_errors.append((symbol, module, importer))

    if not symbol_errors:
        return ""

    symbol_lines = "\n".join(
        f"- Module '{module}' must define/export symbol '{symbol}' for importer '{importer}'."
        for symbol, module, importer in symbol_errors[:12]
    )
    return (
        "CROSS-FILE SYMBOL REPAIR: an importing file already exists, but the "
        "sibling/exporting module does not define a symbol that importer needs. "
        "Do not edit the importing file. Do not remove or weaken the import. "
        "For the symbol errors below, update the exporting module named after "
        "`from ...` and make the exporting module define or export exactly the "
        "missing symbol(s). If this repair prompt also names package or typecheck "
        "targets, repair those named targets in the same batch. Do not create "
        "unrelated files. Do not read files first. Do not list directories. Do "
        "not explore. Do not explain.\n"
        f"{symbol_lines}\n"
    )
