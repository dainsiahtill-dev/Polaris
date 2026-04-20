from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


class SDKUnavailableError(RuntimeError):
    """Raised when a required SDK dependency is missing."""


@dataclass
class SDKConfig:
    """Configuration for SDK clients."""

    api_key: str | None = None
    base_url: str | None = None
    timeout: int = 60
    max_retries: int = 3
    headers: dict[str, str] | None = None
    additional_params: dict[str, Any] = field(default_factory=dict)


@dataclass
class SDKMessage:
    """Unified message format for SDK calls."""

    role: str
    content: str
    thinking: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SDKResponse:
    """Unified response format for SDK calls."""

    content: str
    thinking: str | None = None
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Truncation-related fields
    truncated: bool = False
    truncation_reason: str | None = None
    finish_reason: str | None = None


class BaseLLMSDK(ABC):
    """Base interface for SDK-backed LLM clients."""

    def __init__(self, config: SDKConfig) -> None:
        self.config = config

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the SDK is reachable with current credentials."""
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        """List available models via SDK."""
        raise NotImplementedError

    @abstractmethod
    def invoke(self, messages: list[SDKMessage], model: str, **kwargs: Any) -> SDKResponse:
        """Invoke the model and return a response."""
        raise NotImplementedError

    @abstractmethod
    def invoke_stream(self, messages: list[SDKMessage], model: str, **kwargs: Any) -> Iterable[str]:
        """Stream model output chunks."""
        raise NotImplementedError

    @abstractmethod
    def supports_feature(self, feature: str) -> bool:
        """Check if a feature is supported by this SDK."""
        raise NotImplementedError
