from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult

# Prefix used by invoke_stream to mark reasoning/thinking tokens.
# Callers should strip this prefix to get the raw reasoning text.
THINKING_PREFIX = "\x00THINKING:"


@dataclass
class ProviderInfo:
    """Basic information about an LLM provider"""

    name: str
    type: str
    description: str
    version: str
    author: str
    documentation_url: str
    supported_features: list[str]
    cost_class: str  # LOCAL, FIXED, METERED
    provider_category: str  # "AGENT" or "LLM"
    autonomous_file_access: bool
    requires_file_interfaces: bool
    model_listing_method: str  # "API", "TUI", "NONE"


@dataclass
class ProviderConfigValidationResult:
    """Result of provider configuration validation.

    Note: This is distinct from other ValidationResult types:
    - ToolArgValidationResult: Tool argument validation
    - FileOpValidationResult: File operation validation
    - LaunchValidationResult: Bootstrap launch validation
    - SchemaValidationResult: Schema validation
    """

    valid: bool
    errors: list[str]
    warnings: list[str]
    normalized_config: dict[str, Any] | None = None


# Backward compatibility alias (deprecated)
ValidationResult = ProviderConfigValidationResult


@dataclass
class ThinkingInfo:
    """Information about thinking/reasoning capabilities"""

    supports_thinking: bool
    confidence: float
    format: str | None
    thinking_text: str | None
    extraction_method: str


@dataclass
class WorkingDirConfig:
    """Configuration for working directory handling"""

    target_directory: str | None
    auto_create: bool
    cleanup_after: bool
    environment_vars: dict[str, str]


class BaseProvider(ABC):
    """Base interface for all LLM providers"""

    @classmethod
    @abstractmethod
    def get_provider_info(cls) -> ProviderInfo:
        """Get basic information about this provider"""
        pass

    @classmethod
    @abstractmethod
    def get_default_config(cls) -> dict[str, Any]:
        """Get default configuration for this provider"""
        pass

    @classmethod
    @abstractmethod
    def validate_config(cls, config: dict[str, Any]) -> ValidationResult:
        """Validate provider configuration"""
        pass

    @abstractmethod
    def health(self, config: dict[str, Any]) -> HealthResult:
        """Check if the provider is accessible and working"""
        pass

    @abstractmethod
    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        """List available models for this provider"""
        pass

    @abstractmethod
    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        """Invoke the LLM with the given prompt and model"""
        pass

    async def invoke_stream(self, prompt: str, model: str, config: dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        Stream invoke the LLM with the given prompt and model.
        """
        # Default fallback for providers without native streaming:
        # be explicit and honest (single-shot output), do not synthesize
        # typewriter-style pseudo streaming.
        result = self.invoke(prompt, model, config)
        if result.ok and result.output:
            yield result.output
        elif result.error:
            yield f"Error: {result.error}"

    async def invoke_stream_events(
        self, prompt: str, model: str, config: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield provider-native structured streaming events when supported.

        Providers that can surface raw SSE / structured deltas should override
        this method. Callers must feature-detect the override and fall back to
        ``invoke_stream()`` when absent.
        """

        del prompt, model, config
        if False:
            yield {}
        raise NotImplementedError("Structured streaming events are not implemented")

    @classmethod
    def extract_thinking_support(cls, response: dict[str, Any]) -> ThinkingInfo:
        """Extract thinking/reasoning information from response"""
        return ThinkingInfo(
            supports_thinking=False, confidence=0.0, format=None, thinking_text=None, extraction_method="default"
        )

    @classmethod
    def get_working_directory_config(cls, config: dict[str, Any]) -> WorkingDirConfig:
        """Get working directory configuration"""
        return WorkingDirConfig(
            target_directory=config.get("working_dir"),
            auto_create=False,
            cleanup_after=False,
            environment_vars=config.get("env", {}),
        )

    @classmethod
    def supports_feature(cls, feature: str) -> bool:
        """Check if provider supports a specific feature"""
        provider_info = cls.get_provider_info()
        return feature in provider_info.supported_features

    @classmethod
    def is_agent_provider(cls) -> bool:
        """Check if this is an Agent-type provider"""
        provider_info = cls.get_provider_info()
        return provider_info.provider_category == "AGENT"

    @classmethod
    def is_llm_provider(cls) -> bool:
        """Check if this is a pure LLM provider"""
        provider_info = cls.get_provider_info()
        return provider_info.provider_category == "LLM"

    @classmethod
    def requires_file_interfaces(cls) -> bool:
        """Check if provider requires file management interfaces"""
        provider_info = cls.get_provider_info()
        return provider_info.requires_file_interfaces

    @classmethod
    def has_autonomous_file_access(cls) -> bool:
        """Check if provider has autonomous file access"""
        provider_info = cls.get_provider_info()
        return provider_info.autonomous_file_access

    @classmethod
    def get_model_listing_method(cls) -> str:
        """Get the method used for model listing"""
        provider_info = cls.get_provider_info()
        return provider_info.model_listing_method


class ProviderRegistry:
    """Registry for managing LLM providers"""

    def __init__(self) -> None:
        self._providers: dict[str, type[BaseProvider]] = {}
        self._lock = threading.RLock()

    def register(self, provider_type: str, provider_class: type[BaseProvider]) -> None:
        """Register a provider class"""
        normalized = str(provider_type or "").strip().lower()
        if not normalized:
            raise ValueError("provider_type is required")
        with self._lock:
            self._providers[normalized] = provider_class

    def unregister(self, provider_type: str) -> bool:
        """Remove a provider class from the registry."""
        normalized = str(provider_type or "").strip().lower()
        if not normalized:
            return False
        with self._lock:
            return self._providers.pop(normalized, None) is not None

    def clear(self) -> None:
        """Clear all registered providers."""
        with self._lock:
            self._providers.clear()

    def get_provider(self, provider_type: str) -> type[BaseProvider] | None:
        """Get a provider class by type"""
        normalized = str(provider_type or "").strip().lower()
        if not normalized:
            return None
        with self._lock:
            return self._providers.get(normalized)

    def list_provider_types(self) -> list[str]:
        """List registered provider type identifiers."""
        with self._lock:
            return list(self._providers.keys())

    def list_providers(self) -> list[ProviderInfo]:
        """List all registered providers"""
        with self._lock:
            providers = list(self._providers.values())
        return [provider_class.get_provider_info() for provider_class in providers]

    def get_provider_info(self, provider_type: str) -> ProviderInfo | None:
        """Get provider information by type"""
        provider_class = self.get_provider(provider_type)
        return provider_class.get_provider_info() if provider_class else None


__all__ = [
    "THINKING_PREFIX",
    "BaseProvider",
    "ProviderInfo",
    "ProviderRegistry",
    "ThinkingInfo",
    "ValidationResult",
    "WorkingDirConfig",
]
