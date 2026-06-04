from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.delivery.http.routers import role_runtime_chat


@pytest.mark.asyncio
async def test_execute_role_chat_nonstreaming_uses_role_runtime(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output="runtime response",
                thinking="runtime thinking",
                usage={},
                metadata={"provider_type": "kimi", "model": "kimi-for-coding"},
            )

    monkeypatch.setattr(role_runtime_chat, "RoleRuntimeService", FakeRoleRuntimeService)

    result = await role_runtime_chat.execute_role_chat_nonstreaming(
        role="pm",
        workspace=str(tmp_path),
        message="plan this",
        payload={"context": {"session_id": "s1"}, "run_id": "r1"},
        default_domain="document",
        host_kind="pm_chat_http",
    )

    assert result["response"] == "runtime response"
    assert result["thinking"] == "runtime thinking"
    assert result["model"] == "kimi-for-coding"
    assert result["provider"] == "kimi"
    assert result["metadata"]["role_runtime_entrypoint"] == "roles.runtime.execute_role_session"
    command = captured["command"]
    assert command.role == "pm"
    assert command.workspace == str(tmp_path)
    assert command.session_id == "s1"
    assert command.run_id == "r1"
    assert command.domain == "document"
    assert command.stream is False
    assert command.host_kind == "pm_chat_http"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["runtime_fallback_used"] is False
    assert command.metadata["fallback_policy"] == "fail_closed"
    assert "legacy_fallback_used" not in result["metadata"]


@pytest.mark.asyncio
async def test_execute_role_chat_streaming_uses_role_runtime(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeResult:
        content = "runtime final"
        output = "runtime final"
        thinking = "runtime thinking"
        profile_version = "strategy.v1"
        tool_policy_id = "tools.strict"

    class FakeRoleRuntimeService:
        async def stream_chat_turn(self, command):
            captured["command"] = command
            yield {
                "type": "fingerprint",
                "profile_hash": "hash-1",
                "profile_id": "profile-1",
                "bundle_id": "bundle-1",
                "bundle_version": "1",
                "run_id": "run-1",
                "turn_index": 0,
            }
            yield {"type": "thinking_chunk", "content": "think"}
            yield {"type": "content_chunk", "content": "hello"}
            yield {"type": "complete", "result": FakeResult()}

    monkeypatch.setattr(role_runtime_chat, "RoleRuntimeService", FakeRoleRuntimeService)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    await role_runtime_chat.execute_role_chat_streaming(
        role="director",
        workspace=str(tmp_path),
        message="build it",
        output_queue=queue,
        payload={"context": {"session_id": "s2"}, "task_id": "t1"},
        default_domain="code",
        host_kind="role_chat_http_stream",
    )

    events: list[dict[str, Any]] = []
    while not queue.empty():
        events.append(queue.get_nowait())

    assert [event["type"] for event in events] == [
        "fingerprint",
        "thinking_chunk",
        "content_chunk",
        "complete",
    ]
    assert events[0]["data"]["fingerprint"] == "hash-1"
    assert events[1]["data"]["content"] == "think"
    assert events[2]["data"]["content"] == "hello"
    assert events[3]["data"]["content"] == "runtime final"
    assert events[3]["data"]["thinking"] == "runtime thinking"
    assert events[3]["data"]["metadata"]["role_runtime_entrypoint"] == "roles.runtime.stream_chat_turn"
    command = captured["command"]
    assert command.role == "director"
    assert command.workspace == str(tmp_path)
    assert command.session_id == "s2"
    assert command.task_id == "t1"
    assert command.domain == "code"
    assert command.stream is True
    assert command.host_kind == "role_chat_http_stream"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["runtime_fallback_used"] is False
