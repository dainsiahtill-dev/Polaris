"""Provider adapter factory with decorator-based registration.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Decorator Pattern for Provider Adapter Registration:
    @provider_adapter("anthropic")
    class AnthropicMessagesAdapter(ProviderAdapter):
        ...

New adapters should use the decorator instead of manual if/elif routing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

    from polaris.kernelone.llm.provider_adapters.base import ProviderAdapter
    from typing_extensions import ParamSpec

    P = ParamSpec("P")

logger = logging.getLogger(__name__)

# Global registry for provider adapters
_ADAPTER_REGISTRY: dict[str, type[ProviderAdapter]] = {}

# Type variable for class decorators
_T = TypeVar("_T")


def provider_adapter(name: str) -> Callable[[_T], _T]:
    """Decorator to register a ProviderAdapter implementation.

    Usage:
        @provider_adapter("anthropic")
        class AnthropicMessagesAdapter(ProviderAdapter):
            ...

    Args:
        name: Provider identifier (e.g., "anthropic", "openai")

    Returns:
        Class decorator function
    """

    def decorator(cls: _T) -> _T:
        normalized = str(name or "").lower().strip()
        if not normalized:
            raise ValueError("provider_adapter name cannot be empty")
        _ADAPTER_REGISTRY[normalized] = cls  # type: ignore[assignment]
        logger.debug(f"Registered provider adapter: {normalized} -> {cls.__name__}")  # type: ignore[attr-defined]
        return cls

    return decorator


def get_adapter(provider: str) -> ProviderAdapter:
    """Get adapter instance for the given provider name.

    Routing (highest priority first):
    - Exact match: "anthropic", "claude" -> AnthropicMessagesAdapter
    - Partial match: contains "anthropic" or "claude" -> AnthropicMessagesAdapter
    - Default: OpenAIResponsesAdapter (backward compatible)

    Args:
        provider: Provider identifier string

    Returns:
        ProviderAdapter instance

    Raises:
        ValueError: If no adapter found for provider
    """
    provider_lower = str(provider or "").lower().strip()

    # Priority 1: Built-in adapters route explicitly so placeholder registrations
    # never shadow the real lazy-imported implementation.
    if "anthropic" in provider_lower or "claude" in provider_lower:
        # Lazy import to avoid circular dependency
        from polaris.kernelone.llm.provider_adapters.anthropic_messages_adapter import (
            AnthropicMessagesAdapter,
        )

        return AnthropicMessagesAdapter()
    if "ollama" in provider_lower:
        from polaris.kernelone.llm.provider_adapters.ollama_chat_adapter import (
            OllamaChatAdapter,
        )

        return OllamaChatAdapter()

    if provider_lower in ("openai", "openai_compat") or "gpt" in provider_lower:
        from polaris.kernelone.llm.provider_adapters.openai_responses_adapter import (
            OpenAIResponsesAdapter,
        )

        return OpenAIResponsesAdapter()

    # Priority 2: Check registry for custom adapters
    if provider_lower in _ADAPTER_REGISTRY:
        adapter_cls = _ADAPTER_REGISTRY[provider_lower]
        return adapter_cls()

    # Priority 3: Default - OpenAI compatible
    from polaris.kernelone.llm.provider_adapters.openai_responses_adapter import (
        OpenAIResponsesAdapter,
    )

    return OpenAIResponsesAdapter()


def get_adapter_class(provider: str) -> type[ProviderAdapter]:
    """Get adapter class for the given provider name.

    Same routing as get_adapter() but returns the class instead of instance.

    Args:
        provider: Provider identifier string

    Returns:
        ProviderAdapter class
    """
    provider_lower = str(provider or "").lower().strip()

    # Priority 1: Built-in adapters route explicitly so placeholder registrations
    # never shadow the real lazy-imported implementation.
    if "anthropic" in provider_lower or "claude" in provider_lower:
        from polaris.kernelone.llm.provider_adapters.anthropic_messages_adapter import (
            AnthropicMessagesAdapter,
        )

        return AnthropicMessagesAdapter
    if "ollama" in provider_lower:
        from polaris.kernelone.llm.provider_adapters.ollama_chat_adapter import (
            OllamaChatAdapter,
        )

        return OllamaChatAdapter

    if provider_lower in ("openai", "openai_compat") or "gpt" in provider_lower:
        from polaris.kernelone.llm.provider_adapters.openai_responses_adapter import (
            OpenAIResponsesAdapter,
        )

        return OpenAIResponsesAdapter

    # Priority 2: Check registry for custom adapters
    if provider_lower in _ADAPTER_REGISTRY:
        return _ADAPTER_REGISTRY[provider_lower]

    # Priority 3: Default - OpenAI compatible
    from polaris.kernelone.llm.provider_adapters.openai_responses_adapter import (
        OpenAIResponsesAdapter,
    )

    return OpenAIResponsesAdapter


def list_registered_adapters() -> list[str]:
    """List all registered adapter names.

    Returns:
        List of provider names that have registered adapters
    """
    return list(_ADAPTER_REGISTRY.keys())


def clear_registry() -> None:
    """Clear all registered adapters. Useful for testing."""
    _ADAPTER_REGISTRY.clear()


# =============================================================================
# Register built-in adapters using decorator pattern
# =============================================================================


@provider_adapter("anthropic")
class _AnthropicMessagesAdapterPlaceholder:
    """Placeholder marker for AnthropicMessagesAdapter registration.

    The actual AnthropicMessagesAdapter is imported lazily by get_adapter()
    to avoid circular imports. This placeholder ensures the "anthropic" key
    is reserved for the decorator registration pattern.
    """

    pass


@provider_adapter("openai")
class _OpenAIResponsesAdapterPlaceholder:
    """Placeholder marker for OpenAIResponsesAdapter registration.

    The actual OpenAIResponsesAdapter is imported lazily by get_adapter()
    to avoid circular imports.
    """

    pass


@provider_adapter("ollama")
class _OllamaChatAdapterPlaceholder:
    """Placeholder marker for OllamaChatAdapter registration."""

    pass
