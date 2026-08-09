from __future__ import annotations

import ast
from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
POLARIS_ROOT = BACKEND_ROOT / "polaris"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_cursor_capability_builder_is_composition_private() -> None:
    public_builder = (
        POLARIS_ROOT
        / "cells/orchestration/workflow_runtime/public/project_completion_cursor_bootstrap.py"
    )
    assert not public_builder.exists()

    forbidden = "polaris.cells.orchestration.workflow_runtime.internal.project_completion_cursor"
    importers: list[str] = []
    for path in POLARIS_ROOT.rglob("*.py"):
        if forbidden not in _imports(path):
            continue
        relative = path.relative_to(BACKEND_ROOT).as_posix()
        if relative == "polaris/cells/orchestration/workflow_runtime/public/project_completion_cursor.py":
            continue
        importers.append(relative)
    assert importers == []

    bootstrap = POLARIS_ROOT / "bootstrap/project_completion_convergence_runtime.py"
    bootstrap_imports = _imports(bootstrap)
    assert forbidden not in bootstrap_imports
    assert "polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor" in bootstrap_imports


def test_runtime_projection_no_longer_imports_or_declares_workflow_runtime_or_task_market() -> None:
    root = POLARIS_ROOT / "cells/runtime/projection"
    production_imports = {
        module
        for path in root.rglob("*.py")
        if "/tests/" not in path.as_posix()
        for module in _imports(path)
    }
    assert not any(
        module.startswith("polaris.cells.orchestration.workflow_runtime")
        for module in production_imports
    )
    descriptor = yaml.safe_load((root / "cell.yaml").read_text(encoding="utf-8"))
    dependencies = set(descriptor.get("depends_on") or [])
    assert "orchestration.workflow_runtime" not in dependencies
    assert "runtime.task_market" not in dependencies
