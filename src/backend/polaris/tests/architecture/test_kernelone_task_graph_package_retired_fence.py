"""Architecture fence for the retired KernelOne task_graph package."""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"
RETIRED_MODULE_PREFIX = "polaris.kernelone.task_graph"
CANONICAL_TASK_ENTITY = "polaris.domain.entities.task"
CANONICAL_TASK_BOARD = "polaris.cells.runtime.task_runtime.internal.task_board"


def _imports_retired_task_graph(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == RETIRED_MODULE_PREFIX or alias.name.startswith(f"{RETIRED_MODULE_PREFIX}."):
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == RETIRED_MODULE_PREFIX or module.startswith(f"{RETIRED_MODULE_PREFIX}."):
                imports.append(module)
    return imports


def test_kernelone_task_graph_package_is_retired() -> None:
    retired_path = POLARIS_ROOT / "kernelone" / "task_graph"
    assert not retired_path.exists(), "Retired KernelOne task_graph package was recreated."


def test_canonical_task_owners_exist() -> None:
    task_entity = POLARIS_ROOT / "domain" / "entities" / "task.py"
    task_board = POLARIS_ROOT / "cells" / "runtime" / "task_runtime" / "internal" / "task_board.py"
    assert task_entity.is_file(), f"{CANONICAL_TASK_ENTITY} must own task entities."
    assert task_board.is_file(), f"{CANONICAL_TASK_BOARD} must own task-board storage/runtime behavior."


def test_active_python_code_does_not_import_retired_task_graph() -> None:
    offenders: list[str] = []
    this_file = Path(__file__).resolve()
    for path in POLARIS_ROOT.rglob("*.py"):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        for imported in _imports_retired_task_graph(path):
            offenders.append(f"{path.relative_to(BACKEND_ROOT)} imports {imported}")

    assert not offenders, (
        f"Task entities belong to {CANONICAL_TASK_ENTITY!r} and task-board runtime behavior "
        f"belongs to {CANONICAL_TASK_BOARD!r}; retired task_graph imports remain:\n"
        + "\n".join(offenders)
    )
