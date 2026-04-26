import json
import os
import sys
import warnings
from pathlib import Path

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.delivery.cli.pm.director_interface_core import DirectorTask, ScriptDirectorAdapter  # noqa: E402


def _make_adapter(
    *,
    tmp_path: Path,
    timeout: int | None,
    task_timeout: int | None = None,
):
    script_path = tmp_path / "src" / "backend" / "polaris" / "delivery" / "cli" / "loop-director.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    pm_task_path = tmp_path / "pm_tasks.contract.json"
    pm_task_path.write_text(
        json.dumps({"tasks": [{"id": "TASK-1", "title": "demo"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result_path = tmp_path / "director.result.json"
    result_path.write_text(
        json.dumps({"status": "success", "changed_files": []}, ensure_ascii=False),
        encoding="utf-8",
    )

    config = {
        "project_root": tmp_path,
        "script": "src/backend/polaris/delivery/cli/loop-director.py",
        "pm_task_path": str(pm_task_path),
        "director_result_path": str(result_path),
        "timeout": timeout,
    }
    if task_timeout is not None:
        config["task_timeout"] = task_timeout

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        adapter = ScriptDirectorAdapter(Path(tmp_path), config=config)
    return adapter


def _sample_task() -> DirectorTask:
    return DirectorTask(
        task_id="TASK-1",
        goal="Implement sample task",
        target_files=[],
        acceptance_criteria=[],
        constraints=[],
        context={},
    )


def test_script_director_passes_task_timeout_with_process_margin(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class _Process:
        returncode = 0

        def __init__(self, cmd, cwd=None, stdout=None, stderr=None) -> None:
            captured["cmd"] = list(cmd)
            captured["cwd"] = cwd

        def communicate(self, timeout=None):
            captured["process_timeout"] = timeout
            return b"", b""

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", _Process)
    adapter = _make_adapter(tmp_path=tmp_path, timeout=600)

    result = adapter.execute(_sample_task())
    assert result.success is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--timeout" in cmd
    timeout_index = cmd.index("--timeout")
    assert cmd[timeout_index + 1] == "570"


def test_script_director_respects_explicit_task_timeout_override(tmp_path, monkeypatch):
    captured: dict[str, object] = {}

    class _Process:
        returncode = 0

        def __init__(self, cmd, cwd=None, stdout=None, stderr=None) -> None:
            captured["cmd"] = list(cmd)

        def communicate(self, timeout=None):
            return b"", b""

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", _Process)
    adapter = _make_adapter(tmp_path=tmp_path, timeout=600, task_timeout=120)

    result = adapter.execute(_sample_task())
    assert result.success is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    timeout_index = cmd.index("--timeout")
    assert cmd[timeout_index + 1] == "120"


def test_script_director_uses_default_task_timeout_when_process_timeout_disabled(
    tmp_path,
    monkeypatch,
):
    captured: dict[str, object] = {}

    class _Process:
        returncode = 0

        def __init__(self, cmd, cwd=None, stdout=None, stderr=None) -> None:
            captured["cmd"] = list(cmd)

        def communicate(self, timeout=None):
            captured["process_timeout"] = timeout
            return b"", b""

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", _Process)
    monkeypatch.delenv("KERNELONE_DIRECTOR_TASK_TIMEOUT", raising=False)
    adapter = _make_adapter(tmp_path=tmp_path, timeout=None)

    result = adapter.execute(_sample_task())
    assert result.success is True
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    timeout_index = cmd.index("--timeout")
    assert cmd[timeout_index + 1] == "600"
