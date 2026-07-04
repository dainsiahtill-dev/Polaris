"""Characterization tests for ``_execute_standard_llm_flow`` Blocks A-D.

These lock the current behavior of the four un-extracted inline blocks inside
``_execute_standard_llm_flow`` BEFORE the decomposition extracts them into
``_phase_*`` helpers:

- Block D success epilogue: the full success result-dict contract returned
  after materialized-paths reconcile + completion-metadata assembly.
- Block D failure fallback: the ``director.materialization.no_physical_files``
  result-dict returned when reported changed files do not materialize on disk.

The orchestrator is driven through the real ``adapter.execute`` entrypoint with
a fake role-dialogue that materializes (or fails to materialize) workspace
files, mirroring the existing ``TestDirectorFailureClosure`` harness.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director import execute_method
from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter


def _make_adapter(tmp_path: Any) -> DirectorAdapter:
    return DirectorAdapter(workspace=str(tmp_path))


@pytest.mark.asyncio
async def test_execute_standard_llm_flow_success_dict_contract(tmp_path: Any) -> None:
    """Block D success epilogue: lock the full success result-dict contract."""
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
    task_id = str(task["id"])

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del message, args, kwargs
        target = tmp_path / "src" / "app.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "APP_STATUS = 'ok'\n\n\ndef main() -> str:\n    return APP_STATUS\n\n\n"
            "if __name__ == '__main__':\n    print(main())\n",
            encoding="utf-8",
        )
        return {
            "content": "Created src/app.py.",
            "success": True,
            "tool_results": [
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "status": "success",
                    "success": True,
                    "arguments": {"file": "src/app.py", "content": "APP_STATUS = 'ok'\n"},
                    "result": {"path": "src/app.py", "ok": True},
                }
            ],
        }

    async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]
    adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]

    result = await adapter.execute(
        task_id=task_id,
        input_data={"task_id": task_id},
        context={"run_id": "run-success-dict"},
    )

    assert result["success"] is True
    assert result["task_id"] == task_id
    assert result["changed_files"] == ["src/app.py"]
    assert result["new_files"] == ["src/app.py"]
    assert result["modified_files"] == []
    assert result["qa_required_for_final_verdict"] is True
    assert result["artifacts"] == []
    assert result["materialization_mode"] == "write_tool_and_workspace_diff"
    assert result["tools_executed"] >= 1
    assert "cognitive_runtime_receipt" in result
    assert "decision_signals" in result
    # completion metadata persisted to the task board (Block D completion side)
    updated = adapter.task_runtime.get_task(task_id)
    assert updated is not None
    assert str(updated.get("status") or "").lower() == "completed"
    raw_metadata = updated.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    raw_adapter_result = metadata.get("adapter_result")
    adapter_result: dict[str, Any] = raw_adapter_result if isinstance(raw_adapter_result, dict) else {}
    assert adapter_result.get("qa_passed") is None
    assert adapter_result.get("new_file_count") == 1
    assert adapter_result.get("modified_file_count") == 0
    assert adapter_result.get("write_tool_evidence") is True
    assert adapter_result.get("materialization_mode") == "write_tool_and_workspace_diff"


@pytest.mark.asyncio
async def test_execute_standard_llm_flow_no_physical_files_dict_contract(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Block D failure fallback: lock the ``no_physical_files`` result-dict.

    The dialogue materializes a valid declared target (so quality + semantic
    gates pass and ``all_affected_files`` is non-empty), but the materialized
    -paths reconcile is forced to report every changed file as unmaterialized,
    driving the ``director.materialization.no_physical_files`` epilogue.
    """
    adapter = _make_adapter(tmp_path)
    task = adapter.task_runtime.create_task_row(
        subject="Create app module",
        description="Create src/app.py with a runnable entry point.",
        metadata={"target_files": ["src/app.py"], "scope_paths": ["src/app.py"]},
    )
    task_id = str(task["id"])

    async def _dialogue(message: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        del message, args, kwargs
        target = tmp_path / "src" / "app.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "APP_STATUS = 'ok'\n\n\ndef main() -> str:\n    return APP_STATUS\n\n\n"
            "if __name__ == '__main__':\n    print(main())\n",
            encoding="utf-8",
        )
        return {
            "content": "Created src/app.py.",
            "success": True,
            "tool_results": [
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "status": "success",
                    "success": True,
                    "arguments": {"file": "src/app.py", "content": "APP_STATUS = 'ok'\n"},
                    "result": {"path": "src/app.py", "ok": True},
                }
            ],
        }

    async def _empty_direct_fallback(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        return {"content": "", "success": False, "error": "runtime_provider_unavailable"}

    def _no_materialized(_adapter: Any, reported_paths: list[str]) -> tuple[list[str], list[str]]:
        return [], list(reported_paths)

    adapter._invoke_role_dialogue_with_timeout = _dialogue  # type: ignore[method-assign]
    adapter._invoke_direct_runtime_provider = _empty_direct_fallback  # type: ignore[method-assign]
    monkeypatch.setattr(execute_method, "_adapter_materialized_file_paths", _no_materialized)

    result = await adapter.execute(
        task_id=task_id,
        input_data={"task_id": task_id},
        context={"run_id": "run-no-physical-files"},
    )

    assert result["success"] is False
    assert result["error_code"] == "director.materialization.no_physical_files"
    assert result["error"] == "Director reported no physically materialized changed files"
    assert result["failure_stage"] == "director_materialization"
    assert result["root_cause_hint"] == "Director reported no physically materialized changed files"
    assert result["changed_files"] == []
    assert result["new_files"] == []
    assert result["modified_files"] == []
    assert result["reported_changed_files"] == ["src/app.py"]
    assert result["unmaterialized_reported_changed_files"] == ["src/app.py"]
    assert result["qa_required_for_final_verdict"] is True
    assert result["artifacts"] == []
    assert result["materialization_mode"] == "write_tool_and_workspace_diff"
    assert any(
        signal.get("code") == "director.materialization.unmaterialized_reported_files"
        for signal in result.get("decision_signals", [])
        if isinstance(signal, dict)
    )
    updated = adapter.task_runtime.get_task(task_id)
    assert updated is not None
    assert str(updated.get("status") or "").lower() == "failed"
