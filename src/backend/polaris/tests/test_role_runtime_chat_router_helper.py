from __future__ import annotations

from typing import Any

import pytest
from polaris.delivery.http.routers import role_chat_jetstream, role_runtime_chat


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "execute"},
        {"engine_version": "old"},
        {"intent": ["execute"]},
        {"execute": True},
        {"mode": "execute"},
    ],
)
def test_nonstreaming_chat_rejects_ambiguous_engine_intent(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        role_runtime_chat.engineering_chat_intent(payload)


@pytest.mark.asyncio
async def test_execute_role_chat_jetstream_uses_role_runtime(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    published_chunks: list[dict[str, Any]] = []

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

    async def fake_publish_chat_chunk(*, session_id: str, chunk: dict[str, Any], seq: int) -> bool:
        published_chunks.append({"session_id": session_id, "seq": seq, **chunk})
        return True

    monkeypatch.setattr(role_chat_jetstream, "RoleRuntimeService", FakeRoleRuntimeService)
    monkeypatch.setattr(role_chat_jetstream, "_publish_chat_chunk", fake_publish_chat_chunk)

    chunks: list[dict[str, Any]] = []

    async def collect_chunk(chunk: dict[str, Any]) -> None:
        chunks.append(chunk)

    session_id = await role_chat_jetstream.execute_role_chat_jetstream(
        role="director",
        workspace=str(tmp_path),
        message="build it",
        payload={"context": {"session_id": "s2"}, "task_id": "t1"},
        default_domain="code",
        host_kind="role_chat_jetstream",
        session_id="chat-director-test",
        on_chunk=collect_chunk,
    )

    assert session_id == "chat-director-test"
    assert [event["type"] for event in chunks] == [
        "fingerprint",
        "thinking_chunk",
        "content_chunk",
        "complete",
    ]
    assert [event["seq"] for event in published_chunks] == [0, 1, 2, 3]
    assert {event["session_id"] for event in published_chunks} == {"chat-director-test"}
    assert chunks[0]["data"]["fingerprint"] == "hash-1"
    assert chunks[1]["data"]["content"] == "think"
    assert chunks[2]["data"]["content"] == "hello"
    assert chunks[3]["data"]["content"] == "runtime final"
    assert chunks[3]["data"]["thinking"] == "runtime thinking"
    assert chunks[3]["data"]["metadata"]["role_runtime_entrypoint"] == "roles.runtime.stream_chat_turn"
    command = captured["command"]
    assert command.role == "director"
    assert command.workspace == str(tmp_path)
    assert command.session_id == "chat-director-test"
    assert command.task_id == "t1"
    assert command.domain == "code"
    assert command.stream is True
    assert command.host_kind == "role_chat_jetstream"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["runtime_fallback_used"] is False
