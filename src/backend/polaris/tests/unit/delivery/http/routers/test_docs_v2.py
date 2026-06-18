"""Tests for Polaris docs init v2 endpoints.

Covers POST /v2/docs/init/* routes.
External services are mocked to avoid LLM provider and storage dependencies.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
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
# POST /v2/docs/init/dialogue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_dialogue_success(client: AsyncClient) -> None:
    """Dialogue endpoint should return ok with reply and fields."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "ollama", "model": "llama3"},
                },
                "providers": {
                    "ollama": {"type": "ollama"},
                },
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.generate_docs_dialogue_turn",
            new_callable=AsyncMock,
            return_value={
                "reply": "Got it, let me clarify.",
                "questions": ["What is the target platform?"],
                "tiaochen": ["Setup project"],
                "meta": {"phase": "clarifying"},
                "handoffs": {},
                "fields": {
                    "goal": "Build a web app",
                    "in_scope": ["Frontend", "Backend"],
                    "out_of_scope": ["Mobile"],
                    "constraints": ["Use existing stack"],
                    "definition_of_done": ["Tests pass"],
                    "backlog": ["Setup", "Implement"],
                },
            },
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/docs/init/dialogue",
            json={"message": "I want to build a web app", "goal": "Build a web app"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["reply"] == "Got it, let me clarify."
        assert data["questions"] == ["What is the target platform?"]
        assert data["fields"]["goal"] == "Build a web app"
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_docs_init_dialogue_architect_not_configured(client: AsyncClient) -> None:
    """Missing architect role should return 409 ARCHITECT_NOT_CONFIGURED."""
    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={"roles": {}, "providers": {}},
    ):
        response = await client.post(
            "/v2/docs/init/dialogue",
            json={"message": "hello"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ARCHITECT_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# POST /v2/docs/init/suggest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_suggest_success(client: AsyncClient) -> None:
    """Suggest endpoint should return ok with suggested fields."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "ollama", "model": "llama3"},
                },
                "providers": {
                    "ollama": {"type": "ollama"},
                },
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
            new_callable=AsyncMock,
            return_value={
                "goal": ["Build a CLI tool"],
                "in_scope": ["Core commands", "Help text"],
                "out_of_scope": ["GUI"],
                "constraints": ["Python 3.11+"],
                "definition_of_done": ["Unit tests pass"],
                "backlog": ["Scaffold", "Implement commands"],
            },
        ) as mock_generate,
    ):
        response = await client.post(
            "/v2/docs/init/suggest",
            json={"goal": "Build a CLI tool"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "Build a CLI tool" in data["fields"]["goal"]
        mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_docs_init_suggest_architect_not_configured(client: AsyncClient) -> None:
    """Missing architect role should return 409 ARCHITECT_NOT_CONFIGURED."""
    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={"roles": {}, "providers": {}},
    ):
        response = await client.post(
            "/v2/docs/init/suggest",
            json={"goal": "Build something"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ARCHITECT_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# POST /v2/docs/init/preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_preview_success(client: AsyncClient) -> None:
    """Preview endpoint should return ok with file list."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
            return_value={
                "roles": {
                    "architect": {"provider_id": "ollama", "model": "llama3"},
                },
                "providers": {
                    "ollama": {"type": "ollama"},
                },
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.generate_docs_ai_fields",
            new_callable=AsyncMock,
            return_value={
                "goal": ["Build an API"],
                "in_scope": ["REST endpoints"],
                "out_of_scope": ["Web UI"],
                "constraints": ["FastAPI"],
                "definition_of_done": ["Postman tests pass"],
                "backlog": ["Setup", "Implement"],
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.build_docs_templates",
            return_value={
                "docs/product/requirements.md": "# Requirements\n",
                "docs/product/plan.md": "# Plan\n",
            },
        ),
        patch(
            "polaris.delivery.http.routers.docs.select_docs_target_root",
            return_value="workspace/docs",
        ),
        patch(
            "polaris.delivery.http.routers.docs.workspace_has_docs",
            return_value=False,
        ),
        patch(
            "polaris.delivery.http.routers.docs.detect_project_profile",
            return_value={"python": True, "node": False},
        ),
    ):
        response = await client.post(
            "/v2/docs/init/preview",
            json={"goal": "Build an API", "mode": "minimal"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["mode"] == "minimal"
        assert len(data["files"]) == 2
        assert data["files"][0]["path"] == "workspace/docs/product/requirements.md"


@pytest.mark.asyncio
async def test_docs_init_preview_architect_not_configured(client: AsyncClient) -> None:
    """Missing architect role should return 409 ARCHITECT_NOT_CONFIGURED."""
    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={"roles": {}, "providers": {}},
    ):
        response = await client.post(
            "/v2/docs/init/preview",
            json={"goal": "Build something"},
        )
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "ARCHITECT_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_docs_preview_ai_fields_falls_back_on_stream_error(mock_settings: Settings) -> None:
    """Docs preview should produce deterministic fields when LLM stream errors."""
    from polaris.delivery.http.routers import docs

    async def stream_error(
        _workspace: str,
        _settings: Settings,
        _fields: dict[str, str],
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "error", "error": "provider unavailable"}

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    fields = {"goal": "Build reliable PM workflow"}

    with patch("polaris.delivery.http.routers.docs.generate_docs_fields_stream", stream_error):
        resolved, used_fallback = await docs._resolve_docs_preview_ai_fields(
            queue=queue,
            workspace=".",
            settings=mock_settings,
            fields=fields,
            timeout_seconds=1.0,
        )

    assert used_fallback is True
    assert resolved["goal"] == ["Build reliable PM workflow"]
    stage = await queue.get()
    assert stage["type"] == "stage"
    assert stage["data"]["stage"] == "llm_fallback"


@pytest.mark.asyncio
async def test_docs_preview_ai_fields_falls_back_on_stream_timeout(mock_settings: Settings) -> None:
    """Docs preview should not wait indefinitely for a silent provider stream."""
    from polaris.delivery.http.routers import docs

    async def hanging_stream(
        _workspace: str,
        _settings: Settings,
        _fields: dict[str, str],
    ) -> AsyncIterator[dict[str, Any]]:
        await asyncio.sleep(3600)
        yield {"type": "result", "fields": {}}

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    fields = {"goal": "Build reliable PM workflow"}

    with patch("polaris.delivery.http.routers.docs.generate_docs_fields_stream", hanging_stream):
        resolved, used_fallback = await docs._resolve_docs_preview_ai_fields(
            queue=queue,
            workspace=".",
            settings=mock_settings,
            fields=fields,
            timeout_seconds=0.01,
        )

    assert used_fallback is True
    assert resolved["backlog"]
    stage = await queue.get()
    assert stage["type"] == "stage"
    assert stage["data"]["fallback"] is True


# ---------------------------------------------------------------------------
# POST /v2/docs/init/apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_apply_success(client: AsyncClient) -> None:
    """Apply endpoint should write files and return created list."""
    with (
        patch(
            "polaris.delivery.http.routers.docs.write_text_atomic",
        ) as mock_write,
        patch(
            "polaris.delivery.http.routers.docs.workspace_has_docs",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.docs.clear_workspace_status",
        ),
        patch(
            "polaris.delivery.http.routers.docs.emit_event",
        ),
        patch(
            "polaris.delivery.http.routers.docs._sync_plan_to_runtime",
        ),
    ):
        response = await client.post(
            "/v2/docs/init/apply",
            json={
                "target_root": "workspace/docs",
                "files": [
                    {"path": "workspace/docs/product/requirements.md", "content": "# Requirements\n"},
                    {"path": "workspace/docs/product/plan.md", "content": "# Plan\n"},
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert len(data["files"]) == 2
        assert mock_write.call_count == 2


@pytest.mark.asyncio
async def test_docs_init_apply_promotes_draft_payload_to_active_docs(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Draft-root approvals must still materialize active docs for PM startup."""
    mock_settings.workspace = str(tmp_path)
    with (
        patch(
            "polaris.delivery.http.routers.docs.write_text_atomic",
        ) as mock_write,
        patch(
            "polaris.delivery.http.routers.docs.workspace_has_docs",
            return_value=True,
        ),
        patch(
            "polaris.delivery.http.routers.docs.clear_workspace_status",
        ),
        patch(
            "polaris.delivery.http.routers.docs.emit_event",
        ),
        patch(
            "polaris.delivery.http.routers.docs._sync_plan_to_runtime",
        ) as mock_sync,
    ):
        response = await client.post(
            "/v2/docs/init/apply",
            json={
                "target_root": "workspace/docs/_drafts/init-20260602-010203",
                "files": [
                    {
                        "path": "workspace/docs/_drafts/init-20260602-010203/product/requirements.md",
                        "content": "# Requirements\n",
                    },
                    {
                        "path": "workspace/docs/_drafts/init-20260602-010203/product/plan.md",
                        "content": "# Plan\n",
                    },
                ],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "workspace/docs/product/requirements.md" in data["files"]
    assert "workspace/docs/product/plan.md" in data["files"]
    assert mock_write.call_count == 4
    mock_sync.assert_called_once()


@pytest.mark.asyncio
async def test_docs_init_apply_writes_runtime_plan_contract(
    client: AsyncClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Successful approval must leave the runtime plan contract readable."""
    from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

    mock_settings.workspace = str(tmp_path)
    response = await client.post(
        "/v2/docs/init/apply",
        json={
            "target_root": "workspace/docs",
            "files": [
                {"path": "workspace/docs/product/requirements.md", "content": "# Requirements\n"},
                {"path": "workspace/docs/product/plan.md", "content": "# Plan\n\n- Build it\n"},
            ],
        },
    )

    assert response.status_code == 200
    cache_root = build_cache_root("", str(tmp_path))
    plan_contract = Path(resolve_artifact_path(str(tmp_path), cache_root, "runtime/contracts/plan.md"))
    requirements_contract = Path(resolve_artifact_path(str(tmp_path), cache_root, "runtime/contracts/requirements.md"))
    assert plan_contract.read_text(encoding="utf-8") == "# Plan\n\n- Build it\n"
    assert requirements_contract.read_text(encoding="utf-8") == "# Requirements\n"


@pytest.mark.asyncio
async def test_docs_init_apply_invalid_target_root(client: AsyncClient) -> None:
    """Invalid target_root should return 400 INVALID_DOCS_PATH."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={
            "target_root": "invalid/path",
            "files": [{"path": "workspace/docs/product/test.md", "content": "# Test\n"}],
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_DOCS_PATH"


@pytest.mark.asyncio
async def test_docs_init_apply_no_files(client: AsyncClient) -> None:
    """Empty files list should return 400 INVALID_REQUEST."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={"target_root": "workspace/docs", "files": []},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_docs_init_apply_unsafe_path(client: AsyncClient) -> None:
    """Unsafe file path should return 400 INVALID_DOCS_PATH."""
    response = await client.post(
        "/v2/docs/init/apply",
        json={
            "target_root": "workspace/docs",
            "files": [{"path": "../etc/passwd", "content": "evil"}],
        },
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "INVALID_DOCS_PATH"


# ---------------------------------------------------------------------------
# POST /v2/docs/init/dialogue/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_dialogue_jetstream_starts_nat_channel_and_publishes_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dialogue jetstream should publish docs init dialogue events through runtime JetStream."""
    from polaris.delivery.http.routers import docs

    scheduled: list[object] = []
    published: list[tuple[str, dict[str, object]]] = []

    async def _fake_generate_docs_dialogue_turn_streaming(**kwargs: object) -> None:
        output_queue = kwargs["output_queue"]
        await output_queue.put({"type": "thinking_chunk", "data": {"content": "分析"}})
        await output_queue.put(
            {
                "type": "complete",
                "data": {
                    "reply": "可以拟定条陈",
                    "questions": [],
                    "fields": {"goal": "Build"},
                },
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

    monkeypatch.setattr(docs, "generate_docs_dialogue_turn_streaming", _fake_generate_docs_dialogue_turn_streaming)
    monkeypatch.setattr(docs, "publish_to_jetstream", _fake_publish_to_jetstream)
    monkeypatch.setattr(docs.asyncio, "create_task", _capture_create_task)

    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={
            "roles": {"architect": {"provider_id": "provider-1", "model": "model-1"}},
            "providers": {"provider-1": {"type": "openai_compatible"}},
        },
    ):
        response = await client.post(
            "/v2/docs/init/dialogue/jetstream",
            json={"message": "请规划", "goal": "Build", "session_id": "docs-dialogue-1"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data == {
        "ok": True,
        "session_id": "docs-dialogue-1",
        "status": "started",
        "channel": "docs-init-dialogue:docs-dialogue-1",
        "subject": "hp.runtime.docs.init.dialogue.docs-dialogue-1",
        "transport": "nat-jetstream",
    }
    assert len(scheduled) == 1

    await scheduled[0]

    assert [payload["payload"]["type"] for _, payload in published] == [
        "start",
        "thinking_chunk",
        "complete",
    ]
    assert {payload["channel"] for _, payload in published} == {"docs-init-dialogue:docs-dialogue-1"}
    assert {subject for subject, _ in published} == {"hp.runtime.docs.init.dialogue.docs-dialogue-1"}


@pytest.mark.asyncio
async def test_docs_init_dialogue_stream_headers(client: AsyncClient) -> None:
    """Dialogue stream should return SSE headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")


# ---------------------------------------------------------------------------
# POST /v2/docs/init/preview/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_init_preview_jetstream_starts_nat_channel_and_publishes_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview jetstream should publish docs init preview events through runtime JetStream."""
    from polaris.delivery.http.routers import docs

    scheduled: list[object] = []
    published: list[tuple[str, dict[str, object]]] = []

    async def _fake_resolve_docs_preview_ai_fields(**kwargs: object) -> tuple[dict[str, list[str]], bool]:
        queue = kwargs["queue"]
        await queue.put({"type": "thinking", "data": {"content": "梳理文档"}})
        return {"goal": ["Build"], "backlog": ["Task 1"]}, False

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

    monkeypatch.setattr(docs, "_resolve_docs_preview_ai_fields", _fake_resolve_docs_preview_ai_fields)
    monkeypatch.setattr(docs, "detect_project_profile", lambda _workspace: {"type": "python"})
    monkeypatch.setattr(docs, "default_qa_commands", lambda _profile: ["pytest"])
    monkeypatch.setattr(docs, "build_docs_templates", lambda *_args: {"docs/00_overview.md": "# Overview"})
    monkeypatch.setattr(docs, "select_docs_target_root", lambda _workspace: "docs")
    monkeypatch.setattr(docs, "resolve_artifact_path", lambda *_args: "/tmp/polaris-docs-preview-test.md")
    monkeypatch.setattr(docs, "workspace_has_docs", lambda _workspace: False)
    monkeypatch.setattr(docs, "publish_to_jetstream", _fake_publish_to_jetstream)
    monkeypatch.setattr(docs.asyncio, "create_task", _capture_create_task)

    with patch(
        "polaris.delivery.http.routers.docs.llm_config.load_llm_config",
        return_value={
            "roles": {"architect": {"provider_id": "provider-1", "model": "model-1"}},
            "providers": {"provider-1": {"type": "openai_compatible"}},
        },
    ):
        response = await client.post(
            "/v2/docs/init/preview/jetstream",
            json={"mode": "minimal", "goal": "Build", "session_id": "docs-preview-1"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data == {
        "ok": True,
        "session_id": "docs-preview-1",
        "status": "started",
        "channel": "docs-init-preview:docs-preview-1",
        "subject": "hp.runtime.docs.init.preview.docs-preview-1",
        "transport": "nat-jetstream",
    }
    assert len(scheduled) == 1

    await scheduled[0]

    event_types = [payload["payload"]["type"] for _, payload in published]
    assert event_types[:2] == ["start", "stage"]
    assert "thinking" in event_types
    assert event_types[-1] == "complete"
    assert {payload["channel"] for _, payload in published} == {"docs-init-preview:docs-preview-1"}
    assert {subject for subject, _ in published} == {"hp.runtime.docs.init.preview.docs-preview-1"}


@pytest.mark.asyncio
async def test_docs_init_preview_stream_headers(client: AsyncClient) -> None:
    """Preview stream should return SSE headers.

    Full SSE event consumption is skipped because testing async generators
    with background tasks inside httpx test clients is non-trivial.
    """
    pytest.skip("SSE streaming test requires special async generator handling")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def async_generator(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    """Yield items for mocking async generators."""
    for item in items:
        yield item
