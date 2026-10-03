"""An empty required artifact must have a lawful restoration tool, not edit-only."""

from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.roles.adapters.internal.director.quality_gate._repair_loop import (
    _run_materialization_quality_repair_retry,
)
from polaris.cells.roles.adapters.tests.test_quality_repair_authorized_scope import (
    _RepairBoundaryAdapter,
)


@pytest.mark.parametrize("allowed", [[], ["index.html"]])
@pytest.mark.asyncio
async def test_empty_owned_target_recovery_offers_full_write_without_scope_expansion(
    tmp_path: Path, allowed: list[str]
) -> None:
    # Test fixture only; no generated Bench workspace or physical tools invoked.
    target = tmp_path / "index.html"
    target.write_text("", encoding="utf-8")
    task = {
        "task_id": "TASK-2",
        "target_files": ["index.html"],
        "scope_paths": ["index.html"],
        "director_execution_envelope": {"authorization": {"allowed_write_paths": allowed}},
    }
    context = {"director_execution_envelope": {"authorization": {"allowed_write_paths": ["index.html"]}}}
    before_task, before_context = deepcopy(task), deepcopy(context)
    adapter = _RepairBoundaryAdapter(tmp_path)

    results, summary = await _run_materialization_quality_repair_retry(
        adapter,
        task=task,
        target_task_id="TASK-2",
        run_id="factory-empty-owned-artifact-unit",
        context=context,
        original_message="Restore the required owned entrypoint without changing its contract.",
        llm_call_timeout=30,
        artifact_quality_errors=["Artifact quality scan failed: declared target file empty 'index.html'"],
        changed_files=["index.html"],
    )

    assert task == before_task and context == before_context
    assert target.read_bytes() == b""
    if not allowed:
        assert adapter.calls == []
        assert results == []
        assert summary["repair_target_files"] == []
        assert summary["success"] is False
        return
    assert len(adapter.calls) == 1
    repair_context = adapter.calls[0]["context"]
    definitions = repair_context["_transaction_kernel_forced_tool_definitions"]
    assert [item["function"]["name"] for item in definitions] == ["write_file"]
    assert repair_context["_transaction_kernel_forced_tool_choice"] == {
        "type": "function",
        "function": {"name": "write_file"},
    }
    assert repair_context["metadata"]["tool_path_contract"]["allowed_target_files"] == ["index.html"]
    assert "complete non-empty file body" in adapter.calls[0]["message"]
    assert "read_file its tail" not in adapter.calls[0]["message"]
    assert summary["repair_target_files"] == ["index.html"]
    assert summary["success"] is False  # No tool receipt or verifier success invented.
    assert results == []
    assert task == before_task and context == before_context
    assert target.read_bytes() == b""
