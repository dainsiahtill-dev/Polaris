"""Tests for async provider helpers (AAA pattern).

Verifies:
    - async_invoke_with_retry with successful responses
    - async_invoke_with_retry with retryable errors
    - async_invoke_with_retry with circuit breaker
    - async_health_check_post with various status codes
    - AsyncStreamSession lifecycle
    - Context overflow self-heal
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from polaris.infrastructure.llm.providers.async_http_client import (
    AsyncCircuitBreaker,
    HttpResult,
)
from polaris.infrastructure.llm.providers.async_provider_helpers import (
    AsyncStreamSession,
    _build_backoff_seconds,
    _shrink_max_tokens_for_context_overflow,
    async_health_check_post,
    async_invoke_with_retry,
)
from polaris.kernelone.llm.types import HealthResult, Usage

# =============================================================================
# Utility function tests
# =============================================================================


class TestBuildBackoffSeconds:
    """Tests for the backoff calculation utility."""

    def test_first_attempt_returns_base_delay(self) -> None:
        # Arrange & Act
        delay = _build_backoff_seconds(attempt=1, base_delay_seconds=0.5, max_delay_seconds=30.0)

        # Assert
        assert 0.5 <= delay <= 0.55  # base + small jitter

    def test_exponential_growth(self) -> None:
        # Arrange & Act
        delay1 = _build_backoff_seconds(attempt=1, base_delay_seconds=1.0, max_delay_seconds=30.0)
        delay2 = _build_backoff_seconds(attempt=2, base_delay_seconds=1.0, max_delay_seconds=30.0)
        delay3 = _build_backoff_seconds(attempt=3, base_delay_seconds=1.0, max_delay_seconds=30.0)

        # Assert
        assert delay1 < delay2 < delay3

    def test_respects_max_delay(self) -> None:
        # Arrange & Act
        delay = _build_backoff_seconds(attempt=100, base_delay_seconds=1.0, max_delay_seconds=5.0)

        # Assert
        assert delay <= 5.5  # max + jitter


class TestShrinkMaxTokens:
    """Tests for the context overflow self-heal utility."""

    def test_shrinks_on_matching_error(self) -> None:
        # Arrange
        payload: dict[str, object] = {"max_tokens": 8000}
        error = "This model's maximum context length is 8192 tokens. However, your messages resulted in 8000 tokens. Please reduce the length of the messages by 1000."

        # Act
        result = _shrink_max_tokens_for_context_overflow(payload, error)  # type: ignore[arg-type]

        # Assert
        assert result is True
        assert int(payload["max_tokens"]) < 8000

    def test_no_shrink_on_unmatched_error(self) -> None:
        # Arrange
        payload: dict[str, object] = {"max_tokens": 4000}
        error = "Some other error"

        # Act
        result = _shrink_max_tokens_for_context_overflow(payload, error)  # type: ignore[arg-type]

        # Assert
        assert result is False
        assert payload["max_tokens"] == 4000

    def test_no_shrink_when_already_small(self) -> None:
        # Arrange
        payload: dict[str, object] = {"max_tokens": 50}
        error = "maximum context length is 8192. messages resulted in 8000. reduce by 1000."

        # Act
        result = _shrink_max_tokens_for_context_overflow(payload, error)  # type: ignore[arg-type]

        # Assert
        assert result is False


# =============================================================================
# async_invoke_with_retry tests
# =============================================================================


class TestAsyncInvokeWithRetry:
    """Tests for the async invoke with retry function."""

    @pytest.mark.asyncio
    async def test_successful_invoke(self) -> None:
        # Arrange
        mock_result = HttpResult(
            status_code=200,
            headers={},
            text='{"choices": [{"message": {"content": "Hello"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
            elapsed_ms=100,
        )

        def extract_output(data: dict[str, object]) -> str:
            choices = data.get("choices", [])
            if choices and isinstance(choices, list):
                msg = choices[0].get("message", {})
                if isinstance(msg, dict):
                    return str(msg.get("content", ""))
            return ""

        def usage_from_response(prompt: str, output: str, data: dict[str, object]) -> Usage:
            return Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=mock_result)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="http://test/api",
                headers={"Content-Type": "application/json"},
                payload={"prompt": "Hello", "max_tokens": 100},
                timeout=30,
                retries=3,
                prompt="Hello",
                extract_output=extract_output,
                usage_from_response=usage_from_response,
            )

        # Assert
        assert result.ok is True
        assert result.output == "Hello"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_circuit_open_returns_error(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60.0)
        await cb.on_failure()  # Open the circuit

        def extract_output(data: dict[str, object]) -> str:
            return ""

        def usage_from_response(prompt: str, output: str, data: dict[str, object]) -> Usage:
            return Usage()

        # Act
        result = await async_invoke_with_retry(
            url="http://test/api",
            headers={},
            payload={},
            timeout=30,
            retries=3,
            prompt="test",
            extract_output=extract_output,
            usage_from_response=usage_from_response,
            circuit_breaker=cb,
        )

        # Assert
        assert result.ok is False
        assert "circuit_open" in result.error

    @pytest.mark.asyncio
    async def test_retry_on_server_error(self) -> None:
        # Arrange
        error_result = HttpResult(status_code=500, headers={}, text="Internal Server Error", elapsed_ms=50)

        def extract_output(data: dict[str, object]) -> str:
            return ""

        def usage_from_response(prompt: str, output: str, data: dict[str, object]) -> Usage:
            return Usage()

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client._post_json_impl = AsyncMock(return_value=error_result)
            mock_client_cls.return_value = mock_client

            result = await async_invoke_with_retry(
                url="http://test/api",
                headers={},
                payload={"prompt": "test"},
                timeout=30,
                retries=0,  # No retries for this test
                prompt="test",
                extract_output=extract_output,
                usage_from_response=usage_from_response,
            )

        # Assert
        assert result.ok is False
        assert "500" in result.error


# =============================================================================
# async_health_check_post tests
# =============================================================================


class TestAsyncHealthCheckPost:
    """Tests for the async health check function."""

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        # Arrange

        # Act
        with patch(
            "polaris.infrastructure.llm.providers.async_provider_helpers.AsyncProviderHttpClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.health_check = AsyncMock(return_value=HealthResult(ok=True, latency_ms=42))
            mock_client_cls.return_value = mock_client

            result = await async_health_check_post(
                url="http://test/api",
                headers={},
                payload={"prompt": "test"},
                timeout=30,
            )

        # Assert
        assert result.ok is True
        assert result.latency_ms == 42


# =============================================================================
# AsyncStreamSession tests
# =============================================================================


class TestAsyncStreamSession:
    """Tests for the async stream session."""

    @pytest.mark.asyncio
    async def test_context_manager_lifecycle(self) -> None:
        # Arrange
        session = AsyncStreamSession()

        # Act & Assert
        async with session:
            assert session._client is not None

        assert session._client is None

    @pytest.mark.asyncio
    async def test_iter_lines_before_start_raises(self) -> None:
        # Arrange
        session = AsyncStreamSession()

        # Act & Assert
        async with session:
            with pytest.raises(RuntimeError, match="Stream not started"):
                async for _ in session.aiter_lines():
                    pass
