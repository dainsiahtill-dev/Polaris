"""Tests for Director result artifact reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal import director_result_artifacts as artifacts


class _FakeTaskRuntimeService:
    rows: list[dict[str, Any]] = []

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def list_task_rows(self) -> list[dict[str, Any]]:
        return list(self.rows)


def test_build_director_result_waits_until_all_contract_tasks_terminal(monkeypatch) -> None:
    monkeypatch.setattr(artifacts, "TaskRuntimeService", _FakeTaskRuntimeService)
    monkeypatch.setattr(
        artifacts,
        "_read_pm_contract_rows",
        lambda _workspace: [
            {"id": "T01", "title": "first", "assigned_to": "director"},
            {"id": "T02", "title": "second", "assigned_to": "director"},
        ],
    )
    _FakeTaskRuntimeService.rows = [
        {
            "id": 1,
            "status": "completed",
            "metadata": {"pm_task_id": "T01", "adapter_result": {"tools_executed": 2}},
        }
    ]

    payload, terminal = artifacts.build_director_result_from_runtime(workspace="C:/project", run_id="director-1")

    assert payload is None
    assert terminal is False


def test_persist_director_result_from_runtime_writes_success_artifact(tmp_path: Path, monkeypatch) -> None:
    result_path = tmp_path / "runtime" / "results" / "director.result.json"
    run_result_path = tmp_path / "runtime" / "runs" / "director-2" / "results" / "director.result.json"
    monkeypatch.setattr(artifacts, "TaskRuntimeService", _FakeTaskRuntimeService)
    monkeypatch.setattr(
        artifacts,
        "_read_pm_contract_rows",
        lambda _workspace: [
            {"id": "T01", "title": "first", "assigned_to": "director"},
            {"id": "T02", "title": "second", "assigned_to": "director"},
        ],
    )

    def resolve_path(_workspace: str, _cache_root: str, logical_path: str) -> str:
        return str(tmp_path / logical_path)

    monkeypatch.setattr(artifacts, "resolve_artifact_path", resolve_path)
    _FakeTaskRuntimeService.rows = [
        {
            "id": 1,
            "status": "completed",
            "metadata": {
                "pm_task_id": "T01",
                "adapter_result": {"tools_executed": 2, "new_files": ["src/a.ts"]},
                "runtime_execution": {"last_result_summary": "changed_files=1"},
            },
        },
        {
            "id": 2,
            "status": "completed",
            "metadata": {
                "pm_task_id": "T02",
                "adapter_result": {"tools_executed": 3, "modified_files": ["src/b.ts"]},
                "runtime_execution": {"last_result_summary": "changed_files=1"},
            },
        },
    ]

    payload = artifacts.persist_director_result_from_runtime(workspace="C:/project", run_id="director-2")

    assert payload is not None
    assert payload["status"] == "success"
    assert payload["successes"] == 2
    assert payload["total"] == 2
    assert result_path.is_file()
    assert run_result_path.is_file()
    persisted = json.loads(result_path.read_text(encoding="utf-8"))
    assert persisted["runtime_result_path"] == str(result_path)
    assert persisted["run_result_path"] == str(run_result_path)
    assert persisted["task_results"][0]["changed_files"] == ["src/a.ts"]
    run_persisted = json.loads(run_result_path.read_text(encoding="utf-8"))
    assert run_persisted == persisted


def test_build_integration_qa_tasks_from_director_result_projects_director_schema() -> None:
    tasks = artifacts.build_integration_qa_tasks_from_director_result(
        {
            "task_results": [
                {
                    "task_id": "T01",
                    "status": "completed",
                    "title": "Implement feature",
                    "summary": "changed_files=2",
                    "changed_files": ["src/a.ts", "src/b.ts"],
                    "tools_executed": 4,
                }
            ]
        }
    )

    assert tasks == [
        {
            "id": "T01",
            "assigned_to": "director",
            "status": "completed",
            "type": "code",
            "title": "Implement feature",
            "summary": "changed_files=2",
            "target_files": ["src/a.ts", "src/b.ts"],
            "scope_paths": ["src/a.ts", "src/b.ts"],
            "metadata": {
                "source": "director_result_artifact",
                "tools_executed": 4,
            },
        }
    ]
