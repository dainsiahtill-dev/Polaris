"""Conventional start uses the existing bounded package command contract."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest


@pytest.mark.parametrize("executable", ["npm", "pnpm", "yarn"])
def test_conventional_start_preserves_exact_argv_and_bounded_timeout(tmp_path: Path, executable: str) -> None:
    service = CommandExecutionService(tmp_path, max_timeout_seconds=2)
    spec = service.build_subprocess_spec(CommandRequest(executable, ["start"], str(tmp_path), 1000))

    assert spec["argv"] == [executable, "start"]
    assert spec["cwd"] == str(tmp_path.resolve())
    assert spec["timeout_seconds"] == 2


@pytest.mark.parametrize("argv", [["npm", "run", "start"], ["npm", "test"], ["npx", "tsc", "--noEmit"]])
def test_existing_package_command_controls_keep_their_exact_shape(tmp_path: Path, argv: list[str]) -> None:
    spec = CommandExecutionService(tmp_path).build_subprocess_spec(CommandRequest(argv[0], argv[1:], str(tmp_path)))
    assert spec["argv"] == argv


@pytest.mark.parametrize("verb", ["publish", "exec", "config"])
def test_unrelated_package_verbs_remain_rejected(tmp_path: Path, verb: str) -> None:
    with pytest.raises(ValueError, match="Unsupported package manager verb"):
        CommandExecutionService(tmp_path).build_subprocess_spec(CommandRequest("npm", [verb], str(tmp_path)))


@pytest.mark.parametrize("args", [["run"], ["run", "--unsafe-script"]])
def test_explicit_run_still_requires_a_valid_script_name(tmp_path: Path, args: list[str]) -> None:
    with pytest.raises(ValueError, match="script name"):
        CommandExecutionService(tmp_path).build_subprocess_spec(CommandRequest("npm", args, str(tmp_path)))


def test_start_does_not_escape_the_workspace_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    with pytest.raises(ValueError, match="outside workspace"):
        CommandExecutionService(workspace).build_subprocess_spec(CommandRequest("npm", ["start"], str(outside)))
