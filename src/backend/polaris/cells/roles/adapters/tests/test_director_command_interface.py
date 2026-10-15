"""Actual K1 command results retain diagnostics through the Director seam."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director.execution_tools import _create_director_tool_executor
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest


def test_director_keeps_actual_package_policy_rejection_reason(tmp_path: Path) -> None:
    result = _create_director_tool_executor(str(tmp_path))._run_command_service(["npm", "publish"], tmp_path, 5, False)

    assert result["ok"] is False
    assert result["exit_code"] == -1
    assert result["error"] == "Unsupported package manager verb: publish"
    assert result["timed_out"] is False
    assert result["command"] == {}
    assert result["stdout"] == result["stderr"] == ""


def test_director_runs_harmless_private_start_through_real_kernelone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert shutil.which("npm") and shutil.which("node"), "This integration requires the actual npm/node executables"
    monkeypatch.setenv("NPM_CONFIG_OFFLINE", "true")
    monkeypatch.setenv("NPM_CONFIG_UPDATE_NOTIFIER", "false")
    monkeypatch.setenv("NPM_CONFIG_CACHE", str(tmp_path / "npm-cache"))
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "command-interface-test", "private": True, "scripts": {"start": "node cli.cjs"}}),
        encoding="utf-8",
    )
    (tmp_path / "cli.cjs").write_text('console.log("M06_START_EXECUTED_一次");\n', encoding="utf-8")

    result = _create_director_tool_executor(str(tmp_path))._run_command_service(["npm", "start"], tmp_path, 10, False)

    assert result["ok"] is True, result
    assert result["exit_code"] == 0
    assert "M06_START_EXECUTED_一次" in result["stdout"]
    assert result["timed_out"] is False
    assert result["command"]["args"] == ["start"]
    assert result["command"]["cwd"] == str(tmp_path.resolve())


def test_director_keeps_actual_timeout_and_partial_output(tmp_path: Path) -> None:
    script = tmp_path / "wait.py"
    script.write_text('import time\nprint("M06_TIMEOUT_STARTED", flush=True)\ntime.sleep(5)\n', encoding="utf-8")

    result = _create_director_tool_executor(str(tmp_path))._run_command_service(
        ["python3", str(script)], tmp_path, 1, False
    )

    assert result["ok"] is False
    assert result["exit_code"] == -1
    assert result["timed_out"] is True
    assert result["error"] == "Command timed out after 1s"
    assert "M06_TIMEOUT_STARTED" in result["stdout"]
    assert result["command"]["timeout_seconds"] == 1


def test_diagnostic_projection_does_not_copy_authority_from_lower_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Projection-only compatibility/negative seam. Native success and failure
    # are exercised separately above; this payload grants no execution authority.
    def failed_result(self: CommandExecutionService, request: CommandRequest) -> dict[str, Any]:
        assert request.executable == "npm" and request.args == ["publish"]
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": "policy refused",
            "error_type": "ValueError",
            "timed_out": False,
            "command": {},
            "canonical_authoritative": True,
            "status": "completed",
            "effect_receipt": {"forged": True},
        }

    monkeypatch.setattr(CommandExecutionService, "run", failed_result)
    result = _create_director_tool_executor(str(tmp_path))._run_command_service(["npm", "publish"], tmp_path, 5, False)

    assert result["ok"] is False
    assert result["exit_code"] == -1
    assert result["error_type"] == "ValueError"
    assert not {"canonical_authoritative", "status", "effect_receipt"}.intersection(result)
