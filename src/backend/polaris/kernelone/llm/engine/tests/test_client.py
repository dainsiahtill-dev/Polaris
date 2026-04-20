"""Tests for ResilientLLMClient and MultiProviderFallbackManager.

Covers:
- Single provider success
- Provider switch on retryable failure
- Fallback response when all providers exhausted
- Semantic error fast-fail (no provider switch)
- invoke_stream() provider fallback on ERROR event
- invoke_stream() non-stream fallback provider
- Circuit breaker integration
- invoke_with_metadata() returns full chain result
"""

from __future__ import annotations

import time as time_module
from typing import TYPE_CHECKING

import pytest
from polaris.kernelone.errors import ErrorCategory
from polaris.kernelone.llm.engine.client import (
    FallbackChainResult,
    MultiProviderFallbackManager,
    ProviderConfig,
    ResilientLLMClient,
)
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, AIStreamEvent, StreamEventType
from polaris.kernelone.llm.engine.resilience import (
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    RetryConfig,
    TimeoutConfig,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# ---------------------------------------------------------------------------
# Mock Providers
# ---------------------------------------------------------------------------


class MockProvider:
    """Mock LLM provider for testing."""

    def __init__(
        self,
        name: str,
        responses: list[AIResponse],
        stream_responses: list[list[AIStreamEvent]] | None = None,
    ) -> None:
        self.name = name
        self._responses = responses
        self._stream_responses = stream_responses or []
        self.call_count = 0
        self.call_count_stream = 0

    async def invoke(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        idx = min(self.call_count - 1, len(self._responses) - 1)
        return self._responses[idx]

    async def invoke_stream(self, request: AIRequest) -> AsyncGenerator[AIStreamEvent, None]:
        self.call_count_stream += 1
        responses = self._stream_responses
        if not responses:
            yield AIStreamEvent.error_event("stream not implemented")
            return
        idx = min(self.call_count_stream - 1, len(responses) - 1)
        for event in responses[idx]:
            yield event


def make_success_response(provider: str = "test-model") -> AIResponse:
    return AIResponse(
        ok=True,
        output="success output",
        model=provider,
        provider_id=provider,
    )


def make_failure_response(
    error: str,
    category: ErrorCategory = ErrorCategory.PROVIDER_ERROR,
) -> AIResponse:
    return AIResponse(
        ok=False,
        output="",
        error=error,
        error_category=category,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_timeout() -> TimeoutConfig:
    return TimeoutConfig(request_timeout=5.0, total_timeout=30.0)


@pytest.fixture
def no_retry_config() -> RetryConfig:
    """RetryConfig with max_attempts=1 so retries don't complicate tests."""
    return RetryConfig(max_attempts=1, base_delay=0.01)


@pytest.fixture
def sample_request() -> AIRequest:
    return AIRequest(
        task_type="dialogue",
        role="test",
        input="hello",
    )


# ---------------------------------------------------------------------------
# ResilientLLMClient - single provider success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_provider_success(sample_request: AIRequest, no_retry_config: RetryConfig) -> None:
    """A single configured provider must return its response directly via ResilientLLMClient."""
    provider = MockProvider("primary", [make_success_response("primary")])
    client = ResilientLLMClient(
        providers=[(provider, ProviderConfig(provider_name="primary", priority=0))],
        default_timeout_config=TimeoutConfig(),
        default_retry_config=no_retry_config,
    )

    # ResilientLLMClient.invoke() returns AIResponse directly
    result = await client.invoke(sample_request)

    assert result.ok is True
    assert result.output == "success output"
    assert result.provider_id == "primary"
    assert provider.call_count == 1


# ---------------------------------------------------------------------------
# MultiProviderFallbackManager - switch provider on retryable failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switches_provider_on_retryable_failure(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """On retryable (TIMEOUT) failure, must try next provider."""
    primary_provider = MockProvider(
        "primary",
        [make_failure_response("timeout", ErrorCategory.TIMEOUT)],
    )
    fallback_provider = MockProvider(
        "fallback",
        [make_success_response("fallback")],
    )

    manager = MultiProviderFallbackManager(
        providers=[
            (primary_provider, ProviderConfig(provider_name="primary", priority=0)),
            (fallback_provider, ProviderConfig(provider_name="fallback", priority=1)),
        ],
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
    )

    # manager.invoke() returns FallbackChainResult
    result = await manager.invoke(sample_request)

    assert result.response.ok is True
    assert result.response.provider_id == "fallback"
    assert "primary" in result.provider_tried
    assert "fallback" in result.provider_tried
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# Semantic error fast-fail (no provider switch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_error_fast_fails_no_switch(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """INVALID_RESPONSE must fast-fail without trying next provider."""
    primary_provider = MockProvider(
        "primary",
        [make_failure_response("invalid response", ErrorCategory.INVALID_RESPONSE)],
    )
    fallback_provider = MockProvider(
        "fallback",
        [make_success_response("fallback")],
    )

    manager = MultiProviderFallbackManager(
        providers=[
            (primary_provider, ProviderConfig(provider_name="primary", priority=0)),
            (fallback_provider, ProviderConfig(provider_name="fallback", priority=1)),
        ],
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
    )

    result = await manager.invoke(sample_request)

    assert result.response.ok is False
    assert result.response.error_category == ErrorCategory.INVALID_RESPONSE
    # Should NOT have tried fallback
    assert result.provider_tried == ["primary"]
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# All providers exhausted - fallback response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_providers_exhausted_returns_fallback(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """When all providers fail, must return fallback response."""
    providers = [
        (
            MockProvider(
                "p1",
                [make_failure_response("error1", ErrorCategory.TIMEOUT)],
            ),
            ProviderConfig(provider_name="p1", priority=0),
        ),
        (
            MockProvider(
                "p2",
                [make_failure_response("error2", ErrorCategory.NETWORK_ERROR)],
            ),
            ProviderConfig(provider_name="p2", priority=1),
        ),
    ]

    manager = MultiProviderFallbackManager(
        providers=providers,
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
    )

    result = await manager.invoke(sample_request)

    assert result.response.ok is False
    assert result.fallback_used is True
    assert result.provider_tried == ["p1", "p2"]
    assert "p1" in result.provider_tried
    assert "p2" in result.provider_tried
    assert result.circuit_open_count == 0


# ---------------------------------------------------------------------------
# invoke_stream() switches provider on ERROR event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_switches_provider_on_error(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """ERROR stream event must trigger switch to next provider."""
    primary_events = [
        AIStreamEvent.chunk_event("partial"),
        AIStreamEvent.error_event("rate limit"),
    ]
    fallback_events = [
        AIStreamEvent.chunk_event("fallback output"),
        AIStreamEvent.complete({}),
    ]

    primary_provider = MockProvider(
        "primary",
        [make_success_response()],
        stream_responses=[primary_events],
    )
    fallback_provider = MockProvider(
        "fallback",
        [make_success_response()],
        stream_responses=[fallback_events],
    )

    manager = MultiProviderFallbackManager(
        providers=[
            (primary_provider, ProviderConfig(provider_name="primary", priority=0)),
            (fallback_provider, ProviderConfig(provider_name="fallback", priority=1)),
        ],
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
    )

    events: list[tuple[AIStreamEvent, str | None]] = []
    async for event, provider_name in manager.invoke_stream(sample_request):
        events.append((event, provider_name))

    provider_names = [name for _, name in events if name is not None]
    assert "primary" in provider_names
    assert "fallback" in provider_names

    # Last event should be the fallback complete
    _last_event, last_provider = events[-1]
    assert last_provider == "fallback"


# ---------------------------------------------------------------------------
# invoke_stream() non-stream fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_non_stream_provider_fallback(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """Provider without invoke_stream must fall back to invoke + CHUNK + COMPLETE."""

    class NonStreamProvider:
        name = "nonstream"
        invoke_called = False

        async def invoke(self, request: AIRequest) -> AIResponse:
            NonStreamProvider.invoke_called = True
            return make_success_response("nonstream")

        # Omit invoke_stream entirely to trigger AttributeError in
        # _invoke_provider_stream, which then falls back to non-stream invoke().

    provider = NonStreamProvider()

    manager = MultiProviderFallbackManager(
        providers=[(provider, ProviderConfig(provider_name="nonstream", priority=0))],
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
    )

    events: list[tuple[AIStreamEvent, str | None]] = []
    async for event, provider_name in manager.invoke_stream(sample_request):
        events.append((event, provider_name))

    assert NonStreamProvider.invoke_called is True
    event_types = [e.type for e, _ in events]
    assert StreamEventType.CHUNK in event_types
    assert StreamEventType.COMPLETE in event_types


# ---------------------------------------------------------------------------
# ResilientLLMClient.invoke_with_metadata()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_with_metadata_returns_chain_result(
    sample_request: AIRequest,
    no_retry_config: RetryConfig,
) -> None:
    """invoke_with_metadata() must return FallbackChainResult with all metadata."""
    provider = MockProvider("primary", [make_success_response("primary")])

    client = ResilientLLMClient(
        providers=[(provider, ProviderConfig(provider_name="primary", priority=0))],
        default_timeout_config=TimeoutConfig(),
        default_retry_config=no_retry_config,
    )

    result = await client.invoke_with_metadata(sample_request)

    assert isinstance(result, FallbackChainResult)
    assert result.response.ok is True
    assert result.response.provider_id == "primary"
    assert result.provider_tried == ["primary"]
    assert result.fallback_used is False
    assert result.circuit_open_count == 0


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_open_skips_provider(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """Open circuit breaker must cause provider to be skipped."""
    registry = CircuitBreakerRegistry()

    # Pre-open the circuit for "primary" by directly setting state
    # Also set last_failure_time to now so _should_attempt_reset returns False
    cb = await registry.get_or_create(
        "primary",
        CircuitBreakerConfig(failure_threshold=1),
    )
    cb.state = CircuitState.OPEN
    cb.last_failure_time = time_module.monotonic()

    primary_provider = MockProvider(
        "primary",
        [make_success_response("primary")],
    )
    fallback_provider = MockProvider(
        "fallback",
        [make_success_response("fallback")],
    )

    manager = MultiProviderFallbackManager(
        providers=[
            (primary_provider, ProviderConfig(provider_name="primary", priority=0)),
            (fallback_provider, ProviderConfig(provider_name="fallback", priority=1)),
        ],
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
        circuit_breaker_registry=registry,
    )

    result = await manager.invoke(sample_request)

    assert result.response.ok is True
    assert result.response.provider_id == "fallback"
    assert result.circuit_open_count == 1
    assert primary_provider.call_count == 0  # Was skipped due to open breaker


# ---------------------------------------------------------------------------
# ProviderConfig enabled flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_provider_is_skipped(
    sample_request: AIRequest,
    no_retry_config: RetryConfig,
) -> None:
    """Disabled providers must be skipped in the fallback chain."""
    primary_provider = MockProvider(
        "primary",
        [make_failure_response("error", ErrorCategory.TIMEOUT)],
    )
    fallback_provider = MockProvider(
        "fallback",
        [make_success_response("fallback")],
    )

    client = ResilientLLMClient(
        providers=[
            (primary_provider, ProviderConfig(provider_name="primary", priority=0, enabled=False)),
            (fallback_provider, ProviderConfig(provider_name="fallback", priority=1)),
        ],
        default_timeout_config=TimeoutConfig(),
        default_retry_config=no_retry_config,
    )

    # ResilientLLMClient.invoke() returns AIResponse directly
    result = await client.invoke(sample_request)

    assert result.ok is True
    assert result.provider_id == "fallback"
    assert primary_provider.call_count == 0  # Was disabled, never called


# ---------------------------------------------------------------------------
# FallbackChainResult dataclass fields
# ---------------------------------------------------------------------------


def test_fallback_chain_result_defaults() -> None:
    """FallbackChainResult must have correct default values."""
    result = FallbackChainResult(response=make_success_response())
    assert result.provider_tried == []
    assert result.circuit_open_count == 0
    assert result.retry_count == 0
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# Provider priority ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_providers_tried_in_priority_order(
    sample_request: AIRequest,
    default_timeout: TimeoutConfig,
    no_retry_config: RetryConfig,
) -> None:
    """Providers must be tried in ascending priority order."""
    call_order: list[str] = []

    class TrackingProvider:
        name: str

        def __init__(self, name: str, responses: list[AIResponse]) -> None:
            self.name = name
            self.responses = responses
            self.call_count = 0

        async def invoke(self, request: AIRequest) -> AIResponse:
            call_order.append(self.name)
            self.call_count += 1
            idx = min(self.call_count - 1, len(self.responses) - 1)
            return self.responses[idx]

        async def invoke_stream(self, request: AIRequest) -> AsyncGenerator[AIStreamEvent, None]:
            raise NotImplementedError

    p0 = TrackingProvider("p0", [make_failure_response("e", ErrorCategory.TIMEOUT)])
    p2 = TrackingProvider("p2", [make_failure_response("e", ErrorCategory.TIMEOUT)])
    p1 = TrackingProvider("p1", [make_success_response()])

    manager = MultiProviderFallbackManager(
        providers=[
            (p2, ProviderConfig(provider_name="p2", priority=2)),
            (p0, ProviderConfig(provider_name="p0", priority=0)),
            (p1, ProviderConfig(provider_name="p1", priority=1)),
        ],
        default_timeout_config=default_timeout,
        default_retry_config=no_retry_config,
    )

    result = await manager.invoke(sample_request)

    assert result.response.ok is True
    # Must be tried in priority order: p0 first, then p1
    assert call_order[0] == "p0"
    assert call_order[1] == "p1"
