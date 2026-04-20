"""KernelOne LLM provider interfaces and compatibility exports."""

from __future__ import annotations

from typing import NoReturn

from .base_provider import (
    THINKING_PREFIX,
    BaseProvider,
    ProviderInfo,
    ProviderRegistry,
    ThinkingInfo,
    ValidationResult,
    WorkingDirConfig,
)
from .registry import (
    ProviderManager,
    get_provider_manager,
    get_provider_registry,
    register_provider,
    reset_provider_runtime,
)
from .stream_thinking_parser import ChunkKind, StreamThinkingParser

__all__ = [
    "THINKING_PREFIX",
    "BaseProvider",
    "ChunkKind",
    "ProviderInfo",
    "ProviderManager",
    "ProviderRegistry",
    "StreamThinkingParser",
    "ThinkingInfo",
    "ValidationResult",
    "WorkingDirConfig",
    "get_provider_manager",
    "get_provider_registry",
    "register_provider",
    "reset_provider_runtime",
]


def __getattr__(name: str) -> NoReturn:
    """Refuse direct infrastructure leakage from KernelOne.

    Providers must be registered into the registry by the assembly/bootstrap layer.
    """
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}. "
        "Infrastructure providers must be injected via Registry/Manager ports."
    )
