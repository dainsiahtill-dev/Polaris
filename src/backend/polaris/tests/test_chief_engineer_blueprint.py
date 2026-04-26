import importlib
import sys
from pathlib import Path


def _load_chief_engineer():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "src" / "backend" / "scripts"
    project_root = repo_root / "src" / "backend"
    loop_module_dir = project_root / "core" / "polaris_loop"
    for entry in (str(scripts_dir), str(project_root), str(loop_module_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return importlib.import_module("pm.chief_engineer")


def test_chief_engineer_analysis_generates_method_level_construction_plan(tmp_path):
    mod = _load_chief_engineer()
    index_path = tmp_path / "src" / "server" / "index.ts"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "\n".join(
            [
                "import { RoomManager } from './rooms';",
                "import { MessageManager } from './messages';",
                "export class ChatServer {",
                "  constructor(private rooms: RoomManager, private messages: MessageManager) {}",
                "}",
            ]
        ),
        encoding="utf-8",
    )

    run_blueprint_path = tmp_path / "run" / "contracts" / "chief_engineer.blueprint.json"
    runtime_blueprint_path = (
        tmp_path / ".polaris" / "runtime" / "contracts" / "chief_engineer.blueprint.json"
    )
    payload = mod.run_chief_engineer_analysis(
        tasks=[
            {
                "id": "TASK-CHAT",
                "assigned_to": "Director",
                "status": "todo",
                "title": "Build websocket chat",
                "goal": "Complete room/message managers and server flow",
                "target_files": [
                    "src/server/index.ts",
                    "src/server/rooms.ts",
                    "src/server/messages.ts",
                ],
                "acceptance_criteria": [
                    "Implement joinRoom leaveRoom removeClient in RoomManager",
                    "Implement broadcastToRoom saveSnapshot in MessageManager",
                ],
            }
        ],
        workspace_full=str(tmp_path),
        run_id="pm-00021",
        pm_iteration=21,
        run_blueprint_path=str(run_blueprint_path),
        runtime_blueprint_path=str(runtime_blueprint_path),
    )

    assert payload["ran"] is True
    assert payload["task_update_count"] == 1
    task_update = payload["task_updates"][0]
    assert "src/server/rooms.ts" in task_update["missing_targets"]
    assert "src/server/messages.ts" in task_update["missing_targets"]
    assert task_update["verify_ready"] is False

    construction_plan = task_update["construction_plan"]
    assert isinstance(construction_plan.get("file_plans"), list)
    method_catalog = construction_plan.get("method_catalog") or []
    assert "joinRoom" in method_catalog
    assert "broadcastToRoom" in method_catalog
    assert run_blueprint_path.is_file()
    assert runtime_blueprint_path.is_file()


def test_chief_engineer_single_task_entrypoint(tmp_path):
    mod = _load_chief_engineer()
    task = {
        "id": "TASK-ONE",
        "assigned_to": "Director",
        "status": "todo",
        "title": "Implement monitor endpoint",
        "goal": "Expose heartbeat and status methods",
        "target_files": ["src/monitor/index.py"],
        "acceptance_criteria": ["implement getHeartbeat and getStatus"],
    }
    result = mod.run_chief_engineer_task(
        task=task,
        workspace_full=str(tmp_path),
        cache_root_full="",
        run_id="pm-00031",
        pm_iteration=31,
    )
    assert result["ok"] is True
    task_update = result["task_update"]
    assert task_update["task_id"] == "TASK-ONE"
    assert "construction_plan" in task_update
