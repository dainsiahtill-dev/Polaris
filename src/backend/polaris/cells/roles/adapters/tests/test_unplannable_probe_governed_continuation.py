"""Adapter dispatch seam: read-only feasibility is not execution authority.

Canned adapter dialogue and read-only probe projections only. No physical
provider, CE admission, TaskRuntime claim or effect receipt is claimed here.
Existing Gateway/claim integration controls cover actual permission denial.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director import quality_gate
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _run_materialization_quality_repair_retry,
)


def _probe() -> dict[str, Any]:
    return {
        "attempted": True,
        "success": False,
        "tool_results": 0,
        "plan_probe_preaudit": {
            "status": "coverage_matched_but_unplannable",
            "plannable_source_tools": [],
            "covered_unplannable_diagnostic_count": 1,
            "covered_unplannable_source_tools": ["deterministic_typescript_missing_member_repair"],
            "covered_unplannable_diagnostics": [{"path": "src/value.ts", "message": "member unavailable"}],
            "read_only": True,
            "agi_execution_authority": False,
        },
    }


class _Execution:
    @staticmethod
    def extract_kernel_tool_results(_result: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    @staticmethod
    async def execute_tools(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_readonly_unplannable_probe_reaches_same_scoped_adapter_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/value.ts").write_text("export const value = {};\n", encoding="utf-8")
    task = {
        "id": "TASK-1",
        "target_files": ["src/value.ts"],
        "metadata": {
            "module_interface_contract": {"modules": [{"path": "src/value.ts", "actual_public_symbols": ["value"]}]}
        },
    }
    context = {"run_id": "director-private", "task_id": "TASK-1"}
    before = deepcopy(context)
    calls = []

    class Adapter:
        workspace = str(tmp_path)
        _execution = _Execution()
        _update_task_progress = staticmethod(lambda *_args, **_kwargs: None)

        async def _invoke_role_dialogue_with_timeout(self, message: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"message": message, **kwargs})
            return {"content": "", "metadata": {}}

    monkeypatch.setattr(quality_gate, "has_materialization_quality_runtime_repair_coverage", lambda _errors: True)
    monkeypatch.setattr(
        quality_gate, "_run_materialization_quality_public_boundary", lambda *_args, **_kwargs: ([], _probe())
    )
    results, summary = await _run_materialization_quality_repair_retry(
        Adapter(),
        task=task,
        target_task_id="TASK-1",
        run_id="director-private",
        context=context,
        original_message="Repair the owned implementation without changing its contract.",
        llm_call_timeout=30,
        artifact_quality_errors=[
            "Artifact quality scan failed: TypeScript project typecheck failed: src/value.ts(1,1): error TS2339: Property 'member' does not exist on type '{}'."
        ],
        changed_files=["src/value.ts"],
    )
    assert len(calls) == 1, {
        key: summary.get(key)
        for key in ("stage", "success_reason", "task_boundary_scope_filter", "repair_target_files")
    }
    assert summary["plan_probe_preaudit"] == _probe()["plan_probe_preaudit"]
    assert context == before
    assert calls[0]["context"]["run_id"] == "director-private"
    assert not calls[0]["context"].get("director_interface_discrepancy_retry")
    assert not calls[0]["context"].get("task_boundary_interface_discrepancy_retry")
    assert results == [] and summary.get("write_tool_evidence") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,target", [([], "owned.txt"), (["owned.txt"], "foreign.txt")])
async def test_actual_gateway_and_claim_still_reject_empty_or_foreign_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: list[str], target: str
) -> None:
    from polaris.cells.roles.kernel.tests.adapters_kernel_boundary_scenarios import observe_line_range_execution

    observation = await observe_line_range_execution(
        tmp_path,
        monkeypatch,
        [("denied", "write_file", {"file": target, "content": "unapproved\n"})],
        scope=scope,
        tolerate_runtime_error=True,
    )
    result = observation["result"]
    assert observation["content"] == observation["original_content"]
    assert not observation["foreign_exists"]
    assert observation["commits"] == []
    if result is not None:
        assert result["batch_receipt"]["failure_count"] == 1
