from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from polaris.cells.roles.runtime.internal.engineering_roles import register_verification_tool
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.internal.engineering_verification_plan import default_verification_commands
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.tests.engineering_support import isolated_workspace as isolated_workspace


def test_empty_verification_uses_detected_go_toolchain(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_workspace / "go.mod").write_text("module example\n", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/go" if name == "go" else None)
    assert default_verification_commands(isolated_workspace) == ("go build ./...", "go test ./...")


def test_empty_verification_uses_package_scripts(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated_workspace / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc", "test": "node --test"}}), encoding="utf-8"
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    assert default_verification_commands(isolated_workspace) == ("npm run build", "npm test")


def test_make_fallback_does_not_invent_missing_cmake(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated_workspace / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (isolated_workspace / "Makefile").write_text("test:\n\t./run-tests\n", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/make" if name == "make" else None)
    assert default_verification_commands(isolated_workspace) == ("make test",)


@pytest.mark.asyncio
async def test_verify_project_empty_commands_receives_host_plan(isolated_workspace: Path) -> None:
    register_verification_tool()
    (isolated_workspace / "go.mod").write_text("module example\n", encoding="utf-8")
    captured: list[tuple[str, ...]] = []

    async def fake_verify(commands: tuple[str, ...]) -> ToolObservation:
        captured.append(commands)
        return ToolObservation("v", "verify_project", True, "ok", 0, False)

    tools = EngineeringTools(
        isolated_workspace,
        PermissionScope((isolated_workspace,), (), frozenset({"execute_command", "verify_project"}), False),
        RunLimits(time.time() + 15, 1_000_000, 64_000),
        asyncio.Event(),
        verify=fake_verify,
    )
    result = await tools.execute("v", "verify_project", {"commands": []})
    assert result.ok and captured == [("go build ./...", "go test ./...")]
