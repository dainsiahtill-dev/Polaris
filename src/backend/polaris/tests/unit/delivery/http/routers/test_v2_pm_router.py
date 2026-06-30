"""Tests for Polaris v2 PM router.

Covers PM v2 endpoints: run_once, start, stop, status, run, get orchestration,
llm-events, cache-stats, cache-clear, token-budget-stats.
External services are mocked to avoid DI container and LLM dependencies.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from polaris.bootstrap.config import Settings
from polaris.cells.runtime.state_owner.public.service import AppState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    """Create a minimal Settings instance for testing."""
    from polaris.bootstrap.config import ServerConfig, Settings
    from polaris.config.nats_config import NATSConfig

    settings = MagicMock(spec=Settings)
    settings.workspace = "."
    settings.workspace_path = "."
    settings.ramdisk_root = ""
    settings.nats = NATSConfig(enabled=False, required=False, url="")
    settings.server = ServerConfig(cors_origins=["*"])
    settings.qa_enabled = True
    settings.debug_tracing = False
    settings.logging = MagicMock()
    settings.logging.enable_debug_tracing = False
    return settings


@pytest.fixture
def mock_app_state(mock_settings: Settings) -> AppState:
    """Create a minimal AppState for testing."""
    return AppState(settings=mock_settings)


@pytest.fixture
async def client(mock_settings: Settings, mock_app_state: AppState) -> AsyncIterator[AsyncClient]:
    """Create an async test client with mocked lifespan."""
    from polaris.delivery.http.app_factory import create_app

    app = create_app(settings=mock_settings)

    class _AllowAllAuth:
        def check(self, _auth_header: str) -> bool:
            return True

    app.state.auth = _AllowAllAuth()

    with (
        patch(
            "polaris.infrastructure.messaging.nats.server_runtime.ensure_local_nats_runtime",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.bootstrap.assembly.assemble_core_services",
        ),
        patch(
            "polaris.infrastructure.di.container.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.kernelone.process.terminate_external_loop_pm_processes",
            return_value=[],
        ),
        patch(
            "polaris.delivery.http.app_factory.sync_process_settings_environment",
        ),
        patch(
            "polaris.delivery.http.routers.primary.get_settings",
            return_value=mock_settings,
        ),
        patch.dict("os.environ", {"KERNELONE_METRICS_ENABLED": "false"}),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


def _pm_startup_diagnostics(
    *,
    workspace: str = ".",
    can_start: bool = True,
    startup_blockers: list[str] | None = None,
    issues: list[str] | None = None,
) -> object:
    """Build a PM diagnostics payload for guarded execution endpoint tests."""
    from polaris.delivery.http.v2.pm import (
        PMDiagnosticsLanceDBStatus,
        PMDiagnosticsLLMStatus,
        PMDiagnosticsPlanningInputStatus,
        PMDiagnosticsResponse,
        PMDiagnosticsWorkspaceStatus,
    )

    blockers = list(startup_blockers or [])
    issue_tokens = list(issues if issues is not None else blockers)
    return PMDiagnosticsResponse(
        ok=not issue_tokens,
        can_start=can_start,
        generated_at="2026-05-24T00:00:00Z",
        lancedb=PMDiagnosticsLanceDBStatus(ok=True, state="ready"),
        llm=PMDiagnosticsLLMStatus(
            ok=True,
            state="ready",
            blocked_roles=[],
            unsupported_roles=[],
            required_ready_roles=["pm"],
        ),
        workspace=PMDiagnosticsWorkspaceStatus(
            ok=True,
            status="ok",
            workspace=workspace,
            docs_present=True,
        ),
        planning_input=PMDiagnosticsPlanningInputStatus(
            ok=True,
            status="ready",
            source="workspace_requirements",
            path=f"{workspace}/docs/product/requirements.md",
            bytes=128,
            chars=120,
            checked_paths=[f"{workspace}/docs/product/requirements.md"],
        ),
        issues=issue_tokens,
        startup_blockers=blockers,
    )


def _write_pm_requirements(workspace: Path, text: str = "Build traceable PM tasks.") -> None:
    requirements_path = workspace / "docs" / "product" / "requirements.md"
    requirements_path.parent.mkdir(parents=True, exist_ok=True)
    requirements_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# PM Service Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_run_once(client: AsyncClient) -> None:
    """PM run_once should execute single iteration."""
    mock_pm = MagicMock()
    mock_pm.run_once = AsyncMock(return_value={"ok": True, "result": "done"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ) as mock_preflight,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/run_once")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["result"] == "done"
        mock_preflight.assert_called_once()


@pytest.mark.asyncio
async def test_pm_start(client: AsyncClient) -> None:
    """PM start should begin loop mode."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ) as mock_preflight,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["state"] == "RUNNING"
        mock_preflight.assert_called_once()


@pytest.mark.asyncio
async def test_pm_start_with_resume(client: AsyncClient) -> None:
    """PM start with resume flag should pass it through."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start?resume=true")
        assert response.status_code == 200
        mock_pm.start_loop.assert_called_once_with(resume=True)


@pytest.mark.asyncio
async def test_pm_lifecycle_start_routes_accept_workspace_query_override(
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    """PM start controls should bind startup diagnostics to the requested desktop workspace."""
    workspace = tmp_path / "product"
    _write_pm_requirements(workspace)
    mock_pm = MagicMock()
    mock_pm.run_once = AsyncMock(return_value={"ok": True, "result": "done"})
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            side_effect=lambda _request, workspace_override=None: _pm_startup_diagnostics(
                workspace=str(workspace_override or "."),
            ),
        ) as mock_preflight,
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        run_once_response = await client.post("/v2/pm/run_once", params={"workspace": str(workspace)})
        start_response = await client.post("/v2/pm/start", params={"workspace": str(workspace)})
        resume_response = await client.post("/v2/pm/start", params={"workspace": str(workspace), "resume": "true"})

    assert run_once_response.status_code == 200
    assert start_response.status_code == 200
    assert resume_response.status_code == 200
    assert run_once_response.json()["workspace"] == str(workspace)
    assert start_response.json()["workspace"] == str(workspace)
    assert resume_response.json()["workspace"] == str(workspace)
    assert [call.kwargs["workspace_override"] for call in mock_preflight.call_args_list] == [
        str(workspace),
        str(workspace),
        str(workspace),
    ]
    mock_pm.start_loop.assert_any_call(resume=False)
    mock_pm.start_loop.assert_any_call(resume=True)


@pytest.mark.asyncio
async def test_pm_start_loop_route_removed(client: AsyncClient) -> None:
    """Deprecated PM start_loop route should not remain as a compatibility alias."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start_loop")
        assert response.status_code == 404
        mock_pm.start_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_pm_run_once_blocks_when_startup_diagnostics_cannot_start(client: AsyncClient) -> None:
    """PM run_once should fail closed when startup diagnostics report blockers."""
    mock_pm = MagicMock()
    mock_pm.run_once = AsyncMock(return_value={"ok": True, "result": "done"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(
                can_start=False,
                startup_blockers=["workspace_docs_missing"],
            ),
        ),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/run_once")

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "PM_START_BLOCKED"
    assert data["error"]["details"]["startup_blockers"] == ["workspace_docs_missing"]
    assert data["error"]["details"]["diagnostics"]["can_start"] is False
    mock_pm.run_once.assert_not_awaited()


@pytest.mark.asyncio
async def test_pm_loop_start_blocks_when_startup_diagnostics_cannot_start(
    client: AsyncClient,
) -> None:
    """PM loop start endpoint should fail closed when startup diagnostics block."""
    mock_pm = MagicMock()
    mock_pm.start_loop = AsyncMock(return_value={"ok": True, "state": "RUNNING"})

    with (
        patch(
            "polaris.delivery.http.dependencies.get_container",
            new_callable=AsyncMock,
        ) as mock_container,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(
                can_start=False,
                startup_blockers=["lancedb_unavailable", "llm_not_ready"],
            ),
        ),
    ):
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/start")

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "PM_START_BLOCKED"
    assert data["error"]["details"]["startup_blockers"] == ["lancedb_unavailable", "llm_not_ready"]
    mock_pm.start_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_pm_stop(client: AsyncClient) -> None:
    """PM stop should halt the service."""
    mock_pm = MagicMock()
    mock_pm.stop = AsyncMock(return_value={"ok": True, "state": "STOPPED"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/stop")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["state"] == "STOPPED"


@pytest.mark.asyncio
async def test_pm_stop_with_graceful_timeout(client: AsyncClient) -> None:
    """PM stop should accept graceful timeout parameters."""
    mock_pm = MagicMock()
    mock_pm.stop = AsyncMock(return_value={"ok": True, "state": "STOPPED"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/stop?graceful=true&graceful_timeout=10.0")
        assert response.status_code == 200
        mock_pm.stop.assert_called_once_with(graceful=True, graceful_timeout=10.0)


@pytest.mark.asyncio
async def test_pm_stop_returns_requested_workspace_evidence(client: AsyncClient, tmp_path: Path) -> None:
    """PM stop should echo the workspace used by desktop lifecycle controls."""
    workspace = tmp_path / "product"
    workspace.mkdir()
    mock_pm = MagicMock()
    mock_pm.stop = AsyncMock(return_value={"ok": True, "state": "IDLE"})

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.post("/v2/pm/stop", params={"workspace": str(workspace)})

    assert response.status_code == 200
    assert response.json()["workspace"] == str(workspace)


@pytest.mark.asyncio
async def test_pm_status(client: AsyncClient) -> None:
    """PM status should return current service status."""
    mock_pm = MagicMock()
    mock_pm.get_status.return_value = {"running": False, "state": "IDLE", "iterations": 0}

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.get("/v2/pm/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False
        assert data["state"] == "IDLE"
        assert data["workspace"] == "."


@pytest.mark.asyncio
async def test_pm_status_uses_requested_workspace(client: AsyncClient, tmp_path: Path) -> None:
    """PM status should expose the workspace used for desktop status evidence."""
    workspace = tmp_path / "product"
    workspace.mkdir()
    mock_pm = MagicMock()
    mock_pm.get_status.return_value = {"running": False, "state": "IDLE", "iterations": 0}

    with patch(
        "polaris.delivery.http.dependencies.get_container",
        new_callable=AsyncMock,
    ) as mock_container:
        mock_container.return_value.resolve_async = AsyncMock(return_value=mock_pm)
        response = await client.get("/v2/pm/status", params={"workspace": workspace.as_posix()})
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False
        assert data["state"] == "IDLE"
        assert data["workspace"] == workspace.as_posix()


@pytest.mark.asyncio
async def test_pm_diagnostics_ready(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should aggregate LanceDB, LLM, and workspace readiness."""
    workspace = tmp_path / "workspace"
    _write_pm_requirements(workspace)
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["can_start"] is True
    assert data["issues"] == []
    assert data["startup_blockers"] == []
    assert data["lancedb"]["state"] == "ready"
    assert data["llm"]["state"] == "ready"
    assert data["workspace"]["workspace"] == str(workspace)
    assert data["workspace"]["docs_present"] is True
    assert data["planning_input"]["ok"] is True
    assert data["planning_input"]["source"] == "workspace_requirements"
    assert data["planning_input"]["chars"] > 0


@pytest.mark.asyncio
async def test_pm_diagnostics_only_blocks_on_pm_role_readiness(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM startup should not be blocked by Director or QA readiness gaps."""
    workspace = tmp_path / "workspace"
    _write_pm_requirements(workspace)
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "BLOCKED",
                "blocked_roles": ["director", "qa"],
                "unsupported_roles": [],
                "required_ready_roles": ["pm", "director", "qa"],
                "roles": {
                    "pm": {
                        "ready": True,
                        "runtime_supported": True,
                        "provider_id": "openai_compat-1",
                        "model": "Qwen3-Max",
                    },
                    "director": {
                        "ready": False,
                        "runtime_supported": True,
                        "provider_id": "codex_cli",
                        "model": "gpt-5-codex",
                    },
                },
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["can_start"] is True
    assert data["issues"] == []
    assert data["startup_blockers"] == []
    assert data["llm"]["ok"] is True
    assert data["llm"]["state"] == "ready"
    assert data["llm"]["blocked_roles"] == []
    assert data["llm"]["details"]["blocked_roles"] == ["director", "qa"]
    assert data["planning_input"]["ok"] is True


@pytest.mark.asyncio
async def test_pm_diagnostics_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should inspect the active desktop workspace_path first."""
    stale_workspace = tmp_path / "repo"
    active_workspace = tmp_path / "product"
    _write_pm_requirements(active_workspace)
    mock_settings.workspace = stale_workspace
    mock_settings.workspace_path = active_workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["can_start"] is True
    assert data["startup_blockers"] == []
    assert data["workspace"]["workspace"] == str(active_workspace)
    assert data["workspace"]["docs_present"] is True
    assert (
        Path(data["planning_input"]["path"]).resolve()
        == (active_workspace / "docs" / "product" / "requirements.md").resolve()
    )


@pytest.mark.asyncio
async def test_pm_diagnostics_accepts_workspace_query_override(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should bind both workspace and LLM checks to the requested workspace."""
    active_workspace = tmp_path / "active"
    requested_workspace = tmp_path / "requested"
    _write_pm_requirements(active_workspace)
    _write_pm_requirements(requested_workspace)
    mock_settings.workspace = str(active_workspace)
    mock_settings.workspace_path = ""

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ) as mock_llm_status,
    ):
        response = await client.get(
            "/v2/pm/diagnostics",
            params={"workspace": str(requested_workspace)},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]["workspace"]).resolve() == requested_workspace.resolve()
    assert (
        Path(data["planning_input"]["path"]).resolve()
        == (requested_workspace / "docs" / "product" / "requirements.md").resolve()
    )
    called_settings = mock_llm_status.call_args.args[0]
    assert Path(str(called_settings.workspace)).resolve() == requested_workspace.resolve()
    assert Path(str(mock_settings.workspace)).resolve() == active_workspace.resolve()


@pytest.mark.asyncio
async def test_pm_diagnostics_blocks_start_when_workspace_docs_missing(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should align with desktop startup gates when docs/ is missing."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["can_start"] is False
    assert data["issues"] == ["workspace_docs_missing"]
    assert data["startup_blockers"] == ["workspace_docs_missing"]
    assert data["workspace"]["ok"] is True
    assert data["workspace"]["status"] == "ok"
    assert data["workspace"]["docs_present"] is False


@pytest.mark.asyncio
async def test_pm_diagnostics_accepts_workspace_persistent_docs(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should honor KernelOne workspace/docs storage layout."""
    from polaris.kernelone.storage import resolve_workspace_persistent_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persistent_requirements = Path(
        resolve_workspace_persistent_path(str(workspace), "workspace/docs/product/requirements.md")
    )
    persistent_requirements.parent.mkdir(parents=True, exist_ok=True)
    persistent_requirements.write_text("# Requirements\n\n- Build the product.\n", encoding="utf-8")
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["can_start"] is True
    assert data["issues"] == []
    assert data["startup_blockers"] == []
    assert data["workspace"]["docs_present"] is True
    assert Path(data["planning_input"]["path"]).resolve() == persistent_requirements.resolve()


@pytest.mark.asyncio
async def test_pm_diagnostics_accepts_polaris_plan_markdown_artifacts(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should accept Polaris-generated plan markdown under workspace/plans."""
    from polaris.kernelone.storage import resolve_workspace_persistent_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persistent_docs_root = Path(resolve_workspace_persistent_path(str(workspace), "workspace/docs"))
    persistent_docs_root.mkdir(parents=True, exist_ok=True)
    persistent_plan = Path(resolve_workspace_persistent_path(str(workspace), "workspace/plans/fashiongen-plan.md"))
    persistent_plan.parent.mkdir(parents=True, exist_ok=True)
    persistent_plan.write_text("# FashionGen Plan\n\n- Build the PM task backlog.\n", encoding="utf-8")
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["can_start"] is True
    assert data["issues"] == []
    assert data["startup_blockers"] == []
    assert data["planning_input"]["source"] == "workspace_plans_markdown"
    assert Path(data["planning_input"]["path"]).resolve() == persistent_plan.resolve()


@pytest.mark.asyncio
async def test_pm_diagnostics_prefers_requirement_markdown_over_newer_status_docs(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should identify the canonical requirements markdown, not fresh status logs."""
    from polaris.kernelone.storage import resolve_workspace_persistent_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persistent_docs_root = Path(resolve_workspace_persistent_path(str(workspace), "workspace/docs"))
    persistent_docs_root.mkdir(parents=True, exist_ok=True)
    canonical = persistent_docs_root / "AI_Studio_项目文档.md"
    status_doc = persistent_docs_root / "implementation-status.md"
    canonical.write_text("# 项目文档\n\n完整核心需求：资产库、生成工作台、批量队列。\n", encoding="utf-8")
    status_doc.write_text("# Implementation Status\n\n当前只剩运行时状态与视觉打磨。\n", encoding="utf-8")
    os.utime(canonical, (1000, 1000))
    os.utime(status_doc, (2000, 2000))
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["can_start"] is True
    assert data["planning_input"]["source"] == "workspace_docs_markdown"
    assert Path(data["planning_input"]["path"]).resolve() == canonical.resolve()


@pytest.mark.asyncio
async def test_pm_diagnostics_blocks_start_when_planning_input_missing(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should fail closed when docs exist but no planning input is present."""
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    mock_settings.workspace = workspace
    mock_settings.workspace_path = workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": True}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "READY",
                "blocked_roles": [],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["can_start"] is False
    assert data["issues"] == ["planning_input_missing"]
    assert data["startup_blockers"] == ["planning_input_missing"]
    assert data["planning_input"]["ok"] is False
    assert data["planning_input"]["status"] == "missing"
    assert data["planning_input"]["checked_paths"]


@pytest.mark.asyncio
async def test_pm_diagnostics_reports_blockers(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """PM diagnostics should return deterministic issue tokens for blocked startup."""
    missing_workspace = tmp_path / "missing"
    mock_settings.workspace = missing_workspace
    mock_settings.workspace_path = missing_workspace

    with (
        patch("polaris.delivery.http.v2.pm.get_lancedb_status", return_value={"ok": False, "error": "missing"}),
        patch(
            "polaris.delivery.http.v2.pm.build_llm_status",
            return_value={
                "state": "BLOCKED",
                "blocked_roles": ["pm"],
                "unsupported_roles": [],
                "required_ready_roles": ["pm"],
            },
        ),
    ):
        response = await client.get("/v2/pm/diagnostics")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["can_start"] is False
    assert data["issues"] == ["lancedb_unavailable", "llm_not_ready", "workspace_unavailable"]
    assert data["startup_blockers"] == ["lancedb_unavailable", "llm_not_ready", "workspace_unavailable"]
    assert data["lancedb"]["error"] == "missing"
    assert data["llm"]["blocked_roles"] == ["pm"]
    assert data["workspace"]["status"] == "missing"


# ---------------------------------------------------------------------------
# PM Orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_run_orchestration(client: AsyncClient) -> None:
    """PM run orchestration should create a run via OrchestrationCommandService."""
    mock_result = MagicMock()
    mock_result.run_id = "run-123"
    mock_result.status = "running"
    mock_result.message = "PM run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ) as mock_preflight,
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": ".",
                "directive": "test directive",
                "stage": "pm",
                "run_director": False,
                "director_iterations": 2,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert data["status"] == "running"
        assert data["stage"] == "pm"
        assert mock_preflight.call_args.kwargs["workspace_override"] == "."
        assert mock_preflight.call_args.kwargs["allow_inline_directive"] is True


@pytest.mark.asyncio
async def test_pm_run_orchestration_defaults_to_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM run should resolve omitted workspace through active desktop settings."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_result = MagicMock()
    mock_result.run_id = "run-active"
    mock_result.status = "running"
    mock_result.message = "PM run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ) as mock_preflight,
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "directive": "test directive",
                "stage": "pm",
            },
        )

    assert response.status_code == 200
    _, kwargs = mock_service.execute_pm_run.await_args
    assert kwargs["workspace"] == "C:/Temp/Product"
    assert response.json()["workspace"] == "C:/Temp/Product"
    assert mock_preflight.call_args.kwargs["workspace_override"] == "C:/Temp/Product"
    assert mock_preflight.call_args.kwargs["allow_inline_directive"] is True


@pytest.mark.asyncio
async def test_pm_run_orchestration_preserves_explicit_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM run should not override explicit non-dot API workspace values."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Product"
    mock_result = MagicMock()
    mock_result.run_id = "run-explicit"
    mock_result.status = "running"
    mock_result.message = "PM run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(workspace="D:/Explicit/Product"),
        ) as mock_preflight,
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": "D:/Explicit/Product",
                "directive": "test directive",
                "stage": "pm",
            },
        )

    assert response.status_code == 200
    _, kwargs = mock_service.execute_pm_run.await_args
    assert kwargs["workspace"] == "D:/Explicit/Product"
    assert response.json()["workspace"] == "D:/Explicit/Product"
    assert mock_preflight.call_args.kwargs["workspace_override"] == "D:/Explicit/Product"
    assert mock_preflight.call_args.kwargs["allow_inline_directive"] is True


@pytest.mark.asyncio
async def test_pm_run_orchestration_with_director(client: AsyncClient) -> None:
    """PM run orchestration with run_director enabled."""
    mock_result = MagicMock()
    mock_result.run_id = "run-456"
    mock_result.status = "running"
    mock_result.message = "PM architect run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ),
        patch("polaris.delivery.http.v2.pm.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": ".",
                "directive": "build a login page",
                "stage": "architect",
                "run_director": True,
                "director_iterations": 3,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-456"
        assert data["stage"] == "architect"
        mock_roles_ready.assert_called_once()
        assert mock_roles_ready.call_args.kwargs["default_roles"] == ["director"]
        assert mock_roles_ready.call_args.kwargs["force_first"] == "director"


@pytest.mark.asyncio
async def test_pm_run_orchestration_with_director_uses_explicit_workspace_for_handoff_gate(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM auto-dispatch should check Director readiness against the requested workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Stale"
    mock_result = MagicMock()
    mock_result.run_id = "run-director-explicit"
    mock_result.status = "running"
    mock_result.message = "PM run started"

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm.get_orchestration_service",
            new_callable=AsyncMock,
        ),
        patch(
            "polaris.cells.roles.adapters.public.service.register_all_adapters",
        ),
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(workspace="D:/Explicit/Product"),
        ),
        patch("polaris.delivery.http.v2.pm.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_service = MagicMock()
        mock_service.execute_pm_run = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": "D:/Explicit/Product",
                "directive": "build a login page",
                "stage": "architect",
                "run_director": True,
            },
        )

    assert response.status_code == 200
    gate_state = mock_roles_ready.call_args.args[0]
    assert Path(str(gate_state.settings.workspace)).as_posix() == "D:/Explicit/Product"
    assert gate_state.settings.workspace_path == "D:/Explicit/Product"
    _, kwargs = mock_service.execute_pm_run.await_args
    assert kwargs["workspace"] == "D:/Explicit/Product"


@pytest.mark.asyncio
async def test_pm_run_orchestration_with_director_blocks_when_director_llm_not_ready(
    client: AsyncClient,
) -> None:
    """PM auto-dispatch should fail closed before run creation when Director LLM is blocked."""
    from polaris.delivery.http.routers._shared import StructuredHTTPException

    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(),
        ) as mock_preflight,
        patch("polaris.delivery.http.v2.pm.ensure_required_roles_ready") as mock_roles_ready,
    ):
        mock_roles_ready.side_effect = StructuredHTTPException(
            status_code=409,
            code="RUNTIME_ROLES_NOT_READY",
            message="One or more required runtime roles are not ready",
            details={
                "required_roles": ["director"],
                "missing_roles": ["director"],
            },
        )

        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": ".",
                "directive": "build a login page",
                "stage": "architect",
                "run_director": True,
                "director_iterations": 3,
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "RUNTIME_ROLES_NOT_READY"
    assert data["error"]["details"]["missing_roles"] == ["director"]
    assert mock_preflight.call_args.kwargs["workspace_override"] == "."
    mock_service_cls.assert_not_called()


@pytest.mark.asyncio
async def test_pm_run_orchestration_blocks_when_startup_diagnostics_cannot_start(
    client: AsyncClient,
) -> None:
    """PM run orchestration should fail closed when readiness diagnostics block."""
    with (
        patch(
            "polaris.cells.orchestration.pm_dispatch.public.service.OrchestrationCommandService",
        ) as mock_service_cls,
        patch(
            "polaris.delivery.http.v2.pm._build_pm_diagnostics_for_request",
            return_value=_pm_startup_diagnostics(
                can_start=False,
                startup_blockers=["workspace_docs_missing", "llm_not_ready"],
            ),
        ) as mock_preflight,
    ):
        response = await client.post(
            "/v2/pm/run",
            json={
                "workspace": ".",
                "directive": "test directive",
                "stage": "pm",
            },
        )

    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "PM_START_BLOCKED"
    assert data["error"]["details"]["startup_blockers"] == ["workspace_docs_missing", "llm_not_ready"]
    assert data["error"]["details"]["diagnostics"]["can_start"] is False
    assert mock_preflight.call_args.kwargs["workspace_override"] == "."
    assert mock_preflight.call_args.kwargs["allow_inline_directive"] is True
    mock_service_cls.assert_not_called()


@pytest.mark.asyncio
async def test_pm_get_orchestration_found(client: AsyncClient) -> None:
    """PM get orchestration should return run details."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-123"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.current_phase.value = "done"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/run-123")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_pm_get_orchestration_honors_requested_workspace(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM run evidence should not leak runs from a different desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-123"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "C:/Temp/Requested"
    mock_snapshot.current_phase.value = "done"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/run-123?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 200
    assert response.json()["workspace"] == "C:/Temp/Requested"


@pytest.mark.asyncio
async def test_pm_get_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM run detail should return 404 when a run belongs to another workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-123"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "D:/Other/Product"
    mock_snapshot.current_phase.value = "done"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/run-123?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 404
    assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pm_cancel_orchestration_run(client: AsyncClient) -> None:
    """PM cancel orchestration should call the runtime cancel path."""
    mock_current_snapshot = MagicMock()
    mock_current_snapshot.run_id = "run-123"
    mock_current_snapshot.status.value = "running"
    mock_current_snapshot.workspace = "."
    mock_current_snapshot.current_phase.value = "pm"
    mock_current_snapshot.tasks = {}

    mock_cancelled_snapshot = MagicMock()
    mock_cancelled_snapshot.run_id = "run-123"
    mock_cancelled_snapshot.status.value = "cancelled"
    mock_cancelled_snapshot.workspace = "."
    mock_cancelled_snapshot.current_phase.value = "pm"
    mock_cancelled_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_current_snapshot)
        mock_service.cancel_run = AsyncMock(return_value=mock_cancelled_snapshot)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/pm/runs/run-123/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert data["status"] == "cancelled"
        assert data["stage"] == "pm"
        mock_service.query_run.assert_awaited_once_with("run-123")
        mock_service.cancel_run.assert_awaited_once_with("run-123")


@pytest.mark.asyncio
async def test_pm_cancel_orchestration_hides_workspace_mismatch(
    client: AsyncClient,
    mock_settings: Settings,
) -> None:
    """PM run cancel should not cancel a run from another desktop workspace."""
    mock_settings.workspace = "C:/Repo/Polaris"
    mock_settings.workspace_path = "C:/Temp/Requested"
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-123"
    mock_snapshot.status.value = "running"
    mock_snapshot.workspace = "D:/Other/Product"
    mock_snapshot.current_phase.value = "pm"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/pm/runs/run-123/cancel?workspace=C%3A%2FTemp%2FRequested")

    assert response.status_code == 404
    mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_pm_cancel_orchestration_terminal_run_is_idempotent(client: AsyncClient) -> None:
    """PM cancel orchestration should return terminal snapshots unchanged."""
    mock_snapshot = MagicMock()
    mock_snapshot.run_id = "run-123"
    mock_snapshot.status.value = "completed"
    mock_snapshot.workspace = "."
    mock_snapshot.current_phase.value = "done"
    mock_snapshot.tasks = {}

    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=mock_snapshot)
        mock_service.cancel_run = AsyncMock()
        mock_orch.return_value = mock_service

        response = await client.post("/v2/pm/runs/run-123/cancel")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-123"
        assert data["status"] == "completed"
        assert data["stage"] == "done"
        mock_service.query_run.assert_awaited_once_with("run-123")
        mock_service.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_pm_cancel_orchestration_not_found(client: AsyncClient) -> None:
    """PM cancel orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.post("/v2/pm/runs/nonexistent/cancel")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pm_get_orchestration_not_found(client: AsyncClient) -> None:
    """PM get orchestration should 404 for unknown run_id."""
    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(return_value=None)
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/nonexistent")
        assert response.status_code == 404
        assert "Run not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_pm_get_orchestration_server_error(client: AsyncClient) -> None:
    """PM get orchestration should 500 on unexpected errors."""
    with patch(
        "polaris.delivery.http.v2.pm.get_orchestration_service",
        new_callable=AsyncMock,
    ) as mock_orch:
        mock_service = MagicMock()
        mock_service.query_run = AsyncMock(side_effect=RuntimeError("db failure"))
        mock_orch.return_value = mock_service

        response = await client.get("/v2/pm/runs/run-123")
        assert response.status_code == 500
        assert "internal error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# LLM Events / Cache / Token Budget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pm_llm_events(client: AsyncClient) -> None:
    """Get PM LLM events should return events."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_start"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-1",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/pm/llm-events?run_id=run-1")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run-1"
        assert len(data["events"]) == 1
        assert data["count"] == 1
        assert data["stats"]["call_start"] == 1


@pytest.mark.asyncio
async def test_pm_llm_events_without_run_id_returns_latest_events(client: AsyncClient) -> None:
    """PM diagnostics should read latest PM LLM events without a run filter."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_error"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_error",
        "run_id": "run-latest",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/pm/llm-events?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["run_id"] is None
    assert data["count"] == 1
    assert data["stats"]["call_error"] == 1
    mock_emitter.get_events.assert_called_once_with(
        run_id=None,
        task_id=None,
        role="pm",
        limit=5,
    )


@pytest.mark.asyncio
async def test_pm_llm_events_filters_active_workspace_by_default(client: AsyncClient) -> None:
    """PM LLM events should filter to active workspace even without query workspace."""
    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": "."}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-active",
        "role": "pm",
    }
    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": "/tmp/other-workspace"}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/pm/llm-events?limit=5")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] == "."
    assert data["count"] == 1
    assert data["events"][0]["run_id"] == "run-active"


@pytest.mark.asyncio
async def test_pm_llm_events_filters_requested_workspace(client: AsyncClient, tmp_path: Path) -> None:
    """PM LLM events should filter shared role-kernel history by requested workspace."""
    requested_workspace = tmp_path / "requested"
    other_workspace = tmp_path / "other"
    requested_workspace.mkdir()
    other_workspace.mkdir()

    matching_event = MagicMock()
    matching_event.event_type = "llm_call_start"
    matching_event.metadata = {"workspace": str(requested_workspace)}
    matching_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-requested",
        "role": "pm",
    }

    other_event = MagicMock()
    other_event.event_type = "llm_call_start"
    other_event.metadata = {"workspace": str(other_workspace)}
    other_event.to_dict.return_value = {
        "event_type": "llm_call_start",
        "run_id": "run-other",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [matching_event, other_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get(
            "/v2/pm/llm-events",
            params={"workspace": str(requested_workspace), "limit": "5"},
        )

    assert response.status_code == 200
    data = response.json()
    assert Path(data["workspace"]).resolve() == requested_workspace.resolve()
    assert data["count"] == 1
    assert data["events"][0]["run_id"] == "run-requested"
    assert data["stats"]["call_start"] == 1


@pytest.mark.asyncio
async def test_pm_llm_events_with_task_filter(client: AsyncClient) -> None:
    """Get PM LLM events should filter by task_id."""
    mock_event = MagicMock()
    mock_event.event_type = "llm_call_end"
    mock_event.metadata = {"workspace": "."}
    mock_event.to_dict.return_value = {
        "event_type": "llm_call_end",
        "run_id": "run-1",
        "task_id": "task-1",
        "role": "pm",
    }

    with patch(
        "polaris.delivery.http.v2.pm.get_global_emitter",
    ) as mock_get_emitter:
        mock_emitter = MagicMock()
        mock_emitter.get_events.return_value = [mock_event]
        mock_get_emitter.return_value = mock_emitter

        response = await client.get("/v2/pm/llm-events?run_id=run-1&task_id=task-1&limit=50")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-1"
        mock_emitter.get_events.assert_called_once_with(
            run_id="run-1",
            task_id="task-1",
            role="pm",
            limit=50,
        )


@pytest.mark.asyncio
async def test_pm_cache_stats(client: AsyncClient) -> None:
    """Get PM cache stats should return cache statistics."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_cache.get_stats.return_value = {"hits": 100, "misses": 20, "size": 120}
        mock_get_cache.return_value = mock_cache

        response = await client.get("/v2/pm/cache-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["hits"] == 100
        assert data["misses"] == 20


@pytest.mark.asyncio
async def test_pm_cache_clear(client: AsyncClient) -> None:
    """Clear PM cache should return success."""
    with patch(
        "polaris.cells.roles.kernel.public.service.get_global_llm_cache",
    ) as mock_get_cache:
        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        response = await client.post("/v2/pm/cache-clear")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        mock_cache.clear.assert_called_once()


@pytest.mark.asyncio
async def test_pm_token_budget_stats(client: AsyncClient) -> None:
    """Get PM token budget stats should return budget information."""
    with patch(
        "polaris.delivery.http.v2.pm.get_global_token_budget",
    ) as mock_get_budget:
        mock_budget = MagicMock()
        mock_budget.get_stats.return_value = {
            "total_budget": 100000,
            "used_tokens": 5000,
            "remaining": 95000,
        }
        mock_get_budget.return_value = mock_budget

        response = await client.get("/v2/pm/token-budget-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_budget"] == 100000
        assert data["used_tokens"] == 5000
