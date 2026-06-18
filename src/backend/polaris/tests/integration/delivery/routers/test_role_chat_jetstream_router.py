"""Test the new chat-streaming-over-JetStream endpoint.

Asserts:
  - POST /v2/role/{role}/chat/jetstream returns 200 + JSON
  - response contains session_id, channel, subject, transport=nat-jetstream
  - Content-Type is application/json, NOT text/event-stream
  - Subject is hp.runtime.chat.<session_id> (workspace-agnostic)
  - Channel is chat:<session_id>
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.llm.dialogue.public import get_registered_roles
from polaris.delivery.http.error_handlers import setup_exception_handlers
from polaris.delivery.http.routers import role_chat as role_chat_router
from polaris.delivery.http.routers._shared import require_auth


def _build_client() -> TestClient:
    app = FastAPI()
    setup_exception_handlers(app)
    app.include_router(role_chat_router.router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=".", ramdisk_root=""),
    )
    return TestClient(app)


def _role() -> str:
    roles = get_registered_roles()
    assert roles, "no registered roles available for test"
    return next(iter(roles))


def test_chat_jetstream_returns_json_with_session_id():
    client = _build_client()
    role = _role()

    fake_publish = AsyncMock(return_value=True)
    with (
        patch(
            "polaris.delivery.http.routers.role_chat_jetstream._publish_chat_chunk",
            fake_publish,
        ),
        patch(
            "polaris.delivery.http.routers.role_chat.asyncio.create_task",
            MagicMock(),
        ) as ct,
    ):
        # Make create_task return a sentinel async-mock so the background
        # coroutine is silently dropped after the response is returned.
        task = MagicMock()
        task.add_done_callback = MagicMock()
        ct.return_value = task

        r = client.post(
            f"/v2/role/{role}/chat/jetstream",
            json={"message": "ping", "max_tokens": 4},
        )

    assert r.status_code == 200, f"got {r.status_code}: {r.text[:200]}"
    assert "application/json" in (r.headers.get("content-type") or "")
    assert "text/event-stream" not in (r.headers.get("content-type") or "")
    body = r.json()
    assert body["status"] == "started"
    assert body["transport"] == "nat-jetstream"
    assert body["channel"].startswith("chat:")
    assert body["subject"].startswith("hp.runtime.chat.")
    assert body["channel"] == f"chat:{body['session_id']}"
    assert body["subject"] == f"hp.runtime.chat.{body['session_id']}"


def test_chat_jetstream_rejects_empty_message():
    client = _build_client()
    role = _role()
    r = client.post(
        f"/v2/role/{role}/chat/jetstream",
        json={"message": "", "max_tokens": 4},
    )
    assert r.status_code in (400, 422)
