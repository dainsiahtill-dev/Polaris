"""Tests for Polaris v2 LLM router.

Covers GET /v2/llm/config, POST /v2/llm/config/migrate,
GET /v2/llm/status, GET /v2/llm/runtime-status,
and GET /v2/llm/runtime-status/{role_id}.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
        patch.dict(
            "os.environ",
            {
                "KERNELONE_METRICS_ENABLED": "false",
                "KERNELONE_RATE_LIMIT_ENABLED": "false",
            },
        ),
    ):
        mock_container.return_value = MagicMock()
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as ac:
            yield ac


# ---------------------------------------------------------------------------
# GET /v2/llm/config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_llm_config_success(client: AsyncClient) -> None:
    """GET /v2/llm/config should return redacted LLM config."""
    mock_config = {"provider": "openai", "model": "gpt-4", "api_key": "sk-123"}
    redacted = {"provider": "openai", "model": "gpt-4", "api_key": "***"}

    with (
        patch(
            "polaris.delivery.http.routers.llm.llm_config.load_llm_config",
            return_value=mock_config,
        ) as mock_load,
        patch(
            "polaris.delivery.http.routers.llm.llm_config.redact_llm_config",
            return_value=redacted,
        ) as mock_redact,
    ):
        response = await client.get("/v2/llm/config")
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4"
        assert data["api_key"] == "***"
        mock_load.assert_called_once()
        mock_redact.assert_called_once_with(mock_config)


# ---------------------------------------------------------------------------
# POST /v2/llm/config/migrate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrate_llm_config_success(client: AsyncClient) -> None:
    """POST /v2/llm/config/migrate should return migrated config."""
    legacy = {"provider": "openai", "model": "gpt-3.5-turbo"}
    migrated = {"provider": "openai", "model": "gpt-4o", "migrated": True}

    with patch(
        "polaris.delivery.http.routers.llm._provider_manager",
    ) as mock_mgr:
        mock_mgr.migrate_legacy_config.return_value = migrated

        response = await client.post("/v2/llm/config/migrate", json=legacy)
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4o"
        assert data["migrated"] is True
        mock_mgr.migrate_legacy_config.assert_called_once_with(legacy)


@pytest.mark.asyncio
async def test_migrate_llm_config_error(client: AsyncClient) -> None:
    """POST /v2/llm/config/migrate should 500 on migration failure."""
    with patch(
        "polaris.delivery.http.routers.llm._provider_manager",
    ) as mock_mgr:
        mock_mgr.migrate_legacy_config.side_effect = RuntimeError("bad config")

        response = await client.post("/v2/llm/config/migrate", json={"old": "value"})
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert "internal error" in data["error"]["message"]


# ---------------------------------------------------------------------------
# GET /v2/llm/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_llm_status_success(client: AsyncClient) -> None:
    """GET /v2/llm/status should return LLM status."""
    status = {
        "ready": True,
        "configured": True,
        "provider": "openai",
        "model": "gpt-4",
    }

    with patch(
        "polaris.delivery.http.routers.llm.build_llm_status",
        return_value=status,
    ) as mock_build:
        response = await client.get("/v2/llm/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True
        assert data["configured"] is True
        assert data["provider"] == "openai"
        mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# GET /v2/llm/runtime-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_runtime_status_success(client: AsyncClient, tmp_path) -> None:
    """GET /v2/llm/runtime-status should return runtime status for all roles."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    # Create a lock file for pm
    lock_file = runtime_dir / "pm.lock"
    lock_file.write_text('{"startedAt": "2024-01-01T00:00:00Z", "pid": 1234}')

    # Create a status file for pm
    status_file = runtime_dir / "pm_status.json"
    status_file.write_text('{"lastRun": "2024-01-01T00:00:00Z", "status": "ok"}')

    with (
        patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.llm.resolve_artifact_path",
            return_value=str(runtime_dir),
        ),
        patch(
            "polaris.delivery.http.routers.llm.load_role_config",
            return_value=MagicMock(provider_id="openai", model="gpt-4", profile="default"),
        ),
    ):
        response = await client.get("/v2/llm/runtime-status")
        assert response.status_code == 200
        data = response.json()
        assert "roles" in data
        assert "timestamp" in data
        assert data["roles"]["pm"]["running"] is True
        assert data["roles"]["pm"]["config"]["provider_id"] == "openai"
        assert data["roles"]["pm"]["config"]["model"] == "gpt-4"


@pytest.mark.asyncio
async def test_get_runtime_status_no_files(client: AsyncClient, tmp_path) -> None:
    """GET /v2/llm/runtime-status should handle missing lock/status files gracefully."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    with (
        patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.llm.resolve_artifact_path",
            return_value=str(runtime_dir),
        ),
        patch(
            "polaris.delivery.http.routers.llm.load_role_config",
            side_effect=ValueError("no config"),
        ),
    ):
        response = await client.get("/v2/llm/runtime-status")
        assert response.status_code == 200
        data = response.json()
        assert "roles" in data
        for role_id in ("pm", "director", "qa", "architect"):
            assert data["roles"][role_id]["running"] is False
            assert data["roles"][role_id]["config"]["provider_id"] is None


# ---------------------------------------------------------------------------
# GET /v2/llm/runtime-status/{role_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_role_runtime_status_success(client: AsyncClient, tmp_path) -> None:
    """GET /v2/llm/runtime-status/{role_id} should return role-specific status."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    lock_file = runtime_dir / "director.lock"
    lock_file.write_text('{"startedAt": "2024-06-01T12:00:00Z", "pid": 5678}')

    status_file = runtime_dir / "director_status.json"
    status_file.write_text('{"lastRun": "2024-06-01T12:00:00Z", "status": "completed"}')

    with (
        patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.llm.resolve_artifact_path",
            return_value=str(runtime_dir),
        ),
        patch(
            "polaris.delivery.http.routers.llm.load_role_config",
            return_value=MagicMock(provider_id="anthropic", model="claude-3", profile="default"),
        ),
    ):
        response = await client.get("/v2/llm/runtime-status/director")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is True
        assert data["roleId"] == "director"
        assert data["config"]["provider_id"] == "anthropic"
        assert data["config"]["model"] == "claude-3"
        assert data["startedAt"] == "2024-06-01T12:00:00Z"
        assert data["pid"] == 5678


@pytest.mark.asyncio
async def test_get_role_runtime_status_invalid_role(client: AsyncClient) -> None:
    """GET /v2/llm/runtime-status/{role_id} should 400 for invalid role."""
    response = await client.get("/v2/llm/runtime-status/invalid_role")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_ROLE_ID"
    assert "invalid role_id" in data["error"]["message"]


@pytest.mark.asyncio
async def test_get_role_runtime_status_docs_alias(client: AsyncClient, tmp_path) -> None:
    """GET /v2/llm/runtime-status/docs should normalize to architect."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    lock_file = runtime_dir / "architect.lock"
    lock_file.write_text('{"startedAt": "2024-03-01T08:00:00Z", "pid": 9999}')

    with (
        patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.llm.resolve_artifact_path",
            return_value=str(runtime_dir),
        ),
        patch(
            "polaris.delivery.http.routers.llm.load_role_config",
            return_value=MagicMock(provider_id="openai", model="gpt-4", profile="default"),
        ),
    ):
        response = await client.get("/v2/llm/runtime-status/docs")
        assert response.status_code == 200
        data = response.json()
        assert data["roleId"] == "architect"
        assert data["running"] is True


@pytest.mark.asyncio
async def test_get_role_runtime_status_no_lock(client: AsyncClient, tmp_path) -> None:
    """GET /v2/llm/runtime-status/{role_id} should return not running when no lock file."""
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    with (
        patch(
            "polaris.delivery.http.routers.llm.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.llm.resolve_artifact_path",
            return_value=str(runtime_dir),
        ),
        patch(
            "polaris.delivery.http.routers.llm.load_role_config",
            return_value=MagicMock(provider_id="openai", model="gpt-4", profile="default"),
        ),
    ):
        response = await client.get("/v2/llm/runtime-status/qa")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False
        assert data["roleId"] == "qa"
        assert data["config"]["provider_id"] == "openai"
