"""Retired HTTP and owner entrypoints reject work before legacy effects."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from polaris.cells.factory.pipeline.public.service import FactoryConfig
from polaris.cells.orchestration.pm_dispatch.internal.orchestration_command_service import (
    OrchestrationCommandService,
)
from polaris.delivery.http.routers import agent, factory, pm_management, role_chat, role_chat_jetstream, role_session
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.v2 import director, orchestration, pm


def _app(workspace: Path) -> FastAPI:
    app = FastAPI()
    for router in (
        factory.router,
        role_session.router,
        role_chat.router,
        pm_management.router,
        pm_management.v2_router,
    ):
        app.include_router(router)
    app.include_router(director.router, prefix="/v2")
    for router in (pm.router, orchestration.router, agent.router):
        app.include_router(router, prefix="/v2")
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=workspace))
    app.dependency_overrides[require_auth] = lambda: None
    app.dependency_overrides[director.require_auth] = lambda: None
    return app


def _effect_probe(marker: Path) -> Any:
    def probe(*args: Any, **kwargs: Any) -> Any:
        marker.write_text("legacy effect attempted", encoding="utf-8")
        raise RuntimeError("legacy_effect_attempted")

    return probe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/v2/roles/sessions", {"role": "pm"}),
        ("PUT", "/v2/roles/sessions/old-id", {"title": "changed"}),
        ("DELETE", "/v2/roles/sessions/old-id", None),
        ("POST", "/v2/roles/sessions/old-id/messages", {"role": "user", "content": "run"}),
        ("POST", "/v2/roles/sessions/old-id/messages/jetstream", {"role": "user", "content": "run"}),
        ("POST", "/v2/roles/sessions/old-id/actions/attach", {"run_id": "old-run"}),
        ("POST", "/v2/roles/sessions/old-id/actions/detach", None),
        ("POST", "/v2/roles/sessions/old-id/audit/events", {"event_type": "message"}),
        ("POST", "/v2/roles/sessions/old-id/audit/export", {}),
        ("POST", "/v2/roles/sessions/old-id/actions/export", {}),
        ("POST", "/v2/roles/sessions/old-id/actions/export-to-workflow", {"target": "factory"}),
    ],
)
async def test_session_mutations_retired_before_storage(
    method: str, path: str, payload: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    monkeypatch.setattr(role_session, "RoleSessionService", _effect_probe(marker))
    app = _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.request(method, path, json=payload)
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    assert "/v2/engineering/runs" in response.json()["detail"]["supported_endpoints"]
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/v2/pm/start", None),
        ("POST", "/v2/pm/run_once", None),
        ("POST", "/v2/pm/run", {}),
        ("POST", "/v2/pm/stop", None),
        ("POST", "/v2/director/stop", None),
        ("POST", "/v2/director/tasks/old/cancel", None),
        ("POST", "/v2/orchestration/runs", {"workspace": "/old"}),
        ("POST", "/v2/orchestration/runs/old/signal", {"signal": "resume"}),
        ("POST", "/v2/orchestration/runs/old/signal", {"signal": "retry"}),
        ("POST", "/v2/orchestration/runs/old/signal", {"signal": "cancel"}),
        ("DELETE", "/v2/orchestration/runs/old", None),
        ("POST", "/v2/pm/runs/old/cancel", None),
        ("POST", "/v2/director/runs/old/cancel", None),
        ("POST", "/v2/agent/turn", {"message": "run", "stream": False}),
        ("POST", "/v2/agent/turn", {"message": "run", "stream": True}),
        ("POST", "/v2/agent/sessions/old/messages", {"message": "run"}),
        ("DELETE", "/v2/agent/sessions/old", None),
    ],
)
async def test_remaining_legacy_http_consumers_reject_before_preflight(
    method: str, path: str, payload: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    probe = _effect_probe(marker)
    monkeypatch.setattr(agent, "RoleSessionService", probe)
    monkeypatch.setattr(pm, "_build_pm_diagnostics_for_request", probe)
    monkeypatch.setattr(pm, "_ensure_director_handoff_llm_ready", probe)
    monkeypatch.setattr(orchestration, "get_orchestration_service", probe)
    app = _app(tmp_path)
    from polaris.delivery.http.dependencies import get_director_service, get_pm_service

    def forbidden_service() -> Any:
        return probe()

    app.dependency_overrides[get_pm_service] = forbidden_service
    app.dependency_overrides[get_director_service] = forbidden_service
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.request(method, path, json=payload)
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/v2/pm/tasks", {"subject": "old task"}),
        ("POST", "/pm/init", None),
        ("POST", "/v2/pm/management/init", None),
        ("POST", "/pm/documents/old.md", {"content": "changed"}),
        ("POST", "/v2/pm/documents/old.md", {"content": "changed"}),
        ("DELETE", "/pm/documents/old.md", None),
        ("DELETE", "/v2/pm/documents/old.md", None),
        ("POST", "/v2/director/start", None),
        ("POST", "/v2/director/tasks", {"subject": "old task"}),
        ("POST", "/v2/director/run", {}),
        ("POST", "/v2/director/integration-qa", {}),
    ],
)
async def test_other_legacy_mutations_retired_before_owner_construction(
    method: str, path: str, payload: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    monkeypatch.setattr(pm_management, "_get_pm_instance", _effect_probe(marker))
    monkeypatch.setattr(director, "get_director_service_dep", _effect_probe(marker))
    monkeypatch.setattr(director, "_ensure_director_lifecycle_can_start", _effect_probe(marker))
    monkeypatch.setattr(director, "_build_director_diagnostics_for_request", _effect_probe(marker))
    app = _app(tmp_path)

    def forbidden_service() -> Any:
        return _effect_probe(marker)()

    app.dependency_overrides[director.get_director_service_dep] = forbidden_service
    # FastAPI dependencies retain their original callable during registration.
    from polaris.delivery.http.dependencies import get_director_service

    app.dependency_overrides[get_director_service] = forbidden_service
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.request(method, path, json=payload)
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start", "cancel", "pause", "resume", "retry_from_checkpoint", "retry_phase"])
async def test_factory_admission_retired_before_workspace_or_store(
    action: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    monkeypatch.setattr(factory, "_resolve_workspace", _effect_probe(marker))
    app = _app(tmp_path)
    path = "/v2/factory/runs" if action == "start" else "/v2/factory/runs/old-run/control"
    payload = {"workspace": str(tmp_path / "foreign"), "persist_workspace": True}
    if action != "start":
        payload = {"action": action, "target_phase": "implementation"}
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(path, json=payload)
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    assert app.state.app_state.settings.workspace == tmp_path
    assert not marker.exists()


@pytest.mark.asyncio
async def test_unversioned_stream_starter_rejects_without_scheduling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    monkeypatch.setattr(role_chat, "ensure_required_roles_ready", _effect_probe(marker), raising=False)
    app = _app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post("/v2/role/pm/chat/jetstream", json={"message": "讨论"})
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["error_code"] == "legacy_engine_retired"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_stream_helper_never_publishes_synthetic_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    monkeypatch.setattr(role_chat_jetstream, "RoleRuntimeService", _effect_probe(marker), raising=False)
    with pytest.raises(HTTPException) as caught:
        await role_chat_jetstream.execute_role_chat_jetstream(
            role="director", workspace=str(tmp_path), message="run", payload={}, default_domain="code", host_kind="test"
        )
    assert caught.value.status_code == 410
    assert isinstance(caught.value.detail, dict)
    assert caught.value.detail["error_code"] == "legacy_engine_retired"
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_schedule_factory_run_task", "_execute_run_with_service"])
async def test_factory_driver_adapters_reject_before_submit(
    method: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.bootstrap import factory_run_driver_runtime
    from polaris.cells.factory.pipeline.public.types import FactoryStartRequest

    marker = tmp_path / "effect.txt"
    monkeypatch.setattr(factory_run_driver_runtime, "get_factory_run_driver_runtime", _effect_probe(marker))
    with pytest.raises(HTTPException) as caught:
        result = getattr(factory, method)(
            service=_OwnerEffectProbe(marker),
            run_id="old-run",
            payload=FactoryStartRequest(workspace=str(tmp_path)),
            state=SimpleNamespace(),
        )
        if method == "_execute_run_with_service":
            await result
    assert caught.value.status_code == 410
    assert isinstance(caught.value.detail, dict)
    assert caught.value.detail["error_code"] == "legacy_engine_retired"
    assert not marker.exists()


class _OwnerEffectProbe:
    """Any attempted legacy dependency leaves durable test evidence."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker
        self._repo: Any = self

    def __getattr__(self, name: str) -> Any:
        return _effect_probe(self.marker)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("create_run", (FactoryConfig(name="old"),)),
        ("start_run", ("old-run",)),
        ("recover_run", ("old-run",)),
        ("resume_recovered_run", ("old-run",)),
        ("retry_run_from_stage", ("old-run",)),
        ("execute_pause", ("old-run",)),
        ("execute_resume", ("old-run",)),
        ("execute_stage", ("old-run", "pm_planning")),
    ],
)
async def test_factory_owner_retired_before_dependency_access(
    method: str, args: tuple[Any, ...], tmp_path: Path
) -> None:
    marker = tmp_path / "effect.txt"
    owner = _OwnerEffectProbe(marker)
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        from polaris.cells.factory.pipeline.public.service import FactoryRunService

        await getattr(FactoryRunService, method)(owner, *args)
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["execute_pm_run", "execute_director_run", "execute_qa_run", "execute_factory_run"])
async def test_orchestration_admission_retired_before_run_identity(
    method: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "effect.txt"
    service = OrchestrationCommandService(settings=SimpleNamespace(workspace=tmp_path))
    monkeypatch.setattr(service, "_generate_run_id", _effect_probe(marker))
    result = await getattr(service, method)(workspace=str(tmp_path))
    assert result.status == "failed"
    assert result.reason_code == "legacy_engine_retired"
    assert result.run_id == ""
    assert service.list_active_runs() == []
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["submit", "signal", "cancel"])
async def test_public_workflow_owner_rejects_before_repository_or_task_effects(operation: str, tmp_path: Path) -> None:
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        OrchestrationMode,
        OrchestrationRunRequest,
        OrchestrationSignal,
        SignalRequest,
        UnifiedOrchestrationService,
    )

    marker = tmp_path / "effect.txt"
    owner = _OwnerEffectProbe(marker)
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        if operation == "submit":
            await UnifiedOrchestrationService.submit_run(
                owner, OrchestrationRunRequest(run_id="old", workspace=str(tmp_path), mode=OrchestrationMode.WORKFLOW)
            )
        elif operation == "signal":
            await UnifiedOrchestrationService.signal_run(owner, "old", SignalRequest(signal=OrchestrationSignal.RETRY))
        else:
            await UnifiedOrchestrationService.cancel_run(owner, "old")
    assert not marker.exists()
