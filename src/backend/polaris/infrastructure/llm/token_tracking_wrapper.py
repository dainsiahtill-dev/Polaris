"""Token tracking wrapper for LLM providers.

Integrates TokenService with LLM providers to track actual usage and enforce budgets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.infrastructure.llm.token_service import TokenService


class TokenTrackingWrapper:
    """Wrapper for LLM providers that tracks token usage.

    This wrapper intercepts invoke() calls and records actual token usage
    to the TokenService for budget tracking and cost management.

    Usage:
        provider = OpenAICompatProvider()
        wrapped = TokenTrackingWrapper(provider)
        result = wrapped.invoke(prompt, model, config)
        # Token usage is automatically tracked
    """

    def __init__(
        self,
        provider: Any,
        token_service: TokenService | None = None,
        budget_limit: int | None = None,
    ) -> None:
        """Initialize the wrapper.

        Args:
            provider: The LLM provider to wrap
            token_service: Optional TokenService instance. When None, lazily
                resolves via ``get_token_service`` at first use to avoid blocking
                module load when KernelOne is not bootstrapped.
            budget_limit: Optional budget limit in tokens
        """
        self._provider = provider
        self._token_service = token_service
        self._budget_limit = budget_limit

    def _get_token_service(self) -> TokenService:
        """Lazily resolve the TokenService instance."""
        if self._token_service is None:
            from polaris.infrastructure.llm.token_service import get_token_service

            self._token_service = get_token_service(budget_limit=self._budget_limit)
        return self._token_service

    def _check_budget(self) -> tuple[bool, str | None]:
        """Check if there's budget available.

        Returns:
            Tuple of (is_allowed, reason_if_denied)
        """
        if not self._budget_limit:
            return True, None

        status = self._get_token_service().get_budget_status()
        if status.is_exceeded:
            return False, f"Token budget exceeded: {status.used_tokens}/{status.budget_limit}"

        return True, None

    def _record_usage(self, result: Any) -> None:
        """Record token usage from invoke result.

        Uses duck-typing to access result.usage (KernelOne InvokeResult) or
        getattr(result, 'usage', None) for any compatible object.
        """
        usage = getattr(result, "usage", None)
        if usage is None:
            return
        # Use total_tokens if available, otherwise estimate from chars
        tokens = getattr(usage, "total_tokens", None)
        if not tokens:
            # Fallback: try to estimate from chars
            prompt_chars = getattr(usage, "prompt_chars", 0) or 0
            completion_chars = getattr(usage, "completion_chars", 0) or 0
            tokens = (prompt_chars + completion_chars) // 4
        if tokens:
            self._get_token_service().record_usage(tokens)

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> Any:
        """Invoke the provider with token tracking."""
        # Check budget before invocation
        allowed, reason = self._check_budget()
        if not allowed:
            # ACGA: Import from public llm module, not internal types submodule
            from polaris.kernelone.llm import InvokeResult, Usage

            return InvokeResult(
                ok=False,
                output="",
                latency_ms=0,
                usage=Usage(
                    prompt_chars=len(prompt),
                    completion_chars=0,
                    estimated=True,
                ),
                error=f"Budget exceeded: {reason}",
            )

        # Call the provider
        result = self._provider.invoke(prompt, model, config)

        # Record actual usage
        self._record_usage(result)

        return result

    def __getattr__(self, name: str) -> Any:
        """Delegate other attributes to the wrapped provider."""
        return getattr(self._provider, name)


class TokenTrackingRegistry:
    """Registry that wraps all providers with token tracking."""

    def __init__(
        self,
        inner_registry: Any,
        token_service: TokenService | None = None,
        budget_limit: int | None = None,
    ) -> None:
        """Initialize with an inner registry.

        Args:
            inner_registry: The actual provider registry
            token_service: Optional TokenService instance
            budget_limit: Optional budget limit
        """
        self._inner = inner_registry
        self._token_service = token_service
        self._budget_limit = budget_limit
        self._wrapped: dict[str, Any] = {}

    def get_provider(self, provider_type: str) -> Any | None:
        """Get a provider wrapped with token tracking."""
        if provider_type in self._wrapped:
            return self._wrapped[provider_type]

        provider = self._inner.get_provider(provider_type)
        if provider is None:
            return None

        wrapped = TokenTrackingWrapper(
            provider,
            token_service=self._token_service,
            budget_limit=self._budget_limit,
        )
        self._wrapped[provider_type] = wrapped
        return wrapped

    def __getattr__(self, name: str) -> Any:
        """Delegate other attributes to the inner registry."""
        return getattr(self._inner, name)


def wrap_provider_registry(
    registry: Any,
    budget_limit: int | None = None,
) -> TokenTrackingRegistry:
    """Wrap a provider registry with token tracking.

    Args:
        registry: The provider registry to wrap
        budget_limit: Optional token budget limit

    Returns:
        A TokenTrackingRegistry that wraps all providers
    """
    return TokenTrackingRegistry(
        inner_registry=registry,
        budget_limit=budget_limit,
    )
