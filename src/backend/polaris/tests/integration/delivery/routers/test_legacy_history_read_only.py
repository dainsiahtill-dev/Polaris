"""Historical HTTP reads preserve stored bytes and never bootstrap missing data."""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRun, FactoryRunService, FactoryRunStatus
from polaris.cells.roles.session.public import RoleSessionArtifactService, RoleSessionService
from polaris.delivery.http.routers import agent, factory, role_session
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.v2 import orchestration
from polaris.kernelone.storage import clear_storage_roots_cache, resolve_storage_roots
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    for key in (
        "KERNELONE_RUNTIME_ROOT",
        "KERNELONE_RUNTIME_CACHE_ROOT",
        "KERNELONE_RAMDISK_ROOT",
        "KERNELONE_CONTEXT_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")
    clear_storage_roots_cache()
    target = tmp_path / "project"
    target.mkdir()
    yield target
    clear_storage_roots_cache()


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        str(p.relative_to(root)): (
            p.stat().st_mtime_ns,
            sha256(p.read_bytes()).hexdigest() if p.is_file() else "directory",
        )
        for p in root.rglob("*")
    }


def _app(workspace: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(factory.router)
    app.include_router(role_session.router)
    app.include_router(agent.router, prefix="/v2")
    app.include_router(orchestration.router, prefix="/v2")
    app.dependency_overrides[orchestration.require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=workspace))
    app.dependency_overrides[require_auth] = lambda: None
    return app


@pytest.mark.asyncio
async def test_missing_history_reads_create_no_directories_or_database(workspace: Path) -> None:
    before = sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*"))
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        for path in ("/v2/roles/sessions", "/v2/factory/runs", "/v2/agent/sessions"):
            response = await client.get(path, params={"workspace": str(workspace)})
            assert response.status_code == 200, response.text
        for path in ("/v2/roles/sessions/missing", "/v2/factory/runs/missing"):
            response = await client.get(path, params={"workspace": str(workspace)})
            assert response.status_code == 404, response.text
    assert sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*")) == before


@pytest.mark.parametrize("operation", ["create", "cleanup"])
def test_read_only_role_owner_rejects_mutation_before_storage(workspace: Path, operation: str) -> None:
    with (
        RoleSessionService(workspace=str(workspace), read_only=True) as reader,
        pytest.raises(PermissionError, match="read-only"),
    ):
        if operation == "create":
            reader.create_session(role="pm", workspace=str(workspace))
        else:
            reader.cleanup_expired_sessions()
    assert list(workspace.rglob("*")) == []


@pytest.mark.asyncio
async def test_agent_history_stays_in_bound_workspace_without_bootstrap(workspace: Path) -> None:
    with RoleSessionService(workspace=str(workspace), enable_events=False, enable_persistence=False) as writer:
        identity = writer.create_session(
            role="pm",
            workspace=str(workspace),
            host_kind="api_server",
            session_type="standalone",
            context_config={"agent_router_v1": True},
        ).id
        writer.add_message(identity, "user", "historical agent message")
    before = _snapshot(workspace)
    clear_storage_roots_cache()
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        listing = await client.get("/v2/agent/sessions", params={"workspace": str(workspace)})
        assert listing.status_code == 200, listing.text
        assert [row["session_id"] for row in listing.json()["sessions"]] == [identity]
        detail = await client.get(f"/v2/agent/sessions/{identity}", params={"workspace": str(workspace)})
        assert detail.status_code == 200, detail.text
        foreign = await client.get(f"/v2/agent/sessions/{identity}", params={"workspace": "/foreign"})
        assert foreign.status_code == 404, foreign.text
    assert _snapshot(workspace) == before


@pytest.mark.asyncio
async def test_orchestration_gets_do_not_initialize_service_and_reject_foreign_workspace(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.orchestration.workflow_runtime.internal import unified_orchestration_service as owner

    marker = workspace.parent / "constructed.txt"

    def forbidden_constructor(*args: Any, **kwargs: Any) -> Any:
        marker.write_text("orchestration initialized", encoding="utf-8")
        raise RuntimeError("history initialized orchestration")

    monkeypatch.setattr(owner, "_service_instance", None)
    monkeypatch.setattr(owner, "UnifiedOrchestrationService", forbidden_constructor)
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        listing = await client.get("/v2/orchestration/runs", params={"workspace": str(workspace)})
        assert listing.status_code == 200, listing.text
        assert listing.json()["runs"] == []
        for suffix in ("/old", "/old/tasks"):
            missing = await client.get("/v2/orchestration/runs" + suffix, params={"workspace": str(workspace)})
            assert missing.status_code == 404, missing.text
        foreign = await client.get("/v2/orchestration/runs", params={"workspace": "/foreign"})
        assert foreign.status_code == 404, foreign.text
    assert not marker.exists()
    assert list(workspace.rglob("*")) == []


@pytest.mark.asyncio
async def test_orchestration_existing_snapshot_cannot_escape_workspace(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.orchestration.workflow_runtime.internal import unified_orchestration_service as owner
    from polaris.cells.orchestration.workflow_runtime.public.service import (
        OrchestrationMode,
        OrchestrationSnapshot,
        RunStatus,
        TaskPhase,
        UnifiedOrchestrationService,
    )

    service = UnifiedOrchestrationService()
    await service._repo.save_snapshot(
        OrchestrationSnapshot(
            run_id="foreign",
            workspace="/foreign",
            mode=OrchestrationMode.WORKFLOW.value,
            status=RunStatus.FAILED,
            current_phase=TaskPhase.INIT,
        )
    )
    monkeypatch.setattr(owner, "_service_instance", service)
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        for suffix in ("/foreign", "/foreign/tasks"):
            response = await client.get("/v2/orchestration/runs" + suffix, params={"workspace": str(workspace)})
            assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["roles", "agent"])
async def test_memory_history_never_constructs_unbound_mutable_session_service(
    workspace: Path, surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.roles.session.internal import context_memory_service

    with RoleSessionService(workspace=str(workspace), enable_events=False, enable_persistence=False) as writer:
        identity = writer.create_session(
            role="pm",
            workspace=str(workspace),
            host_kind="api_server",
            session_type="standalone",
            context_config={"agent_router_v1": True},
        ).id
    marker = workspace.parent / "unbound-service.txt"

    def forbidden_default_service(*args: Any, **kwargs: Any) -> Any:
        marker.write_text("unbound mutable service attempted", encoding="utf-8")
        raise RuntimeError("unbound historical session service")

    monkeypatch.setattr(context_memory_service, "RoleSessionService", forbidden_default_service)
    before = _snapshot(workspace)
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        response = await client.get(
            f"/v2/{surface}/sessions/{identity}/memory/search", params={"q": "history", "workspace": str(workspace)}
        )
    assert response.status_code == 200, response.text
    assert not marker.exists()
    assert _snapshot(workspace) == before


@pytest.mark.asyncio
async def test_existing_role_history_is_scoped_and_sqlite_read_only(workspace: Path) -> None:
    with RoleSessionService(workspace=str(workspace), enable_events=False, enable_persistence=False) as service:
        session = service.create_session(role="pm", workspace=str(workspace), host_kind="web", title="historical")
        identity = session.id
        service.add_message(identity, "user", "历史内容")
        foreign = service.create_session(role="pm", workspace="/foreign", host_kind="web", title="foreign")
        foreign_identity = foreign.id
    artifact_service = RoleSessionArtifactService(workspace)
    artifact_service.write_artifact(identity, "document", "history artifact")
    before = _snapshot(workspace)
    clear_storage_roots_cache()
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        listing = await client.get("/v2/roles/sessions", params={"workspace": str(workspace)})
        assert listing.status_code == 200, listing.text
        assert [row["id"] for row in listing.json()["sessions"]] == [identity]
        for suffix in ("", "/messages", "/artifacts", "/audit"):
            response = await client.get(f"/v2/roles/sessions/{identity}{suffix}", params={"workspace": str(workspace)})
            assert response.status_code == 200, response.text
            foreign = await client.get(
                f"/v2/roles/sessions/{foreign_identity}{suffix}", params={"workspace": str(workspace)}
            )
            assert foreign.status_code == 404, foreign.text
        foreign_query = await client.get(f"/v2/roles/sessions/{identity}", params={"workspace": "/foreign"})
        assert foreign_query.status_code == 404, foreign_query.text
    assert _snapshot(workspace) == before
    with RoleSessionService(workspace=str(workspace), read_only=True) as reader:
        assert reader.db.execute(text("PRAGMA query_only")).scalar() == 1
        with pytest.raises(OperationalError, match=r"readonly|read-only"):
            reader.db.execute(text("UPDATE conversations SET title='illegal'"))
    assert _snapshot(workspace) == before


@pytest.mark.asyncio
async def test_factory_history_reader_avoids_executor_and_preserves_snapshot(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.cells.factory.pipeline.internal.factory_run_service import _service_core
    from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore

    runtime_root = Path(resolve_storage_roots(str(workspace)).runtime_root)
    store = FactoryStore(runtime_root / "factory")
    historical = FactoryRun(
        id="factory_history",
        config=FactoryConfig(name="historical"),
        status=FactoryRunStatus.FAILED,
        created_at="2026-10-20T00:00:00+00:00",
        metadata={"workspace": str(workspace)},
    )
    await store.save_run(historical)
    before = _snapshot(workspace)
    clear_storage_roots_cache()

    def forbidden_executor(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("history instantiated legacy executor")

    monkeypatch.setattr(_service_core, "OrchestrationStageExecutor", forbidden_executor)
    async with AsyncClient(transport=ASGITransport(app=_app(workspace)), base_url="http://test") as client:
        for suffix in ("", "/factory_history", "/factory_history/artifacts", "/factory_history/events"):
            response = await client.get("/v2/factory/runs" + suffix, params={"workspace": str(workspace)})
            assert response.status_code == 200, response.text
        foreign = await client.get("/v2/factory/runs/factory_history", params={"workspace": "/foreign"})
        assert foreign.status_code == 404, foreign.text
    assert _snapshot(workspace) == before
    # Explicit read mode cannot opt back into retired execution.
    reader = FactoryRunService(workspace=workspace, read_only=True)
    with pytest.raises(RuntimeError, match="legacy_engine_retired"):
        await reader.create_run(FactoryConfig(name="forbidden"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,kwargs",
    [
        ("update_run_metadata", ({"should_not_persist": True},), {}),
        ("cancel_run", (), {}),
        ("complete_run", (), {}),
        ("settle_terminal_run", (), {}),
        ("recover_stale_workspace_owner", (), {"expected_fencing_token": 1, "reason": "must remain historical"}),
        ("reconcile_stage_execution_for_reentry", (), {"operation": "history"}),
        ("assert_mutation_allowed", (), {}),
        ("assert_automatic_router_mutation_allowed", (), {"operation": "quality_gate_rework"}),
        ("apply_automatic_router_mutation", (), {"operation": "quality_gate_rework", "mutation": lambda run: True}),
    ],
)
async def test_read_only_factory_mutations_preserve_all_historical_files_and_locks(
    workspace: Path, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    from polaris.cells.factory.pipeline.internal.factory_event_chain import (
        FactoryRunAdmissionV1,
        build_factory_run_admitted_event,
    )

    writer = FactoryRunService(workspace=workspace)
    historical = FactoryRun(
        id="factory_history_guard",
        config=FactoryConfig(name="historical"),
        status=FactoryRunStatus.FAILED,
        created_at="2026-10-20T00:00:00+00:00",
        metadata={"workspace": str(workspace)},
    )
    await writer._append_event(
        historical.id,
        build_factory_run_admitted_event(
            FactoryRunAdmissionV1(
                factory_run_id=historical.id,
                created_at=historical.created_at,
                name=historical.config.name,
                description=historical.config.description,
            )
        ),
        publish=False,
    )
    await writer.store.save_run(historical)
    reader = FactoryRunService(workspace=workspace, read_only=True)
    before = _snapshot(workspace)
    caught: Exception | None = None
    try:
        await getattr(reader, method)(historical.id, *args, **kwargs)
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError) as exc:
        caught = exc
    assert _snapshot(workspace) == before
    assert isinstance(caught, PermissionError), repr(caught)


@pytest.mark.parametrize("operation", ["write_artifact", "delete_artifact", "export_artifacts"])
def test_read_only_artifact_owner_cannot_write_or_export(workspace: Path, operation: str) -> None:
    artifact = RoleSessionArtifactService(workspace).write_artifact("old", "document", "preserved historical artifact")
    owner = RoleSessionArtifactService(workspace, read_only=True)
    before = _snapshot(workspace)
    with pytest.raises(PermissionError, match="read-only"):
        if operation == "write_artifact":
            owner.write_artifact("old", "document", "must not persist")
        elif operation == "delete_artifact":
            owner.delete_artifact("old", artifact.id)
        else:
            owner.export_artifacts("old", workspace / "must-not-create")
    assert _snapshot(workspace) == before
