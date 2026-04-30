from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _load_loop_director():
    module_path = BACKEND_ROOT / "scripts" / "loop-director.py"
    spec = importlib.util.spec_from_file_location("loop_director_note", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_pm_tasks_builds_description_with_constraints_and_acceptance() -> None:
    loop_director = _load_loop_director()
    payload = {
        "tasks": [
            {
                "id": "TASK-001",
                "title": "Build weather CLI",
                "goal": "Create a CLI to render weather info",
                "acceptance": ["Supports --city", "Shows temperature in C"],
                "constraints": ["No third-party dependencies"],
                "target_files": ["src/main.py", "README.md"],
                "scope_paths": ["src"],
            }
        ]
    }
    tasks = loop_director.extract_pm_tasks(payload)
    assert len(tasks) == 1
    description = tasks[0]["description"]
    assert "Goal: Create a CLI to render weather info" in description
    assert "Acceptance:" in description
    assert "Constraints:" in description
    assert tasks[0]["metadata"]["target_files"] == ["src/main.py", "README.md"]
    assert tasks[0]["metadata"]["scope_paths"] == ["src"]


def test_extract_pm_tasks_preserves_task_id_in_metadata() -> None:
    loop_director = _load_loop_director()
    payload = {
        "tasks": [
            {
                "id": "TASK-002",
                "title": "Update docs",
                "goal": "Adjust README intro",
            }
        ]
    }
    tasks = loop_director.extract_pm_tasks(payload)
    assert tasks[0]["task_id"] == "TASK-002"
