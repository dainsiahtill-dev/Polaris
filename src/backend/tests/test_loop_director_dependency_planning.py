from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_loop_director_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "src" / "backend" / "polaris" / "delivery" / "cli" / "loop-director.py"
    if not module_path.is_file():
        module_path = repo_root / "src" / "backend" / "scripts" / "loop-director.py"
    spec = importlib.util.spec_from_file_location("loop_director_dependency_planning", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_task_execution_order_prefers_dependency_roots() -> None:
    loop_director = _load_loop_director_module()
    tasks = [
        {
            "task_id": "TASK-2",
            "subject": "dependent",
            "description": "",
            "priority": 1,
            "blocked_by": ["TASK-1"],
            "metadata": {},
        },
        {
            "task_id": "TASK-1",
            "subject": "root",
            "description": "",
            "priority": 1,
            "blocked_by": [],
            "metadata": {},
        },
    ]

    ordered, warnings = loop_director._plan_task_execution_order(tasks, limit=1)
    assert len(ordered) == 1
    assert ordered[0]["task_id"] == "TASK-1"
    assert warnings == []


def test_plan_task_execution_order_ignores_unknown_dependencies() -> None:
    loop_director = _load_loop_director_module()
    tasks = [
        {
            "task_id": "TASK-1",
            "subject": "root",
            "description": "",
            "priority": 1,
            "blocked_by": ["UNKNOWN-TASK"],
            "metadata": {},
        }
    ]

    ordered, warnings = loop_director._plan_task_execution_order(tasks, limit=1)
    assert len(ordered) == 1
    assert ordered[0]["task_id"] == "TASK-1"
    assert any("unknown_dependencies_ignored:TASK-1" in item for item in warnings)


@pytest.mark.asyncio
async def test_runner_uses_planned_order_for_single_iteration(monkeypatch) -> None:
    loop_director = _load_loop_director_module()
    executed: list[str] = []

    class _FakeDirector:
        async def stop(self) -> None:
            return None

    async def _fake_initialize(self) -> None:
        self.director = _FakeDirector()

    async def _fake_execute_task(self, task_data: dict, timeout: int = 300) -> bool:
        executed.append(str(task_data.get("task_id") or ""))
        task_data["_runtime_task_id"] = f"runtime-{task_data.get('task_id')}"
        self.results["tasks_executed"] += 1
        return True

    monkeypatch.setattr(loop_director.DirectorV2Runner, "initialize", _fake_initialize)
    monkeypatch.setattr(loop_director.DirectorV2Runner, "execute_task", _fake_execute_task)
    monkeypatch.setattr(
        loop_director,
        "load_pm_task_contract",
        lambda _path: {
            "tasks": [
                {"id": "TASK-2", "title": "dependent", "depends_on": ["TASK-1"]},
                {"id": "TASK-1", "title": "root", "depends_on": []},
            ]
        },
    )

    runner = loop_director.DirectorV2Runner(workspace="C:/Temp/test", config=object())
    result = await runner.run(pm_task_path="ignored.json", iterations=1, timeout=30)

    assert executed == ["TASK-1"]
    assert result["tasks_executed"] == 1
