from __future__ import annotations

from pathlib import Path

from polaris.delivery.cli.pm.chief_engineer import run_chief_engineer_analysis


def test_chief_engineer_analysis_writes_blueprint_for_director_task(tmp_path: Path) -> None:
    run_blueprint_path = tmp_path / "run" / "contracts" / "chief_engineer.blueprint.json"
    runtime_blueprint_path = tmp_path / "runtime" / "contracts" / "chief_engineer.blueprint.json"
    source_file = tmp_path / "src" / "server" / "index.ts"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text(
        "export function joinRoom() { return true; }\n",
        encoding="utf-8",
    )

    result = run_chief_engineer_analysis(
        tasks=[
            {
                "id": "TASK-COMPLEX",
                "assigned_to": "Director",
                "status": "todo",
                "title": "Wire room API",
                "goal": "Implement joinRoom and broadcastToRoom.",
                "target_files": ["src/server/index.ts"],
                "scope_paths": ["src/server"],
                "acceptance_criteria": ["implement joinRoom", "implement broadcastToRoom"],
                "constraints": [],
            }
        ],
        workspace_full=str(tmp_path),
        run_id="pm-00042",
        pm_iteration=42,
        run_blueprint_path=str(run_blueprint_path),
        runtime_blueprint_path=str(runtime_blueprint_path),
    )

    assert result["ran"] is True
    assert result["task_update_count"] == 1
    assert run_blueprint_path.is_file()
    assert runtime_blueprint_path.is_file()
    task_update = result["task_update_map"]["TASK-COMPLEX"]
    assert task_update["task_id"] == "TASK-COMPLEX"
    assert "construction_plan" in task_update
    assert "src/server/index.ts" in task_update["scope_for_apply"]
