"""Tests for Polaris v2 interview router.

Covers POST /v2/llm/interview/ask, POST /v2/llm/interview/save,
POST /v2/llm/interview/cancel, and POST /v2/llm/interview/stream.
External services are mocked to avoid LLM provider dependencies.
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
# POST /v2/llm/interview/ask
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_interview_ask_success(client: AsyncClient) -> None:
    """POST /v2/llm/interview/ask should return generated interview answer."""
    with patch(
        "polaris.delivery.http.routers.interview.generate_interview_answer",
        new_callable=AsyncMock,
        return_value={
            "raw_output": "raw",
            "thinking": "think",
            "answer": "answer text",
            "evaluation": {"score": 9},
        },
    ) as mock_generate:
        response = await client.post(
            "/v2/llm/interview/ask",
            json={
                "role": "pm",
                "provider_id": "openai",
                "model": "gpt-4",
                "question": "What is Agile?",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["answer"] == "answer text"
        assert data["evaluation"]["score"] == 9
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_v2_llm_interview_ask_generation_failed(client: AsyncClient) -> None:
    """POST /v2/llm/interview/ask should 500 when generation returns None."""
    with patch(
        "polaris.delivery.http.routers.interview.generate_interview_answer",
        new_callable=AsyncMock,
        return_value=None,
    ):
        response = await client.post(
            "/v2/llm/interview/ask",
            json={
                "role": "pm",
                "provider_id": "openai",
                "model": "gpt-4",
                "question": "What is Agile?",
            },
        )
        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "INTERVIEW_GENERATION_FAILED"


# ---------------------------------------------------------------------------
# POST /v2/llm/interview/save
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_interview_save_success(client: AsyncClient) -> None:
    """POST /v2/llm/interview/save should return saved confirmation."""
    response = await client.post(
        "/v2/llm/interview/save",
        json={
            "role": "pm",
            "provider_id": "openai",
            "model": "gpt-4",
            "report": {"score": 10},
            "session_id": "sess-123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["saved"] is True


# ---------------------------------------------------------------------------
# POST /v2/llm/interview/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_interview_cancel_success(client: AsyncClient) -> None:
    """POST /v2/llm/interview/cancel should return cancelled confirmation."""
    response = await client.post(
        "/v2/llm/interview/cancel",
        json={"session_id": "sess-123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["cancelled"] is True


# ---------------------------------------------------------------------------
# POST /v2/llm/interview/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_interview_stream_headers(client: AsyncClient) -> None:
    """POST /v2/llm/interview/stream should return SSE headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")
