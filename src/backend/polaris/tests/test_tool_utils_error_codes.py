"""Tool execution error code tests.

These tests verify error code mapping in the tool execution pipeline.
Imports migrated from core.polaris_loop.director_tooling.executor_core and
core.polaris_loop.tool_contract to polaris.kernelone.tool_execution.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

if importlib.util.find_spec("polaris.kernelone.tool_execution") is None:
    pytest.skip("Module not available: polaris.kernelone.tool_execution", allow_module_level=True)

from polaris.kernelone.tool_execution import validate_tool_step
from polaris.kernelone.tool_execution.executor_core import run_tool_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
LOOP_CORE_ROOT = BACKEND_ROOT / "core" / "polaris_loop"
for entry in (str(BACKEND_ROOT), str(LOOP_CORE_ROOT)):
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)


def _state(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_full=str(workspace),
        npm_timeout=0,
        events_full="",
        current_run_id="",
        cache_root_full="",
        current_task_id="TASK-001",
        current_task_fingerprint="fp-001",
        current_pm_iteration=1,
        current_director_iteration=1,
    )


def test_validate_tool_step_reports_invalid_args() -> None:
    ok, code, _message = validate_tool_step("repo_read_head", {"path": "server.py"})
    assert ok is False
    assert code == "INVALID_TOOL_ARGS"


def test_run_tool_plan_infers_path_not_file_error_code(monkeypatch, tmp_path: Path) -> None:
    class _Result:
        returncode = 1
        stdout = '{"ok": false, "tool": "repo_read_head", "error": "Not a file: server.py"}'
        stderr = ""

    def _fake_run(*_args, **_kwargs):
        return _Result()

    monkeypatch.setattr(
        "polaris.kernelone.tool_execution.executor_core.subprocess.run",
        _fake_run,
    )

    outputs = run_tool_plan(
        _state(tmp_path),
        [{"tool": "repo_read_head", "args": {"file": "server.py", "n": 10}}],
        str(tmp_path / "tool.log"),
        {},
        {},
    )
    assert outputs[0]["ok"] is False
    assert outputs[0]["error_code"] == "PATH_NOT_FILE"


def test_run_tool_plan_infers_path_not_directory_error_code(monkeypatch, tmp_path: Path) -> None:
    class _Result:
        returncode = 1
        stdout = '{"ok": false, "tool": "repo_tree", "error": "Not a directory: src"}'
        stderr = ""

    def _fake_run(*_args, **_kwargs):
        return _Result()

    monkeypatch.setattr(
        "polaris.kernelone.tool_execution.executor_core.subprocess.run",
        _fake_run,
    )

    outputs = run_tool_plan(
        _state(tmp_path),
        [{"tool": "repo_tree", "args": {"path": "src", "depth": 2}}],
        str(tmp_path / "tool.log"),
        {},
        {},
    )
    assert outputs[0]["ok"] is False
    assert outputs[0]["error_code"] == "PATH_NOT_DIRECTORY"


def test_run_tool_plan_sets_runtime_error_code_when_no_json_stdout(monkeypatch, tmp_path: Path) -> None:
    class _Result:
        returncode = 1
        stdout = ""
        stderr = "runtime failure"

    def _fake_run(*_args, **_kwargs):
        return _Result()

    monkeypatch.setattr(
        "polaris.kernelone.tool_execution.executor_core.subprocess.run",
        _fake_run,
    )

    outputs = run_tool_plan(
        _state(tmp_path),
        [{"tool": "repo_tree", "args": {"path": "src", "depth": 1}}],
        str(tmp_path / "tool.log"),
        {},
        {},
    )
    assert outputs[0]["ok"] is False
    assert outputs[0]["error_code"] == "TOOL_RUNTIME_ERROR"
