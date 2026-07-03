"""Regression tests for factory workspace-quality write evidence detection."""

from __future__ import annotations

from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.kernelone.tools.tool_kinds import DEPRECATED_WRITE_TOOLS


def test_workspace_quality_mutation_detector_recognizes_deprecated_write_catalog() -> None:
    deprecated_tool = next(iter(DEPRECATED_WRITE_TOOLS))

    assert OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {"success": True, "tool": deprecated_tool, "result": {}}
    )


def test_workspace_quality_mutation_detector_recognizes_repair_operation() -> None:
    assert OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {"success": True, "result": {"operation": "text_replace"}}
    )

