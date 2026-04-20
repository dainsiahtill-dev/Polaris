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

import asyncio
import logging
import threading
import warnings
from typing import TYPE_CHECKING, Any

from .base_provider import BaseProvider, ProviderRegistry

if TYPE_CHECKING:
    from polaris.infrastructure.llm.providers.provider_registry import (
        ProviderManager as InfrastructureProviderManager,
    )

logger = logging.getLogger(__name__)
_provider_registry = ProviderRegistry()

# Singleton ProviderManager instance (lazy, set by get_provider_manager())
_provider_manager: InfrastructureProviderManager | None = None


class ProviderManager:
    """KernelOne-native provider manager (legacy).

    This class is retained for type compatibility (cells layer imports the
    type from here) but its instance is no longer used at runtime.
    All runtime calls go through the infrastructure ProviderManager via
    get_provider_manager().

    Deprecated: Instances of this class are no longer created. Use
    get_provider_manager() which returns the infrastructure singleton.
    """

    def __init__(self, registry: ProviderRegistry) -> None:
        warnings.warn(
            "ProviderManager() is deprecated. Use get_provider_manager() to obtain the infrastructure singleton.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._registry = registry
        self._instances: dict[str, BaseProvider] = {}
        self._instance_locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    @staticmethod
    def _normalize_provider_type(provider_type: str) -> str:
        token = str(provider_type or "").strip().lower()
        if token == "cli":
            return "codex_cli"
        return token

    def _get_instance_lock(self, provider_type: str) -> threading.Lock:
        with self._global_lock:
            lock = self._instance_locks.get(provider_type)
            if lock is None:
                lock = threading.Lock()
                self._instance_locks[provider_type] = lock
            return lock

    def register_provider(
        self,
        provider_type: str,
        provider_class: type[BaseProvider],
    ) -> None:
        """Register a provider class and invalidate cached instances."""
        normalized = self._normalize_provider_type(provider_type)
        if not normalized:
            raise ValueError("provider_type is required")
        self._registry.register(normalized, provider_class)
        with self._global_lock:
            self._instances.pop(normalized, None)

    def get_provider_class(self, provider_type: str) -> type[BaseProvider] | None:
        """Return a provider class from the registry."""
        normalized = self._normalize_provider_type(provider_type)
        if not normalized:
            return None
        return self._registry.get_provider(normalized)

    def get_provider_instance(self, provider_type: str) -> BaseProvider | None:
        """Return a cached provider instance, creating it lazily if necessary."""
        normalized = self._normalize_provider_type(provider_type)
        if not normalized:
            return None

        with self._global_lock:
            existing = self._instances.get(normalized)
        if existing is not None:
            return existing

        instance_lock = self._get_instance_lock(normalized)
        with instance_lock:
            with self._global_lock:
                existing = self._instances.get(normalized)
            if existing is not None:
                return existing
            provider_class = self._registry.get_provider(normalized)
            if provider_class is None:
                return None
            instance = provider_class()
            with self._global_lock:
                self._instances[normalized] = instance
            return instance

    async def get_provider_instance_async(
        self,
        provider_type: str,
    ) -> BaseProvider | None:
        """Async wrapper for concurrency-heavy paths."""
        return await asyncio.to_thread(self.get_provider_instance, provider_type)

    def list_provider_types(self) -> list[str]:
        """List registered provider types."""
        return self._registry.list_provider_types()

    def list_provider_info(self) -> list[Any]:
        """List provider metadata for all registered providers."""
        return self._registry.list_providers()

    def get_provider_info(self, provider_type: str) -> Any | None:
        """Get provider metadata for one provider."""
        normalized = self._normalize_provider_type(provider_type)
        if not normalized:
            return None
        return self._registry.get_provider_info(normalized)

    def validate_provider_config(
        self,
        provider_type: str,
        config: dict[str, Any],
    ) -> bool:
        """Validate a config against the registered provider class."""
        provider_class = self.get_provider_class(provider_type)
        if provider_class is None:
            return False
        try:
            return bool(provider_class.validate_config(config).valid)
        except (AttributeError, TypeError, ValueError) as exc:
            # Provider validation errors: missing method, wrong args, invalid config
            logger.warning(
                "Provider config validation failed: provider_type=%s error=%s",
                provider_type,
                exc,
            )
            return False

    def supports_feature(self, provider_type: str, feature: str) -> bool:
        """Check if the provider class advertises a feature."""
        provider_class = self.get_provider_class(provider_type)
        if provider_class is None:
            return False
        try:
            return bool(provider_class.supports_feature(feature))
        except (AttributeError, TypeError) as exc:
            # Provider feature check errors: missing method or wrong args
            logger.warning(
                "Provider feature probe failed: provider_type=%s feature=%s error=%s",
                provider_type,
                feature,
                exc,
            )
            return False

    def get_provider_default_config(self, provider_type: str) -> dict[str, Any] | None:
        """Return the default configuration dict for the named provider class."""
        provider_class = self.get_provider_class(provider_type)
        if provider_class is None:
            return None
        try:
            return dict(provider_class.get_default_config())
        except (AttributeError, TypeError) as exc:
            # Provider config errors: missing method or wrong return type
            logger.warning(
                "get_provider_default_config failed: provider_type=%s error=%s",
                provider_type,
                exc,
            )
            return None

    def get_provider_for_config(self, config: dict[str, Any]) -> str | None:
        """Determine provider type from a configuration dict."""
        provider_type = config.get("type")
        if provider_type:
            if provider_type == "cli":
                command = str(config.get("command", "")).lower()
                if "gemini" in command:
                    return "gemini_cli"
                return "codex_cli"
            return str(provider_type)

        command = config.get("command", "").lower()
        base_url = config.get("base_url", "").lower()

        if "codex" in command:
            return "codex_cli"
        if "gemini" in command:
            return "gemini_cli"
        if "minimax" in base_url:
            return "minimax"
        if "generativelanguage.googleapis.com" in base_url:
            return "gemini_api"
        if "api.openai.com" in base_url:
            return "openai_compat"
        if "192.168.1.2:11434" in base_url or "192.168.1.2:11434" in base_url:
            return "ollama"
        if "anthropic" in base_url:
            return "anthropic_compat"
        if "kimi" in base_url:
            return "kimi"

        return None

    def migrate_legacy_config(self, legacy_config: dict[str, Any]) -> dict[str, Any]:
        """Migrate legacy configuration to new provider format."""
        migrated = dict(legacy_config) if legacy_config else {}
        providers = migrated.get("providers", {})
        if not isinstance(providers, dict):
            migrated["providers"] = {}
            return migrated

        migrated_providers: dict[str, dict[str, Any]] = {}
        for provider_id, provider_config in providers.items():
            if not isinstance(provider_config, dict):
                continue

            provider_type = self.get_provider_for_config(provider_config)
            if not provider_type:
                migrated_providers[provider_id] = dict(provider_config)
                continue

            default_config = self.get_provider_default_config(provider_type) or {}
            merged_config = {**default_config, **provider_config}
            merged_config["type"] = provider_type
            migrated_providers[provider_id] = merged_config

        migrated["providers"] = migrated_providers
        return migrated

    def health_check_all(self, configs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Perform health checks on all configured providers."""
        results: dict[str, dict[str, Any]] = {}

        for provider_id, config in configs.items():
            provider_type = self.get_provider_for_config(config)
            if not provider_type:
                results[provider_id] = {"ok": False, "error": "Unknown provider type"}
                continue

            provider_instance = self.get_provider_instance(provider_type)
            if not provider_instance:
                results[provider_id] = {
                    "ok": False,
                    "error": f"Could not instantiate provider {provider_type}",
                }
                continue

            try:
                health_result = provider_instance.health(config)
                results[provider_id] = health_result.to_dict()
            except (AttributeError, OSError, ConnectionError, TimeoutError) as exc:
                # Health check errors: network, timeout, or provider issues
                results[provider_id] = {"ok": False, "error": str(exc)}

        return results

    def clear_instances(self) -> None:
        """Drop all cached provider instances."""
        with self._global_lock:
            self._instances.clear()
            self._instance_locks.clear()


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


# Lazy proxy singleton - defers to get_provider_manager() to avoid circular import
# Use __getattr__ at module level for lazy initialization
class _LazyProviderManager:
    """Lazy proxy that defers to get_provider_manager() on first access."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_provider_manager(), name)

    def __repr__(self) -> str:
        return f"<LazyProviderManager wrapping {get_provider_manager()!r}>"


_provider_manager_proxy: Any = _LazyProviderManager()


def __getattr__(name: str) -> Any:
    """Module-level lazy access for provider_manager to avoid circular import."""
    if name == "provider_manager":
        return _provider_manager_proxy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Module-level alias for ruff static analysis (actual lazy loading via __getattr__)
provider_manager: Any = _LazyProviderManager()


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
    "ProviderManager",
    "get_provider_manager",
    "get_provider_registry",
    "provider_manager",
    "register_provider",
    "reset_provider_runtime",
]
