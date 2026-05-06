from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.domain.entities.policy import Policy
from polaris.domain.state_machine.phase_executor import PhaseExecutor
from polaris.domain.state_machine.task_phase import PhaseContext


def _write_package(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_node_checks_prefer_project_build_script(tmp_path, monkeypatch) -> None:
    _write_package(tmp_path / "package.json", {"scripts": {"build": "node scripts/build.mjs"}})
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    calls: list[str] = []

    class FakeCommandExecutionService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def parse_command(self, command: str, **kwargs: Any) -> str:
            calls.append(command)
            return command

        def run(self, request: str) -> dict[str, Any]:
            return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(
        "polaris.domain.state_machine.phase_executor.CommandExecutionService",
        FakeCommandExecutionService,
    )

    executor = PhaseExecutor(str(tmp_path), Policy())
    errors = executor._run_node_checks(PhaseContext(task_id="T1", workspace=str(tmp_path)))

    assert errors == []
    assert calls == ["npm run build"]


def test_node_checks_skip_tsc_when_compiler_is_not_available(tmp_path, monkeypatch) -> None:
    _write_package(tmp_path / "package.json", {"scripts": {}})
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    class FakeCommandExecutionService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def parse_command(self, command: str, **kwargs: Any) -> str:
            raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(
        "polaris.domain.state_machine.phase_executor.CommandExecutionService",
        FakeCommandExecutionService,
    )

    executor = PhaseExecutor(str(tmp_path), Policy())
    errors = executor._run_node_checks(PhaseContext(task_id="T1", workspace=str(tmp_path)))

    assert errors == []


def test_node_check_failure_preserves_stderr(tmp_path, monkeypatch) -> None:
    _write_package(tmp_path / "package.json", {"scripts": {"build": "node scripts/build.mjs"}})

    class FakeCommandExecutionService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def parse_command(self, command: str, **kwargs: Any) -> str:
            return command

        def run(self, request: str) -> dict[str, Any]:
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "build tool missing",
            }

    monkeypatch.setattr(
        "polaris.domain.state_machine.phase_executor.CommandExecutionService",
        FakeCommandExecutionService,
    )

    executor = PhaseExecutor(str(tmp_path), Policy())
    errors = executor._run_node_checks(PhaseContext(task_id="T1", workspace=str(tmp_path)))

    assert len(errors) == 1
    assert "Node build failed" in errors[0]
    assert "build tool missing" in errors[0]


def test_python_mypy_failure_preserves_stderr(tmp_path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")

    class FakeCommandExecutionService:
        def __init__(self, workspace: str) -> None:
            self.workspace = workspace

        def parse_command(self, command: str, **kwargs: Any) -> str:
            return command

        def run(self, request: str) -> dict[str, Any]:
            return {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "mypy executable missing",
            }

    monkeypatch.setattr(
        "polaris.domain.state_machine.phase_executor.CommandExecutionService",
        FakeCommandExecutionService,
    )

    executor = PhaseExecutor(str(tmp_path), Policy())
    errors = executor._run_python_checks(PhaseContext(task_id="T1", workspace=str(tmp_path)))

    assert len(errors) == 1
    assert "MyPy check failed" in errors[0]
    assert "mypy executable missing" in errors[0]
