"""Architecture fences for Execution Control Plane single-source semantics."""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_FACTORY_LEDGER = _BACKEND_ROOT / "polaris/cells/factory/pipeline/internal/run_ledger.py"
_BENCH_RUNNER = _BACKEND_ROOT / "scripts/factory_bench/run_factory_bench.py"


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(module: ast.Module, name: str) -> ast.FunctionDef:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            names.add(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def test_factory_run_ledger_reads_public_fact_projection() -> None:
    """Factory must consume the public FactStream-first read model."""

    function = _function(_module(_FACTORY_LEDGER), "load_run_ledger_projection")
    called = _called_names(function)

    assert "read_run_ledger_projection" in called
    assert "read_events" not in called


def test_bench_cannot_synthesize_task_boundary_or_tool_lifecycle_facts() -> None:
    """Bench is an observer and cannot author execution-control-plane facts."""

    module = _module(_BENCH_RUNNER)
    function_names = {
        node.name
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imported_names = {
        alias.name
        for node in module.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }

    assert "_read_task_boundary_verdict_from_run_ledger_projection" in function_names
    assert "_append_task_boundary_verdict_to_run_ledger" not in function_names
    assert "_append_tool_dispatch_failure_to_run_ledger" not in function_names
    assert "evaluate_task_boundary_verdict" not in imported_names
    assert "append_tool_call_lifecycle_event" not in imported_names

