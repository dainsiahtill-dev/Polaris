from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.llm.dialogue.public import service as dialogue_service
from polaris.cells.llm.dialogue.public.contracts import InvokeRoleDialogueCommandV1


@pytest.mark.asyncio
async def test_invoke_role_dialogue_uses_role_runtime(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output="runtime response",
                usage={"tokens": 7},
                metadata={"provider_type": "role_runtime", "model": "test-model"},
                tool_calls=("search_code",),
                artifacts=("artifact-1",),
            )

    monkeypatch.setattr(dialogue_service, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

    result = await dialogue_service.LlmDialogueService(settings=SimpleNamespace()).invoke_role_dialogue(
        InvokeRoleDialogueCommandV1(
            workspace=str(tmp_path),
            role="pm",
            message="plan this",
            context={"session_id": "session-1", "run_id": "run-1", "domain": "document"},
            metadata={"task_id": "task-1"},
        )
    )

    assert result.ok is True
    assert result.status == "ok"
    assert result.content == "runtime response"
    assert result.metadata["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
    assert result.metadata["runtime_fallback_used"] is False
    assert result.metadata["fallback_policy"] == "fail_closed"
    assert "legacy_fallback_used" not in result.metadata

    command = captured["command"]
    assert command.role == "pm"
    assert command.workspace == str(tmp_path)
    assert command.user_message == "plan this"
    assert command.session_id == "session-1"
    assert command.run_id == "run-1"
    assert command.task_id == "task-1"
    assert command.domain == "document"
    assert command.host_kind == "llm_dialogue_public_service"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["runtime_fallback_used"] is False
    assert command.metadata["fallback_policy"] == "fail_closed"
    assert "legacy_fallback_used" not in command.metadata
