import requests
from polaris.infrastructure.llm.providers.provider_helpers import (
    CircuitBreaker,
    close_stream_sessions,
    get_stream_session,
    invoke_with_retry,
)
from polaris.kernelone.common.clock import MockClock


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
    """Verify retry uses exponential backoff with increasing delays.

    Uses MockClock injection instead of patching time.sleep.
    """
    attempts = {"count": 0}
    mock = MockClock()

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

    result = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=3,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=_usage_from_response,
        backoff_base_seconds=0.1,
        backoff_max_seconds=1.0,
        clock=mock,
    )

    assert result.ok is True
    assert attempts["count"] == 3
    # 2 retries: first backoff ~0.1, second backoff ~0.2 (exponential)
    sleep_calls = mock.sleep_calls
    assert len(sleep_calls) == 2, f"Expected 2 sleep calls, got {sleep_calls}"
    assert sleep_calls[1] > sleep_calls[0], f"Expected increasing backoff, got {sleep_calls}"


def test_invoke_with_retry_opens_circuit_after_threshold(monkeypatch):
    """Verify circuit opens after failure threshold is reached.

    Uses MockClock injection instead of patching time.sleep.
    """
    monkeypatch.setattr(
        "polaris.infrastructure.llm.providers.provider_helpers.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.exceptions.ConnectionError("boom")),
    )

    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout_seconds=60)
    mock = MockClock()

    first = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=0,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=_usage_from_response,
        circuit_breaker=breaker,
        clock=mock,
    )
    second = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=0,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=_usage_from_response,
        circuit_breaker=breaker,
        clock=mock,
    )
    third = invoke_with_retry(
        "https://example.com/v1/chat",
        {},
        {"model": "demo"},
        timeout=5,
        retries=0,
        prompt="hello",
        extract_output=_extract_output,
        usage_from_response=_usage_from_response,
        circuit_breaker=breaker,
        clock=mock,
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
