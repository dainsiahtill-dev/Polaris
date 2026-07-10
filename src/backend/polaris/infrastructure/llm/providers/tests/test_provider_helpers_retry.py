"""Provider helper retry behavior."""

from __future__ import annotations

import requests
from polaris.infrastructure.llm.providers import provider_helpers
from polaris.infrastructure.llm.providers.provider_helpers import invoke_with_retry
from polaris.kernelone.common.clock import MockClock
from polaris.kernelone.llm.engine.contracts import Usage


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


class _CircuitBreakerProbe:
    def __init__(self) -> None:
        self.before_calls = 0
        self.failures = 0

    def before_call(self) -> None:
        self.before_calls += 1

    def on_failure(self) -> None:
        self.failures += 1

    def on_success(self) -> None:
        pass


def _usage(_prompt: str, _output: str, _data: dict[str, object]) -> Usage:
    return Usage(input_tokens=0, output_tokens=0, total_tokens=0)


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
