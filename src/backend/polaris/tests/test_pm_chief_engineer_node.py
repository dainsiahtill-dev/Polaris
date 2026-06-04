from __future__ import annotations

import argparse
from typing import Any

from polaris.delivery.cli.pm.nodes.chief_engineer_node import ChiefEngineerNode
from polaris.delivery.cli.pm.nodes.protocols import RoleContext


def test_chief_engineer_node_applies_blueprint_updates_to_director_tasks(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_pre_dispatch_chief_engineer(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "ran": True,
            "hard_failure": False,
            "reason": "chief_engineer_updated",
            "summary": "ChiefEngineer produced a construction blueprint.",
            "task_update_count": 1,
            "blueprint_path": "run/contracts/chief_engineer.blueprint.json",
            "runtime_blueprint_path": "runtime/contracts/chief_engineer.blueprint.json",
            "task_update_map": {
                "TASK-1": {
                    "task_id": "TASK-1",
                    "scope_for_apply": ["src/api.ts", "src/router.ts"],
                    "missing_targets": ["src/router.ts"],
                    "blueprint_scope": {"module": "api"},
                    "construction_plan": {
                        "file_plans": [
                            {
                                "path": "src/api.ts",
                                "method_names": ["registerRoutes"],
                            }
                        ],
                        "method_catalog": ["registerRoutes"],
                    },
                    "constraints": ["Preserve public API compatibility."],
                }
            },
        }

    monkeypatch.setattr(
        "polaris.cells.chief_engineer.blueprint.public.run_pre_dispatch_chief_engineer",
        fake_pre_dispatch_chief_engineer,
    )

    context = RoleContext(
        workspace_full="C:/workspace/project",
        cache_root_full="C:/workspace/project/.polaris",
        run_dir="C:/workspace/project/.polaris/runs/pm-00001",
        run_id="pm-00001",
        pm_iteration=1,
        args=argparse.Namespace(run_dir=""),
        events_path="C:/workspace/project/.polaris/events.jsonl",
        dialogue_path="C:/workspace/project/.polaris/dialogue.jsonl",
        last_tasks=[
            {
                "id": "TASK-1",
                "title": "Wire API routes",
                "assigned_to": "Director",
                "scope_paths": ["src/api.ts"],
                "constraints": ["Use existing style."],
            }
        ],
    )

    result = ChiefEngineerNode().execute(context)

    assert result.success is True
    assert result.next_role == "Director"
    assert captured["workspace_full"] == "C:/workspace/project"
    assert result.blueprint == {
        "blueprint_path": "run/contracts/chief_engineer.blueprint.json",
        "runtime_blueprint_path": "runtime/contracts/chief_engineer.blueprint.json",
    }
    [updated_task] = result.tasks
    assert updated_task["construction_plan"]["method_catalog"] == ["registerRoutes"]
    assert updated_task["chief_engineer"] == {
        "scope_for_apply": ["src/api.ts", "src/router.ts"],
        "missing_targets": ["src/router.ts"],
        "blueprint_scope": {"module": "api"},
    }
    assert updated_task["scope_paths"] == ["src/api.ts", "src/router.ts"]
    assert updated_task["constraints"] == [
        "Use existing style.",
        "Preserve public API compatibility.",
    ]
    assert result.metadata["task_update_count"] == 1
