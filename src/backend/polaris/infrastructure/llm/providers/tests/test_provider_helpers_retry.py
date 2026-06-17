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
