"""Architecture fences for DEO-2C deferred Director repair effects."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
DIRECTOR_RUNTIME_ROOT = POLARIS_ROOT / "cells" / "director" / "runtime"
ADAPTER_BRIDGE = (
    POLARIS_ROOT / "cells" / "roles" / "adapters" / "internal" / "director" / "runtime_repair_tool_adapter.py"
)
ROLES_KERNEL_ROOT = POLARIS_ROOT / "cells" / "roles" / "kernel"
FOLLOWUP_PATH = ROLES_KERNEL_ROOT / "internal" / "transaction" / "deferred_repair_followup.py"
TOOL_BATCH_EXECUTOR_PATH = ROLES_KERNEL_ROOT / "internal" / "transaction" / "tool_batch_executor.py"
DEFERRED_REQUEST_TYPE = "DeferredDirectorRepairRequestV1"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path), filename=path.as_posix())


def _production_python_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        path for path in sorted(root.rglob("*.py")) if "tests" not in path.parts and "__pycache__" not in path.parts
    )


def _imported_modules(tree: ast.AST) -> tuple[str, ...]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    assert len(matches) == 1, f"expected exactly one {name}, found {len(matches)}"
    return matches[0]


def _call_leaf_names(node: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name):
            names.append(child.func.id)
        elif isinstance(child.func, ast.Attribute):
            names.append(child.func.attr)
    return tuple(names)


def test_director_runtime_never_depends_on_role_execution_cells() -> None:
    violations: list[str] = []
    for path in _production_python_files(DIRECTOR_RUNTIME_ROOT):
        for module in _imported_modules(_tree(path)):
            if module.startswith(("polaris.cells.roles.adapters", "polaris.cells.roles.kernel")):
                violations.append(f"{path.relative_to(BACKEND_ROOT)}:{module}")
    assert violations == []


def test_adapter_bridge_is_pure_projection_without_execution_or_admission() -> None:
    tree = _tree(ADAPTER_BRIDGE)
    bridge = _function(tree, "run_runtime_repair_with_director_tools")
    parameter_names = {
        argument.arg for argument in (*bridge.args.posonlyargs, *bridge.args.args, *bridge.args.kwonlyargs)
    }
    assert "executor_factory" not in parameter_names

    call_names = set(_call_leaf_names(bridge))
    assert call_names.isdisjoint(
        {
            "execute_tool",
            "run_director_repair",
            "admit_directed_effect",
            "claim_directed_effect",
            "complete_directed_effect",
            "execute_tool_batch",
        }
    )

    forbidden_symbols = {
        "DirectorToolExecutor",
        "TaskRuntimeDirectedEffectClaimCommandV1",
        "TaskRuntimeDirectedEffectCompleteCommandV1",
        "TaskRuntimeDirectedEffectPrepareCommandV1",
    }
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    imported_names = {
        alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    assert forbidden_symbols.isdisjoint(referenced_names | imported_names)


def test_typed_deferred_request_has_no_provider_or_llm_consumer() -> None:
    forbidden_roots = (
        POLARIS_ROOT / "cells" / "roles" / "kernel" / "internal" / "llm_caller",
        POLARIS_ROOT / "infrastructure" / "llm",
    )
    offenders = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for root in forbidden_roots
        for path in _production_python_files(root)
        if DEFERRED_REQUEST_TYPE in _source(path)
    ]
    assert offenders == []


def test_only_kernel_consumes_deferred_request_outside_the_pure_adapter_producer() -> None:
    allowed_outside_kernel = {ADAPTER_BRIDGE}
    offenders: list[str] = []
    for path in _production_python_files(POLARIS_ROOT):
        if DEFERRED_REQUEST_TYPE not in _source(path):
            continue
        if path.is_relative_to(ROLES_KERNEL_ROOT) or path in allowed_outside_kernel:
            continue
        offenders.append(path.relative_to(BACKEND_ROOT).as_posix())
    assert offenders == []


def test_kernel_followup_is_one_visible_non_recursive_batch() -> None:
    tree = _tree(TOOL_BATCH_EXECUTOR_PATH)
    followup = _function(tree, "_execute_deferred_repair_followup")
    calls = _call_leaf_names(followup)
    assert calls.count("_build_tool_batch_runtime") == 1
    assert calls.count("execute_batch") == 1
    assert "execute_tool_batch" not in calls

    batch_count_increments = [
        node
        for node in ast.walk(followup)
        if isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Attribute)
        and node.target.attr == "tool_batch_count"
    ]
    assert len(batch_count_increments) == 1

    followup_builder = _function(_tree(FOLLOWUP_PATH), "build_deferred_repair_followup")
    builder_calls = set(_call_leaf_names(followup_builder))
    assert "synthesize_batch" in builder_calls
    assert "execute_batch" not in builder_calls
    assert "execute_tool_batch" not in builder_calls
