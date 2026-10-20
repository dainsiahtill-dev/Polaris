"""Synthetic loopback provider fixtures; exercise real HTTP and native codecs only."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncGenerator, Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from typing import Any

import pytest
from polaris.kernelone.llm.engine.client import ProviderConfig, ResilientLLMClient
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, AIStreamEvent
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.resilience import RetryConfig, TimeoutConfig
from polaris.kernelone.llm.engine.stream import StreamExecutor


@dataclass
class _EngineProvider:
    """Test-only configuration bridge; execution and provider transports stay real."""

    name: str
    executor: AIExecutor
    stream_executor: StreamExecutor

    async def invoke(self, request: AIRequest) -> AIResponse:
        return await self.executor.invoke(request)

    async def invoke_stream(self, request: AIRequest) -> AsyncGenerator[AIStreamEvent, None]:
        async for event in self.stream_executor.invoke_stream(request):
            yield event


@dataclass
class ScriptedProvider:
    """Finite synthetic responses served over a real loopback HTTP connection."""

    base_url: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)
    responses: deque[tuple[int, str, bytes]] = field(default_factory=deque)

    def enqueue_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.responses.append((status, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8")))

    def enqueue_stream(self, events: list[dict[str, Any] | str]) -> None:
        body = "".join(
            "data: " + (event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)) + "\n\n"
            for event in events
        )
        self.responses.append((200, "text/event-stream", body.encode("utf-8")))

    def client(self, protocol: str, workspace: Path, monkeypatch: pytest.MonkeyPatch) -> ResilientLLMClient:
        provider_type = "anthropic_compat" if protocol == "anthropic" else "openai_compat"
        config = {
            "type": provider_type,
            "base_url": self.base_url,
            "api_path": "/v1/messages" if protocol == "anthropic" else "/v1/chat/completions",
            "api_key": "synthetic-loopback-only",
            "max_context_tokens": 32768,
            "max_output_tokens": 1024,
            "max_tokens": 512,
            "timeout": 3,
            "retries": 0,
        }
        executor = AIExecutor(workspace=str(workspace))
        stream_executor = StreamExecutor(workspace=str(workspace))
        monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: dict(config))
        monkeypatch.setattr(stream_executor, "_get_provider_config", lambda _provider_id: dict(config))
        provider = _EngineProvider(provider_type, executor, stream_executor)
        return ResilientLLMClient(
            [(provider, ProviderConfig(provider_name=provider_type))],
            default_retry_config=RetryConfig(max_attempts=1),
            default_timeout_config=TimeoutConfig(request_timeout=5, total_timeout=6),
        )


@pytest.fixture
def isolated_workspace() -> Iterator[Path]:
    """All disposable test workspaces live under the authorized Polaris root."""
    root = Path.home() / ".polaris" / "test-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="engineering-native-", dir=root) as directory:
        yield Path(directory)


@pytest.fixture
def scripted_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[ScriptedProvider]:
    """Synthetic provider, never evidence of real-model or Bench capability."""
    monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", "0")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    script = ScriptedProvider()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            script.requests.append({"path": self.path, "body": json.loads(body.decode("utf-8"))})
            if script.responses:
                status, content_type, response = script.responses.popleft()
            else:
                status, content_type, response = 500, "application/json", b'{"error":"script exhausted"}'
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    script.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield script
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def private_sandbox(isolated_workspace: Path) -> Callable[[list[str]], list[str]]:
    """Explicit full grant for this private fixture, with real namespace isolation."""
    from polaris.kernelone.process.sandbox import build_workspace_sandbox_argv

    def prepare(argv: list[str]) -> list[str]:
        return build_workspace_sandbox_argv(
            argv,
            workspace=isolated_workspace,
            cwd=isolated_workspace,
            read_roots=(isolated_workspace,),
            write_roots=(isolated_workspace,),
            network_allowed=False,
        )

    return prepare
