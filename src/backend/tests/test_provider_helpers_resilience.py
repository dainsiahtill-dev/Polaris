import requests
from polaris.infrastructure.llm.providers.provider_helpers import (
    CircuitBreaker,
    close_stream_sessions,
    get_stream_session,
    invoke_with_retry,
)


def _extract_output(payload):
    return str(payload.get("output") or "")


def _usage_from_response(prompt, output, payload):
    del payload
    return type(
        "UsageLike",
        (),
        {"prompt_tokens": len(prompt), "completion_tokens": len(output), "total_tokens": len(prompt) + len(output)},
    )()


def test_invoke_with_retry_uses_exponential_backoff(monkeypatch):
    attempts = {"count": 0}
    sleep_calls = []

    def _fake_post(*args, **kwargs):
        del args, kwargs
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.exceptions.Timeout("timeout")

        class _Resp:
            status_code = 200

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"output": "ok"}

        return _Resp()

    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.requests.post", _fake_post)
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.time.sleep",
        lambda delay: sleep_calls.append(delay),
    )

    result = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=3,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=lambda prompt, output, payload: type(
            "UsageLike",
            (),
            {
                "prompt_tokens": len(prompt),
                "completion_tokens": len(output),
                "total_tokens": len(prompt) + len(output),
            },
        )(),
        backoff_base_seconds=0.1,
        backoff_max_seconds=1.0,
    )

    assert result.ok is True
    assert attempts["count"] == 3
    assert len(sleep_calls) == 2
    assert sleep_calls[1] > sleep_calls[0]


def test_invoke_with_retry_opens_circuit_after_threshold(monkeypatch):
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.exceptions.ConnectionError("boom")),
    )
    monkeypatch.setattr("polaris.infrastructure.llm.providers.provider_helpers.time.sleep", lambda delay: None)

    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=60)

    first = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=0,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=lambda prompt, output, payload: type(
            "UsageLike",
            (),
            {
                "prompt_tokens": len(prompt),
                "completion_tokens": len(output),
                "total_tokens": len(prompt) + len(output),
            },
        )(),
        circuit_breaker=breaker,
    )
    second = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=0,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=lambda prompt, output, payload: type(
            "UsageLike",
            (),
            {
                "prompt_tokens": len(prompt),
                "completion_tokens": len(output),
                "total_tokens": len(prompt) + len(output),
            },
        )(),
        circuit_breaker=breaker,
    )
    third = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=0,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=lambda prompt, output, payload: type(
            "UsageLike",
            (),
            {
                "prompt_tokens": len(prompt),
                "completion_tokens": len(output),
                "total_tokens": len(prompt) + len(output),
            },
        )(),
        circuit_breaker=breaker,
    )

    assert first.ok is False
    assert second.ok is False
    assert third.ok is False
    assert "circuit_open" in str(third.error)


def test_stream_session_reuse_and_cleanup():
    async def _run():
        first = await get_stream_session("test_provider", timeout_seconds=5)
        second = await get_stream_session("test_provider", timeout_seconds=5)
        assert first is second
        await close_stream_sessions("test_provider")
        assert first.closed is True

    import asyncio

    asyncio.run(_run())
