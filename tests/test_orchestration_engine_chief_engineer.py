import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_orchestration_engine():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "src" / "backend" / "scripts"
    project_root = repo_root / "src" / "backend"
    loop_module_dir = project_root / "core" / "polaris_loop"
    for entry in (str(scripts_dir), str(project_root), str(loop_module_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return importlib.import_module("pm.orchestration_engine")


def test_chief_engineer_auto_skips_low_complexity_round(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "id": "TASK-SIMPLE",
            "assigned_to": "Director",
            "status": "todo",
            "target_files": ["src/main.py"],
            "acceptance_criteria": ["main runs"],
        }
    ]

    result = mod._run_pre_dispatch_chief_engineer(
        args=SimpleNamespace(chief_engineer_mode="auto"),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00041",
        iteration=41,
        tasks=tasks,
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
    )

    assert result["ran"] is False
    assert result["reason"] == "auto_skipped_low_complexity"
    assert Path(result["result_path"]).is_file()


def test_chief_engineer_mode_on_enriches_director_task_contract(tmp_path):
    mod = _load_orchestration_engine()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "id": "TASK-COMPLEX",
            "assigned_to": "Director",
            "status": "todo",
            "target_files": ["src/server/index.ts"],
            "acceptance_criteria": ["implement joinRoom and broadcastToRoom"],
            "constraints": [],
        }
    ]

    def _analysis_runner(**kwargs):
        return {
            "reason": "chief_engineer_updated",
            "summary": "ChiefEngineer generated construction blueprint.",
            "task_update_count": 1,
            "hard_failure": False,
            "stats": {},
            "task_update_map": {
                "TASK-COMPLEX": {
                    "task_id": "TASK-COMPLEX",
                    "scope_for_apply": ["src/server/index.ts", "src/server/rooms.ts"],
                    "missing_targets": ["src/server/rooms.ts"],
                    "construction_plan": {
                        "file_plans": [
                            {
                                "path": "src/server/index.ts",
                                "method_names": ["joinRoom", "broadcastToRoom"],
                            }
                        ],
                        "method_catalog": ["joinRoom", "broadcastToRoom"],
                    },
                }
            },
        }

    result = mod._run_pre_dispatch_chief_engineer(
        args=SimpleNamespace(chief_engineer_mode="on"),
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_dir=str(run_dir),
        run_id="pm-00042",
        iteration=42,
        tasks=tasks,
        run_events=str(tmp_path / "events.jsonl"),
        dialogue_full=str(tmp_path / "dialogue.jsonl"),
        analysis_runner=_analysis_runner,
    )

    assert result["ran"] is True
    assert result["task_update_count"] == 1
    enriched = tasks[0]
    assert "chief_engineer" in enriched
    assert "construction_plan" in enriched
    assert "src/server/rooms.ts" in enriched["target_files"]
    assert "src/server/rooms.ts" in enriched["scope_paths"]
    assert any("[ChiefEngineer]" in item for item in enriched.get("constraints") or [])
    assert Path(result["result_path"]).is_file()
