"""Provider helper retry behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, AsyncContextManager

import pytest
import requests
from polaris.infrastructure.llm.providers import provider_helpers
from polaris.infrastructure.llm.providers.provider_helpers import CircuitBreaker, invoke_with_retry
from polaris.kernelone.common.clock import MockClock
from polaris.kernelone.llm.engine.contracts import Usage, bind_physical_provider_dispatch_port


class _Http200Response:
    ok = True
    status_code = 200
    text = '{"choices":[{"message":{"content":"ok"}}]}'
    headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}


class _RecordingSyncDispatchPort:
    def __init__(self) -> None:
        self.wire_requests: list[dict[str, Any]] = []

    def dispatch_sync(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        self.wire_requests.append(dict(wire_request))
        return send(wire_request)


class _AsyncOnlyDispatchPort:
    async def dispatch_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Awaitable[Any]],
    ) -> Any:
        return await send(wire_request)

    async def dispatch_blocking_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        send: Callable[[Mapping[str, Any]], Any],
    ) -> Any:
        return send(wire_request)

    def dispatch_stream_async(
        self,
        *,
        wire_request: Mapping[str, Any],
        open_stream: Callable[[Mapping[str, Any]], AsyncContextManager[Any]],
        consume: Callable[[Any], AsyncIterator[Any]],
    ) -> AsyncIterator[Any]:
        raise AssertionError("stream dispatch is not expected")


class _Http500Response:
    ok = False
    status_code = 500
    text = '{"error":{"message":"","type":"InternalServerError","code":500}}'

    def raise_for_status(self) -> None:
        raise requests.HTTPError("500 Server Error: Internal Server Error")


class _Http429Response:
    ok = False
    status_code = 429
    text = '{"error":{"message":"rate limited","code":429}}'
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        raise requests.HTTPError("429 Client Error: Too Many Requests")


class _CircuitBreakerProbe(CircuitBreaker):
    def __init__(self) -> None:
        super().__init__()
        self.before_calls = 0
        self.failures = 0

    def before_call(self) -> None:
        self.before_calls += 1
        super().before_call()

    def on_failure(self) -> None:
        self.failures += 1
        super().on_failure()


def _usage(_prompt: str, _output: str, _data: dict[str, object]) -> Usage:
    return Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def _extract(data: dict[str, Any]) -> str:
    return str(data["choices"][0]["message"]["content"])


def test_explicit_sync_dispatch_port_wins_over_context_binding(monkeypatch) -> None:
    bound = _RecordingSyncDispatchPort()
    explicit = _RecordingSyncDispatchPort()
    monkeypatch.setattr(provider_helpers, "_blocking_http_post", lambda *_args, **_kwargs: _Http200Response())

    with bind_physical_provider_dispatch_port(bound):
        result = invoke_with_retry(
            "https://example.test/invoke",
            headers={},
            payload={"messages": []},
            timeout=1,
            retries=0,
            prompt="prompt",
            extract_output=_extract,
            usage_from_response=_usage,
            physical_dispatch_port=explicit,
        )

    assert result.ok is True
    assert len(explicit.wire_requests) == 1
    assert bound.wire_requests == []


def test_bound_sync_dispatch_port_wraps_each_physical_retry(monkeypatch) -> None:
    port = _RecordingSyncDispatchPort()
    responses: list[object] = [requests.ConnectionError("retry"), _Http200Response()]

    def _post(*_args: object, **_kwargs: object) -> _Http200Response:
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        assert isinstance(response, _Http200Response)
        return response

    monkeypatch.setattr(provider_helpers, "_blocking_http_post", _post)

    with bind_physical_provider_dispatch_port(port):
        result = invoke_with_retry(
            "https://example.test/invoke",
            headers={},
            payload={"messages": []},
            timeout=1,
            retries=1,
            prompt="prompt",
            extract_output=_extract,
            usage_from_response=_usage,
            backoff_base_seconds=0,
            backoff_max_seconds=0,
        )

    assert result.ok is True
    assert len(port.wire_requests) == 2


def test_bound_async_only_port_fails_closed_before_raw_sync_post(monkeypatch) -> None:
    raw_posts = 0

    def _post(*_args: object, **_kwargs: object) -> _Http200Response:
        nonlocal raw_posts
        raw_posts += 1
        return _Http200Response()

    monkeypatch.setattr(provider_helpers, "_blocking_http_post", _post)

    with (
        bind_physical_provider_dispatch_port(_AsyncOnlyDispatchPort()),
        pytest.raises(
            RuntimeError,
            match="dispatch_sync",
        ),
    ):
        invoke_with_retry(
            "https://example.test/invoke",
            headers={},
            payload={"messages": []},
            timeout=1,
            retries=0,
            prompt="prompt",
            extract_output=_extract,
            usage_from_response=_usage,
        )

    assert raw_posts == 0


def test_http_5xx_returns_retryable_failure_without_local_backoff(monkeypatch) -> None:
    clock = MockClock()
    calls = 0

    def _post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Http500Response()

    monkeypatch.setattr(provider_helpers, "_blocking_http_post", _post)

    result = invoke_with_retry(
        "http://localhost:8189/v1/chat/completions",
        headers={},
        payload={"messages": []},
        timeout=1,
        retries=3,
        prompt="build",
        extract_output=lambda _data: "",
        usage_from_response=_usage,
        clock=clock,
    )

    assert result.ok is False
    assert "500 Server Error" in str(result.error)
    assert calls == 1
    assert clock.sleep_calls == []


def test_http_429_retries_without_opening_circuit_breaker(monkeypatch) -> None:
    clock = MockClock()
    breaker = _CircuitBreakerProbe()
    calls = 0

    def _post(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Http429Response()

    monkeypatch.setenv("KERNELONE_LLM_RATE_LIMIT_MAX_RETRIES", "1")
    monkeypatch.setattr(provider_helpers, "_blocking_http_post", _post)

    result = invoke_with_retry(
        "http://localhost:8189/v1/chat/completions",
        headers={},
        payload={"messages": []},
        timeout=1,
        retries=0,
        prompt="build",
        extract_output=lambda _data: "",
        usage_from_response=_usage,
        circuit_breaker=breaker,
        clock=clock,
    )

    assert result.ok is False
    assert "429 Rate limited" in str(result.error)
    assert calls == 2
    assert breaker.before_calls == 2
    assert breaker.failures == 0
    assert len(clock.sleep_calls) == 1


def test_blocking_http_post_preserves_tuple_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        ok = True

    def _post(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _Response()

    monkeypatch.setattr(provider_helpers.requests, "post", _post)

    response = provider_helpers._blocking_http_post(
        "http://localhost:8189/v1/chat/completions",
        headers={},
        payload={"messages": []},
        timeout=(10.0, 422.0),
    )

    assert response.ok is True
    assert captured["timeout"] == (10.0, 422.0)


def test_blocking_http_get_preserves_tuple_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        ok = True

    def _get(*_args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return _Response()

    monkeypatch.setattr(provider_helpers.requests, "get", _get)

    response = provider_helpers._blocking_http_get(
        "http://localhost:8189/v1/models",
        headers={},
        timeout=(5.0, 10.0),
    )

    assert response.ok is True
    assert captured["timeout"] == (5.0, 10.0)
