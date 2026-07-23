# -*- coding: utf-8 -*-
# ruff: noqa: UP009
"""Physical-dispatch sentinels for direct synchronous provider routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.async_gemini_api_provider import AsyncGeminiAPIProvider
from polaris.infrastructure.llm.providers.async_http_client import AsyncProviderHttpClient, HttpResult
from polaris.infrastructure.llm.providers.async_ollama_provider import AsyncOllamaProvider
from polaris.infrastructure.llm.providers.gemini_api_provider import GeminiAPIProvider
from polaris.infrastructure.llm.providers.kimi_provider import KimiProvider
from polaris.infrastructure.llm.providers.minimax_provider import MiniMaxProvider
from polaris.infrastructure.llm.providers.ollama_provider import OllamaProvider
from polaris.infrastructure.llm.providers.openai_provider import OpenAIProvider
from polaris.kernelone.llm.engine.contracts import bind_physical_provider_dispatch_port


class _RecordingSyncDispatchPort:
    def __init__(self) -> None:
        self.wire_requests: list[Mapping[str, Any]] = []

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        self.wire_requests.append(wire_request)
        return send(wire_request)


class _JsonResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.headers = {"Content-Type": "application/json"}

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _AsyncContent:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[bytes]:
        async def _iterate() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                yield chunk

        return _iterate()


class _AsyncResponse:
    ok = True
    status = 200
    status_code = 200

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.content = _AsyncContent(chunks)
        self.headers = {"Content-Type": "text/event-stream"}

    async def __aenter__(self) -> _AsyncResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None


class _AsyncSession:
    def __init__(self, response: _AsyncResponse) -> None:
        self.response = response
        self.posts: list[dict[str, Any]] = []
        self.closed = False

    def post(self, _endpoint: str, **kwargs: Any) -> _AsyncResponse:
        self.posts.append(kwargs)
        return self.response

    async def close(self) -> None:
        self.closed = True


class _RecordingStreamDispatchPort:
    def __init__(self) -> None:
        self.wire_requests: list[Mapping[str, Any]] = []

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], Any],
        consume: Callable[[Any], AsyncIterator[Any]],
    ) -> AsyncIterator[Any]:
        self.wire_requests.append(wire_request)

        async def _dispatch() -> AsyncIterator[Any]:
            async with open_stream(wire_request) as response:
                async for item in consume(response):
                    yield item

        return _dispatch()


class _RecordingAsyncDispatchPort:
    def __init__(self) -> None:
        self.wire_requests: list[Mapping[str, Any]] = []

    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        self.wire_requests.append(wire_request)
        return await send(wire_request)


def _assert_single_governed_post(
    port: _RecordingSyncDispatchPort,
    raw_posts: list[dict[str, Any]],
    *,
    expected_model: str,
) -> None:
    assert len(port.wire_requests) == 1
    assert len(raw_posts) == 1
    wire = port.wire_requests[0]
    assert wire["transport"]["kind"] == "requests.post"
    assert raw_posts[0]["json"] == wire["body"]
    if "model" in wire["body"]:
        assert wire["body"]["model"] == expected_model
    else:
        assert expected_model in str(wire["endpoint"])


def test_gemini_direct_invoke_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    gemini_api_config: dict[str, Any],
    sample_gemini_response: dict[str, Any],
) -> None:
    raw_posts: list[dict[str, Any]] = []
    port = _RecordingSyncDispatchPort()

    def _post(_url: str, **kwargs: Any) -> _JsonResponse:
        raw_posts.append(kwargs)
        return _JsonResponse(sample_gemini_response)

    monkeypatch.setattr("requests.post", _post)
    with bind_physical_provider_dispatch_port(port):
        result = GeminiAPIProvider().invoke("hello", "gemini-1.5-pro", gemini_api_config)

    assert result.ok is True
    _assert_single_governed_post(port, raw_posts, expected_model="gemini-1.5-pro")


def test_ollama_direct_invoke_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    ollama_config: dict[str, Any],
    sample_ollama_response: dict[str, Any],
) -> None:
    raw_posts: list[dict[str, Any]] = []
    port = _RecordingSyncDispatchPort()

    def _post(_url: str, **kwargs: Any) -> _JsonResponse:
        raw_posts.append(kwargs)
        return _JsonResponse(sample_ollama_response)

    monkeypatch.setattr("requests.post", _post)
    with bind_physical_provider_dispatch_port(port):
        result = OllamaProvider().invoke("hello", "llama2", ollama_config)

    assert result.ok is True
    _assert_single_governed_post(port, raw_posts, expected_model="llama2")


def test_minimax_direct_invoke_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    minimax_config: dict[str, Any],
) -> None:
    raw_posts: list[dict[str, Any]] = []
    port = _RecordingSyncDispatchPort()
    payload = {
        "base_resp": {"status_code": 0, "status_msg": "Success"},
        "choices": [{"message": {"content": "ok", "role": "assistant"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }

    def _post(_url: str, **kwargs: Any) -> _JsonResponse:
        raw_posts.append(kwargs)
        return _JsonResponse(payload)

    monkeypatch.setattr("requests.post", _post)
    with bind_physical_provider_dispatch_port(port):
        result = MiniMaxProvider().invoke("hello", "MiniMax-M2.1", minimax_config)

    assert result.ok is True
    _assert_single_governed_post(port, raw_posts, expected_model="MiniMax-M2.1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "config", "chunks"),
    [
        (
            OpenAIProvider(),
            {"base_url": "https://example.test/v1", "api_path": "/v1/chat/completions"},
            (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]\n\n"),
        ),
        (
            AnthropicProvider(),
            {
                "base_url": "https://example.test/v1",
                "api_path": "/v1/messages",
                "api_key": "secret",
            },
            (b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"ok"}}\n\n',),
        ),
        (
            MiniMaxProvider(),
            {"base_url": "https://example.test/v1", "api_key": "secret"},
            (b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]\n\n"),
        ),
    ],
)
async def test_structured_stream_route_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    provider: OpenAIProvider | AnthropicProvider | MiniMaxProvider,
    config: dict[str, Any],
    chunks: tuple[bytes, ...],
) -> None:
    session = _AsyncSession(_AsyncResponse(chunks))
    port = _RecordingStreamDispatchPort()
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        lambda _old: _async_value(session),
    )

    with bind_physical_provider_dispatch_port(port):
        events = [item async for item in provider.invoke_stream_events("hello", "model-1", config)]

    assert events
    assert len(port.wire_requests) == len(session.posts) == 1
    assert port.wire_requests[0]["body"]["stream"] is True
    assert session.closed is True


@pytest.mark.asyncio
async def test_kimi_custom_stream_handler_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _AsyncSession(
        _AsyncResponse((b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', b"data: [DONE]\n\n"))
    )
    port = _RecordingStreamDispatchPort()
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        lambda _old: _async_value(session),
    )
    config = {"base_url": "https://example.test/v1", "api_key": "secret"}

    with bind_physical_provider_dispatch_port(port):
        tokens = [item async for item in KimiProvider().invoke_stream("hello", "kimi-k2", config)]

    assert tokens == ["ok"]
    assert len(port.wire_requests) == len(session.posts) == 1
    assert port.wire_requests[0]["body"]["stream"] is True
    assert session.closed is True


@pytest.mark.asyncio
async def test_async_ollama_native_stream_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _AsyncSession(_AsyncResponse((b'{"message":{"content":"ok"}}\n',)))
    port = _RecordingStreamDispatchPort()
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers._close_and_create_session",
        lambda _old: _async_value(session),
    )
    config = {"base_url": "http://example.test:11434", "api_path": "/api/chat"}

    with bind_physical_provider_dispatch_port(port):
        events = [item async for item in AsyncOllamaProvider().invoke_stream("hello", "llama3", config)]

    assert events == [{"message": {"content": "ok"}}]
    assert len(port.wire_requests) == len(session.posts) == 1
    assert port.wire_requests[0]["body"]["stream"] is True
    assert session.closed is True


@pytest.mark.asyncio
async def test_async_gemini_single_result_stream_crosses_bound_physical_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_posts: list[tuple[str, dict[str, Any]]] = []
    port = _RecordingAsyncDispatchPort()
    response_payload = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    }

    async def _post_json_impl(
        _self: AsyncProviderHttpClient,
        url: str,
        _headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float,
    ) -> HttpResult:
        del timeout
        raw_posts.append((url, payload))
        return HttpResult(status_code=200, headers={}, text=json.dumps(response_payload), elapsed_ms=1)

    monkeypatch.setattr(AsyncProviderHttpClient, "_post_json_impl", _post_json_impl)
    config = {"base_url": "https://example.test", "api_key": "secret", "retries": 0}

    with bind_physical_provider_dispatch_port(port):
        events = [item async for item in AsyncGeminiAPIProvider().invoke_stream("hello", "gemini-1.5-pro", config)]

    assert events == [{"text": "ok"}]
    assert len(port.wire_requests) == len(raw_posts) == 1
    assert port.wire_requests[0]["transport"]["kind"] == "httpx.AsyncClient.post"


async def _async_value(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_gemini_stream_to_thread_preserves_bound_sync_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    gemini_api_config: dict[str, Any],
    sample_gemini_response: dict[str, Any],
) -> None:
    raw_posts: list[dict[str, Any]] = []
    port = _RecordingSyncDispatchPort()

    def _post(_url: str, **kwargs: Any) -> _JsonResponse:
        raw_posts.append(kwargs)
        return _JsonResponse(sample_gemini_response)

    monkeypatch.setattr("requests.post", _post)
    with bind_physical_provider_dispatch_port(port):
        chunks = [
            item async for item in GeminiAPIProvider().invoke_stream("hello", "gemini-1.5-pro", gemini_api_config)
        ]

    assert chunks
    _assert_single_governed_post(port, raw_posts, expected_model="gemini-1.5-pro")


@pytest.mark.asyncio
async def test_ollama_stream_to_thread_preserves_bound_sync_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    ollama_config: dict[str, Any],
    sample_ollama_response: dict[str, Any],
) -> None:
    raw_posts: list[dict[str, Any]] = []
    port = _RecordingSyncDispatchPort()

    def _post(_url: str, **kwargs: Any) -> _JsonResponse:
        raw_posts.append(kwargs)
        return _JsonResponse(sample_ollama_response)

    monkeypatch.setattr("requests.post", _post)
    with bind_physical_provider_dispatch_port(port):
        chunks = [item async for item in OllamaProvider().invoke_stream("hello", "llama2", ollama_config)]

    assert chunks
    _assert_single_governed_post(port, raw_posts, expected_model="llama2")
