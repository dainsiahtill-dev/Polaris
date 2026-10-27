"""Tests for the Polaris v2 Agent router.

Contract coverage
-----------------
* The read routes (``GET /v2/agent/sessions`` and the memory sub-routes)
  still delegate to the canonical ``roles.session`` owner and are exercised
  against that real router surface.
* The retired write routes (``POST .../messages``, ``DELETE /sessions/{id}``
  and ``POST /turn``) are explicitly retired by the platform.  Their former
  assertions targeted a writer that no longer exists, so each of those nodes
  now asserts the real rejection contract (``410 legacy_engine_retired``)
  instead of a resurrected writer.
* One node proves the current, supported engineering entry really advances,
  so a 410 rejection is a redirection and not a dead end.

The engineering owner is always the real
``roles.runtime.public.engineering_service.EngineeringRunService``; the shared
router fixture supplies a real ``Settings`` snapshot bound to a private
workspace.  No local mock ``Settings`` may be reintroduced: the owner chain
serialises the host configuration to JSON, so a mock child breaks app
construction before any route is reached.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _transport_rate_limit_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the transport token bucket out of this router contract module.

    The original module disabled the transport bucket explicitly, and that
    behaviour is retained here.  The measured default is 10 requests per
    second with a burst of 20, and this module's positive node issues more
    than 20 requests to the same client host while polling the engineering
    run, so the bucket can answer ``429`` instead of the router contract.

    The flag is read when the ASGI middleware stack is built, so it has to
    be set before the first request.  Autouse fixtures are set up before the
    shared ``client`` fixture, so the built middleware really observes the
    disabled flag.  The positive node asserts that observation through the
    middleware's own diagnostics, so this fixture cannot silently degrade
    into an ineffective environment write.
    """
    monkeypatch.setenv("KERNELONE_RATE_LIMIT_ENABLED", "false")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_session(session_id: str = "sess-123") -> MagicMock:
    session = MagicMock()
    session.id = session_id
    session.role = "assistant"
    session.host_kind = "api_server"
    session.session_type = "standalone"
    session.workspace = "."
    session.title = "Agent assistant session"
    session.state = "active"
    session.message_count = 0
    session.context_config = '{"agent_router_v1": true, "workspace": "."}'
    session.capability_profile = None
    session.created_at.isoformat.return_value = "2024-01-01T00:00:00"
    session.updated_at.isoformat.return_value = "2024-01-01T00:00:00"
    session.to_dict.return_value = {
        "id": session_id,
        "role": "assistant",
        "host_kind": "api_server",
        "session_type": "standalone",
        "workspace": ".",
        "title": "Agent assistant session",
        "state": "active",
        "message_count": 0,
        "context_config": {"agent_router_v1": True, "workspace": "."},
        "capability_profile": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00",
    }
    return session


def _make_fake_message(message_id: str = "msg-1", role: str = "user", content: str = "hello") -> MagicMock:
    msg = MagicMock()
    msg.id = message_id
    msg.conversation_id = "sess-123"
    msg.sequence = 0
    msg.role = role
    msg.content = content
    msg.thinking = None
    msg.meta = None
    msg.to_dict.return_value = {
        "id": message_id,
        "conversation_id": "sess-123",
        "sequence": 0,
        "role": role,
        "content": content,
        "thinking": None,
        "meta": {},
        "created_at": "2024-01-01T00:00:00",
    }
    return msg


def _assert_legacy_engine_retired(response: Any, *, operation: str) -> dict[str, Any]:
    """Assert the retired-writer contract and return the rejection detail.

    The platform rejects the retired write before preflight, storage or
    scheduling, so the response must carry the retirement code and must name
    the current supported entry points.
    """
    assert response.status_code == 410, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    detail = payload["detail"]
    assert isinstance(detail, dict)
    assert detail["error_code"] == "legacy_engine_retired"
    assert operation in detail["message"]
    assert "/v2/engineering/runs" in detail["supported_endpoints"]
    assert "/v2/role/{role}/chat" in detail["supported_endpoints"]
    return detail


def _app_of(client: AsyncClient) -> Any:
    """Return the ASGI application that owns the engineering service."""
    return client._transport.app  # type: ignore[attr-defined]


def _engineering_service(client: AsyncClient) -> Any:
    """Return the real engineering owner bound to the private workspace."""
    return _app_of(client).state.engineering_run_service


def _checked_artifact_reply(provider: ScriptedProvider) -> None:
    """Replace only model decisions; file effects and verification stay real.

    The Director writes one artifact and then runs a real verification command
    against it, so the run reaches a terminal accepted state through the real
    engineering owner instead of a fabricated receipt.
    """
    command = "python3 -c \"from pathlib import Path; assert Path('agent_router_artifact.txt').read_text(encoding='utf-8')=='2'\""
    provider.enqueue_json(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": identity,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                            for identity, name, arguments in [
                                (
                                    "write-artifact",
                                    "write_file",
                                    {"file": "agent_router_artifact.txt", "content": "2"},
                                ),
                                ("check-artifact", "verify_project", {"commands": [command]}),
                            ]
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    provider.enqueue_json(
        {"choices": [{"message": {"role": "assistant", "content": "Current entry reply."}, "finish_reason": "stop"}]}
    )


async def _await_terminal(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """Read the run until it reaches a terminal state.

    The 30 second bound uses ``asyncio.wait_for`` because the project floor
    is Python 3.10 (``requires-python = ">=3.10"``) and ``asyncio.timeout``
    only exists from 3.11.  ``wait_for`` keeps the same semantics here: it
    cancels the awaited work on expiry and raises ``asyncio.TimeoutError``.
    """
    return await asyncio.wait_for(_poll_until_terminal(client, session_id), timeout=30)


async def _poll_until_terminal(client: AsyncClient, session_id: str) -> dict[str, Any]:
    """Poll the run projection until it reports a terminal state."""
    while True:
        response = await client.get(f"/v2/engineering/runs/{session_id}")
        assert response.status_code == 200, response.text
        row = response.json()
        assert isinstance(row, dict)
        if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return dict(row)
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_list_agent_sessions(client: AsyncClient) -> None:
    """V2 list sessions should return 200 with mocked sessions."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_sessions.return_value = [fake_session]
        mock_service.get_messages.return_value = [fake_message]

        response = await client.get("/v2/agent/sessions")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["session_id"] == "sess-123"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_get_agent_session(client: AsyncClient) -> None:
    """V2 get session should return 200 for existing session."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = fake_session
        mock_service.get_messages.return_value = [fake_message]

        response = await client.get("/v2/agent/sessions/sess-123")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "sess-123"
        assert data["role"] == "assistant"


@pytest.mark.asyncio
async def test_v2_get_agent_session_not_found(client: AsyncClient) -> None:
    """V2 get session should return 404 for missing session."""
    with patch(
        "polaris.delivery.http.routers.agent.RoleSessionService",
    ) as mock_service_cls:
        mock_service = MagicMock()
        mock_service_cls.return_value.__enter__.return_value = mock_service
        mock_service.get_session.return_value = None

        response = await client.get("/v2/agent/sessions/missing-id")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_search_agent_session_memory(client: AsyncClient) -> None:
    """V2 memory search should return 200 with results."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.search_memory.return_value = MagicMock(
            ok=True,
            payload=[{"id": "item-1", "content": "test"}],
            error_code=None,
            error_message=None,
        )

        response = await client.get(
            "/v2/agent/sessions/sess-123/memory/search",
            params={"q": "test query"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["total"] == 1
        assert data["items"][0]["id"] == "item-1"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/artifacts/{artifact_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_read_agent_session_memory_artifact(client: AsyncClient) -> None:
    """V2 read artifact should return 200 with artifact data."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.read_artifact.return_value = MagicMock(
            ok=True,
            payload={"artifact_id": "art-1", "content": "artifact content"},
            error_code=None,
            error_message=None,
        )

        response = await client.get(
            "/v2/agent/sessions/sess-123/memory/artifacts/art-1",
            params={"start_line": 1, "end_line": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["artifact"]["artifact_id"] == "art-1"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/episodes/{episode_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_read_agent_session_memory_episode(client: AsyncClient) -> None:
    """V2 read episode should return 200 with episode data."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.read_episode.return_value = MagicMock(
            ok=True,
            payload={"episode_id": "ep-1", "intent": "test"},
            error_code=None,
            error_message=None,
        )

        response = await client.get("/v2/agent/sessions/sess-123/memory/episodes/ep-1")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["episode"]["episode_id"] == "ep-1"


# ---------------------------------------------------------------------------
# GET /v2/agent/sessions/{session_id}/memory/state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_read_agent_session_memory_state(client: AsyncClient) -> None:
    """V2 read memory state should return 200 with state value."""
    fake_session = _make_fake_session("sess-123")
    fake_message = _make_fake_message("msg-1", "user", "hello")

    with (
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionService",
        ) as mock_session_cls,
        patch(
            "polaris.delivery.http.routers.agent.RoleSessionContextMemoryService",
        ) as mock_memory_cls,
    ):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session
        mock_session.get_session.return_value = fake_session
        mock_session.get_messages.return_value = [fake_message]

        mock_memory = MagicMock()
        mock_memory_cls.return_value.__enter__.return_value = mock_memory
        mock_memory.get_state.return_value = MagicMock(
            ok=True,
            payload={"current_goal": "test goal"},
            error_code=None,
            error_message=None,
        )

        response = await client.get(
            "/v2/agent/sessions/sess-123/memory/state",
            params={"path": "run_card"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["path"] == "run_card"
        assert data["value"]["current_goal"] == "test goal"


# ---------------------------------------------------------------------------
# POST /v2/agent/sessions/{session_id}/messages  (retired writer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_send_agent_message(client: AsyncClient) -> None:
    """Retired message writer must be rejected instead of executed.

    The former contract asserted a 200 reply produced by the removed
    ``_execute_agent_message`` writer.  That writer is retired, so the honest
    contract is the platform rejection, and it must not produce a reply.
    """
    response = await client.post(
        "/v2/agent/sessions/sess-123/messages",
        json={"message": "hello", "role": "assistant"},
    )
    detail = _assert_legacy_engine_retired(response, operation="Legacy send_agent_message")
    assert "reply" not in detail
    assert "session_id" not in detail


# ---------------------------------------------------------------------------
# Removed legacy POST /v2/agent/sessions/{session_id}/messages/stream route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_send_agent_message_stream_route_is_not_registered(client: AsyncClient) -> None:
    """V2 stream message route must not exist."""
    response = await client.post(
        "/v2/agent/sessions/sess-123/messages/stream",
        json={"message": "hello", "role": "assistant"},
    )

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_nested_v2_agent_aliases_are_not_registered(client: AsyncClient) -> None:
    """Agent routes are mounted once under /v2 and must not expose /v2/agent/v2 aliases."""
    cases = [
        ("GET", "/v2/agent/v2/sessions"),
        ("GET", "/v2/agent/v2/sessions/sess-123"),
        ("GET", "/v2/agent/v2/sessions/sess-123/memory/search?q=test"),
        ("POST", "/v2/agent/v2/sessions/sess-123/messages"),
        ("DELETE", "/v2/agent/v2/sessions/sess-123"),
        ("POST", "/v2/agent/v2/turn"),
    ]

    for method, path in cases:
        response = await client.request(method, path, json={"message": "hello", "role": "assistant"})
        assert response.status_code == 404, path


# ---------------------------------------------------------------------------
# DELETE /v2/agent/sessions/{session_id}  (retired writer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_delete_agent_session(client: AsyncClient) -> None:
    """Retired session delete writer must be rejected instead of executed.

    The former contract asserted a 200 delete for an existing session.  The
    delete writer is retired, so the honest contract is the platform
    rejection and no session is removed.
    """
    response = await client.delete("/v2/agent/sessions/sess-123")
    detail = _assert_legacy_engine_retired(response, operation="Legacy delete_agent_session")
    assert "sess-123" not in detail.get("message", "") or "retired" in detail["message"]


@pytest.mark.asyncio
async def test_v2_delete_agent_session_not_found(client: AsyncClient) -> None:
    """Retired delete writer rejects before any session lookup.

    The former contract asserted a 404 ``SESSION_NOT_FOUND`` for a missing
    session.  The retired writer is rejected before preflight and storage, so
    the missing-session outcome is no longer reachable on this route and the
    retirement code is the real answer.
    """
    response = await client.delete("/v2/agent/sessions/missing-id")
    detail = _assert_legacy_engine_retired(response, operation="Legacy delete_agent_session")
    assert detail["error_code"] != "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v2/agent/turn  (retired writer)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_agent_turn(client: AsyncClient) -> None:
    """Retired agent turn writer must be rejected instead of executed.

    The former contract patched the removed ``RoleRuntimeService`` execution
    entry and asserted a 200 reply.  That execution entry is retired, so the
    honest contract is the platform rejection and no turn reply is produced.
    """
    response = await client.post(
        "/v2/agent/turn",
        json={"message": "hello", "role": "assistant", "stream": False},
    )
    detail = _assert_legacy_engine_retired(response, operation="Legacy agent_turn")
    assert "reply" not in detail


@pytest.mark.asyncio
async def test_v2_agent_turn_stream(client: AsyncClient) -> None:
    """Retired agent turn stream writer must not issue a stream URL.

    The former contract asserted a 200 jetstream URL for ``stream=True``.
    The turn writer is retired, so no transport URL may be handed out.
    """
    response = await client.post(
        "/v2/agent/turn",
        json={"message": "hello", "role": "assistant", "stream": True},
    )
    detail = _assert_legacy_engine_retired(response, operation="Legacy agent_turn")
    assert "jetstream_url" not in detail
    assert "stream_url" not in detail


# ---------------------------------------------------------------------------
# Current supported engineering entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_agent_retirement_redirects_to_a_working_engineering_entry(
    client: AsyncClient,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The advertised current entry really advances after the retirement.

    The retired agent writer rejects with a pointer to the current supported
    entry points.  This node proves that pointer is real: the retired writes
    leave no run, event, tool call, provider request or file behind, and the
    current ``POST /v2/engineering/runs`` entry creates a run that reaches a
    terminal accepted state through the real engineering owner.

    SUBSTITUTES USED HERE (this node is component behaviour, not a Bench or
    product-delivery proof):

    * the model port is a synthetic loopback server that replays two fixed
      responses, so the Director's *decisions* are scripted;
    * auth is the shared fixture's allow-all double, NATS, the DI container
      and the ASGI lifespan are replaced by the shared fixture's doubles.

    What is NOT substituted: the engineering owner, its permission profile
    resolution, the file write, the verification command and its exit code,
    the SQLite engineering journal and the terminal acceptance projection.
    """
    service = _engineering_service(client)
    workspace = str(service.workspace)

    # The transport bucket must really be out of the way for this contract.
    # The middleware is built when the ASGI stack starts, so the module
    # fixture's flag is observed here; a silently ineffective fixture would
    # turn this node's polling into ``429`` answers instead of run facts.
    from polaris.delivery.http.middleware.rate_limit import _last_rate_limit_middleware

    middleware = _last_rate_limit_middleware
    assert middleware is not None
    assert middleware.diagnostics_snapshot()["enabled"] is False

    with sqlite3.connect(service.store.database_path) as connection:
        runs_before = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        events_before = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        tool_calls_before = connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
        tables_before = sorted(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        )

    # The retired writes must not create, mutate or schedule anything.
    rejected = await client.post(
        "/v2/agent/sessions/sess-123/messages",
        json={"message": "hello", "role": "assistant"},
    )
    _assert_legacy_engine_retired(rejected, operation="Legacy send_agent_message")
    rejected = await client.delete("/v2/agent/sessions/sess-123")
    _assert_legacy_engine_retired(rejected, operation="Legacy delete_agent_session")
    rejected = await client.post(
        "/v2/agent/turn",
        json={"message": "hello", "role": "assistant", "stream": False},
    )
    _assert_legacy_engine_retired(rejected, operation="Legacy agent_turn")

    with sqlite3.connect(service.store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs_before
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == tool_calls_before
        assert (
            sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"))
            == tables_before
        )
    # The retired writes must not leave a provider request behind either.
    assert not scripted_provider.requests
    # And they must not create a file in the bound workspace.
    assert not (service.workspace / "agent_router_artifact.txt").exists()

    # The current supported entry is the real engineering owner, driven by a
    # synthetic loopback model port.  Only the model decisions are scripted;
    # the run, its file effect, its verification command and its terminal
    # state are real.
    # Keep the LLM config inside this node's private workspace so the run
    # never reads or writes the developer's global model configuration.
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(service.workspace / ".polaris" / "runtime" / "llm-config.json"))
    service._client = scripted_provider.client("openai", service.workspace, monkeypatch)
    _checked_artifact_reply(scripted_provider)

    created = await client.post(
        "/v2/engineering/runs",
        json={
            "instruction": "Create and verify agent_router_artifact.txt.",
            "workspace": workspace,
            "request_key": "agent-router-current-entry",
        },
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    row = await _await_terminal(client, session_id)
    assert row["session_id"] == session_id
    assert row["engine_version"] == "engineering_v2"
    assert row["workspace"] == workspace
    assert row["status"] == "completed", row
    assert row["verification"] == "passed", row
    assert row["project_outcome"] == "accepted", row
    assert row["roles"]["director"] == "completed"
    assert row["roles"]["pm"] == row["roles"]["chief_engineer"] == row["roles"]["qa"] == "not_called"
    assert (service.workspace / "agent_router_artifact.txt").read_text(encoding="utf-8") == "2"
    assert scripted_provider.requests
    # The verification really ran a command against the current candidate.
    assert row["checks"], row
    assert row["checks"][0]["exit_code"] == 0, row
    assert row["verification_commands"], row
    assert row["require_qa"] is False
    # The current engineering record stays in the single engineering store;
    # no legacy role-session or task-board mirror is written.
    assert not (service.workspace / ".polaris" / "runtime" / "factory").exists()
    assert not (service.workspace / ".polaris" / "runtime" / "tasks" / "plan.json").exists()

    with sqlite3.connect(service.store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == runs_before + 1
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] > events_before
        assert connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] > tool_calls_before
