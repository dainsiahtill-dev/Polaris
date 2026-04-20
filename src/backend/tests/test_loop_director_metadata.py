from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_loop_director_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "src" / "backend" / "polaris" / "delivery" / "cli" / "loop-director.py"
    if not module_path.is_file():
        module_path = repo_root / "src" / "backend" / "scripts" / "loop-director.py"
    spec = importlib.util.spec_from_file_location("loop_director_metadata", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_pm_tasks_includes_chief_engineer_plan_and_constraints() -> None:
    loop_director = _load_loop_director_module()
    contract = {
        "tasks": [
            {
                "id": "PM-0001-F2",
                "title": "Requirements implementation",
                "goal": "Implement core modules.",
                "description": "Use Rust conventions.",
                "priority": 1,
                "phase": "implementation",
                "constraints": ["Follow ChiefEngineer construction plan."],
                "metadata": {"detected_language": "rust"},
                "construction_plan": {
                    "file_plans": [
                        {"path": "src/service.rs", "method_names": ["fetch_feed"]}
                    ]
                },
            }
        ]
    }

    extracted = loop_director.extract_pm_tasks(contract)
    assert len(extracted) == 1
    task_data = extracted[0]
    metadata = task_data.get("metadata")
    description = str(task_data.get("description") or "")
    assert isinstance(metadata, dict)
    assert metadata.get("phase") == "implementation"
    assert "construction_plan" in metadata
    assert "Constraints:" in description
    assert "ChiefEngineer Construction Plan:" in description
