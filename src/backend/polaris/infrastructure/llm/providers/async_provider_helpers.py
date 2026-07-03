"""Async provider helpers for LLM provider communication.

This module provides async versions of the core provider helper functions,
replacing sync ``requests`` calls with native ``httpx.AsyncClient`` I/O.

Key functions:
    - ``async_invoke_with_retry``: Async version of ``invoke_with_retry``
    - ``async_health_check_post``: Async version of ``health_check_post``
    - ``AsyncStreamSession``: Async streaming session using httpx

Migration guide:
    Replace ``from polaris.infrastructure.llm.providers.provider_helpers import ...``
    with ``from polaris.infrastructure.llm.providers.async_provider_helpers import ...``
    and use ``await`` for all function calls.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from polaris.kernelone.common.clock import ClockPort, RealClock
from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.llm.response_parser import LLMResponseParser
from polaris.kernelone.llm.types import (
    HealthResult,
    InvokeResult,
    Usage,
)

from .async_http_client import (
    AsyncCircuitBreaker,
    AsyncProviderHttpClient,
    CircuitOpenError,
    ProviderHttpClientError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocols for type-safe callbacks
# ---------------------------------------------------------------------------


class ExtractOutputFn(Protocol):
    """Protocol for extracting output text from provider response."""

    def __call__(self, data: dict[str, Any]) -> str: ...


class UsageFromResponseFn(Protocol):
    """Protocol for extracting usage from provider response."""

    def __call__(self, prompt: str, output: str, data: dict[str, Any]) -> Usage: ...


# ---------------------------------------------------------------------------
# Async invoke with retry
# ---------------------------------------------------------------------------


async def async_invoke_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
    retries: int,
    prompt: str,
    extract_output: ExtractOutputFn,
    usage_from_response: UsageFromResponseFn,
    *,
    circuit_breaker: AsyncCircuitBreaker | None = None,
    circuit_key: str | None = None,
    backoff_base_seconds: float = 0.5,
    backoff_max_seconds: float = 30.0,
    clock: ClockPort | None = None,
) -> InvokeResult:
    """POST *url* with JSON *payload*, retrying up to *retries* times on failure.

    Async version of ``provider_helpers.invoke_with_retry`` using httpx.

    Args:
        url: Endpoint URL.
        headers: HTTP headers.
        payload: JSON payload.
        timeout: Request timeout in seconds.
        retries: Maximum retry attempts.
        prompt: Original prompt (for usage estimation).
        extract_output: Callback to extract output from response JSON.
        usage_from_response: Callback to compute usage from response.
        circuit_breaker: Optional async circuit breaker.
        circuit_key: Key for circuit breaker registry.
        backoff_base_seconds: Base backoff delay.
        backoff_max_seconds: Maximum backoff delay.
        clock: Optional clock for testability.

    Returns:
        InvokeResult with success/failure details.
    """
    _clock: ClockPort = clock if clock is not None else RealClock()
    attempt = 0
    retries = max(0, int(retries))
    breaker = circuit_breaker or AsyncCircuitBreaker()

    start = _clock.time()
    overflow_heal_attempts = 0

    async with AsyncProviderHttpClient(
        timeout=float(timeout) if timeout > 0 else 30.0,
        circuit_breaker=breaker,
    ) as client:
        while True:
            try:
                await breaker.before_call()
            except CircuitOpenError as exc:
                usage = Usage.estimate(prompt, "")
                return InvokeResult(
                    ok=False,
                    output="",
                    latency_ms=int((_clock.time() - start) * 1000),
                    usage=usage,
                    error=str(exc),
                )

            try:
                result = await client._post_json_impl(url, headers, payload, timeout=float(timeout))

                if result.status_code >= 400:
                    error_body = result.text
                    logger.warning(
                        "[async-provider-helpers] HTTP %s from %s: %s",
                        result.status_code,
                        url,
                        error_body[:500] if error_body else "(empty)",
                    )

                    # Context overflow self-heal
                    if result.status_code == 400 and overflow_heal_attempts < 3:
                        healed = _shrink_max_tokens_for_context_overflow(payload, error_body)
                        if not healed and overflow_heal_attempts > 0 and "maximum context length" in (error_body or ""):
                            try:
                                current_max = int(payload.get("max_tokens") or 0)
                            except (TypeError, ValueError):
                                current_max = 0
                            if current_max > 128:
                                payload["max_tokens"] = max(64, current_max // 2)
                                logger.warning(
                                    "[async-provider-helpers] context overflow heal #%s: halving max_tokens -> %s",
                                    overflow_heal_attempts + 1,
                                    payload["max_tokens"],
                                )
                                healed = True
                        if healed:
                            overflow_heal_attempts += 1
                            continue

                    # Server errors are retryable
                    if 500 <= result.status_code < 600:
                        await breaker.on_failure()
                        latency_ms = int((_clock.time() - start) * 1000)
                        usage = Usage.estimate(prompt, "")
                        return InvokeResult(
                            ok=False,
                            output="",
                            latency_ms=latency_ms,
                            usage=usage,
                            error=(
                                f"{result.status_code} Server Error from {url}: "
                                f"{error_body[:500] if error_body else '(empty)'}"
                            ),
                        )

                    # Client errors are not retryable
                    result.raise_for_status()

                # Success path
                data = _parse_json(result.text)
                latency_ms = int((_clock.time() - start) * 1000)
                output = extract_output(data)
                finalized = LLMResponseParser.finalize_response(data, visible_text=output)
                usage = usage_from_response(prompt, finalized.output, data)
                await breaker.on_success()

                if not finalized.ok:
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=usage,
                        error=finalized.error,
                        raw=data,
                        thinking=finalized.thinking,
                    )

                return InvokeResult(
                    ok=True,
                    output=finalized.output,
                    latency_ms=latency_ms,
                    usage=usage,
                    raw=data,
                    thinking=finalized.thinking,
                )

            except (
                ProviderHttpClientError,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                await breaker.on_failure()
                attempt += 1
                if attempt > retries:
                    latency_ms = int((_clock.time() - start) * 1000)
                    usage = Usage.estimate(prompt, "")
                    return InvokeResult(
                        ok=False,
                        output="",
                        latency_ms=latency_ms,
                        usage=usage,
                        error=str(exc),
                    )

                delay = _build_backoff_seconds(
                    attempt=attempt,
                    base_delay_seconds=backoff_base_seconds,
                    max_delay_seconds=backoff_max_seconds,
                )
                await asyncio.sleep(delay)

            except (KeyboardInterrupt, SystemExit):
                raise


# ---------------------------------------------------------------------------
# Async health check
# ---------------------------------------------------------------------------


async def async_health_check_post(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> HealthResult:
    """POST-based async health check with standard error classification.

    Async version of ``provider_helpers.health_check_post`` using httpx.
    """
    async with AsyncProviderHttpClient(timeout=float(timeout)) as client:
        return await client.health_check(url, headers, payload)


# ---------------------------------------------------------------------------
# Async stream session
# ---------------------------------------------------------------------------


@dataclass
class AsyncStreamSession:
    """Async streaming session using httpx.

    Replaces the sync ``aiohttp`` based stream sessions.
    """

    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _response: httpx.Response | None = field(default=None, repr=False)

    async def __aenter__(self) -> AsyncStreamSession:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(DEFAULT_OPERATION_TIMEOUT_SECONDS),
            limits=httpx.Limits(max_connections=100),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._response is not None:
            await self._response.aclose()
            self._response = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def post_stream(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> None:
        """POST and start streaming response."""
        if self._client is None:
            raise RuntimeError("Session not initialized. Use 'async with session:' context manager.")
        payload_with_stream = {**payload, "stream": True}
        self._response = await self._client.send(
            self._client.build_request(
                "POST",
                url,
                headers=headers,
                json=payload_with_stream,
                timeout=httpx.Timeout(timeout) if timeout else None,
            ),
            stream=True,
        )

    async def aiter_lines(self) -> Any:
        """Iterate over response lines."""
        if self._response is None:
            raise RuntimeError("Stream not started")
        async for line in self._response.aiter_lines():
            yield line

    async def aiter_bytes(self) -> Any:
        """Iterate over response bytes."""
        if self._response is None:
            raise RuntimeError("Stream not started")
        async for chunk in self._response.aiter_bytes():
            yield chunk

    @property
    def status_code(self) -> int:
        if self._response is None:
            raise RuntimeError("Stream not started")
        return self._response.status_code


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def _parse_json(text: str) -> dict[str, Any]:
    """Parse JSON text, raising ValueError on failure."""
    import json

    return json.loads(text)


def _build_backoff_seconds(
    *,
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
) -> float:
    """Calculate exponential backoff with jitter."""
    import random

    delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


def _shrink_max_tokens_for_context_overflow(payload: dict[str, Any], error_body: str) -> bool:
    """Try to shrink max_tokens when context window is exceeded."""
    import re

    if not error_body:
        return False

    # Pattern 1: vLLM format "X output tokens...at least Y input tokens"
    match = re.search(
        r"maximum context length is (\d+) tokens.*?(\d+) output tokens.*?at least (\d+) input tokens",
        error_body,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        # Pattern 2: OpenAI format "your messages resulted in X tokens...reduce by Y"
        match = re.search(
            r"maximum context length is (\d+).*?your messages resulted in (\d+).*?Please reduce.*?by (\d+)",
            error_body,
            re.IGNORECASE,
        )
    if not match:
        return False

    window, reported_tokens, adjustment = (int(g) for g in match.groups())
    new_max = window - adjustment - 16
    try:
        current = int(payload.get("max_tokens") or 0)
    except (TypeError, ValueError):
        current = 0
    if new_max < 64 or (current and new_max >= current):
        return False

    payload["max_tokens"] = new_max
    logger.warning(
        "[async-provider-helpers] context overflow self-heal: window=%s tokens=%s adjustment=%s -> max_tokens=%s",
        window,
        reported_tokens,
        adjustment,
        new_max,
    )
    return True


__all__ = [
    "AsyncStreamSession",
    "async_health_check_post",
    "async_invoke_with_retry",
]
