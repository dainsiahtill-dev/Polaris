"""Compression Strategy Registry - 压缩策略注册表

工厂模式实现，支持策略注册和装饰器语法。
统一管理和选择上下文压缩策略。

Design constraints:
    - KernelOne-only: no Polaris business semantics
    - All text I/O uses explicit UTF-8 encoding
    - 100% type annotations, complete docstrings
    - No hidden side effects: registry state explicitly managed
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from polaris.kernelone.context.contracts import ContextBudget

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompressionCost:
    """Cost estimation for compression operation.

    Attributes:
        tokens: Estimated token count for the content.
        compute_units: Estimated compute cost (arbitrary units).
        latency_ms: Estimated latency in milliseconds.
    """

    tokens: int = 0
    compute_units: float = 0.0
    latency_ms: float = 0.0


@dataclass(frozen=True)
class CompressionResult:
    """Result of a compression operation.

    Attributes:
        content: Compressed content string.
        original_tokens: Token count before compression.
        compressed_tokens: Token count after compression.
        metadata: Additional metadata about the compression.
    """

    content: str
    original_tokens: int = 0
    compressed_tokens: int = 0
    metadata: dict[str, Any] | None = None


@runtime_checkable
class CompressionStrategy(Protocol):
    """Protocol for compression strategies.

    All compression strategies must implement this interface
to be registered and used by the CompressionRegistry.

    Example:
        ```python
        class MyStrategy:
            async def compress(self, content: str, budget: ContextBudget) -> CompressionResult:
                return CompressionResult(content=content[:100])

            def estimate_cost(self, content: str) -> CompressionCost:
                return CompressionCost(tokens=len(content) // 4)
        ```
    """

    async def compress(self, content: str, budget: ContextBudget) -> CompressionResult:
        """Compress content within the given budget.

        Args:
            content: The content to compress.
            budget: The budget constraints for compression.

        Returns:
            CompressionResult with compressed content and metadata.
        """
        ...

    def estimate_cost(self, content: str) -> CompressionCost:
        """Estimate the cost of compressing the given content.

        Args:
            content: The content to estimate cost for.

        Returns:
            CompressionCost with token and compute estimates.
        """
        ...


# Type variable for strategy classes
T = TypeVar("T", bound=CompressionStrategy)


class CompressionRegistry:
    """Registry for compression strategies.

    Implements factory pattern with decorator-based registration.
    Supports strategy selection based on content type and budget.

    Example:
        ```python
        registry = CompressionRegistry()

        # Register via decorator
        @registry.register_as("code")
        class CodeCompressor:
            async def compress(self, content: str, budget: ContextBudget) -> CompressionResult:
                return CompressionResult(content=content)
            def estimate_cost(self, content: str) -> CompressionCost:
                return CompressionCost(tokens=len(content))

        # Register manually
        registry.register("text", TextCompressor())

        # Select strategy
        strategy = registry.select("code", budget)
        ```

    Attributes:
        _strategies: Dictionary mapping strategy names to instances.
        _selectors: List of strategy selection functions.
    """

    def __init__(self) -> None:
        """Initialize an empty compression registry."""
        self._strategies: dict[str, CompressionStrategy] = {}
        self._selectors: list[Callable[[str, ContextBudget], str | None]] = []
        self._register_default_selectors()

    def _register_default_selectors(self) -> None:
        """Register default strategy selection logic."""
        # Content-type based selector
        def content_type_selector(content_type: str, budget: ContextBudget) -> str | None:
            """Select strategy based on content type."""
            content_type = content_type.lower()

            # Direct match
            if content_type in self._strategies:
                return content_type

            # Fuzzy match for common types
            type_mappings: dict[str, list[str]] = {
                "code": ["python", "javascript", "typescript", "java", "cpp", "c", "go", "rust"],
                "text": ["markdown", "txt", "plain"],
                "json": ["yaml", "yml", "toml"],
                "log": ["error", "debug", "info"],
            }

            for strategy_type, subtypes in type_mappings.items():
                if content_type in subtypes and strategy_type in self._strategies:
                    return strategy_type

            return None

        self._selectors.append(content_type_selector)

    def register(self, name: str, strategy: CompressionStrategy) -> CompressionStrategy:
        """Register a compression strategy.

        Args:
            name: Unique identifier for the strategy.
            strategy: The compression strategy instance.

        Returns:
            The registered strategy (for chaining).

        Raises:
            ValueError: If name is empty or strategy doesn't implement the protocol.
        """
        if not name:
            raise ValueError("Strategy name cannot be empty")

        if not isinstance(strategy, CompressionStrategy):
            raise ValueError(
                f"Strategy must implement CompressionStrategy protocol, "
                f"got {type(strategy).__name__}"
            )

        if name in self._strategies:
            logger.warning("Overwriting existing strategy: %s", name)

        self._strategies[name] = strategy
        logger.debug("Registered compression strategy: %s", name)
        return strategy

    def register_as(self, name: str) -> Callable[[type[T]], type[T]]:
        """Decorator to register a strategy class.

        Args:
            name: Unique identifier for the strategy.

        Returns:
            Decorator function that registers the class.

        Example:
            ```python
            registry = CompressionRegistry()

            @registry.register_as("code")
            class CodeCompressor:
                async def compress(self, content: str, budget: ContextBudget) -> CompressionResult:
                    return CompressionResult(content=content)
                def estimate_cost(self, content: str) -> CompressionCost:
                    return CompressionCost(tokens=len(content))
            ```
        """

        def decorator(cls: type[T]) -> type[T]:
            """Register the class as a strategy."""
            instance = cls()
            self.register(name, instance)
            return cls

        return decorator

    def select(self, content_type: str, budget: ContextBudget) -> CompressionStrategy:
        """Select the best compression strategy for given content and budget.

        Tries selectors in order, falls back to "default" or raises error.

        Args:
            content_type: Type of content to compress (e.g., "code", "text", "json").
            budget: Budget constraints for compression.

        Returns:
            The selected compression strategy.

        Raises:
            ValueError: If no suitable strategy is found.
        """
        # Try custom selectors
        for selector in self._selectors:
            strategy_name = selector(content_type, budget)
            if strategy_name and strategy_name in self._strategies:
                logger.debug("Selected strategy '%s' for content type '%s'", strategy_name, content_type)
                return self._strategies[strategy_name]

        # Fall back to default
        if "default" in self._strategies:
            logger.debug("Using default strategy for content type '%s'", content_type)
            return self._strategies["default"]

        raise ValueError(
            f"No compression strategy found for content type '{content_type}'. "
            f"Available strategies: {list(self._strategies.keys())}"
        )

    def list_strategies(self) -> list[str]:
        """List all registered strategy names.

        Returns:
            Sorted list of registered strategy names.
        """
        return sorted(self._strategies.keys())

    def get_strategy(self, name: str) -> CompressionStrategy | None:
        """Get a strategy by name.

        Args:
            name: The strategy name.

        Returns:
            The strategy instance or None if not found.
        """
        return self._strategies.get(name)

    def unregister(self, name: str) -> bool:
        """Unregister a strategy.

        Args:
            name: The strategy name to remove.

        Returns:
            True if removed, False if not found.
        """
        if name in self._strategies:
            del self._strategies[name]
            logger.debug("Unregistered compression strategy: %s", name)
            return True
        return False

    def clear(self) -> None:
        """Clear all registered strategies."""
        self._strategies.clear()
        logger.debug("Cleared all compression strategies")


__all__ = [
    "CompressionCost",
    "CompressionRegistry",
    "CompressionResult",
    "CompressionStrategy",
]
