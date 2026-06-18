"""Tests for Polaris v2 interview router.

Covers POST /v2/llm/interview/ask, POST /v2/llm/interview/save,
POST /v2/llm/interview/cancel, and POST /v2/llm/interview/stream.
External services are mocked to avoid LLM provider dependencies.
"""

from __future__ import annotations

import json
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
async def test_v2_llm_interview_ask_accepts_frontend_camel_case_payload(client: AsyncClient) -> None:
    """POST /v2/llm/interview/ask should accept the desktop payload shape."""
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
                "roleId": "pm",
                "providerId": "openai_compat-1",
                "model": "Qwen3-Max",
                "question": "How would you plan a complex delivery?",
                "expectedCriteria": ["任务拆分", "风险控制"],
                "expectsThinking": True,
                "sessionId": "sess-ui-123",
            },
        )

    assert response.status_code == 200
    assert mock_generate.await_args is not None
    kwargs = mock_generate.await_args.kwargs
    assert kwargs["role"] == "pm"
    assert kwargs["criteria"] == ["任务拆分", "风险控制"]


@pytest.mark.asyncio
async def test_v2_llm_interview_ask_prefers_active_workspace_path(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Interview generation should use the same active workspace as LLM status projection."""
    stale_workspace = tmp_path / "stale"
    active_workspace = tmp_path / "active"
    stale_workspace.mkdir()
    active_workspace.mkdir()
    mock_settings.workspace = str(stale_workspace)
    mock_settings.workspace_path = str(active_workspace)

    with patch(
        "polaris.delivery.http.routers.interview.generate_interview_answer",
        new_callable=AsyncMock,
        return_value={
            "raw_output": "raw",
            "thinking": "",
            "answer": "answer text",
            "evaluation": {"score": 9},
        },
    ) as mock_generate:
        response = await client.post(
            "/v2/llm/interview/ask",
            json={
                "role": "pm",
                "provider_id": "openai_compat-1",
                "model": "Qwen3-Max",
                "question": "How would you plan a complex delivery?",
            },
        )

    assert response.status_code == 200
    assert mock_generate.await_args is not None
    assert mock_generate.await_args.kwargs["workspace"] == str(active_workspace)


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


def test_save_interview_report_updates_llm_readiness_index(tmp_path: Path) -> None:
    """A passed interactive interview should unblock the exact role/provider/model binding."""
    from polaris.cells.llm.evaluation.public.service import load_llm_test_index, save_interview_report
    from polaris.cells.runtime.projection.internal.llm_status import build_llm_status

    index_path = tmp_path / ".polaris" / "llm_test_index.json"
    status_settings = MagicMock()
    status_settings.workspace = str(tmp_path)
    status_settings.workspace_path = str(tmp_path)
    status_settings.ramdisk_root = ""
    status_settings.qa_enabled = True
    config_payload = {
        "schema_version": 1,
        "providers": {
            "openai_compat-1": {"type": "openai_compat"},
        },
        "roles": {
            "pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"},
        },
        "policies": {
            "required_ready_roles": ["pm"],
        },
    }
    with (
        patch(
            "polaris.cells.llm.evaluation.internal.index._resolve_index_paths",
            return_value=[str(index_path)],
        ),
        patch(
            "polaris.cells.runtime.projection.internal.llm_status.llm_config.load_llm_config",
            return_value=config_payload,
        ),
        patch(
            "polaris.cells.runtime.projection.internal.llm_status.build_cache_root",
            return_value=str(tmp_path / ".polaris" / "runtime"),
        ),
    ):
        result = save_interview_report(
            workspace=str(tmp_path),
            role="pm",
            provider_id="openai_compat-1",
            model="Qwen3-Max",
            session_id="sess-pm-pass",
            report={
                "id": "sess-pm-pass",
                "overallStatus": "passed",
                "provider": {"id": "openai_compat-1", "model": "Qwen3-Max"},
                "summary": {"totalQuestions": 2, "passedQuestions": 2, "averageRating": 1},
            },
        )
        index = load_llm_test_index(str(tmp_path))
        status = build_llm_status(status_settings)

    assert result["saved"] is True
    assert result["readiness_updated"] is True
    report_path = result["report_path"]
    with open(report_path, encoding="utf-8") as handle:
        artifact = json.load(handle)

    assert artifact["target"] == {
        "role": "pm",
        "provider_id": "openai_compat-1",
        "model": "Qwen3-Max",
    }
    assert artifact["final"]["ready"] is True
    assert index["roles"]["pm"]["ready"] is True
    assert index["roles"]["pm"]["model"] == "Qwen3-Max"
    assert index["providers"]["openai_compat-1"]["role"] == "pm"
    assert status["blocked_roles"] == []
    assert status["state"] == "READY"
    assert status["roles"]["pm"]["readiness_source"] == "role_index"
    assert status["interviews"]["latest_by_provider"]["openai_compat-1"]["status"] == "passed"


@pytest.mark.asyncio
async def test_v2_llm_interview_save_accepts_frontend_camel_case_payload(
    client: AsyncClient,
    tmp_path: Path,
    mock_settings: Settings,
) -> None:
    """POST /v2/llm/interview/save should persist the desktop report and return its path."""
    mock_settings.workspace = str(tmp_path)
    mock_settings.workspace_path = str(tmp_path)
    index_path = tmp_path / ".polaris" / "llm_test_index.json"
    config_payload = {
        "roles": {"pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"}},
    }

    with (
        patch(
            "polaris.cells.llm.evaluation.internal.index._resolve_index_paths",
            return_value=[str(index_path)],
        ),
        patch(
            "polaris.delivery.http.routers.interview.llm_config.load_llm_config",
            return_value=config_payload,
        ),
    ):
        response = await client.post(
            "/v2/llm/interview/save",
            json={
                "roleId": "pm",
                "providerId": "openai_compat-1",
                "model": "Qwen3-Max",
                "sessionId": "sess-ui-save",
                "report": {
                    "id": "sess-ui-save",
                    "overallStatus": "passed",
                    "provider": {"id": "openai_compat-1", "model": "Qwen3-Max"},
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["saved"] is True
    assert data["readiness_updated"] is True
    assert data["report_path"].endswith(".json")


@pytest.mark.asyncio
async def test_v2_llm_interview_save_prefers_active_workspace_path(
    client: AsyncClient,
    tmp_path: Path,
    mock_settings: Settings,
) -> None:
    """Passed PM interviews must update readiness for the active desktop workspace, not a stale setting."""
    stale_workspace = tmp_path / "stale"
    active_workspace = tmp_path / "active"
    stale_workspace.mkdir()
    active_workspace.mkdir()
    mock_settings.workspace = str(stale_workspace)
    mock_settings.workspace_path = str(active_workspace)
    index_path = active_workspace / ".polaris" / "llm_test_index.json"
    config_payload = {
        "roles": {"pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"}},
    }

    with (
        patch(
            "polaris.cells.llm.evaluation.internal.index._resolve_index_paths",
            return_value=[str(index_path)],
        ),
        patch(
            "polaris.delivery.http.routers.interview.llm_config.load_llm_config",
            return_value=config_payload,
        ),
    ):
        response = await client.post(
            "/v2/llm/interview/save",
            json={
                "roleId": "pm",
                "providerId": "openai_compat-1",
                "model": "Qwen3-Max",
                "sessionId": "sess-active-save",
                "report": {
                    "id": "sess-active-save",
                    "overallStatus": "passed",
                    "provider": {"id": "openai_compat-1", "model": "Qwen3-Max"},
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["readiness_updated"] is True
    normalized_report_path = str(data["report_path"]).replace("\\", "/")
    assert "/projects/active-" in normalized_report_path
    assert "/projects/stale-" not in normalized_report_path


@pytest.mark.asyncio
async def test_v2_llm_interview_save_does_not_overwrite_role_readiness_for_unbound_provider(
    client: AsyncClient,
    tmp_path: Path,
    mock_settings: Settings,
) -> None:
    """Candidate/probe interviews must not poison the active role readiness slot."""
    mock_settings.workspace = str(tmp_path)
    mock_settings.workspace_path = str(tmp_path)
    index_path = tmp_path / ".polaris" / "llm_test_index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "version": "2.0",
                "roles": {
                    "pm": {
                        "ready": True,
                        "grade": "PASS",
                        "provider_id": "openai_compat-1",
                        "model": "Qwen3-Max",
                        "last_run_id": "sess-current-pm",
                        "timestamp": "2026-06-08T00:00:00+00:00",
                        "suites": {"interview": {"ok": True}},
                    }
                },
                "providers": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config_payload = {
        "roles": {"pm": {"provider_id": "openai_compat-1", "model": "Qwen3-Max"}},
    }

    with (
        patch(
            "polaris.cells.llm.evaluation.internal.index._resolve_index_paths",
            return_value=[str(index_path)],
        ),
        patch(
            "polaris.delivery.http.routers.interview.llm_config.load_llm_config",
            return_value=config_payload,
        ),
    ):
        response = await client.post(
            "/v2/llm/interview/save",
            json={
                "role": "pm",
                "provider_id": "e2e-provider",
                "model": "e2e-model",
                "session_id": "sess-e2e-probe",
                "report": {
                    "id": "sess-e2e-probe",
                    "overallStatus": "passed",
                    "target": {"role": "pm", "provider_id": "e2e-provider", "model": "e2e-model"},
                    "final": {"ready": True, "grade": "PASS"},
                    "suites": {"interview": {"ok": True}},
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["readiness_updated"] is True
    assert data["role_readiness_updated"] is False
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["roles"]["pm"]["provider_id"] == "openai_compat-1"
    assert index["roles"]["pm"]["model"] == "Qwen3-Max"
    assert index["roles"]["pm"]["last_run_id"] == "sess-current-pm"
    assert index["providers"]["e2e-provider"]["last_run_id"] == "sess-e2e-probe"


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


@pytest.mark.asyncio
async def test_v2_llm_interview_jetstream_starts_nat_channel_and_publishes_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v2/llm/interview/jetstream should publish interview events through runtime JetStream."""
    from polaris.delivery.http.routers import interview

    scheduled: list[object] = []
    published: list[tuple[str, dict[str, object]]] = []

    async def _fake_run_interview_streaming(settings, role, provider_id, model, question, output_queue, **kwargs):
        assert role == "pm"
        assert provider_id == "provider-1"
        assert model == "model-1"
        assert kwargs["session_id"] == "interactive-1"
        await output_queue.put({"type": "content_chunk", "data": {"content": "计划"}})
        await output_queue.put(
            {
                "type": "complete",
                "data": {"sessionId": "interactive-1", "answer": "计划", "ok": True},
            }
        )

    async def _fake_publish_to_jetstream(*, subject: str, payload: dict[str, object]) -> bool:
        published.append((subject, payload))
        return True

    class _CapturedTask:
        def __init__(self, coro: object) -> None:
            self.coro = coro

        def add_done_callback(self, callback) -> None:
            self.callback = callback

    def _capture_create_task(coro):
        scheduled.append(coro)
        return _CapturedTask(coro)

    monkeypatch.setattr(interview, "run_interactive_interview_streaming", _fake_run_interview_streaming)
    monkeypatch.setattr(interview, "publish_to_jetstream", _fake_publish_to_jetstream)
    monkeypatch.setattr(interview.asyncio, "create_task", _capture_create_task)

    response = await client.post(
        "/v2/llm/interview/jetstream",
        json={
            "role": "pm",
            "provider_id": "provider-1",
            "model": "model-1",
            "question": "请制定计划",
            "session_id": "interactive-1",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "ok": True,
        "session_id": "interactive-1",
        "status": "started",
        "channel": "llm-interview:interactive-1",
        "subject": "hp.runtime.llm.interview.interactive-1",
        "transport": "nat-jetstream",
    }
    assert len(scheduled) == 1

    await scheduled[0]

    assert [payload["payload"]["type"] for _, payload in published] == [
        "start",
        "content_chunk",
        "complete",
    ]
    assert {payload["channel"] for _, payload in published} == {"llm-interview:interactive-1"}
    assert {subject for subject, _ in published} == {"hp.runtime.llm.interview.interactive-1"}


# ---------------------------------------------------------------------------
# POST /v2/llm/interview/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_llm_interview_stream_headers(client: AsyncClient) -> None:
    """POST /v2/llm/interview/stream should fail closed to Nat-JetStream."""
    response = await client.post(
        "/v2/llm/interview/stream",
        json={
            "role": "pm",
            "provider_id": "provider-1",
            "model": "model-1",
            "question": "hello",
            "session_id": "interactive-1",
        },
    )

    assert response.status_code == 410
    assert "text/event-stream" not in response.headers.get("content-type", "")
    data = response.json()
    assert data["error"]["code"] == "SSE_REMOVED"
    assert data["error"]["details"]["replacement"] == "/v2/llm/interview/jetstream"
    assert data["error"]["details"]["transport"] == "nat-jetstream"
