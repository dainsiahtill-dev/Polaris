"""Fence workflow runtime exports from the retired RuntimeBackend alias."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SOURCE_ROOT = ROOT / "src/backend/polaris"


def test_runtime_backend_alias_is_not_public() -> None:
    """Workflow runtime exports the explicit protocol name only."""

    import polaris.cells.orchestration.workflow_runtime as workflow_runtime
    import polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime as runtime_engine
    import polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.embedded as embedded_runtime
    import polaris.cells.orchestration.workflow_runtime.public as workflow_runtime_public
    import polaris.cells.orchestration.workflow_runtime.public.runtime as public_runtime
    import polaris.kernelone.workflow.base as workflow_base

    modules = (
        workflow_base,
        runtime_engine,
        embedded_runtime,
        public_runtime,
        workflow_runtime_public,
        workflow_runtime,
    )
    for module in modules:
        assert not hasattr(module, "RuntimeBackend")
        assert "RuntimeBackend" not in getattr(module, "__all__", ())
        assert hasattr(module, "RuntimeBackendPort")


def test_production_source_does_not_reintroduce_runtime_backend_alias() -> None:
    """Production code must use RuntimeBackendPort for workflow runtime typing."""

    offenders: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts or "/tests/" in path.as_posix():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id == "RuntimeBackend") or (isinstance(node, ast.alias) and node.name == "RuntimeBackend"):
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
