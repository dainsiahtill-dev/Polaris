"""Tests for AsyncProviderHttpClient (AAA pattern).

Verifies:
    - Async HTTP POST/GET with proper timeout handling
    - Circuit breaker state transitions (closed → open → half_open → closed)
    - HealthResult mapping for various HTTP status codes
    - Context manager lifecycle (aenter/aexit)
    - Error propagation for connection failures
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.infrastructure.llm.providers.async_http_client import (
    AsyncCircuitBreaker,
    AsyncProviderHttpClient,
    CircuitOpenError,
    HttpResult,
    ProviderHttpClientError,
)

# =============================================================================
# AsyncCircuitBreaker tests
# =============================================================================


class TestAsyncCircuitBreaker:
    """Tests for the async circuit breaker state machine."""

    @pytest.mark.asyncio
    async def test_initial_state_is_closed(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=3)

        # Act
        state = cb.state

        # Assert
        assert state == "closed"

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=3)

        # Act
        for _ in range(3):
            await cb.on_failure()

        # Assert
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_open_circuit_raises_circuit_open_error(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60.0)
        await cb.on_failure()

        # Act & Assert
        with pytest.raises(CircuitOpenError, match="circuit_open"):
            await cb.before_call()

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=5)
        await cb.on_failure()
        await cb.on_failure()

        # Act
        await cb.on_success()

        # Assert
        assert cb.state == "closed"
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_after_recovery_timeout(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.0)
        await cb.on_failure()
        assert cb.state == "open"

        # Act
        await cb.before_call()

        # Assert
        assert cb.state == "half_open"

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=0.0)
        await cb.on_failure()
        await cb.before_call()  # → half_open

        # Act
        await cb.on_failure()

        # Assert
        assert cb.state == "open"


# =============================================================================
# HttpResult tests
# =============================================================================


class TestHttpResult:
    """Tests for the HttpResult DTO."""

    def test_raise_for_status_on_success(self) -> None:
        # Arrange
        result = HttpResult(status_code=200, headers={}, text="OK", elapsed_ms=10)

        # Act & Assert
        result.raise_for_status()  # Should not raise

    def test_raise_for_status_on_client_error(self) -> None:
        # Arrange
        result = HttpResult(status_code=400, headers={}, text="Bad Request", elapsed_ms=10)

        # Act & Assert
        with pytest.raises(ProviderHttpClientError) as exc_info:
            result.raise_for_status()
        assert exc_info.value.status_code == 400

    def test_raise_for_status_on_server_error(self) -> None:
        # Arrange
        result = HttpResult(status_code=500, headers={}, text="Internal Server Error", elapsed_ms=10)

        # Act & Assert
        with pytest.raises(ProviderHttpClientError) as exc_info:
            result.raise_for_status()
        assert exc_info.value.status_code == 500

    def test_frozen_immutable(self) -> None:
        # Arrange
        result = HttpResult(status_code=200, headers={}, text="OK", elapsed_ms=10)

        # Act & Assert
        with pytest.raises(AttributeError):
            result.status_code = 201  # type: ignore[misc]


# =============================================================================
# AsyncProviderHttpClient tests
# =============================================================================


class TestAsyncProviderHttpClient:
    """Tests for the async HTTP client."""

    @pytest.mark.asyncio
    async def test_context_manager_lifecycle(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient(timeout=5.0)

        # Act
        async with client:
            assert client._client is not None

        # Assert
        assert client._client is None

    @pytest.mark.asyncio
    async def test_post_json_without_context_raises(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient()

        # Act & Assert
        with pytest.raises(RuntimeError, match="not initialized"):
            await client.post_json("http://test", {}, {"key": "value"})

    @pytest.mark.asyncio
    async def test_health_check_401_returns_auth_error(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient(timeout=5.0)

        async with client:
            with patch.object(client, "_post_json_impl", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = HttpResult(status_code=401, headers={}, text="Unauthorized", elapsed_ms=5)

                # Act
                result = await client.health_check("http://test/api", {}, {"prompt": "test"})

        # Assert
        assert result.ok is False
        assert "Authentication failed" in result.error

    @pytest.mark.asyncio
    async def test_health_check_429_returns_rate_limit_error(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient(timeout=5.0)

        async with client:
            with patch.object(client, "_post_json_impl", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = HttpResult(
                    status_code=429,
                    headers={"retry-after": "30"},
                    text="Too Many Requests",
                    elapsed_ms=5,
                )

                # Act
                result = await client.health_check("http://test/api", {}, {"prompt": "test"})

        # Assert
        assert result.ok is False
        assert "429" in result.error
        assert "30" in result.error

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient(timeout=5.0)

        async with client:
            with patch.object(client, "_post_json_impl", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = HttpResult(status_code=200, headers={}, text="OK", elapsed_ms=42)

                # Act
                result = await client.health_check("http://test/api", {}, {"prompt": "test"})

        # Assert
        assert result.ok is True
        assert result.latency_ms == 42

    @pytest.mark.asyncio
    async def test_health_check_circuit_open(self) -> None:
        # Arrange
        cb = AsyncCircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60.0)
        await cb.on_failure()
        client = AsyncProviderHttpClient(timeout=5.0, circuit_breaker=cb)

        # Act
        result = await client.health_check("http://test/api", {}, {"prompt": "test"})

        # Assert
        assert result.ok is False
        assert "circuit_open" in result.error

    @pytest.mark.asyncio
    async def test_post_json_returns_http_result(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient(timeout=5.0)

        async with client:
            with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.headers = {"content-type": "application/json"}
                mock_response.text = '{"ok": true}'
                mock_post.return_value = mock_response

                # Act
                result = await client.post_json(
                    "http://test/api",
                    {"Content-Type": "application/json"},
                    {"prompt": "hello"},
                )

        # Assert
        assert result.status_code == 200
        assert result.text == '{"ok": true}'
        assert result.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_get_json_returns_http_result(self) -> None:
        # Arrange
        client = AsyncProviderHttpClient(timeout=5.0)

        async with client:
            with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.headers = {}
                mock_response.text = "[]"
                mock_get.return_value = mock_response

                # Act
                result = await client.get_json("http://test/models", {"Authorization": "Bearer key"})

        # Assert
        assert result.status_code == 200
        assert result.text == "[]"
