"""Tests for Polaris tests v2 endpoints.

Covers POST /v2/llm/test, GET /v2/llm/test/{test_run_id},
and GET /v2/llm/test/{test_run_id}/transcript.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

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
# POST /v2/llm/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_test_success(client: AsyncClient) -> None:
    """Starting a test run should return the test report."""
    mock_context = MagicMock()
    mock_context.effective_provider_id = "openai"
    mock_context.model = "gpt-4"
    mock_context.role = "connectivity"
    mock_context.suites = ["connectivity"]
    mock_context.use_direct_config = False
    mock_context.provider_cfg = None

    mock_report = {
        "test_run_id": "run-123",
        "target": {"role": "connectivity", "provider_id": "openai", "model": "gpt-4"},
        "suites": {},
        "final": {"ready": True, "grade": "PASS", "next_action": "proceed"},
    }

    with (
        patch(
            "polaris.delivery.http.routers.tests.resolve_llm_test_execution_context",
            return_value=mock_context,
        ) as mock_resolve,
        patch(
            "polaris.delivery.http.routers.tests.run_llm_tests",
            new_callable=AsyncMock,
            return_value=mock_report,
        ) as mock_run,
        patch(
            "polaris.delivery.http.routers.tests.build_cache_root",
            return_value="/tmp/cache",
        ),
    ):
        response = await client.post(
            "/v2/llm/test",
            json={"provider_id": "openai", "model": "gpt-4", "suites": ["connectivity"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["test_run_id"] == "run-123"
        assert data["final"]["grade"] == "PASS"
        mock_resolve.assert_called_once()
        mock_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_v2_llm_test_provider_config_error(client: AsyncClient) -> None:
    """Provider config error should return structured 404."""
    from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError

    with (
        patch(
            "polaris.delivery.http.routers.tests.resolve_llm_test_execution_context",
            side_effect=ProviderNotFoundError("provider missing"),
        ),
        patch(
            "polaris.delivery.http.routers.tests.build_cache_root",
            return_value="/tmp/cache",
        ),
    ):
        response = await client.post(
            "/v2/llm/test",
            json={"provider_id": "unknown", "model": "gpt-4"},
        )
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "PROVIDER_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /v2/llm/test/{test_run_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_test_report_success(client: AsyncClient, tmp_path: Path) -> None:
    """Getting an existing test report should return normalized payload."""
    report_file = tmp_path / "report.json"
    report_file.write_text(
        '{"run_id": "run-123", "provider_id": "openai", "model": "gpt-4", '
        '"role": "connectivity", "summary": {"ready": true}, "suites": {}}',
        encoding="utf-8",
    )

    with (
        patch(
            "polaris.delivery.http.routers.tests.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.tests.resolve_artifact_path",
            return_value=str(report_file),
        ),
        patch(
            "polaris.delivery.http.routers.tests.os.path.isfile",
            return_value=True,
        ),
    ):
        response = await client.get("/v2/llm/test/run-123")
        assert response.status_code == 200
        data = response.json()
        assert data["test_run_id"] == "run-123"
        assert data["target"]["provider_id"] == "openai"
        assert data["final"]["ready"] is True


@pytest.mark.asyncio
async def test_v2_llm_test_report_not_found(client: AsyncClient) -> None:
    """Getting a missing test report should return 404."""
    with (
        patch(
            "polaris.delivery.http.routers.tests.build_cache_root",
            return_value="/tmp/cache",
        ),
        patch(
            "polaris.delivery.http.routers.tests.resolve_artifact_path",
            return_value="/tmp/cache/runtime/llm_tests/run-999/LLM_TEST_REPORT.json",
        ),
        patch(
            "polaris.delivery.http.routers.tests.os.path.isfile",
            return_value=False,
        ),
    ):
        response = await client.get("/v2/llm/test/run-999")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "REPORT_NOT_FOUND"


@pytest.mark.asyncio
async def test_v2_llm_test_report_invalid_run_id(client: AsyncClient) -> None:
    """Invalid test run id should return 400."""
    response = await client.get("/v2/llm/test/run-123$bad")
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_TEST_RUN_ID"


# ---------------------------------------------------------------------------
# GET /v2/llm/test/{test_run_id}/transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_test_transcript_success(client: AsyncClient, tmp_path: Path) -> None:
    """Getting an existing transcript should return its content."""
    transcript_file = tmp_path / "transcript.md"
    transcript_file.write_text("# Test Transcript\n\nAll tests passed.", encoding="utf-8")

    with (
        patch(
            "polaris.delivery.http.routers.tests.build_cache_root",
            return_value=str(tmp_path),
        ),
        patch(
            "polaris.delivery.http.routers.tests.resolve_artifact_path",
            return_value=str(transcript_file),
        ),
        patch(
            "polaris.delivery.http.routers.tests.os.path.isfile",
            return_value=True,
        ),
    ):
        response = await client.get("/v2/llm/test/run-123/transcript")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["content"] == "# Test Transcript\n\nAll tests passed."


@pytest.mark.asyncio
async def test_v2_llm_test_transcript_not_found(client: AsyncClient) -> None:
    """Getting a missing transcript should return 404."""
    with (
        patch(
            "polaris.delivery.http.routers.tests.build_cache_root",
            return_value="/tmp/cache",
        ),
        patch(
            "polaris.delivery.http.routers.tests.resolve_artifact_path",
            return_value="/tmp/cache/runtime/llm_tests/run-999/LLM_TEST_TRANSCRIPT.md",
        ),
        patch(
            "polaris.delivery.http.routers.tests.os.path.isfile",
            return_value=False,
        ),
    ):
        response = await client.get("/v2/llm/test/run-999/transcript")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "TRANSCRIPT_NOT_FOUND"
