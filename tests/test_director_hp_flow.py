from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_loop_director_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "src" / "backend" / "scripts" / "loop-director.py"
    if not module_path.is_file():
        raise RuntimeError(f"Failed to locate loop-director.py: {module_path}")
    spec = importlib.util.spec_from_file_location("loop_director", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_map_pm_priority_to_director_supports_int_and_string() -> None:
    mod = _load_loop_director_module()
    assert mod.map_pm_priority_to_director(1) == mod.TaskPriority.CRITICAL
    assert mod.map_pm_priority_to_director(8) == mod.TaskPriority.LOW
    assert mod.map_pm_priority_to_director("urgent") == mod.TaskPriority.CRITICAL
    assert mod.map_pm_priority_to_director("normal") == mod.TaskPriority.MEDIUM


def test_extract_pm_tasks_builds_description_and_preserves_ids() -> None:
    mod = _load_loop_director_module()
    payload = {
        "tasks": [
            {
                "id": "TASK-FAST-1",
                "title": "Fix typo in UI",
                "goal": "Correct typo in card label",
                "acceptance_criteria": ["UI label corrected"],
                "constraints": ["No API behavior changes"],
                "target_files": ["src/frontend/card.tsx"],
                "scope_paths": ["src/frontend"],
            }
        ]
    }
    tasks = mod.extract_pm_tasks(payload)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["task_id"] == "TASK-FAST-1"
    assert task["target_files"] == ["src/frontend/card.tsx"]
    assert task["scope_paths"] == ["src/frontend"]
    assert "Goal: Correct typo in card label" in task["description"]
    assert "Acceptance:" in task["description"]


def test_extract_pm_tasks_accepts_acceptance_alias() -> None:
    mod = _load_loop_director_module()
    payload = {
        "tasks": [
            {
                "id": "TASK-FAST-2",
                "title": "README update",
                "goal": "Document setup",
                "acceptance": ["README contains quickstart"],
                "target_files": ["README.md"],
                "scope_paths": ["."],
            }
        ]
    }
    tasks = mod.extract_pm_tasks(payload)
    assert len(tasks) == 1
    assert "Acceptance:" in tasks[0]["description"]
