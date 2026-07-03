"""Async base provider interface for LLM providers.

This module provides an async version of the BaseProvider interface
for providers that use native async I/O (httpx, aiohttp).

The sync BaseProvider is preserved for backward compatibility.
Async providers should inherit from AsyncBaseProvider instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.providers.base_provider import (
    ProviderConfigValidationResult,
    ProviderInfo,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult


class AsyncBaseProvider(ABC):
    """Async base interface for LLM providers using native async I/O.

    This is the async equivalent of BaseProvider. Providers that use
    httpx.AsyncClient or other async HTTP clients should inherit from
    this class instead of BaseProvider.

    Key differences from BaseProvider:
        - health(), list_models(), invoke() are async methods
        - invoke_stream() yields dicts (not strings) for structured events
    """

    @classmethod
    @abstractmethod
    def get_provider_info(cls) -> ProviderInfo:
        """Get basic information about this provider."""
        ...

    @classmethod
    @abstractmethod
    def get_default_config(cls) -> dict[str, Any]:
        """Get default configuration for this provider."""
        ...

    @classmethod
    @abstractmethod
    def validate_config(cls, config: dict[str, Any]) -> ProviderConfigValidationResult:
        """Validate provider configuration."""
        ...

    @abstractmethod
    async def health(self, config: dict[str, Any]) -> HealthResult:
        """Check provider health (async)."""
        ...

    @abstractmethod
    async def list_models(self, config: dict[str, Any]) -> ModelListResult:
        """List available models (async)."""
        ...

    @abstractmethod
    async def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        """Invoke the model with a prompt (async)."""
        ...

    @abstractmethod
    def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """Stream invoke results as structured events (async generator).

        Yields dicts with keys:
            - "error": True if error occurred
            - "code": HTTP status code (optional)
            - "message": Error message (optional)
            - Provider-specific response data
        """
        ...


__all__ = [
    "AsyncBaseProvider",
    "ProviderConfigValidationResult",
    "ProviderInfo",
]
