from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.util
import inspect
import logging
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from polaris.kernelone.llm.providers import BaseProvider, ProviderInfo
from polaris.kernelone.llm.providers.registry import (
    get_provider_registry,
)

from .anthropic_provider import AnthropicProvider
from .async_provider_adapter import AsyncProviderClassAdapter
from .codex_cli_provider import CodexCLIProvider
from .codex_sdk_provider import CodexSDKProvider
from .gemini_api_provider import GeminiAPIProvider
from .gemini_cli_provider import GeminiCLIProvider

logger = logging.getLogger(__name__)


class ProviderManager:
    """Manages provider registration, discovery, and instantiation"""

    # TTL for cached provider instances (seconds)
    _INSTANCE_TTL_SECONDS: float = 300.0  # 5 minutes
    # Consecutive failure threshold — evict provider after this many failures
    _FAILURE_EVICTION_THRESHOLD: int = 3
    # Maximum failure count before capping (prevents unbounded growth)
    _MAX_FAILURE_COUNT: int = 100
    # Class-level singleton reference for reset_for_testing
    _instance: ClassVar[ProviderManager | None] = None

    def __init__(self) -> None:
        self._provider_classes: dict[str, type[BaseProvider]] = {}
        self._factory_default_provider_classes: Mapping[str, type[BaseProvider]] = MappingProxyType({})
        self._provider_instances: dict[str, BaseProvider] = {}
        self._instance_timestamps: dict[str, float] = {}  # creation time per instance
        self._instance_failures: dict[str, int] = {}  # consecutive failure count
        self._instance_locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()
        default_provider_classes = ProviderManager._register_default_providers(self)
        # Factory route trust is sealed to bootstrap-owned concrete classes.
        # Runtime registration may add ordinary providers, but cannot replace a
        # governed key and inherit its physical-dispatch qualification.
        # Call the exact base implementation and seal its explicit return value;
        # virtual bootstrap overrides must never self-declare a trusted class.
        self._factory_default_provider_classes = MappingProxyType(default_provider_classes)

    def _register_default_providers(self) -> dict[str, type[BaseProvider]]:
        """Register all default providers"""
        registered: dict[str, type[BaseProvider]] = {}

        def register(provider_type: str, provider_class: type[BaseProvider]) -> None:
            ProviderManager.register_provider(self, provider_type, provider_class)
            registered[provider_type] = provider_class

        # Register enhanced providers
        register("codex_sdk", CodexSDKProvider)
        register("codex_cli", CodexCLIProvider)  # Use proper Codex CLI provider
        register("gemini_cli", GeminiCLIProvider)
        minimax = ProviderManager._register_optional_provider(self, "minimax", "minimax_provider", "MiniMaxProvider")
        if minimax is not None:
            registered["minimax"] = minimax
        kimi = ProviderManager._register_optional_provider(self, "kimi", "kimi_provider", "KimiProvider")
        if kimi is not None:
            registered["kimi"] = kimi
        register("gemini_api", GeminiAPIProvider)

        # Register async Ollama provider (with sync adapter)
        ollama = ProviderManager._register_optional_provider(
            self,
            "ollama",
            "async_ollama_provider",
            "AsyncOllamaProvider",
            async_adapter=True,
        )
        if ollama is not None:
            registered["ollama"] = ollama

        # The provider type strings are stable user configuration identifiers.
        # The implementation classes and modules use canonical provider names.
        openai = ProviderManager._register_optional_provider(
            self,
            "openai_compat",
            "openai_provider",
            "OpenAIProvider",
        )
        if openai is not None:
            registered["openai_compat"] = openai
        register("anthropic_compat", AnthropicProvider)
        return registered

    def _register_optional_provider(
        self,
        provider_type: str,
        module_name: str,
        class_name: str,
        *,
        async_adapter: bool = False,
    ) -> type[BaseProvider] | None:
        """Register a provider only when its optional transport dependencies exist."""
        try:
            module = importlib.import_module(f".{module_name}", package=__package__)
            provider_class = getattr(module, class_name)
            if async_adapter:
                provider_class = AsyncProviderClassAdapter.create(provider_class)
        except (AttributeError, ImportError) as exc:
            logger.info("%s provider unavailable: %s", provider_type, exc)
            return None
        ProviderManager.register_provider(self, provider_type, provider_class)
        return provider_class

    def register_provider(self, provider_type: str, provider_class: type[BaseProvider]) -> None:
        """Register a provider class"""
        if not issubclass(provider_class, BaseProvider):
            raise ValueError(f"Provider class {provider_class} must inherit from BaseProvider")

        with self._global_lock:
            # Check if this is a re-registration with different class
            self._provider_classes.get(provider_type)

            self._provider_classes[provider_type] = provider_class
            # If provider class changed, force lazy recreation of the instance.
            self._provider_instances.pop(provider_type, None)
            self._instance_timestamps.pop(provider_type, None)
            self._instance_failures.pop(provider_type, None)

            # Cleanup stale locks for providers that no longer exist
            # This prevents unbounded growth of _instance_locks (M-26)
            registered_types = set(self._provider_classes.keys())
            stale_locks = [pt for pt in self._instance_locks if pt not in registered_types]
            for stale_pt in stale_locks:
                self._instance_locks.pop(stale_pt, None)

            # Create lock for new provider if not exists
            if provider_type not in self._instance_locks:
                self._instance_locks[provider_type] = threading.Lock()

        # Also register with the kernelone registry for backward compatibility
        kernelone_registry = get_provider_registry()
        kernelone_registry.register(provider_type, provider_class)

    @staticmethod
    def _normalize_provider_type(provider_type: str) -> str:
        token = str(provider_type or "").strip()
        return "codex_cli" if token == "cli" else token

    def _get_instance_lock(self, provider_type: str) -> threading.Lock:
        with self._global_lock:
            lock = self._instance_locks.get(provider_type)
            if lock is None:
                lock = threading.Lock()
                self._instance_locks[provider_type] = lock
            return lock

    def get_provider_class(self, provider_type: str) -> type[BaseProvider] | None:
        """Get a provider class by type"""
        resolved = self._normalize_provider_type(provider_type)
        with self._global_lock:
            return self._provider_classes.get(resolved)

    def is_factory_default_provider_implementation(
        self,
        provider_type: str,
        provider_instance: object,
    ) -> bool:
        """Attest exact bootstrap implementation identity for Factory dispatch."""

        resolved = self._normalize_provider_type(provider_type)
        with self._global_lock:
            sealed_class = self._factory_default_provider_classes.get(resolved)
            registered_class = self._provider_classes.get(resolved)
            return (
                sealed_class is not None
                and registered_class is sealed_class
                and type(provider_instance) is sealed_class
            )

    def get_factory_default_provider_instance(self, provider_type: str) -> BaseProvider | None:
        """Return a trusted default without constructing an untrusted replacement."""

        resolved = self._normalize_provider_type(provider_type)
        if not resolved:
            return None
        with self._global_lock:
            sealed_class = self._factory_default_provider_classes.get(resolved)
            if sealed_class is None or self._provider_classes.get(resolved) is not sealed_class:
                return None
            return self._get_provider_instance_locked(
                resolved,
                now=time.monotonic(),
                required_class=sealed_class,
            )

    def _get_provider_instance_locked(
        self,
        resolved: str,
        *,
        now: float,
        required_class: type[BaseProvider] | None = None,
    ) -> BaseProvider | None:
        """Get/create one instance while ``_global_lock`` is held."""

        existing = self._provider_instances.get(resolved)
        if existing is not None:
            if required_class is not None and type(existing) is not required_class:
                return None
            age = now - self._instance_timestamps.get(resolved, now)
            failures = self._instance_failures.get(resolved, 0)
            if age <= self._INSTANCE_TTL_SECONDS and failures < self._FAILURE_EVICTION_THRESHOLD:
                return existing
            logger.debug(
                "Evicting provider instance: type=%s age=%.1fs failures=%d",
                resolved,
                age,
                failures,
            )
            self._provider_instances.pop(resolved, None)
            self._instance_timestamps.pop(resolved, None)
            self._instance_failures.pop(resolved, None)

        provider_class = self._provider_classes.get(resolved)
        if provider_class is None or (required_class is not None and provider_class is not required_class):
            return None
        instance = provider_class()
        self._provider_instances[resolved] = instance
        self._instance_timestamps[resolved] = time.monotonic()
        self._instance_failures.pop(resolved, None)
        return instance

    def get_provider_instance(self, provider_type: str) -> BaseProvider | None:
        """Get or create a provider instance.

        Instances are evicted when:
        - TTL (5 min) expires
        - Consecutive failure count exceeds threshold (3)
        """
        resolved = self._normalize_provider_type(provider_type)
        if not resolved:
            return None

        # H-NEW Fix: Hold global_lock throughout check-evict-recreate to prevent TOCTOU race.
        # Another thread could evict/recreate between our check and eviction without this.
        with self._global_lock:
            return self._get_provider_instance_locked(resolved, now=time.monotonic())

    def record_provider_failure(self, provider_type: str) -> None:
        """Record a provider failure for TTL-based eviction tracking.

        After _FAILURE_EVICTION_THRESHOLD consecutive failures the provider
        instance is evicted on the next get_provider_instance() call.
        """
        resolved = self._normalize_provider_type(provider_type)
        if not resolved:
            return
        with self._global_lock:
            count = self._instance_failures.get(resolved, 0) + 1
            # M-27 Fix: Cap failure count to prevent unbounded growth for providers
            # that are never instantiated (e.g., failure recorded but instance never created)
            self._instance_failures[resolved] = min(count, self._MAX_FAILURE_COUNT)
            if count >= self._FAILURE_EVICTION_THRESHOLD:
                logger.warning(
                    "Provider %s evicted after %d consecutive failures",
                    resolved,
                    count,
                )
                self._provider_instances.pop(resolved, None)
                self._instance_timestamps.pop(resolved, None)
                self._instance_failures.pop(resolved, None)  # reset failure count on eviction

    async def get_provider_instance_async(self, provider_type: str) -> BaseProvider | None:
        """Async wrapper for concurrency-heavy contexts."""
        return await asyncio.to_thread(self.get_provider_instance, provider_type)

    def list_provider_types(self) -> list[str]:
        """List all registered provider types"""
        with self._global_lock:
            return list(self._provider_classes.keys())

    def list_provider_info(self) -> list[ProviderInfo]:
        """List information for all registered providers"""
        info_list = []
        with self._global_lock:
            provider_classes = list(self._provider_classes.values())
        for provider_class in provider_classes:
            try:
                info = provider_class.get_provider_info()
                info_list.append(info)
            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Error getting provider info for %s: %s",
                    provider_class,
                    str(e),
                )
        return info_list

    def get_provider_info(self, provider_type: str) -> ProviderInfo | None:
        """Get provider information by type"""
        provider_class = self.get_provider_class(provider_type)
        if provider_class:
            try:
                return provider_class.get_provider_info()
            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Error getting provider info for %s: %s",
                    provider_type,
                    str(e),
                )
        return None

    def validate_provider_config(self, provider_type: str, config: dict[str, Any]) -> bool:
        """Validate provider configuration"""
        provider_class = self.get_provider_class(provider_type)
        if provider_class:
            try:
                result = provider_class.validate_config(config)
                return result.valid
            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Error validating config for %s: %s",
                    provider_type,
                    str(e),
                )
                return False
        return False

    def get_provider_default_config(self, provider_type: str) -> dict[str, Any] | None:
        """Get default configuration for a provider"""
        provider_class = self.get_provider_class(provider_type)
        if provider_class:
            try:
                return provider_class.get_default_config()
            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Error getting default config for %s: %s",
                    provider_type,
                    str(e),
                )
        return None

    def supports_feature(self, provider_type: str, feature: str) -> bool:
        """Check if a provider supports a specific feature"""
        provider_class = self.get_provider_class(provider_type)
        if provider_class:
            try:
                return provider_class.supports_feature(feature)
            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Error checking feature support for %s: %s",
                    provider_type,
                    str(e),
                )
        return False

    def discover_providers(self, providers_dir: str | None = None) -> None:
        """Discover and register providers from a directory"""
        if providers_dir is None:
            providers_dir = str(Path(__file__).parent)

        providers_path = Path(providers_dir)  # type: ignore[arg-type]
        if not providers_path.exists():
            return

        # Look for provider files
        for file_path in providers_path.glob("*_provider.py"):
            if file_path.name.startswith("__"):
                continue

            module_name = file_path.stem
            try:
                # Import the module
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                if spec is None or spec.loader is None:
                    logger.warning("Could not load spec for %s", str(file_path))
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Look for provider classes
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseProvider) and obj != BaseProvider and not name.startswith("_"):
                        # Register the provider
                        provider_type = obj.get_provider_info().type
                        self.register_provider(provider_type, obj)
                        logger.info(
                            "Discovered and registered provider: %s",
                            provider_type,
                        )

            except (RuntimeError, ValueError) as e:
                logger.warning(
                    "Error discovering provider from %s: %s",
                    str(file_path),
                    str(e),
                )

    def get_provider_for_config(self, config: dict[str, Any]) -> str | None:
        """Determine provider type from configuration"""
        provider_type = config.get("type")
        if provider_type:
            if provider_type == "cli":
                command = str(config.get("command", "")).lower()
                if "gemini" in command:
                    return "gemini_cli"
                return "codex_cli"
            return provider_type

        # Try to infer from other config fields
        command = config.get("command", "").lower()
        base_url = config.get("base_url", "").lower()

        if "codex" in command:
            return "codex_cli"
        elif "gemini" in command:
            return "gemini_cli"
        elif "minimax" in base_url:
            return "minimax"
        elif "generativelanguage.googleapis.com" in base_url:
            return "gemini_api"
        elif "api.openai.com" in base_url:
            return "openai_compat"
        elif "120.24.117.59:11434" in base_url or "120.24.117.59:11434" in base_url:
            return "ollama"
        elif "anthropic" in base_url:
            return "anthropic_compat"

        return None

    def migrate_legacy_config(self, legacy_config: dict[str, Any]) -> dict[str, Any]:
        """Migrate legacy configuration to new provider format"""
        migrated = legacy_config.copy()

        providers = migrated.get("providers", {})
        migrated_providers = {}

        for provider_id, provider_config in providers.items():
            if not isinstance(provider_config, dict):
                continue

            # Determine provider type
            provider_type = self.get_provider_for_config(provider_config)
            if not provider_type:
                # Keep as-is if we can't determine the type
                migrated_providers[provider_id] = provider_config
                continue

            # Get default config for the provider
            default_config = self.get_provider_default_config(provider_type) or {}

            # Merge with existing config
            merged_config = {**default_config, **provider_config}

            # Ensure type is set
            merged_config["type"] = provider_type

            migrated_providers[provider_id] = merged_config

        migrated["providers"] = migrated_providers
        return migrated

    def health_check_all(self, configs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Perform health checks on all configured providers"""
        results = {}

        for provider_id, config in configs.items():
            provider_type = self.get_provider_for_config(config)
            if not provider_type:
                results[provider_id] = {"ok": False, "error": "Unknown provider type"}
                continue

            provider_instance = self.get_provider_instance(provider_type)
            if not provider_instance:
                results[provider_id] = {"ok": False, "error": f"Could not instantiate provider {provider_type}"}
                continue

            try:
                health_result = provider_instance.health(config)
                results[provider_id] = health_result.to_dict()
            except (RuntimeError, ValueError) as e:
                logger.warning("Health check failed for provider %s: %s", provider_id, e)
                results[provider_id] = {"ok": False, "error": str(e)}

        return results

    def clear_instances(self) -> None:
        """Drop all cached provider instances and reset TTL/failure tracking."""
        with self._global_lock:
            self._provider_instances.clear()
            self._instance_timestamps.clear()
            self._instance_failures.clear()

    @classmethod
    def reset_for_testing(cls) -> None:
        """Reset for test isolation. Clears instance cache and failure tracking."""
        instance = cls._instance
        if instance is not None:
            with instance._global_lock:
                instance._provider_instances.clear()
                instance._instance_timestamps.clear()
                instance._instance_failures.clear()


# Global provider manager instance
provider_manager = ProviderManager()


def get_token_tracking_provider(provider_type: str, budget_limit: int | None = None) -> Any | None:
    """Get a provider instance wrapped with token tracking.

    Args:
        provider_type: The type of provider to get
        budget_limit: Optional token budget limit

    Returns:
        A provider instance wrapped with TokenTrackingWrapper, or None if not found
    """
    from ..token_tracking_wrapper import TokenTrackingWrapper

    # Check for budget in environment
    if budget_limit is None:
        env_budget = os.environ.get("KERNELONE_TOKEN_BUDGET")
        if env_budget:
            with contextlib.suppress(ValueError):
                budget_limit = int(env_budget)

    instance = provider_manager.get_provider_instance(provider_type)
    if instance is None:
        return None

    # Only wrap if budget is set
    if budget_limit:
        return TokenTrackingWrapper(instance, budget_limit=budget_limit)

    return instance
