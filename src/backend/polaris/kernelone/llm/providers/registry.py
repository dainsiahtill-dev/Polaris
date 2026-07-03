"""KernelOne-owned provider registry accessors.

Infrastructure adapters may register concrete provider implementations here
without forcing KernelOne to import infrastructure packages at module import
time.

Architecture (Post-P1-022 fix):
    get_provider_manager() now delegates to the infrastructure ProviderManager
    singleton as the single source of truth. The dual-registration complexity
    has been eliminated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base_provider import BaseProvider, ProviderRegistry

if TYPE_CHECKING:
    from polaris.infrastructure.llm.providers.provider_registry import (
        ProviderManager as InfrastructureProviderManager,
    )

_provider_registry = ProviderRegistry()

# Singleton ProviderManager instance (lazy, set by get_provider_manager())
_provider_manager: InfrastructureProviderManager | None = None


def get_provider_registry() -> ProviderRegistry:
    """Return the process-wide KernelOne provider registry.

    Note:
        This registry is populated by bootstrap (provider_bootstrap.py).
        For most use cases, prefer get_provider_manager() which delegates
        to the infrastructure ProviderManager as the single source of truth.
    """
    return _provider_registry


def get_provider_manager() -> InfrastructureProviderManager:
    """Return the process-wide ProviderManager singleton.

    Delegates to the infrastructure ProviderManager singleton to serve as
    the single source of truth, eliminating dual-registration complexity.

    Bootstrap flow:
        1. infrastructure ProviderManager registers default providers
           (codex_sdk, codex_cli, gemini_cli, minimax, kimi, gemini_api,
            ollama, openai_compat, anthropic_compat)
        2. bootstrap injects this manager into ServiceLocator
        3. All runtime calls flow through this singleton

    Returns:
        The infrastructure ProviderManager instance.
    """
    global _provider_manager
    if _provider_manager is None:
        # Lazy import to avoid circular dependency.
        # infrastructure/provider_registry.py imports from this module,
        # so we defer the import until first use.
        from polaris.infrastructure.llm.providers.provider_registry import (
            provider_manager as _infra_manager,
        )

        _provider_manager = _infra_manager
    return _provider_manager


def register_provider(
    provider_type: str,
    provider_class: type[BaseProvider],
) -> None:
    """Register a concrete provider implementation in the KernelOne registry."""
    get_provider_manager().register_provider(provider_type, provider_class)


def reset_provider_runtime() -> None:
    """Reset process-wide provider registry and cached instances.

    Intended for tests and isolated bootstrap scenarios.
    """
    from polaris.infrastructure.llm.providers.provider_registry import ProviderManager

    _provider_registry.clear()
    ProviderManager.reset_for_testing()


__all__ = [
    "get_provider_manager",
    "get_provider_registry",
    "register_provider",
    "reset_provider_runtime",
]
