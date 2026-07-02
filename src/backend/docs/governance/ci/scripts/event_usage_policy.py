"""Pure policy for KernelOne event usage governance checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

RULE_ID = "CELL_KERNELONE_05"
LOCAL_EVENT_EMITTER_NAMES = frozenset({"_emit_event", "emit_event"})


@dataclass(frozen=True)
class EventUsagePolicyResult:
    """Evaluation result for canonical event usage governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _canonical_event_paths(workspace: Path) -> tuple[Path, ...]:
    """Return canonical KernelOne event module paths."""
    events_dir = workspace / "polaris" / "kernelone" / "events"
    return (
        events_dir / "fact_events.py",
        events_dir / "session_events.py",
        events_dir / "__init__.py",
    )


def _verify_kernelone_has_events(workspace: Path) -> bool:
    """Return true when the canonical KernelOne event API is present."""
    for path in _canonical_event_paths(workspace):
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "emit_fact_event" in content or "emit_session_event" in content:
            return True
    return False


def _iter_cell_python_files(workspace: Path) -> tuple[Path, ...]:
    """Return non-test Python files under polaris/cells."""
    cells_dir = workspace / "polaris" / "cells"
    if not cells_dir.exists():
        return ()
    return tuple(
        py_file for py_file in cells_dir.rglob("*.py") if "test" not in py_file.parts and "_fixture" not in py_file.name
    )


def _relative_path(workspace: Path, path: Path) -> str:
    """Return a stable repository-relative path."""
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _read_python_source(path: Path) -> str | None:
    """Read a Python file, returning None when it cannot be inspected."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_python_source(path: Path, content: str) -> ast.Module | None:
    """Parse Python source, returning None when it cannot be inspected."""
    try:
        return ast.parse(content, filename=str(path))
    except SyntaxError:
        return None


def _find_local_event_emitters(workspace: Path) -> tuple[str, ...]:
    """Return local event emitter definitions that bypass KernelOne events."""
    violations: list[str] = []
    for py_file in _iter_cell_python_files(workspace):
        content = _read_python_source(py_file)
        if content is None or "emit_event" not in content:
            continue

        tree = _parse_python_source(py_file, content)
        if tree is None:
            continue
        rel_path = _relative_path(workspace, py_file)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in LOCAL_EVENT_EMITTER_NAMES:
                violations.append(f"Local event emitter at {rel_path}:{node.lineno}")
    return tuple(violations)


def _check_cells_import_kernelone_events(workspace: Path) -> tuple[bool, tuple[str, ...]]:
    """Return whether key role cells use canonical events and non-canonical imports."""
    cells_dir = workspace / "polaris" / "cells"
    relevant_dirs = (
        cells_dir / "roles" / "kernel",
        cells_dir / "roles" / "session",
    )
    uses_canonical = False
    non_canonical_importers: list[str] = []

    for directory in relevant_dirs:
        if not directory.exists():
            continue
        for py_file in directory.rglob("*.py"):
            if "test" in py_file.parts:
                continue
            content = _read_python_source(py_file)
            if content is None or ("polaris.kernelone.events" not in content and ".events" not in content):
                continue

            tree = _parse_python_source(py_file, content)
            if tree is None:
                continue
            rel_path = _relative_path(workspace, py_file)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = node.module or ""
                if module == "polaris.kernelone.events" or module.startswith("polaris.kernelone.events."):
                    uses_canonical = True
                if module.startswith("polaris.cells") and ".events" in module and "kernelone" not in module:
                    non_canonical_importers.append(rel_path)

    return uses_canonical, tuple(sorted(set(non_canonical_importers)))


def evaluate_event_usage(workspace: Path) -> EventUsagePolicyResult:
    """Evaluate whether Cells use KernelOne as the canonical event source.

    The policy focuses on executable event-emitter definitions and
    non-canonical imports. Historical class-name regex scanning is intentionally
    excluded because it matched ordinary domain classes and produced large
    false-positive sets.

    Complexity:
        O(f + n) time for scanned files and AST nodes, and O(v + w) space for
        emitted violations and warnings. File I/O dominates runtime.
    """
    if not _verify_kernelone_has_events(workspace):
        return EventUsagePolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("Canonical events not found in kernelone/events/",),
        )

    evidence = ["Canonical events verified in kernelone/events/"]
    violations = list(_find_local_event_emitters(workspace))

    if not violations:
        evidence.append("No local event emitter definitions found in cells/")

    uses_canonical, non_canonical_importers = _check_cells_import_kernelone_events(workspace)
    if uses_canonical:
        evidence.append("Cells properly import from kernelone.events")

    warnings = tuple(f"Non-canonical event import in {path}" for path in non_canonical_importers)

    return EventUsagePolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=tuple(violations),
        warnings=warnings,
    )
