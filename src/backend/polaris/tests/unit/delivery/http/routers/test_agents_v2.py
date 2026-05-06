"""Tests for Polaris v2 agents router.

Covers POST /v2/agents/apply and POST /v2/agents/feedback.
External services are mocked to avoid filesystem dependencies.
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
# POST /v2/agents/apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_agents_apply_success(client: AsyncClient, tmp_path) -> None:
    """POST /v2/agents/apply should copy draft to AGENTS.md."""
    draft_path = tmp_path / "draft_AGENTS.md"
    draft_path.write_text("# Draft", encoding="utf-8")

    mock_state = MagicMock()
    mock_state.settings.workspace = str(tmp_path)

    with (
        patch(
            "polaris.delivery.http.routers.agents.get_state",
            return_value=mock_state,
        ),
        patch(
            "polaris.delivery.http.routers.agents.resolve_safe_path",
            return_value=str(draft_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.path.isfile",
            side_effect=lambda p: str(p) == str(draft_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.makedirs",
        ),
        patch(
            "polaris.delivery.http.routers.agents.shutil.copyfile",
        ) as mock_copy,
    ):
        response = await client.post(
            "/v2/agents/apply",
            json={"draft_path": "draft_AGENTS.md"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "AGENTS.md" in data["target_path"]
        mock_copy.assert_called_once()


@pytest.mark.asyncio
async def test_v2_agents_apply_draft_not_found(client: AsyncClient) -> None:
    """POST /v2/agents/apply should 404 when draft not found."""
    mock_state = MagicMock()
    mock_state.settings.workspace = "."

    with (
        patch(
            "polaris.delivery.http.routers.agents.get_state",
            return_value=mock_state,
        ),
        patch(
            "polaris.delivery.http.routers.agents.resolve_safe_path",
            return_value="/nonexistent/draft.md",
        ),
        patch(
            "polaris.delivery.http.routers.agents.build_cache_root",
            return_value="/tmp",
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.path.isfile",
            return_value=False,
        ),
    ):
        response = await client.post(
            "/v2/agents/apply",
            json={"draft_path": "missing.md"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "DRAFT_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_agents_apply_already_exists(client: AsyncClient, tmp_path) -> None:
    """POST /v2/agents/apply should 409 when AGENTS.md already exists."""
    draft_path = tmp_path / "draft_AGENTS.md"
    draft_path.write_text("# Draft", encoding="utf-8")
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Existing", encoding="utf-8")

    mock_state = MagicMock()
    mock_state.settings.workspace = str(tmp_path)

    with (
        patch(
            "polaris.delivery.http.routers.agents.get_state",
            return_value=mock_state,
        ),
        patch(
            "polaris.delivery.http.routers.agents.resolve_safe_path",
            return_value=str(draft_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.path.isfile",
            side_effect=lambda p: str(p) in (str(agents_md), str(draft_path)),
        ),
    ):
        response = await client.post(
            "/v2/agents/apply",
            json={"draft_path": "draft_AGENTS.md"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "AGENTS_MD_EXISTS"


# ---------------------------------------------------------------------------
# POST /v2/agents/feedback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_agents_feedback_success(client: AsyncClient, tmp_path) -> None:
    """POST /v2/agents/feedback should save feedback text."""
    feedback_path = tmp_path / "feedback.md"

    mock_state = MagicMock()
    mock_state.settings.workspace = str(tmp_path)

    with (
        patch(
            "polaris.delivery.http.routers.agents.get_state",
            return_value=mock_state,
        ),
        patch(
            "polaris.delivery.http.routers.agents.resolve_artifact_path",
            return_value=str(feedback_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.makedirs",
        ),
        patch(
            "polaris.delivery.http.routers.agents.format_mtime",
            return_value="2024-01-01T00:00:00",
        ),
        patch(
            "polaris.delivery.http.routers.agents.open",
        ) as mock_open,
    ):
        response = await client.post(
            "/v2/agents/feedback",
            json={"text": "Great work!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "path" in data
        mock_open.assert_called_once()


@pytest.mark.asyncio
async def test_v2_agents_feedback_clear(client: AsyncClient, tmp_path) -> None:
    """POST /v2/agents/feedback with empty text should clear feedback."""
    feedback_path = tmp_path / "feedback.md"
    feedback_path.write_text("old feedback", encoding="utf-8")

    mock_state = MagicMock()
    mock_state.settings.workspace = str(tmp_path)

    with (
        patch(
            "polaris.delivery.http.routers.agents.get_state",
            return_value=mock_state,
        ),
        patch(
            "polaris.delivery.http.routers.agents.resolve_artifact_path",
            return_value=str(feedback_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.path.isfile",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.agents.os.remove",
        ) as mock_remove,
    ):
        response = await client.post(
            "/v2/agents/feedback",
            json={"text": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["cleared"] is True
        mock_remove.assert_called_once()
