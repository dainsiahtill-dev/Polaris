from __future__ import annotations

from typing import Any

import pytest
from polaris.application.orchestration.director_orchestrator import (
    DirectorExecutionConfig,
    DirectorOrchestrator,
)


class _RecordingTaskBoard:
    def __init__(self) -> None:
        self.updates: list[tuple[int, dict[str, Any]]] = []

    def update(self, task_id: int, **kwargs: Any) -> None:
        self.updates.append((task_id, dict(kwargs)))


class _FakeDirectorAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((task_id, input_data, context))
        return {
            "success": True,
            "task_id": task_id,
            "changed_files": ["src/fish/arena.ts"],
            "qa_required_for_final_verdict": True,
            "materialization_mode": "write_tool_and_workspace_diff",
        }


@pytest.mark.asyncio
async def test_director_orchestrator_execute_task_uses_canonical_adapter(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_adapter = _FakeDirectorAdapter()

    def fake_create_role_adapter(role_id: str, workspace: str) -> _FakeDirectorAdapter:
        assert role_id == "director"
        assert workspace == str(tmp_path)
        return fake_adapter

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.public.service.create_role_adapter",
        fake_create_role_adapter,
    )
    orchestrator = DirectorOrchestrator(DirectorExecutionConfig(workspace=str(tmp_path), execution_mode="serial"))
    board = _RecordingTaskBoard()
    orchestrator._task_board = board

    result = await orchestrator.execute_task(
        {
            "id": "1",
            "subject": "Implement fish arena",
            "description": "Create multiplayer fish arena code.",
        }
    )

    assert result.success is True
    assert result.status == "completed"
    assert result.metadata["adapter"] == "roles.adapters.director"
    assert result.metadata["changed_files"] == ["src/fish/arena.ts"]
    assert fake_adapter.calls
    call_task_id, input_data, context = fake_adapter.calls[-1]
    assert call_task_id == "1"
    assert input_data["metadata"]["source"] == "application.director_orchestrator"
    assert context["metadata"] == {"source": "application.director_orchestrator"}
    assert board.updates
    task_id, update = board.updates[-1]
    assert task_id == 1
    assert update["status"] == "completed"
    adapter_result = update["metadata"]["adapter_result"]
    assert adapter_result["materialization_mode"] == "write_tool_and_workspace_diff"
