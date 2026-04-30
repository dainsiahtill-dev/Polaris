from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.roles.session.internal.conversation import Base
from polaris.cells.roles.session.internal.role_session_service import RoleSessionService
from polaris.delivery.http.routers import role_session as role_session_router
from polaris.kernelone.context.context_os import CodeContextDomainAdapter, StateFirstContextOS
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


def _make_workspace_tempdir() -> Path:
    root = Path.cwd() / ".tmp_role_session_context_memory_router"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="case-", dir=str(root)))


def _make_sqlite_db_path() -> Path:
    root = Path.cwd() / ".tmp_role_session_context_memory_router_db"
    root.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix="role-session-router-", suffix=".db", dir=str(root))
    os.close(fd)
    return Path(raw)


def _install_temp_session_db(db_path: Path) -> tuple[Any, Any, Any]:
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


def _seed_context_os_session(session_factory: Any) -> str:
    projection = asyncio.run(
        StateFirstContextOS(domain_adapter=CodeContextDomainAdapter()).project(
            messages=[
                {
                    "role": "user",
                    "content": "Fix polaris/kernelone/context/session_continuity.py and preserve session continuity runtime.",
                    "sequence": 1,
                },
                {
                    "role": "assistant",
                    "content": "I will update the continuity runtime and keep the public facade stable.",
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
    )
    snapshot = projection.snapshot.to_dict()
    artifact_store = list(snapshot.get("artifact_store") or [])
    artifact_refs = []
    if artifact_store:
        artifact_refs.append(str(artifact_store[0].get("artifact_id") or ""))
    snapshot["episode_store"] = [
        {
            "episode_id": "ep_1",
            "from_sequence": 1,
            "to_sequence": 3,
            "intent": "Preserve session continuity runtime while exposing restore memory.",
            "outcome": "Continuity runtime evidence was captured and prepared for HTTP restore.",
            "decisions": ["Keep SessionContinuityEngine as the public facade."],
            "facts": ["session_continuity.py remained part of the active runtime context."],
            "artifact_refs": artifact_refs,
            "entities": ["SessionContinuityEngine", "session_continuity.py"],
            "reopen_conditions": ["Reopen if HTTP restore endpoints diverge from persisted memory."],
            "source_spans": ["t1:t3"],
            "digest_64": "Continuity runtime evidence captured for restore.",
            "digest_256": "Preserve session continuity runtime and expose persisted restore memory over HTTP.",
            "narrative_1k": "The runtime kept continuity evidence, stored one artifact, and prepared an episode card for HTTP restore flows.",
        }
    ]
    with RoleSessionService(db=session_factory()) as service:
        session = service.create_session(
            role="director",
            context_config={"state_first_context_os": snapshot},
        )
        return str(session.id)


def _build_test_client(workspace: Path) -> TestClient:
    app = FastAPI()
    app.include_router(role_session_router.router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(
        settings=SimpleNamespace(workspace=str(workspace)),
    )
    return TestClient(app)


class TestRoleSessionContextMemoryRouter:
    def test_search_memory_endpoint_returns_items(self) -> None:
        workspace = _make_workspace_tempdir()
        db_path = _make_sqlite_db_path()
        engine, session_factory, conv_mod = _install_temp_session_db(db_path)
        try:
            session_id = _seed_context_os_session(session_factory)
            with _build_test_client(workspace) as client:
                response = client.get(
                    f"/v2/roles/sessions/{session_id}/memory/search",
                    params={"q": "session_continuity.py", "limit": 4},
                )

            assert response.status_code == 200
            payload = response.json()
            assert payload["ok"] is True
            assert payload["total"] >= 1
            assert any("session_continuity.py" in str(item.get("text") or "") for item in payload["items"])
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
            with contextlib.suppress(PermissionError):
                db_path.unlink(missing_ok=True)
            shutil.rmtree(workspace, ignore_errors=True)

    def test_read_artifact_endpoint_returns_payload(self) -> None:
        workspace = _make_workspace_tempdir()
        db_path = _make_sqlite_db_path()
        engine, session_factory, conv_mod = _install_temp_session_db(db_path)
        try:
            session_id = _seed_context_os_session(session_factory)
            with _build_test_client(workspace) as client:
                search = client.get(
                    f"/v2/roles/sessions/{session_id}/memory/search",
                    params={"q": "SessionContinuityEngine", "kind": "artifact", "limit": 1},
                ).json()
                artifact_id = str(search["items"][0]["id"])
                response = client.get(
                    f"/v2/roles/sessions/{session_id}/memory/artifacts/{artifact_id}",
                    params={"start_line": 1, "end_line": 1},
                )

            assert response.status_code == 200
            payload = response.json()
            assert payload["ok"] is True
            assert payload["artifact"]["artifact_id"] == artifact_id
            assert "SessionContinuityEngine" in str(payload["artifact"].get("content") or "")
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
            with contextlib.suppress(PermissionError):
                db_path.unlink(missing_ok=True)
            shutil.rmtree(workspace, ignore_errors=True)

    def test_read_episode_and_state_endpoints_return_context_os_views(self) -> None:
        workspace = _make_workspace_tempdir()
        db_path = _make_sqlite_db_path()
        engine, session_factory, conv_mod = _install_temp_session_db(db_path)
        try:
            session_id = _seed_context_os_session(session_factory)
            with _build_test_client(workspace) as client:
                episode_search = client.get(
                    f"/v2/roles/sessions/{session_id}/memory/search",
                    params={"q": "continuity runtime", "kind": "episode", "limit": 1},
                ).json()
                episode_id = str(episode_search["items"][0]["id"])
                episode_response = client.get(
                    f"/v2/roles/sessions/{session_id}/memory/episodes/{episode_id}",
                )
                state_response = client.get(
                    f"/v2/roles/sessions/{session_id}/memory/state",
                    params={"path": "run_card"},
                )

            assert episode_response.status_code == 200
            episode_payload = episode_response.json()
            assert episode_payload["ok"] is True
            assert episode_payload["episode"]["episode_id"] == episode_id

            assert state_response.status_code == 200
            state_payload = state_response.json()
            assert state_payload["ok"] is True
            assert state_payload["path"] == "run_card"
            assert state_payload["value"]["current_goal"]
            assert isinstance(state_payload["value"]["active_entities"], list)
        finally:
            conv_mod._engine = None
            conv_mod._SessionLocal = None
            engine.dispose()
            with contextlib.suppress(PermissionError):
                db_path.unlink(missing_ok=True)
            shutil.rmtree(workspace, ignore_errors=True)
