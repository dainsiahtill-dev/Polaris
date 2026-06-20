from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


class _FakeDirectorAdapter:
    def __init__(self, *, materialize: bool = True) -> None:
        self.materialize = materialize
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((task_id, input_data, context))
        if self.materialize:
            workspace = Path(str(context.get("workspace") or ""))
            target = workspace / "src/fish/arena.ts"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export const arena = 'fish';\n", encoding="utf-8")
        return {
            "success": True,
            "task_id": task_id,
            "changed_files": ["src/fish/arena.ts"],
            "new_files": ["src/fish/arena.ts"],
            "modified_files": [],
            "qa_required_for_final_verdict": True,
            "materialization_mode": "write_tool_and_workspace_diff",
        }


def _execution_payload(tmp_path: Path, cache_root: Path) -> dict[str, Any]:
    return {
        "phase": "execution",
        "task_id": "task-fish-arena",
        "workspace": str(tmp_path),
        "run_id": "run-fish",
        "task": {
            "id": "task-fish-arena",
            "title": "Implement multiplayer fish arena",
            "goal": "Create a playable big-fish-eats-small-fish arena.",
            "target_files": ["src/fish/arena.ts"],
            "scope_paths": ["src/fish"],
            "acceptance_criteria": ["arena has fish movement and scoring"],
        },
        "context": {
            "workspace": str(tmp_path),
            "metadata": {"target_files": ["src/fish/arena.ts"]},
        },
        "director_config": {"type": "auto"},
        "runtime_metadata": {"cache_root_full": str(cache_root)},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name",
    [
        "polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.director_activities",
        "polaris.cells.orchestration.workflow_activity.internal.activities.director_activities",
    ],
)
async def test_director_workflow_execution_phase_routes_to_canonical_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    module = pytest.importorskip(module_name)
    fake_adapter = _FakeDirectorAdapter()

    def fake_create_role_adapter(role_id: str, workspace: str) -> _FakeDirectorAdapter:
        assert role_id == "director"
        assert workspace == str(tmp_path)
        return fake_adapter

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.public.service.create_role_adapter",
        fake_create_role_adapter,
    )
    # workflow_runtime resolves the adapter through the inverted factory port
    # (get_orchestration_role_adapter_factory), which reads the module global
    # captured at registration time — so patch that global too. workflow_activity
    # still uses the direct create_role_adapter import patched above.
    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.internal."
        "unified_orchestration_service._role_adapter_factory",
        fake_create_role_adapter,
    )

    cache_root = tmp_path / ".polaris-test-cache"
    result = await module.execute_task_phase(_execution_payload(tmp_path, cache_root))

    assert result["success"] is True
    assert result["changed_files"] == ["src/fish/arena.ts"]
    assert fake_adapter.calls
    task_id, input_data, context = fake_adapter.calls[-1]
    assert task_id == "task-fish-arena"
    assert input_data["metadata"]["source"] in {
        "workflow_runtime.director_activity",
        "workflow_activity.director_activity",
    }
    assert context["metadata"]["workflow_activity"] in {
        "workflow_runtime.director_execution",
        "workflow_activity.director_execution",
    }

    result_path = cache_root / "workflow" / "run-fish" / "task-fish-arena" / "director.result.json"
    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    assert persisted["adapter_result"]["materialization_mode"] == "write_tool_and_workspace_diff"
    assert persisted["changed_files"] == ["src/fish/arena.ts"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module_name",
    [
        "polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.activities.director_activities",
        "polaris.cells.orchestration.workflow_activity.internal.activities.director_activities",
    ],
)
async def test_director_workflow_execution_rejects_unmaterialized_changed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    module = pytest.importorskip(module_name)
    fake_adapter = _FakeDirectorAdapter(materialize=False)

    def fake_create_role_adapter(role_id: str, workspace: str) -> _FakeDirectorAdapter:
        assert role_id == "director"
        assert workspace == str(tmp_path)
        return fake_adapter

    monkeypatch.setattr(
        "polaris.cells.roles.adapters.public.service.create_role_adapter",
        fake_create_role_adapter,
    )
    # workflow_runtime resolves the adapter through the inverted factory port
    # (get_orchestration_role_adapter_factory), which reads the module global
    # captured at registration time — so patch that global too. workflow_activity
    # still uses the direct create_role_adapter import patched above.
    monkeypatch.setattr(
        "polaris.cells.orchestration.workflow_runtime.internal."
        "unified_orchestration_service._role_adapter_factory",
        fake_create_role_adapter,
    )

    cache_root = tmp_path / ".polaris-test-cache"
    result = await module.execute_task_phase(_execution_payload(tmp_path, cache_root))

    assert result["success"] is False
    assert result.get("changed_files", []) == []
    assert result["payload"].get("changed_files", []) == []
    assert "did not materialize" in result["summary"]

    result_path = cache_root / "workflow" / "run-fish" / "task-fish-arena" / "director.result.json"
    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    assert persisted["changed_files"] == []
    assert persisted["reported_changed_files"] == ["src/fish/arena.ts"]
    assert persisted["unmaterialized_reported_changed_files"] == ["src/fish/arena.ts"]
