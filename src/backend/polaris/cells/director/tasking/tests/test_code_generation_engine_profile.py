"""Tests for Director runtime codegen execution-profile propagation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.director.tasking.internal.code_generation_engine import CodeGenerationEngine


@pytest.mark.asyncio
async def test_director_runtime_codegen_passes_execution_profile_temperature(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command: Any) -> Any:
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output="",
                thinking=None,
                role="director",
                metadata={},
                usage={},
                tool_calls=(),
                artifacts=(),
                error_message=None,
                error_code=None,
            )

    monkeypatch.setattr(
        "polaris.cells.roles.runtime.public.service.RoleRuntimeService",
        FakeRoleRuntimeService,
    )
    task = MagicMock()
    task.id = "T-profile"
    task.subject = "Fix Python API bug"
    task.description = "Repair pytest regression in the FastAPI handler"
    task.metadata = {
        "target_files": ["src/api.py"],
        "tech_stack": {"language": "python", "framework": "fastapi"},
        "project_type": "api",
    }
    engine = CodeGenerationEngine(str(tmp_path), executor=MagicMock())

    await engine._invoke_director_role_response(
        task=task,
        prompt="Return a valid patch.",
        timeout=30,
        round_label="1/1",
        round_files=["src/api.py"],
        session_id="session-profile",
    )

    command = captured["command"]
    profile = command.context["director_execution_profile"]
    assert command.context["_transaction_kernel_temperature_override"] == 0.05
    assert command.context["task_type"] == "bugfix"
    assert command.context["phase"] == "repair"
    assert profile["schema_version"] == "task.execution_profile.v1"
    assert profile["task_type"] == "bugfix"
    assert profile["temperature"] == 0.05
    assert command.metadata["temperature_source"] == "task.execution_profile.v1"
