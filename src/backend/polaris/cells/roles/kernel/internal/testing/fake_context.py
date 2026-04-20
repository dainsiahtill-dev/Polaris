"""Fake Context Assembler for testing.

Provides a programmable fake context assembler for testing context
assembly without requiring real workspace or file system access.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable


@runtime_checkable
class ContextAssemblerProtocol(Protocol):
    """Protocol for context assembler implementations.

    This protocol defines the interface expected by kernel components
    for context assembly. Both real and fake implementations must satisfy
    this protocol.
    """

    def build_context(self, request: Any) -> Any:
        """Build context from a request.

        Args:
            request: Context request object.

        Returns:
            Context result object.
        """
        ...

    def build_system_context(self, base_prompt: str, appendix: str | None = None) -> str:
        """Build system context (prompt part).

        Args:
            base_prompt: Base system prompt.
            appendix: Optional appendix to add.

        Returns:
            Complete system prompt.
        """
        ...


@dataclass
class ContextBuildRecord:
    """Record of a context build operation.

    Attributes:
        build_index: Sequential build number (0-indexed).
        request: The request that was used.
        result: The result that was returned.
    """

    build_index: int
    request: Any
    result: Any


@dataclass
class FakeContextResult:
    """Fake context result for testing.

    Mimics the structure of real ContextResult from kernelone.context.contracts.
    """

    messages: list[dict[str, str]] = field(default_factory=list)
    token_estimate: int = 0
    context_sources: tuple[str, ...] = ()
    compression_applied: bool = False
    compression_strategy: str = "none"

    def to_tuple(self) -> tuple:
        """Convert to tuple format for compatibility."""
        return (
            self.messages,
            self.token_estimate,
            self.context_sources,
            self.compression_applied,
            self.compression_strategy,
        )


class FakeContextAssembler:
    """Programmable fake context assembler for testing.

    This class implements ContextAssemblerProtocol and allows tests to
    pre-program context assembly results without requiring real workspace
    or file system access.

    Features:
        - Pre-programmed context results
        - Dynamic context building based on request
        - Build recording and verification
        - Token estimation simulation
        - Compression simulation

    Example:
        >>> assembler = FakeContextAssembler()
        >>> assembler.set_default_result(
        ...     messages=[{"role": "user", "content": "Hello"}],
        ...     token_estimate=10
        ... )
        >>>
        >>> # Use in test
        >>> result = assembler.build_context(request)
        >>> assert result.token_estimate == 10
    """

    def __init__(self) -> None:
        """Initialize the fake context assembler."""
        self._default_result: FakeContextResult = FakeContextResult()
        self._request_handlers: list[tuple[Callable[[Any], bool], FakeContextResult]] = []
        self._build_records: list[ContextBuildRecord] = []
        self._build_count: int = 0
        self._system_context_template: str = "{base_prompt}\n\n{appendix}"
        self._message_transformer: Callable[[list[dict[str, str]]], list[dict[str, str]]] | None = None

    @property
    def build_count(self) -> int:
        """Number of context builds performed."""
        return self._build_count

    @property
    def build_records(self) -> list[ContextBuildRecord]:
        """Get a copy of all build records."""
        return copy.deepcopy(self._build_records)

    def set_default_result(
        self,
        messages: list[dict[str, str]] | None = None,
        token_estimate: int = 0,
        context_sources: tuple[str, ...] | None = None,
        compression_applied: bool = False,
        compression_strategy: str = "none",
    ) -> FakeContextAssembler:
        """Set the default result for all context builds.

        Args:
            messages: List of message dictionaries.
            token_estimate: Estimated token count.
            context_sources: Tuple of context source names.
            compression_applied: Whether compression was applied.
            compression_strategy: Name of compression strategy used.

        Returns:
            Self for method chaining.
        """
        self._default_result = FakeContextResult(
            messages=list(messages) if messages else [],
            token_estimate=token_estimate,
            context_sources=context_sources or (),
            compression_applied=compression_applied,
            compression_strategy=compression_strategy,
        )
        return self

    def add_request_handler(
        self,
        matcher: Callable[[Any], bool],
        result: FakeContextResult,
    ) -> FakeContextAssembler:
        """Add a handler for specific request patterns.

        Handlers are checked in order before falling back to default result.

        Args:
            matcher: Function that takes a request and returns True if this handler applies.
            result: Result to return when matcher returns True.

        Returns:
            Self for method chaining.
        """
        self._request_handlers.append((matcher, result))
        return self

    def set_system_context_template(self, template: str) -> FakeContextAssembler:
        """Set the template for building system context.

        Template should contain {base_prompt} and {appendix} placeholders.

        Args:
            template: Template string with placeholders.

        Returns:
            Self for method chaining.
        """
        self._system_context_template = template
        return self

    def set_message_transformer(
        self,
        transformer: Callable[[list[dict[str, str]]], list[dict[str, str]]],
    ) -> FakeContextAssembler:
        """Set a transformer function for messages.

        The transformer is applied to messages before returning results.

        Args:
            transformer: Function that transforms message list.

        Returns:
            Self for method chaining.
        """
        self._message_transformer = transformer
        return self

    def reset(self) -> FakeContextAssembler:
        """Reset the assembler state.

        Returns:
            Self for method chaining.
        """
        self._default_result = FakeContextResult()
        self._request_handlers.clear()
        self._build_records.clear()
        self._build_count = 0
        self._system_context_template = "{base_prompt}\n\n{appendix}"
        self._message_transformer = None
        return self

    def build_context(self, request: Any) -> FakeContextResult:
        """Build context from a request.

        Args:
            request: Context request object.

        Returns:
            FakeContextResult with assembled context.
        """
        # Check handlers first
        for matcher, result in self._request_handlers:
            try:
                if matcher(request):
                    return self._return_result(result, request)
            except (RuntimeError, ValueError):
                continue

        # Fall back to default
        return self._return_result(self._default_result, request)

    def _return_result(self, result: FakeContextResult, request: Any) -> FakeContextResult:
        """Return a result, applying transformations and recording."""
        # Copy result to avoid mutation
        output = FakeContextResult(
            messages=copy.deepcopy(result.messages),
            token_estimate=result.token_estimate,
            context_sources=result.context_sources,
            compression_applied=result.compression_applied,
            compression_strategy=result.compression_strategy,
        )

        # Apply message transformer if set
        if self._message_transformer is not None:
            output.messages = self._message_transformer(output.messages)

        self._record_build(request, output)
        return output

    def build_system_context(self, base_prompt: str, appendix: str | None = None) -> str:
        """Build system context (prompt part).

        Args:
            base_prompt: Base system prompt.
            appendix: Optional appendix to add.

        Returns:
            Complete system prompt.
        """
        return self._system_context_template.format(
            base_prompt=base_prompt,
            appendix=appendix or "",
        )

    def _record_build(self, request: Any, result: FakeContextResult) -> None:
        """Record a build operation."""
        record = ContextBuildRecord(
            build_index=self._build_count,
            request=copy.deepcopy(request),
            result=copy.deepcopy(result),
        )
        self._build_records.append(record)
        self._build_count += 1

    def assert_build_count(self, expected: int) -> None:
        """Assert that the build count matches expected.

        Args:
            expected: Expected number of builds.

        Raises:
            AssertionError: If build count doesn't match.
        """
        if self._build_count != expected:
            raise AssertionError(f"Expected {expected} context builds, but got {self._build_count}")

    def create_simple_result(
        self,
        user_message: str,
        system_message: str | None = None,
        token_estimate: int | None = None,
    ) -> FakeContextResult:
        """Create a simple context result with user and optional system message.

        Args:
            user_message: The user message content.
            system_message: Optional system message content.
            token_estimate: Optional token estimate (defaults to message length / 4).

        Returns:
            FakeContextResult with the messages.
        """
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": user_message})

        if token_estimate is None:
            token_estimate = len(user_message) // 4 + (len(system_message) // 4 if system_message else 0)

        return FakeContextResult(
            messages=messages,
            token_estimate=token_estimate,
            context_sources=("fake_assembler",),
        )

    def estimate_tokens(self, messages: list[dict[str, str]]) -> int:
        """Estimate token count for messages.

        This is a stub implementation that returns a simple
        estimate based on message content length.

        Args:
            messages: List of message dictionaries.

        Returns:
            Estimated token count.
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += len(content) // 4
        return total

    def compress_context(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
    ) -> tuple[list[dict[str, str]], bool]:
        """Compress context to fit within token budget.

        This is a stub implementation that returns messages as-is
        with compression_applied=False.

        Args:
            messages: List of message dictionaries.
            max_tokens: Maximum tokens allowed.

        Returns:
            Tuple of (compressed messages, whether compression was applied).
        """
        return messages, False


__all__ = [
    "ContextAssemblerProtocol",
    "ContextBuildRecord",
    "FakeContextAssembler",
    "FakeContextResult",
]
