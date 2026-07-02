"""Pure policy for KernelOne LLM invocation governance checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

RULE_ID = "CELL_KERNELONE_08"
LOCAL_LLM_CALLER_NAMES = frozenset({"_call_role_llm", "role_llm_invoke", "RoleLLMInvoker"})


@dataclass(frozen=True)
class LlmImportPolicyResult:
    """Evaluation result for canonical LLM invocation governance."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _relative_path(workspace: Path, path: Path) -> str:
    """Return a stable repository-relative path."""
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _iter_cell_python_files(workspace: Path) -> tuple[Path, ...]:
    """Return non-test Python files under polaris/cells."""
    cells_dir = workspace / "polaris" / "cells"
    if not cells_dir.exists():
        return ()
    return tuple(
        py_file for py_file in cells_dir.rglob("*.py") if "test" not in py_file.parts and "_fixture" not in py_file.name
    )


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


def _find_local_llm_callers(workspace: Path) -> tuple[str, ...]:
    """Return local LLM caller definitions outside KernelOne."""
    violations: list[str] = []
    for py_file in _iter_cell_python_files(workspace):
        content = _read_python_source(py_file)
        if content is None or not any(name in content for name in LOCAL_LLM_CALLER_NAMES):
            continue

        tree = _parse_python_source(py_file, content)
        if tree is None:
            continue

        rel_path = _relative_path(workspace, py_file)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and (
                node.name in LOCAL_LLM_CALLER_NAMES
            ):
                violations.append(f"Local LLM caller at {rel_path}:{node.lineno}")

    return tuple(violations)


def evaluate_llm_import(workspace: Path) -> LlmImportPolicyResult:
    """Evaluate whether role LLM invocation is centralized in KernelOne.

    The policy preserves the existing hard requirement that
    ``polaris/kernelone/llm`` exists, then reports Cell-local definitions of
    role LLM caller primitives as violations.

    Complexity:
        O(f + n) time for scanned files and AST nodes, and O(v) space for
        emitted violations. Text prefiltering avoids parsing unrelated files.
    """
    if not (workspace / "polaris" / "kernelone" / "llm").exists():
        return LlmImportPolicyResult(
            rule_id=RULE_ID,
            passed=False,
            violations=("kernelone/llm/ directory not found",),
        )

    evidence = ["kernelone/llm/ directory exists"]
    violations = _find_local_llm_callers(workspace)
    if not violations:
        evidence.append("No local _call_role_llm implementations found in cells/")

    return LlmImportPolicyResult(
        rule_id=RULE_ID,
        passed=not violations,
        evidence=tuple(evidence),
        violations=violations,
    )
