"""Architecture guard for workflow-engine handler-registry exports."""

from __future__ import annotations

from polaris.cells.orchestration import workflow_engine


def test_workflow_engine_exports_explicit_handler_registry_names() -> None:
    """The workflow-engine Cell must not publish a generic HandlerRegistry alias."""
    assert hasattr(workflow_engine, "CellHandlerRegistry")
    assert hasattr(workflow_engine, "HandlerRegistryPort")
    assert "HandlerRegistryPort" in workflow_engine.__all__
    assert not hasattr(workflow_engine, "HandlerRegistry")
    assert "HandlerRegistry" not in workflow_engine.__all__
