"""Adapter for async providers to work with sync ProviderManager.

This module provides a bridge between async providers (AsyncBaseProvider)
and the sync ProviderManager interface. It wraps async methods with
sync-compatible implementations using asyncio.run() or thread pool.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from polaris.kernelone.llm.providers import BaseProvider, ProviderConfigValidationResult, ProviderInfo
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult

from .async_base_provider import AsyncBaseProvider

logger = logging.getLogger(__name__)

# Module-level thread pool for async-to-sync bridging
_bridge_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="async-bridge")


def _run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously.

    Uses a dedicated thread pool to avoid blocking the main event loop
    when called from async contexts.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're in an async context - use thread pool
        future = _bridge_pool.submit(asyncio.run, coro)
        return future.result()
    else:
        # We're in a sync context - run directly
        return asyncio.run(coro)


class AsyncProviderAdapter(BaseProvider):
    """Adapter that wraps an AsyncBaseProvider to work with sync ProviderManager.

    This allows async providers to be registered with the existing sync-based
    ProviderManager without modifying the manager's interface.

    Usage::

        from .async_ollama_provider import AsyncOllamaProvider
        from .async_provider_adapter import AsyncProviderAdapter

        # Register async provider with sync manager
        adapter = AsyncProviderAdapter(AsyncOllamaProvider)
        manager.register_provider("ollama_async", adapter)
    """

    def __init__(self, async_provider_class: type[AsyncBaseProvider]) -> None:
        self._async_class = async_provider_class
        self._async_instance: AsyncBaseProvider | None = None

    def _get_async_instance(self) -> AsyncBaseProvider:
        if self._async_instance is None:
            self._async_instance = self._async_class()
        return self._async_instance

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        """Delegate to the async provider class."""
        # This is called on the class, not instance
        # We need to handle this differently
        raise NotImplementedError("Use instance method or register async provider directly")

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        """Delegate to the async provider class."""
        raise NotImplementedError("Use instance method or register async provider directly")

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ProviderConfigValidationResult:
        """Delegate to the async provider class."""
        raise NotImplementedError("Use instance method or register async provider directly")

    def health(self, config: dict[str, Any]) -> HealthResult:
        """Sync wrapper for async health check."""
        instance = self._get_async_instance()
        return _run_async(instance.health(config))

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        """Sync wrapper for async model listing."""
        instance = self._get_async_instance()
        return _run_async(instance.list_models(config))

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        """Sync wrapper for async invoke."""
        instance = self._get_async_instance()
        return _run_async(instance.invoke(prompt, model, config))


class AsyncProviderClassAdapter:
    """Adapter that wraps an AsyncBaseProvider class for class-level operations.

    This adapter allows async provider classes to be registered with the
    ProviderManager while maintaining class-level method compatibility.

    Usage::

        from .async_ollama_provider import AsyncOllamaProvider
        from .async_provider_adapter import AsyncProviderClassAdapter

        # Create adapter class
        OllamaAsyncAdapter = AsyncProviderClassAdapter.create(AsyncOllamaProvider)

        # Register with manager
        manager.register_provider("ollama_async", OllamaAsyncAdapter)
    """

    @staticmethod
    def create(
        async_class: type[AsyncBaseProvider],
    ) -> type[BaseProvider]:
        """Create a sync-compatible provider class from an async provider class.

        Returns a new class that inherits from BaseProvider and wraps
        all async methods with sync implementations.
        """

        class AdaptedProvider(BaseProvider):
            """Dynamically created sync adapter for async provider."""

            _async_class = async_class
            _async_instance: AsyncBaseProvider | None = None

            @classmethod
            def _get_instance(cls) -> AsyncBaseProvider:
                if cls._async_instance is None:
                    cls._async_instance = cls._async_class()
                return cls._async_instance

            @classmethod
            def get_provider_info(cls) -> ProviderInfo:
                return cls._async_class.get_provider_info()

            @classmethod
            def get_default_config(cls) -> dict[str, Any]:
                return cls._async_class.get_default_config()

            @classmethod
            def validate_config(cls, config: dict[str, Any]) -> ProviderConfigValidationResult:
                return cls._async_class.validate_config(config)

            def health(self, config: dict[str, Any]) -> HealthResult:
                instance = self._get_instance()
                return _run_async(instance.health(config))

            def list_models(self, config: dict[str, Any]) -> ModelListResult:
                instance = self._get_instance()
                return _run_async(instance.list_models(config))

            def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
                instance = self._get_instance()
                return _run_async(instance.invoke(prompt, model, config))

        # Preserve the original class name for debugging
        AdaptedProvider.__name__ = f"Adapted{async_class.__name__}"
        AdaptedProvider.__qualname__ = f"Adapted{async_class.__qualname__}"

        return AdaptedProvider


__all__ = [
    "AsyncProviderAdapter",
    "AsyncProviderClassAdapter",
]
