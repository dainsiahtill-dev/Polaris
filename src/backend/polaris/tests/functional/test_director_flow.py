from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_loop_director():
    repo_root = Path(__file__).resolve().parents[2]
    backend_root = repo_root / "src" / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    module_path = backend_root / "scripts" / "loop-director.py"
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
        def __init__(self, *_args, **_kwargs):
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
