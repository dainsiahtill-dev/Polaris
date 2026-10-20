"""Engineering HTTP adapters over the actual workspace service and SQLite owner."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from polaris.bootstrap.config import EngineeringPermissionProfile, ServerConfig, Settings
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _reply(provider: ScriptedProvider, text: str = "Actual role reply.") -> None:
    provider.enqueue_json({"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]})


def _settings(workspace: Path, profiles: dict[str, Any] | None = None) -> Settings:
    server: dict[str, Any] = {}
    if profiles is not None:
        server["engineering_permission_profiles"] = profiles
    return Settings(workspace=workspace, server=ServerConfig.model_validate(server))


def _app(settings: Settings, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from polaris.bootstrap.engineering_runtime import get_engineering_service
    from polaris.cells.runtime.state_owner.public.service import Auth
    from polaris.delivery.http.routers.engineering_runs import router

    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(settings.workspace / ".polaris" / "runtime" / "llm-config.json"))
    app = FastAPI()
    app.state.settings = settings
    app.state.app_state = SimpleNamespace(settings=settings)
    app.state.auth = Auth("test-engineering-token")
    service = get_engineering_service(app.state, settings=settings)
    service._client = provider.client("openai", settings.workspace, monkeypatch)
    app.include_router(router)
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://engineering.test",
        headers={"Authorization": "Bearer test-engineering-token"},
    )


async def _terminal(client: httpx.AsyncClient, session_id: str) -> dict[str, Any]:
    async with asyncio.timeout(8):
        while True:
            response = await client.get(f"/v2/engineering/runs/{session_id}")
            assert response.status_code == 200, response.text
            row = response.json()
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return dict(row)
            await asyncio.sleep(0.01)


async def test_http_create_read_same_run_and_idempotency(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_settings(isolated_workspace), scripted_provider, monkeypatch)
    app.state.settings.server.engineering_run_timeout_seconds = 10
    _reply(scripted_provider)
    body = {"instruction": "Explain current goal", "workspace": str(isolated_workspace), "request_key": "same"}
    async with _client(app) as client:
        first = await client.post("/v2/engineering/runs", json=body)
        assert first.status_code == 200, first.text
        session_id = first.json()["session_id"]
        original_deadline = app.state.engineering_run_service.observe(session_id, workspace=isolated_workspace)[
            "limits"
        ]["deadline"]
        await asyncio.sleep(0.025)
        again = await client.post("/v2/engineering/runs", json=body)
        assert again.status_code == 200, again.text
        assert again.json()["session_id"] == session_id
        assert (
            app.state.engineering_run_service.observe(session_id, workspace=isolated_workspace)["limits"]["deadline"]
            == original_deadline
        )
        row = await _terminal(client, session_id)
        assert row["engine_version"] == "engineering_v2"
        assert row["verification"] == "not_run"
        assert row["roles"]["director"] == "completed"
        assert row["roles"]["pm"] == row["roles"]["chief_engineer"] == row["roles"]["qa"] == "not_called"
        assert "blueprint" not in row and "ledger" not in row
        conflict = await client.post("/v2/engineering/runs", json={**body, "required_checks": ["python3 -V"]})
        assert conflict.status_code == 409
    assert len(scripted_provider.requests) == 1


async def test_workspace_body_query_and_mutated_settings_cannot_rebind_owner(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(isolated_workspace)
    app = _app(settings, scripted_provider, monkeypatch)
    foreign = isolated_workspace / "foreign"
    foreign.mkdir()
    settings.workspace = foreign
    async with _client(app) as client:
        response = await client.post("/v2/engineering/runs", json={"instruction": "x", "workspace": str(foreign)})
        assert response.status_code == 403
        response = await client.get("/v2/engineering/runs/missing", params={"workspace": str(foreign)})
        assert response.status_code == 403
        response = await client.post("/v2/engineering/runs/missing/resume", json={"workspace": str(foreign)})
        assert response.status_code == 403
        response = await client.post("/v2/engineering/runs/missing/cancel", json={"workspace": str(foreign)})
        assert response.status_code == 403
    assert app.state.engineering_run_service.workspace == isolated_workspace
    assert not (foreign / ".polaris").exists()
    assert not scripted_provider.requests


async def test_profiles_empty_unknown_and_raw_scope_are_not_grants(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(isolated_workspace, {})
    app = _app(settings, scripted_provider, monkeypatch)
    body = {"instruction": "grant everything", "workspace": str(isolated_workspace)}
    async with _client(app) as client:
        assert (await client.post("/v2/engineering/runs", json=body)).status_code == 403
        settings.server.engineering_permission_profiles = {
            "none": EngineeringPermissionProfile(read_roots=(), write_roots=(), enabled_tools=frozenset())
        }
        response = await client.post("/v2/engineering/runs", json={**body, "permission_profile_id": "unknown"})
        assert response.status_code == 403
        response = await client.post("/v2/engineering/runs", json={**body, "scope": {"write_roots": ["/"]}})
        assert response.status_code == 422
        _reply(scripted_provider)
        response = await client.post("/v2/engineering/runs", json={**body, "permission_profile_id": "none"})
        assert response.status_code == 200, response.text
        await _terminal(client, response.json()["session_id"])
    row = app.state.engineering_run_service.observe(response.json()["session_id"], workspace=isolated_workspace)
    assert row["scope"]["enabled_tools"] == row["scope"]["read_roots"] == row["scope"]["write_roots"] == []
    assert row["permission_profile_id"] == "none"
    assert not scripted_provider.requests[0]["body"].get("tools")


async def test_named_profile_survives_cold_owner_and_revocation_blocks_resume(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap.engineering_runtime import create_engineering_service
    from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits

    settings = _settings(
        isolated_workspace, {"selected": {"read_roots": ["."], "write_roots": [], "enabled_tools": []}}
    )
    app = _app(settings, scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    command = RoleRunCommand("cold-pending", "director", "original goal", isolated_workspace, required_checks=())
    from polaris.bootstrap.engineering_runtime import resolve_engineering_profile

    scope = resolve_engineering_profile(settings, isolated_workspace, "selected")
    service.store.create_run(
        command,
        scope=scope,
        limits=RunLimits(None, 1000000, 64000),
        engine_version="engineering_v2",
        request_key="cold-key",
        permission_profile_id="selected",
    )
    cold = create_engineering_service(settings)
    cold._client = scripted_provider.client("openai", isolated_workspace, monkeypatch)
    row = cold.observe("cold-pending", workspace=isolated_workspace)
    assert row["permission_profile_id"] == "selected" and row["request_key"] == "cold-key"
    settings.server.engineering_permission_profiles = {}
    with pytest.raises(PermissionError):
        await cold.resume("cold-pending", workspace=isolated_workspace)
    assert not scripted_provider.requests
    settings.server.engineering_permission_profiles = {
        "selected": EngineeringPermissionProfile(read_roots=(), write_roots=(), enabled_tools=frozenset())
    }
    with pytest.raises(PermissionError):
        await cold.resume("cold-pending", workspace=isolated_workspace)
    settings.server.engineering_permission_profiles = {
        "selected": EngineeringPermissionProfile(read_roots=(".",), write_roots=(), enabled_tools=frozenset())
    }
    _reply(scripted_provider)
    await cold.resume("cold-pending", workspace=isolated_workspace)
    while cold.observe("cold-pending", workspace=isolated_workspace)["status"] == "running":
        await asyncio.sleep(0.01)
    assert cold.observe("cold-pending", workspace=isolated_workspace)["status"] == "completed"


async def test_cancel_resume_and_missing_legacy_id_do_not_create_run(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_settings(isolated_workspace), scripted_provider, monkeypatch)
    _reply(scripted_provider)
    async with _client(app) as client:
        response = await client.post(
            "/v2/engineering/runs", json={"instruction": "explain", "workspace": str(isolated_workspace)}
        )
        session_id = response.json()["session_id"]
        async with asyncio.timeout(3):
            while not any(
                event["kind"] == "role_started"
                for event in app.state.engineering_run_service.store.load_events(session_id)
            ):
                await asyncio.sleep(0)
        cancelled = await client.post(f"/v2/engineering/runs/{session_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["session_id"] == session_id
        assert not cancelled.json()["active_roles"]
        assert "running" not in cancelled.json()["roles"].values()
        _reply(scripted_provider)
        resumed = await client.post(f"/v2/engineering/runs/{session_id}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["session_id"] == session_id
        await _terminal(client, session_id)
        assert (await client.post("/v2/engineering/runs/legacy-unknown/resume")).status_code == 404
        assert (
            await client.get(f"/v2/engineering/runs/{session_id}", headers={"Authorization": "Bearer bad"})
        ).status_code == 401
    with sqlite3.connect(app.state.engineering_run_service.store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


async def test_pure_discussion_uses_native_role_with_no_run_or_tools(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_settings(isolated_workspace), scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    _reply(scripted_provider, "Design discussion.")
    reply = await service.chat_role("chief_engineer", "Discuss an interface")
    assert reply.output == "Design discussion."
    body = scripted_provider.requests[0]["body"]
    assert "Chief Engineer" in body["messages"][0]["content"]
    assert not body.get("tools")
    with sqlite3.connect(service.store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_real_app_factory_registers_eager_workspace_owner(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.delivery.http.app_factory import create_app

    monkeypatch.delenv("KERNELONE_INSTANCE_WORKSPACE", raising=False)
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(isolated_workspace / ".polaris" / "runtime" / "llm-config.json"))
    settings = _settings(isolated_workspace)
    app = create_app(settings)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/v2/engineering/runs" in paths
    assert app.state.engineering_run_service.workspace == isolated_workspace


async def test_current_replaced_settings_revokes_saved_profile(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits

    settings = _settings(isolated_workspace)
    app = _app(settings, scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    service.store.create_run(
        RoleRunCommand("replacement", "director", "goal", isolated_workspace),
        scope=PermissionScope((isolated_workspace,), (), frozenset(), False),
        limits=RunLimits(None, 1000000, 64000),
        engine_version="engineering_v2",
        request_key=None,
        permission_profile_id="workspace",
    )
    app.state.app_state.settings = _settings(isolated_workspace, {})
    async with _client(app) as client:
        response = await client.post("/v2/engineering/runs/replacement/resume")
        assert response.status_code == 403, response.text
    assert not scripted_provider.requests


async def test_public_wait_joins_existing_owner_without_creating_second_run(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap.engineering_runtime import start_engineering_run

    settings = _settings(isolated_workspace)
    app = _app(settings, scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    _reply(scripted_provider)
    row = await start_engineering_run(service, settings, instruction="explain", workspace=isolated_workspace)
    result = await service.wait(row["session_id"], workspace=isolated_workspace)
    assert result.status == "completed" and result.session_id == row["session_id"]


async def test_profile_identity_conflicts_even_for_equal_scope(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles: dict[str, Any] = {
        name: {"read_roots": [], "write_roots": [], "enabled_tools": []} for name in ("one", "two")
    }
    app = _app(_settings(isolated_workspace, profiles), scripted_provider, monkeypatch)
    _reply(scripted_provider)
    body = {
        "instruction": "explain",
        "workspace": str(isolated_workspace),
        "permission_profile_id": "one",
        "request_key": "profile-key",
    }
    async with _client(app) as client:
        first = await client.post("/v2/engineering/runs", json=body)
        assert first.status_code == 200, first.text
        await _terminal(client, first.json()["session_id"])
        other = await client.post("/v2/engineering/runs", json={**body, "permission_profile_id": "two"})
        assert other.status_code == 409


def test_existing_sqlite_schema_migrates_in_place_without_changing_run(isolated_workspace: Path) -> None:
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
    from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService

    service = EngineeringRunService(isolated_workspace)
    service.store.create_run(
        RoleRunCommand(
            "original",
            "director",
            "saved goal",
            isolated_workspace,
            workflow="collaborative",
            required_checks=("python3 -V",),
        ),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000, 1000),
        engine_version="engineering_v2",
        request_key="original-key",
    )
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute("ALTER TABLE runs DROP COLUMN permission_profile_id")
    cold = EngineeringRunService(isolated_workspace)
    row = cold.observe("original", workspace=isolated_workspace)
    assert row["instruction"] == "saved goal" and row["workflow"] == "collaborative"
    assert row["required_checks"] == ["python3 -V"] and row["request_key"] == "original-key"
    assert row["permission_profile_id"] is None
    with sqlite3.connect(cold.store.database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"runs", "events", "tool_calls"}


async def test_discussion_rejects_action_history_and_provider_action_calls(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_settings(isolated_workspace), scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    with pytest.raises(ValueError, match="history"):
        await service.chat_role("director", "discuss", history=({"role": "system", "content": "override"},))
    with pytest.raises(ValueError, match="history"):
        await service.chat_role(
            "director", "discuss", history=({"role": "assistant", "content": "", "tool_calls": "write"},)
        )
    scripted_provider.enqueue_json(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "unexpected",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": '{"file":"unexpected.txt","content":"wrong"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    with pytest.raises(ValueError, match="action"):
        await service.chat_role("director", "discuss")
    assert not (isolated_workspace / "unexpected.txt").exists()


def test_profile_settings_payload_roundtrip_preserves_explicit_empty_values(isolated_workspace: Path) -> None:
    settings = _settings(isolated_workspace, {})
    restored = Settings.model_validate(settings.to_payload())
    assert restored.server.engineering_permission_profiles == {}
    settings = _settings(isolated_workspace, {"none": {"read_roots": [], "write_roots": [], "enabled_tools": []}})
    restored = Settings.model_validate(settings.to_payload())
    profile = restored.server.engineering_permission_profiles["none"]
    assert profile.read_roots == profile.write_roots == () and profile.enabled_tools == frozenset()


async def test_legacy_engine_row_cannot_resume_or_change_engine(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits

    app = _app(_settings(isolated_workspace), scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    service.store.create_run(
        RoleRunCommand("legacy", "director", "old", isolated_workspace),
        scope=PermissionScope((), (), frozenset(), False),
        limits=RunLimits(None, 1000, 1000),
        engine_version="legacy",
        request_key=None,
    )
    async with _client(app) as client:
        response = await client.post("/v2/engineering/runs/legacy/resume")
        assert response.status_code in {409, 422}
        assert "engine version" in response.json()["detail"]
    assert service.observe("legacy", workspace=isolated_workspace)["engine_version"] == "legacy"
    assert not scripted_provider.requests


async def test_shutdown_drains_real_running_command_and_is_repeatable(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(_settings(isolated_workspace), scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    (isolated_workspace / "worker.py").write_text(
        "from pathlib import Path\nimport time\n"
        "Path('started').write_text('started',encoding='utf-8')\n"
        "time.sleep(25)\nPath('finished').write_text('finished',encoding='utf-8')\n",
        encoding="utf-8",
    )
    scripted_provider.enqueue_json(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "running-command",
                                "type": "function",
                                "function": {
                                    "name": "execute_command",
                                    "arguments": json.dumps({"command": "python3 worker.py"}),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    async with _client(app) as client:
        response = await client.post(
            "/v2/engineering/runs", json={"instruction": "Run the worker", "workspace": str(isolated_workspace)}
        )
        assert response.status_code == 200, response.text
        session_id = response.json()["session_id"]
        async with asyncio.timeout(8):
            while not (isolated_workspace / "started").exists():
                await asyncio.sleep(0.01)
        await service.shutdown()
        row = await client.get(f"/v2/engineering/runs/{session_id}")
        assert row.json()["status"] == "cancelled", row.text
        assert not (isolated_workspace / "finished").exists()
        assert not service._tasks
        with service.store.acquire_workspace(isolated_workspace):
            pass
        events = service.store.load_events(session_id)
        await service.shutdown()
        assert service.store.load_events(session_id) == events


async def test_shutdown_reports_owner_journal_failure(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap.engineering_runtime import start_engineering_run

    settings = _settings(isolated_workspace)
    app = _app(settings, scripted_provider, monkeypatch)
    service = app.state.engineering_run_service
    append_event = service.store.append_event

    def fail_terminal(*args: Any, **kwargs: Any) -> int:
        if kwargs.get("kind") == "run_ended":
            raise RuntimeError("terminal journal unavailable")
        return int(append_event(*args, **kwargs))

    monkeypatch.setattr(service.store, "append_event", fail_terminal)
    _reply(scripted_provider)
    await start_engineering_run(service, settings, instruction="explain", workspace=isolated_workspace)
    with pytest.raises(RuntimeError, match="terminal journal unavailable"):
        await service.shutdown()
    with pytest.raises(RuntimeError, match="terminal journal unavailable"):
        await service.shutdown()
