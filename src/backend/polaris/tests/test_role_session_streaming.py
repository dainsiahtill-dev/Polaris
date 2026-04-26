from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.cells.runtime.state_owner.internal.state import AppState
from polaris.delivery.http.routers import role_session


def _make_request(workspace: str) -> SimpleNamespace:
    settings = SimpleNamespace(workspace=workspace, ramdisk_root="")
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_state=AppState(settings=settings),
            )
        )
    )


class _DetachedSession:
    def __init__(self, owner: _FakeRoleSessionService) -> None:
        self._owner = owner

    @property
    def role(self) -> str:
        if self._owner.closed:
            raise RuntimeError("detached role")
        return "qa"

    @property
    def context_config(self) -> str | None:
        if self._owner.closed:
            raise RuntimeError("detached context")
        return None

    @property
    def title(self) -> str:
        if self._owner.closed:
            raise RuntimeError("detached title")
        return "qa-session"


class _FakeRoleSessionService:
    add_calls: list[dict[str, str | None]] = []

    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> _FakeRoleSessionService:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.closed = True
        return False

    def get_session(self, session_id: str) -> _DetachedSession | None:
        return _DetachedSession(self) if session_id == "session-1" else None

    def get_messages(self, session_id: str, limit: int = 100, offset: int = 0) -> list[object]:
        return []

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        thinking: str | None = None,
        meta: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.add_calls.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "thinking": thinking,
            }
        )
        return {"ok": True, "role": role, "content": content, "meta": meta or {}}


@pytest.mark.asyncio
async def test_send_message_stream_snapshots_session_data_before_service_closes(monkeypatch, tmp_path) -> None:
    request = _make_request(str(tmp_path))
    payload = role_session.SendMessageRequest(role="user", content="inspect workspace")
    _FakeRoleSessionService.add_calls = []
    captured_invocation: dict[str, object] = {}

    async def _fake_generate_role_response_streaming(**kwargs):
        captured_invocation.update(kwargs)
        output_queue = kwargs["output_queue"]
        await output_queue.put({"type": "content_chunk", "data": {"content": "done"}})
        await output_queue.put(
            {
                "type": "complete",
                "data": {"content": "done", "thinking": ""},
            }
        )
        await output_queue.put({"type": "done"})

    monkeypatch.setattr(role_session, "RoleSessionService", _FakeRoleSessionService)
    monkeypatch.setattr(
        role_session,
        "generate_role_response_streaming",
        _fake_generate_role_response_streaming,
    )

    response = await role_session.send_message_stream(request, "session-1", payload)

    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))

    body = "".join(chunks)

    assert "event: content_chunk" in body
    assert 'data: {"content": "done"}' in body
    assert "event: complete" in body
    assert captured_invocation["role"] == "qa"
    assert captured_invocation["session_id"] == "session-1"
    assert captured_invocation["history"] == ()
    assert captured_invocation["context"] == {}
    assert _FakeRoleSessionService.add_calls[0]["role"] == "user"
    assert _FakeRoleSessionService.add_calls[-1]["role"] == "assistant"
