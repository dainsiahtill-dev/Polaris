"""Pure policy for KernelOne tool-compression governance checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

RULE_ID = "CELL_KERNELONE_07"
LOCAL_TOOL_COMPRESSION_NAMES = frozenset(
    {
        "compact_result_payload",
        "ToolLoopSafetyPolicy",
        "ToolCompaction",
        "compress_tool_result",
    }
)


@dataclass(frozen=True)
class ToolCompressionPolicyResult:
    """Evaluation result for canonical tool-compression governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _canonical_tool_paths(workspace: Path) -> tuple[Path, ...]:
    """Return canonical KernelOne tool module paths."""
    tool_dir = workspace / "polaris" / "kernelone" / "tool"
    return (
        tool_dir / "compaction.py",
        tool_dir / "safety.py",
        tool_dir / "transcript.py",
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
    """Parse Python source, returning None for syntax-invalid files."""
    try:
        return ast.parse(content, filename=str(path))
    except SyntaxError:
        return None


def _iter_cell_python_files(workspace: Path) -> tuple[Path, ...]:
    """Return non-test Python files under polaris/cells."""
    cells_dir = workspace / "polaris" / "cells"
    if not cells_dir.exists():
        return ()
    return tuple(
        py_file for py_file in cells_dir.rglob("*.py") if "test" not in py_file.parts and "_fixture" not in py_file.name
    )


def _find_local_tool_compression(workspace: Path) -> tuple[str, ...]:
    """Return local tool-compression definitions outside KernelOne."""
    violations: list[str] = []
    for py_file in _iter_cell_python_files(workspace):
        content = _read_python_source(py_file)
        if content is None or not any(name in content for name in LOCAL_TOOL_COMPRESSION_NAMES):
            continue

        tree = _parse_python_source(py_file, content)
        if tree is None:
            continue

        rel_path = _relative_path(workspace, py_file)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
                node.name in LOCAL_TOOL_COMPRESSION_NAMES
            ):
                violations.append(f"Local tool compression at {rel_path}:{node.lineno}: {node.name}")

    return tuple(violations)


def evaluate_tool_compression(workspace: Path) -> ToolCompressionPolicyResult:
    """Evaluate whether tool-compression logic is centralized in KernelOne.

    The policy flags Cell-local implementations of core tool compaction and
    loop-safety primitives. It does not block when canonical modules are absent
    because the historical fitness rule treated absence as a warning while
    keeping migration progress observable.

    Complexity:
        O(f + n) time for scanned files and AST nodes, and O(v) space for
        emitted violations. Text prefiltering avoids parsing files that cannot
        contain relevant symbols.
    """
    canonical_found = any(path.exists() for path in _canonical_tool_paths(workspace))
    evidence: list[str] = []
    warnings: list[str] = []
    if canonical_found:
        evidence.append("Canonical kernelone/tool/ modules exist")
    else:
        warnings.append("Canonical kernelone/tool/ modules not found")

    violations = _find_local_tool_compression(workspace)
    if not violations:
        evidence.append("No local tool compression implementations found in cells/")

    return ToolCompressionPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=violations,
        warnings=tuple(warnings),
    )
