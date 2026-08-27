"""Regression coverage for Factory-to-runtime post-execution repair evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl
from polaris.cells.roles.adapters.public import service as role_adapter_service


class _Executor:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def _workspace_quality_repair_target_files(self) -> list[str]:
        return ["tests/test_cipher.cpp"]

    def _workspace_quality_repair_diagnostic_target_files(self, _errors: list[str]) -> list[str]:
        return ["tests/test_cipher.cpp"]

    def _workspace_quality_repair_changed_files(self) -> list[str]:
        return []

    def _workspace_quality_repair_blueprint_evidence(self, *, run_id: str) -> tuple[str, str]:
        del run_id
        return "", ""


def test_factory_forwards_exact_diagnostics_and_attempt_to_post_execution_schedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: dict[str, Any] = {}
    execution_attempt = object()
    diagnostics = [
        "tests/test_cipher.cpp:112:41: error: expected unqualified-id before '&' token",
    ]

    def fake_materialization_schedule(
        _adapter: Any,
        **_kwargs: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [], {"source_tools": [], "tool_results": 0}

    def fake_post_execution_schedule(
        workspace: str | Path,
        *,
        task_id: str,
        artifact_quality_errors: list[str],
        execution_attempt: Any | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        observed.update(
            {
                "workspace": str(workspace),
                "task_id": task_id,
                "artifact_quality_errors": list(artifact_quality_errors),
                "execution_attempt": execution_attempt,
            }
        )
        return [
            {
                "tool": "director_repair_kernel",
                "success": True,
                "result": {
                    "source_tool": "deterministic_cpp_standard_include_repair",
                    "status": "deferred_repair_effects_pending",
                    "deferred_request": {"source_tool": "deterministic_cpp_standard_include_repair"},
                },
            }
        ], {"schema_version": "director.post_execution_repair_kernel.v1"}

    monkeypatch.setattr(
        role_adapter_service,
        "run_director_materialization_quality_repair_schedule",
        fake_materialization_schedule,
    )
    monkeypatch.setattr(
        role_adapter_service,
        "run_director_post_execution_repair_schedule",
        fake_post_execution_schedule,
    )

    results, summary = factory_workspace_quality_impl._apply_workspace_quality_repairs(
        _Executor(tmp_path),
        run_id="factory-regression",
        artifact_quality_errors=diagnostics,
        task_id="TASK-tests",
        execution_attempt=execution_attempt,
        repair_task={"target_files": ["tests/test_cipher.cpp"]},
    )

    assert observed == {
        "workspace": str(tmp_path),
        "task_id": "TASK-tests",
        "artifact_quality_errors": diagnostics,
        "execution_attempt": execution_attempt,
    }
    assert results[0]["result"]["source_tool"] == "deterministic_cpp_standard_include_repair"
    assert summary["source_tools"] == ["deterministic_cpp_standard_include_repair"]
    assert summary["tool_results"] == 1
