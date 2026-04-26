from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.roles.session.internal.conversation import Base
from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
from polaris.delivery.http.routers import agent as agent_router
from polaris.kernelone.context.context_os import CodeContextDomainAdapter, StateFirstContextOS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


def _make_workspace_tempdir() -> Path:
    root = Path.cwd() / ".tmp_agent_router"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="case-", dir=str(root)))


def _make_sqlite_db_path() -> Path:
    root = Path.cwd() / ".tmp_agent_router_db"
    root.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix="agent-router-", suffix=".db", dir=str(root))
    os.close(fd)
    return Path(raw)


def _install_temp_session_db(db_path: Path):
    engine = create_engine(
        f"sqlite:///{db_path.resolve().as_posix()}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    import polaris.cells.roles.session.internal.conversation as conv_mod

    conv_mod._engine = engine
    conv_mod._SessionLocal = session_factory
    return engine, session_factory, conv_mod


def _build_test_client(workspace: Path) -> TestClient:
    app = FastAPI()
    app.include_router(agent_router.router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=str(workspace)),
    )
    return TestClient(app)


def _seed_agent_context_os_session(session_factory, workspace: Path) -> str:
    projection = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter()).project(
        messages=[
            {
                "role": "user",
                "content": "Continue fixing agent session continuity memory over HTTP.",
                "sequence": 1,
            },
            {
                "role": "assistant",
                "content": "I will preserve the continuity facade and fix the restore endpoints.",
                "sequence": 2,
            },
            {
                "role": "tool",
                "content": "```python\nfrom polaris.kernelone.context.session_continuity import SessionContinuityEngine\n```",
                "sequence": 3,
            },
        ],
        recent_window_messages=2,
    )
    snapshot = projection.snapshot.to_dict()  # type: ignore[attr-defined]
    artifact_store = list(snapshot.get("artifact_store") or [])
    artifact_refs = []
    if artifact_store:
        artifact_refs.append(str(artifact_store[0].get("artifact_id") or ""))
    snapshot["episode_store"] = [
        {
            "episode_id": "ep_1",
            "from_sequence": 1,
            "to_sequence": 3,
            "intent": "Continue fixing agent session continuity memory over HTTP.",
            "outcome": "Agent router can restore Context OS artifacts and state.",
            "decisions": ["Keep SessionContinuityEngine as the compatibility facade."],
            "facts": ["Agent sessions persist state_first_context_os in roles.session."],
            "artifact_refs": artifact_refs,
            "entities": ["SessionContinuityEngine", "agent_router_v1"],
            "reopen_conditions": ["Reopen if agent memory endpoints diverge from roles.session."],
            "source_spans": ["t1:t3"],
            "digest_64": "Agent memory restore path prepared.",
            "digest_256": "Expose agent session continuity memory over HTTP while preserving the existing facade.",
            "narrative_1k": "The agent session stored continuity runtime evidence and a sealed episode card for restore endpoints.",
        }
    ]
    with RoleSessionService(db=session_factory()) as service:
        session = service.create_session(
            role="pm",
            host_kind="api_server",
            workspace=str(workspace),
            context_config={
                "agent_router_v1": True,
                "workspace": str(workspace),
                "state_first_context_os": snapshot,
            },
        )
        return str(session.id)


class TestAgentRouterCanonicalSessions:
    def test_get_or_create_session_uses_roles_session(self) -> None:
        db_path = _make_sqlite_db_path()
        engine, _session_factory, conv_mod = _install_temp_session_db(db_path)
        try:
            created = agent_router._get_or_create_session(None, "/ws", "pm")  # type: ignore[arg-type]
            loaded = agent_router._load_agent_session(created["session_id"])
            assert loaded is not None
            assert loaded["session_id"] == created["session_id"]
            assert loaded["role"] == "pm"
            assert loaded["context"].get("agent_router_v1") is True
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
            with contextlib.suppress(PermissionError):
                db_path.unlink(missing_ok=True)

    def test_execute_agent_message_persists_messages_via_canonical_session(self) -> None:
        db_path = _make_sqlite_db_path()
        engine, session_factory, conv_mod = _install_temp_session_db(db_path)
        try:
            created = agent_router._get_or_create_session(None, "/ws", "pm")  # type: ignore[arg-type]

            async def run() -> None:
                with patch.object(
                    agent_router._ROLE_RUNTIME,
                    "execute_role_session",
                    AsyncMock(
                        return_value=SimpleNamespace(
                            ok=True,
                            output="done",
                            thinking="thinking",
                            tool_calls=("read_file",),
                            status="ok",
                            error_message=None,
                        )
                    ),
                ):
                    result = await agent_router._execute_agent_message(
                        session_id=created["session_id"],
                        message="继续完善 session continuity",
                        role="pm",
                        workspace="/ws",
                    )
                    assert result["ok"] is True

            asyncio.run(run())

            with RoleSessionService(db=session_factory()) as service:
                messages = service.get_messages(created["session_id"], limit=10, offset=0)
                assert [message.role for message in messages] == ["user", "assistant"]
                assert messages[0].content == "继续完善 session continuity"
                assert messages[1].content == "done"
                session = service.get_session(created["session_id"])
                assert session is not None
                payload = session.to_dict(include_messages=False)
                assert payload["context_config"].get("agent_router_v1") is True
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
            with contextlib.suppress(PermissionError):
                db_path.unlink(missing_ok=True)

    def test_agent_memory_endpoints_restore_context_os_payloads(self) -> None:
        workspace = _make_workspace_tempdir()
        db_path = _make_sqlite_db_path()
        engine, session_factory, conv_mod = _install_temp_session_db(db_path)
        try:
            session_id = _seed_agent_context_os_session(session_factory, workspace)
            with _build_test_client(workspace) as client:
                search_response = client.get(
                    f"/agent/sessions/{session_id}/memory/search",
                    params={"q": "SessionContinuityEngine", "kind": "artifact", "limit": 1},
                )
                assert search_response.status_code == 200
                search_payload = search_response.json()
                assert search_payload["ok"] is True
                artifact_id = str(search_payload["items"][0]["id"])

                artifact_response = client.get(
                    f"/agent/sessions/{session_id}/memory/artifacts/{artifact_id}",
                    params={"start_line": 1, "end_line": 1},
                )
                assert artifact_response.status_code == 200
                artifact_payload = artifact_response.json()
                assert artifact_payload["ok"] is True
                assert artifact_payload["artifact"]["artifact_id"] == artifact_id

                episode_search = client.get(
                    f"/agent/sessions/{session_id}/memory/search",
                    params={"q": "restore endpoints", "kind": "episode", "limit": 1},
                )
                assert episode_search.status_code == 200
                episode_search_payload = episode_search.json()
                assert episode_search_payload["ok"] is True
                episode_id = str(episode_search_payload["items"][0]["id"])

                episode_response = client.get(
                    f"/agent/sessions/{session_id}/memory/episodes/{episode_id}",
                )
                assert episode_response.status_code == 200
                episode_payload = episode_response.json()
                assert episode_payload["ok"] is True
                assert episode_payload["episode"]["episode_id"] == episode_id

                state_response = client.get(
                    f"/agent/sessions/{session_id}/memory/state",
                    params={"path": "run_card"},
                )
                assert state_response.status_code == 200
                state_payload = state_response.json()
                assert state_payload["ok"] is True
                assert state_payload["path"] == "run_card"
                assert state_payload["value"]["current_goal"]
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
            with contextlib.suppress(PermissionError):
                db_path.unlink(missing_ok=True)
            shutil.rmtree(workspace, ignore_errors=True)
