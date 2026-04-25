"""Tests for workflow_runtime public process_launch module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from polaris.cells.orchestration.workflow_runtime.public.process_launch import (
    ProcessLaunchRequest,
    ProcessLaunchResult,
    RunMode,
)


class TestRunMode:
    def test_str(self) -> None:
        assert str(RunMode.SINGLE) == "single"
        assert str(RunMode.LOOP) == "loop"

    def test_is_persistent(self) -> None:
        assert RunMode.LOOP.is_persistent() is True
        assert RunMode.SINGLE.is_persistent() is False

    def test_is_director_mode(self) -> None:
        assert RunMode.ONE_SHOT.is_director_mode() is True
        assert RunMode.SINGLE.is_director_mode() is False

    def test_is_pm_mode(self) -> None:
        assert RunMode.SINGLE.is_pm_mode() is True
        assert RunMode.ONE_SHOT.is_pm_mode() is False


class TestProcessLaunchRequest:
    def test_post_init_resolves_workspace(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(workspace=tmp_path)
        assert req.workspace.is_absolute()

    def test_post_init_splits_string_command(self) -> None:
        req = ProcessLaunchRequest(command="python -c 'print(1)'")
        assert isinstance(req.command, list)
        assert req.command[0] == "python"

    def test_validate_empty_command(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(command=[], workspace=tmp_path)
        errors = req.validate()
        assert any("Command cannot be empty" in e for e in errors)

    def test_validate_missing_workspace(self) -> None:
        req = ProcessLaunchRequest(command=["echo", "hi"], workspace=Path("/nonexistent"))
        errors = req.validate()
        assert any("Workspace does not exist" in e for e in errors)

    def test_validate_invalid_timeout(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(command=["echo", "hi"], workspace=tmp_path, timeout=0)
        errors = req.validate()
        assert any("Invalid timeout" in e for e in errors)

    def test_validate_utf8_env(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(
            command=["echo", "hi"],
            workspace=tmp_path,
            env_vars={"key": "value"},
        )
        assert req.validate() == []

    def test_with_timeout(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(command=["echo", "hi"], workspace=tmp_path)
        req2 = req.with_timeout(60)
        assert req2.timeout == 60

    def test_with_env(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(command=["echo", "hi"], workspace=tmp_path, env_vars={"a": "1"})
        req2 = req.with_env(b="2")
        assert req2.env_vars == {"a": "1", "b": "2"}

    def test_get_effective_command_line(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(command=["echo", "hello world"], workspace=tmp_path)
        line = req.get_effective_command_line()
        assert "echo" in line
        assert "hello world" in line

    def test_to_dict_masks_secrets(self, tmp_path: Path) -> None:
        req = ProcessLaunchRequest(
            command=["echo", "hi"],
            workspace=tmp_path,
            env_vars={"api_token": "secret", "normal": "ok"},
        )
        d = req.to_dict()
        assert d["env_vars"]["api_token"] == "***"
        assert d["env_vars"]["normal"] == "ok"


class TestProcessLaunchResult:
    def test_is_success(self) -> None:
        result = ProcessLaunchResult(success=True, pid=123)
        assert result.is_success() is True

    def test_is_success_no_pid(self) -> None:
        result = ProcessLaunchResult(success=True, pid=None)
        assert result.is_success() is False

    def test_is_completed(self) -> None:
        result = ProcessLaunchResult(success=True, exit_code=0)
        assert result.is_completed() is True

    def test_duration_ms(self) -> None:
        result = ProcessLaunchResult(success=True)
        assert result.duration_ms() >= 0

    def test_to_dict(self) -> None:
        result = ProcessLaunchResult(success=True, pid=123, exit_code=0)
        d = result.to_dict()
        assert d["success"] is True
        assert d["pid"] == 123
