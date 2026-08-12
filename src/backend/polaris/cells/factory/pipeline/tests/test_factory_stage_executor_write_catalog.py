"""Regression tests for factory workspace-quality write evidence detection."""

from __future__ import annotations

from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.kernelone.tools.tool_kinds import DEPRECATED_WRITE_TOOLS


def test_workspace_quality_mutation_detector_requires_physical_receipt_for_deprecated_write_catalog() -> None:
    deprecated_tool = next(iter(DEPRECATED_WRITE_TOOLS))

    assert not OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {"success": True, "tool": deprecated_tool, "result": {}}
    )
    assert OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {
            "success": True,
            "tool": deprecated_tool,
            "result": {
                "file": "src/app.ts",
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
            },
        }
    )


def test_workspace_quality_mutation_detector_rejects_operation_without_hashes_or_noop() -> None:
    assert not OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {"success": True, "result": {"operation": "text_replace"}}
    )
    assert not OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {
            "success": True,
            "result": {
                "operation": "text_replace",
                "path": "src/app.ts",
                "before_hash": "a" * 64,
                "after_hash": "a" * 64,
            },
        }
    )
    assert OrchestrationStageExecutor._workspace_quality_repair_result_has_mutation(
        {
            "success": True,
            "result": {
                "operation": "text_replace",
                "path": "src/app.ts",
                "before_hash": "a" * 64,
                "after_hash": "b" * 64,
            },
        }
    )
