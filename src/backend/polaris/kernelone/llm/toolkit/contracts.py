"""LLM Toolkit Core Contracts - 基础契约定义

此模块定义了core层使用的基础契约，避免core层依赖app层。
这些契约是core层和app层之间的桥梁，实现了依赖倒置原则。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..error_categories import ErrorCategory
from ..shared_contracts import (
    AIRequest,
    AIResponse,
    CompressionResult,
    ModelSpec,
    StreamEventType,
    TaskType,
    TokenBudgetDecision,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@dataclass
class StreamChunk:
    """One chunk in a streaming response from an LLM provider.

    Represents a single unit of streamed content delivered asynchronously.
    Chunks are accumulated by the caller to reconstruct the full response.

    Attributes:
        content: The text content of this chunk.
        event_type: The stream event type (CHUNK, ERROR, etc.).
        metadata: Arbitrary per-chunk metadata (e.g. token counts).
        is_final: True when this is the last chunk in the stream.
    """

    content: str = ""
    event_type: StreamEventType = StreamEventType.CHUNK
    metadata: dict[str, Any] = field(default_factory=dict)
    is_final: bool = False


@dataclass
class AIError:
    """Structured error raised when an LLM provider call fails.

    Provides machine-readable categorization to enable retry decisions
    and error classification without parsing free-text messages.

    Attributes:
        message: Human-readable error description.
        category: Canonical error category (e.g. RATE_LIMIT, AUTH_FAILURE).
        retryable: True if the call can safely be retried without changes.
        details: Optional provider-specific error details.
    """

    message: str
    category: ErrorCategory = ErrorCategory.UNKNOWN
    retryable: bool = False
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this error to a JSON-compatible dictionary.

        Omits the ``details`` key when it is None to keep output minimal.

        Returns:
            Dictionary with message, category, retryable, and optional details.
        """
        result: dict[str, Any] = {
            "message": self.message,
            "category": self.category.value,
            "retryable": self.retryable,
        }
        if self.details:
            result["details"] = self.details
        return result


# ═══════════════════════════════════════════════════════════════════
# Token 估算接口 (抽象，避免直接依赖app层实现)
# ═══════════════════════════════════════════════════════════════════


class TokenEstimatorPort(ABC):
    """Abstract interface for token-count estimation.

    Used by the KernelOne context engine to budget tokens before sending
    requests to the LLM. Implementations are injected at bootstrap.

    The default implementation is ``TokenEstimatorAdapter`` from
    ``polaris.kernelone.llm.engine.token_estimator``.
    """

    @abstractmethod
    def estimate_tokens(self, text: str, model: str | None = None) -> int:
        """Estimate the number of tokens in a single text string.

        Args:
            text: The string to estimate. Must not be None.
            model: Target model identifier (e.g. "gpt-4o"). If None,
                uses a model-agnostic heuristic.

        Returns:
            Estimated token count. Always non-negative.
        """
        pass

    @abstractmethod
    def estimate_messages_tokens(self, messages: list[dict[str, str]], model: str | None = None) -> int:
        """Estimate the total tokens for a list of chat messages.

        Counts both the content and the overhead (role markers, formatting)
        per the target model's tokenization scheme.

        Args:
            messages: List of ``{"role": str, "content": str}`` dicts
                in the same format accepted by the LLM chat API.
            model: Target model identifier. If None, uses a default.

        Returns:
            Estimated total token count for the entire message list.
        """
        pass


# ═══════════════════════════════════════════════════════════════════
# Provider 接口 (抽象，避免直接依赖app层实现)
# ═══════════════════════════════════════════════════════════════════


class ProviderPort(ABC):
    """Abstract interface for LLM provider operations.

    All LLM providers (OpenAI, Anthropic, Ollama, etc.) must implement
    this interface so that the KernelOne runtime is provider-agnostic.
    Implementations are registered with ``ServiceLocator`` at bootstrap.
    """

    @abstractmethod
    async def generate(self, request: AIRequest) -> AIResponse:
        """Generate a non-streaming LLM response for the given request.

        Args:
            request: The AI call request containing model, messages, and options.

        Returns:
            AIResponse with generated text and usage metadata.

        Raises:
            AIError: When the provider call fails; ``AIError.retryable``
                indicates whether the error is safe to retry.
        """
        pass

    @abstractmethod
    async def generate_stream(self, request: AIRequest) -> AsyncGenerator[StreamChunk, None]:
        """Generate a streaming LLM response for the given request.

        Args:
            request: The AI call request containing model, messages, and options.

        Yields:
            StreamChunk events: content chunks, reasoning chunks, and final
                metadata. The caller assembles these into a complete response.

        Raises:
            AIError: When the provider call fails; the generator should
                yield an error chunk rather than raising synchronously.
        """
        pass


# ═══════════════════════════════════════════════════════════════════
# 服务定位器 (用于依赖注入)
# ═══════════════════════════════════════════════════════════════════


class _ServiceLocator:
    """服务定位器 - 用于core层获取app层实现

    这是一个简单的依赖注入容器，允许app层注册实现，core层获取使用。
    避免了直接的循环依赖。
    """

    _registry_lock = threading.RLock()

    _token_estimator: TokenEstimatorPort | None = None
    _provider: ProviderPort | None = None

    @classmethod
    def register_token_estimator(cls, estimator: TokenEstimatorPort) -> None:
        """注册token估算器实现"""
        with cls._registry_lock:
            cls._token_estimator = estimator

    @classmethod
    def get_token_estimator(cls) -> TokenEstimatorPort | None:
        """获取token估算器实现"""
        with cls._registry_lock:
            estimator = cls._token_estimator
        if estimator is not None:
            return estimator

        cls._register_default_token_estimator()
        with cls._registry_lock:
            return cls._token_estimator

    @classmethod
    def register_provider(cls, provider: ProviderPort) -> None:
        """注册provider实现"""
        with cls._registry_lock:
            cls._provider = provider

    @classmethod
    def get_provider(cls) -> ProviderPort | None:
        """获取provider实现"""
        with cls._registry_lock:
            return cls._provider

    @classmethod
    def reset(cls) -> None:
        """Reset locator state for tests and isolated bootstrap."""
        with cls._registry_lock:
            cls._token_estimator = None
            cls._provider = None

    @classmethod
    def _register_default_token_estimator(cls) -> None:
        with cls._registry_lock:
            if cls._token_estimator is not None:
                return
        try:
            from polaris.kernelone.llm.engine.token_estimator import TokenEstimatorAdapter
        except ImportError:
            return
        with cls._registry_lock:
            if cls._token_estimator is None:
                cls._token_estimator = TokenEstimatorAdapter()


ServiceLocator = _ServiceLocator


__all__ = [
    "AIError",
    "AIRequest",
    "AIResponse",
    "CompressionResult",
    "ErrorCategory",
    # 数据类
    "ModelSpec",
    "ProviderPort",
    # 服务定位器
    "ServiceLocator",
    "StreamChunk",
    "StreamEventType",
    # 枚举
    "TaskType",
    "TokenBudgetDecision",
    # 接口
    "TokenEstimatorPort",
    "Usage",
]
