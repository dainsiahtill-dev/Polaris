from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_loop_director():
    backend_root = Path(__file__).resolve().parents[3]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    module_path = backend_root / "polaris" / "delivery" / "cli" / "loop-director.py"
    spec = importlib.util.spec_from_file_location("loop_director", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-director.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_director_async_main_writes_result_json(tmp_path, monkeypatch):
    loop_director = _load_loop_director()

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    pm_task_path = workspace / "pm_tasks.contract.json"
    pm_task_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "PM-NEW-1",
                        "title": "Implement greeting",
                        "goal": "Create greet helper",
                        "priority": 1,
                        "target_files": ["src/example.py"],
                        "metadata": {"detected_language": "python"},
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result_path = workspace / "runtime" / "results" / "director.result.json"

    class _FakeRunner:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, **_kwargs):
            return {
                "success": True,
                "tasks_executed": 1,
                "files_created": ["src/example.py"],
                "errors": [],
            }

    monkeypatch.setattr(loop_director, "DirectorV2Runner", _FakeRunner)

    args = SimpleNamespace(
        workspace=str(workspace),
        iterations=1,
        timeout=1,
        pm_task_path=str(pm_task_path),
        director_result_path=str(result_path),
        model="fake",
        prompt_profile="generic",
        show_output=False,
        no_rollback_on_fail=False,
        log_path="",
        events_path="",
        planner_response_path="",
        ollama_response_path="",
        qa_response_path="",
        reviewer_response_path="",
    )

    exit_code = loop_director.asyncio.run(loop_director.async_main(args))
    assert exit_code == 0
    assert result_path.is_file()

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["status"] == "success"
    assert payload["tasks_executed"] == 1
    assert "src/example.py" in payload["changed_files"]


def test_director_runner_initializes_service_before_executing_tasks(tmp_path, monkeypatch):
    loop_director = _load_loop_director()

    pm_task_path = tmp_path / "pm_tasks.contract.json"
    pm_task_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "PM-INIT-1",
                        "title": "Initialize Director",
                        "goal": "Director runner must initialize its service before executing tasks.",
                        "priority": 1,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    calls = []

    class _FakeDirector:
        async def stop(self):
            calls.append("stop")

    async def _fake_initialize(self):
        calls.append("initialize")
        self.director = _FakeDirector()

    async def _fake_execute_task(self, task_data, timeout):
        calls.append(("execute", task_data["task_id"], timeout))
        task_data["_runtime_task_id"] = f"runtime-{task_data['task_id']}"
        self.results["tasks_executed"] += 1
        return True

    monkeypatch.setattr(loop_director.DirectorV2Runner, "initialize", _fake_initialize)
    monkeypatch.setattr(loop_director.DirectorV2Runner, "execute_task", _fake_execute_task)

    config = loop_director.DirectorConfig(workspace=str(tmp_path))
    runner = loop_director.DirectorV2Runner(str(tmp_path), config)

    result = loop_director.asyncio.run(
        runner.run(
            pm_task_path=str(pm_task_path),
            iterations=1,
            timeout=30,
        )
    )

    assert result["success"] is True
    assert result["errors"] == []
    assert calls == ["initialize", ("execute", "PM-INIT-1", 30), "stop"]
