"""Resilient LLM Client - Multi-Provider Fallback with Circuit Breaker Protection.

Architecture:
    ResilientLLMClient
        ├── MultiProviderFallbackManager  (fallback chain orchestration)
        │   ├── CircuitBreakerRegistry     (per-provider breakers)
        │   └── ResilienceManager          (retry + timeout per provider)
        └── invoke() / invoke_stream()     (public API)

Design principles:
    - Each provider has its own CircuitBreaker instance, isolated from other providers.
    - Providers are tried in priority order (lower priority number = higher precedence).
    - Non-retryable errors (INVALID_RESPONSE, JSON_PARSE, CONFIG_ERROR) fast-fail
      the entire chain; retryable errors trigger provider switch with backoff.
    - Fallback response is returned only after all providers are exhausted.
    - No global state; all components are injected or created in __init__.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.errors import ErrorCategory

from .contracts import AIRequest, AIResponse, AIStreamEvent, StreamEventType
from .resilience import (
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitBreakerRegistry,
    ResilienceManager,
    RetryConfig,
    TimeoutConfig,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------


class LLMProvider(Protocol):
    """Protocol for LLM providers usable by ResilientLLMClient.

    Providers must implement this protocol to be usable with the fallback chain.
    The BaseProvider class from kernelone.llm.providers.base_provider satisfies this.
    """

    name: str

    async def invoke(self, request: AIRequest) -> AIResponse:
        """Synchronous-style invoke wrapped as async."""
        ...

    async def invoke_stream(self, request: AIRequest) -> AsyncGenerator[AIStreamEvent, None]:
        """Stream invoke, yields AIStreamEvent objects."""
        ...


# ---------------------------------------------------------------------------
# Provider Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for a single provider in the fallback chain.

    Attributes:
        provider_name: Unique identifier for the provider.
        priority: Lower number = higher priority. Providers are tried in ascending order.
        timeout_config: Per-provider timeout settings.
        retry_config: Per-provider retry settings.
        circuit_breaker_config: Per-provider circuit breaker settings.
        enabled: Whether this provider is active in the fallback chain.
    """

    provider_name: str
    priority: int = 0
    timeout_config: TimeoutConfig | None = None
    retry_config: RetryConfig | None = None
    circuit_breaker_config: CircuitBreakerConfig | None = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# Fallback Chain Result
# ---------------------------------------------------------------------------


@dataclass
class FallbackChainResult:
    """Result of a fallback chain execution.

    Attributes:
        response: The final AIResponse (success or fallback).
        provider_tried: List of provider names that were attempted.
        circuit_open_count: Number of providers that had open circuit breakers.
        retry_count: Total retry attempts across all providers.
        fallback_used: True if fallback response was returned.
    """

    response: AIResponse
    provider_tried: list[str] = field(default_factory=list)
    circuit_open_count: int = 0
    retry_count: int = 0
    fallback_used: bool = False


# ---------------------------------------------------------------------------
# Multi-Provider Fallback Manager
# ---------------------------------------------------------------------------


class MultiProviderFallbackManager:
    """Manages fallback chain across multiple LLM providers.

    Each provider is protected by its own CircuitBreaker and uses a
    ResilienceManager for retry and timeout handling.
    """

    def __init__(
        self,
        providers: list[tuple[LLMProvider, ProviderConfig]],
        default_timeout_config: TimeoutConfig | None = None,
        default_retry_config: RetryConfig | None = None,
        circuit_breaker_registry: CircuitBreakerRegistry | None = None,
    ) -> None:
        # Sort by priority (ascending)
        self._sorted_providers: list[tuple[LLMProvider, ProviderConfig]] = sorted(
            providers, key=lambda x: x[1].priority
        )
        self._default_timeout = default_timeout_config or TimeoutConfig()
        self._default_retry = default_retry_config or RetryConfig()
        self._cb_registry = circuit_breaker_registry or CircuitBreakerRegistry()

    def _get_resilience_manager(
        self,
        provider: LLMProvider,
        config: ProviderConfig,
    ) -> ResilienceManager:
        """Create a ResilienceManager for a specific provider."""
        return ResilienceManager(
            timeout_config=config.timeout_config or self._default_timeout,
            retry_config=config.retry_config or self._default_retry,
        )

    async def _get_circuit_breaker(
        self,
        provider: LLMProvider,
        config: ProviderConfig,
    ) -> Any:
        """Get or create a circuit breaker for a provider."""
        return await self._cb_registry.get_or_create(
            provider.name,
            config.circuit_breaker_config,
        )

    def _classify_and_decide(
        self,
        response: AIResponse,
    ) -> tuple[bool, ErrorCategory]:
        """Classify a failed response and decide if fallback is possible.

        Returns:
            (should_continue_fallback, error_category)
            - should_continue_fallback=True: try next provider
            - should_continue_fallback=False: return current response (fast-fail)
        """
        if response.ok:
            return False, ErrorCategory.UNKNOWN  # No error, don't fallback

        category = response.error_category or ErrorCategory.UNKNOWN

        # Semantic layer errors: fast-fail, don't try other providers
        if category in (
            ErrorCategory.INVALID_RESPONSE,
            ErrorCategory.JSON_PARSE,
            ErrorCategory.CONFIG_ERROR,
        ):
            logger.debug("Semantic error %s - fast-fail, no fallback", category.value)
            return False, category

        # Transport layer errors: try next provider
        return True, category

    async def invoke(
        self,
        request: AIRequest,
    ) -> FallbackChainResult:
        """Execute LLM request with provider fallback.

        Tries providers in priority order. For each provider:
        1. Check circuit breaker (fast-fail if OPEN)
        2. Execute with ResilienceManager (retry + timeout)
        3. On success: return immediately
        4. On retryable failure: try next provider
        5. On non-retryable failure: return immediately
        6. After all providers fail: return fallback response
        """
        provider_tried: list[str] = []
        circuit_open_count = 0
        total_retry_count = 0
        last_response: AIResponse | None = None

        for provider, config in self._sorted_providers:
            if not config.enabled:
                continue

            provider_tried.append(provider.name)
            breaker = await self._get_circuit_breaker(provider, config)
            resilience = self._get_resilience_manager(provider, config)

            try:
                response = await breaker.call(
                    resilience.execute_with_resilience,
                    lambda p=provider, req=request: p.invoke(req),
                    operation_name=f"llm_invoke.{provider.name}",
                )
            except CircuitBreakerOpenError:
                circuit_open_count += 1
                logger.debug(
                    "Circuit breaker open for %s, trying next provider",
                    provider.name,
                )
                continue

            # Track retry count from platform metadata
            total_retry_count += getattr(response, "platform_retry_count", 0)

            should_continue, _ = self._classify_and_decide(response)
            if not should_continue:
                return FallbackChainResult(
                    response=response,
                    provider_tried=provider_tried,
                    circuit_open_count=circuit_open_count,
                    retry_count=total_retry_count,
                    fallback_used=False,
                )

            last_response = response
            logger.debug(
                "Provider %s failed (retryable), trying next provider",
                provider.name,
            )

        # All providers exhausted - return fallback
        fallback_response = self._create_fallback_response(request, last_response)
        return FallbackChainResult(
            response=fallback_response,
            provider_tried=provider_tried,
            circuit_open_count=circuit_open_count,
            retry_count=total_retry_count,
            fallback_used=True,
        )

    async def invoke_stream(
        self,
        request: AIRequest,
    ) -> AsyncGenerator[tuple[AIStreamEvent, str | None], None]:
        """Execute streaming LLM request with provider fallback.

        Tries providers in priority order. For each provider:
        1. Check circuit breaker (fast-fail if OPEN, skip to next)
        2. Invoke the provider's stream
        3. Yield events until ERROR or COMPLETE
        4. On ERROR: stop this provider, try next
        5. On COMPLETE: yield and return
        6. After all providers exhausted: yield a fallback ERROR event

        Yields:
            Tuples of (event, provider_name). Provider name is None for
            fallback/error events indicating no specific provider.

        Note:
            Once chunks from one provider have been yielded to the caller,
            switching to another provider means the caller receives chunks
            from multiple sources. Callers should handle this appropriately.
        """
        last_error: str | None = None

        for provider, config in self._sorted_providers:
            if not config.enabled:
                continue

            breaker = await self._get_circuit_breaker(provider, config)
            timeout = (config.timeout_config or self._default_timeout).request_timeout

            try:
                async for event in self._stream_provider(provider, request, timeout, breaker):
                    yield event, provider.name

                    # Check if we should stop this provider and try next
                    if event.type == StreamEventType.ERROR:
                        last_error = event.error
                        logger.debug(
                            "Provider %s stream error (%s), trying next provider",
                            provider.name,
                            event.error,
                        )
                        break  # Exit async-for, try next provider

                    if event.type == StreamEventType.COMPLETE:
                        # Success - entire stream completed
                        return

            except CircuitBreakerOpenError:
                logger.debug(
                    "Circuit breaker open for %s in stream mode, trying next",
                    provider.name,
                )
                continue

            except asyncio.TimeoutError:
                last_error = f"Stream timeout for provider {provider.name}"
                logger.debug("Stream timeout for %s, trying next provider", provider.name)
                yield AIStreamEvent.error_event(last_error), provider.name
                continue

            except (RuntimeError, ConnectionError, TimeoutError) as exc:
                last_error = str(exc)
                logger.debug(
                    "Provider %s stream exception (%s), trying next provider",
                    provider.name,
                    exc,
                )
                yield AIStreamEvent.error_event(last_error), provider.name
                continue

        # All providers exhausted - yield final fallback error
        fallback_error = f"All stream providers exhausted. Last error: {last_error}"
        logger.warning(fallback_error)
        yield AIStreamEvent.error_event(fallback_error), None

    async def _stream_provider(
        self,
        provider: LLMProvider,
        request: AIRequest,
        timeout: float,
        breaker: Any,  # CircuitBreaker instance
    ) -> AsyncGenerator[AIStreamEvent, None]:
        """Stream from a single provider through its circuit breaker.

        Manually checks OPEN state before streaming and records
        success/failure after streaming completes or errors.
        """
        # Fast-fail if circuit is OPEN (check without acquiring lock for speed)
        if breaker.state.value == "open":
            raise CircuitBreakerOpenError(circuit_name=breaker.name)

        try:
            async for event in self._invoke_provider_stream(provider, request, timeout):
                yield event
            # Stream completed successfully (COMPLETE event was already yielded)
            await breaker._on_success()
        except (RuntimeError, ConnectionError, TimeoutError):
            await breaker._on_failure()
            raise

    async def _invoke_provider_stream(
        self,
        provider: LLMProvider,
        request: AIRequest,
        timeout: float,
    ) -> AsyncGenerator[AIStreamEvent, None]:
        """Invoke provider's stream, falling back to non-stream invoke if needed."""
        try:
            stream_gen = provider.invoke_stream(request)  # type: ignore[call-arg]
            async for event in stream_gen:  # type: ignore[attr-defined]
                yield event
            return
        except (NotImplementedError, AttributeError):
            pass

        # Provider doesn't support streaming - fall back to non-stream invoke.
        # asyncio.to_thread does NOT work for async def invoke because it runs
        # the callable in a thread without an event loop; the coroutine is never
        # awaited. Use asyncio.iscoroutinefunction to route correctly.
        if inspect.iscoroutinefunction(provider.invoke):
            response = await asyncio.wait_for(provider.invoke(request), timeout=timeout)
        else:
            # Protocol says invoke is async, but we handle sync just in case.
            response = await asyncio.wait_for(
                asyncio.to_thread(provider.invoke, request),  # type: ignore[arg-type]
                timeout=timeout,
            )
        if response.ok:
            yield AIStreamEvent.chunk_event(response.output)
            yield AIStreamEvent.complete({})
        else:
            yield AIStreamEvent.error_event(response.error or "invoke_failed")

    def _create_fallback_response(
        self,
        request: AIRequest,
        last_response: AIResponse | None,
    ) -> AIResponse:
        """Create a fallback response after all providers are exhausted."""
        last_error = getattr(last_response, "error", None) if last_response else None

        return AIResponse(
            ok=False,
            output="",
            error=f"All providers exhausted. Last error: {last_error}",
            error_category=ErrorCategory.PROVIDER_ERROR,
            provider_id=None,
            model=getattr(request, "model", None),
        )


# ---------------------------------------------------------------------------
# Resilient LLM Client
# ---------------------------------------------------------------------------


class ResilientLLMClient:
    """High-level LLM client with multi-provider fallback and resilience.

    This is the primary entry point for LLM calls that require high availability.
    It wraps MultiProviderFallbackManager with a simpler interface.

    Example:
        client = ResilientLLMClient(
            providers=[
                (openai_provider, ProviderConfig(provider_name="openai", priority=0)),
                (anthropic_provider, ProviderConfig(provider_name="anthropic", priority=1)),
            ],
            default_timeout_config=TimeoutConfig(request_timeout=DEFAULT_SHORT_TIMEOUT_SECONDS, total_timeout=DEFAULT_OPERATION_TIMEOUT_SECONDS),
        )
        result = await client.invoke(request)
    """

    def __init__(
        self,
        providers: list[tuple[LLMProvider, ProviderConfig]],
        default_timeout_config: TimeoutConfig | None = None,
        default_retry_config: RetryConfig | None = None,
    ) -> None:
        self._manager = MultiProviderFallbackManager(
            providers=providers,
            default_timeout_config=default_timeout_config,
            default_retry_config=default_retry_config,
        )

    async def invoke(
        self,
        request: AIRequest,
    ) -> AIResponse:
        """Invoke LLM with fallback across multiple providers.

        Args:
            request: The AIRequest to process.

        Returns:
            AIResponse - either from a successful provider or a fallback response.
        """
        result = await self._manager.invoke(request)
        return result.response

    async def invoke_with_metadata(
        self,
        request: AIRequest,
    ) -> FallbackChainResult:
        """Invoke LLM and return detailed metadata about the fallback chain.

        Useful for debugging and observability.

        Returns:
            FallbackChainResult with provider_tried, circuit_open_count, etc.
        """
        return await self._manager.invoke(request)

    async def invoke_stream(
        self,
        request: AIRequest,
    ) -> AsyncGenerator[AIStreamEvent, None]:
        """Invoke LLM with streaming and provider fallback.

        Tries providers in priority order. Yields AIStreamEvent objects.
        On ERROR event, switches to next provider. After all providers
        exhausted, yields a final fallback ERROR event.

        Yields:
            AIStreamEvent objects from the active provider.

        Example:
            async for event in client.invoke_stream(request):
                if event.type == StreamEventType.ERROR:
                    # Switch to next provider or handle error
                    pass
                elif event.type == StreamEventType.CHUNK:
                    print(event.chunk, end="")
                elif event.type == StreamEventType.COMPLETE:
                    break
        """
        # Delegate to manager and strip the provider_name from tuples
        async for event, _ in self._manager.invoke_stream(request):
            yield event

    def get_circuit_breaker_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all circuit breakers in the registry.

        Returns:
            Dictionary mapping provider names to their circuit breaker status.
        """
        return self._manager._cb_registry.get_all_status()


__all__ = [
    "FallbackChainResult",
    "LLMProvider",
    "MultiProviderFallbackManager",
    "ProviderConfig",
    "ResilientLLMClient",
]
