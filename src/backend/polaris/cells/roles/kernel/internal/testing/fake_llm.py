"""Fake LLM Invoker for testing.

Provides a programmable fake LLM implementation that satisfies the
LLMInvokerProtocol interface used by the kernel.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from polaris.cells.roles.kernel.internal.testing.exceptions import FakeLLMExhaustedError

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable


@runtime_checkable
class LLMInvokerProtocol(Protocol):
    """Protocol for LLM invoker implementations.

    This protocol defines the interface expected by kernel components
    for LLM invocation. Both real and fake implementations must satisfy
    this protocol.
    """

    async def call(
        self,
        profile: Any,
        system_prompt: str,
        context: Any,
        response_model: type | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> Any:
        """Invoke LLM with non-streaming mode."""
        ...

    async def call_stream(
        self,
        profile: Any,
        system_prompt: str,
        context: Any,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Invoke LLM with streaming mode."""
        ...


@dataclass
class LLMCallRecord:
    """Record of a single LLM call for verification.

    Attributes:
        call_index: Sequential call number (0-indexed).
        profile: The role profile used for the call.
        system_prompt: The system prompt sent to LLM.
        context: The context request.
        response_model: Optional structured output model.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens to generate.
        kwargs: Additional keyword arguments.
    """

    call_index: int
    profile: Any
    system_prompt: str
    context: Any
    response_model: type | None
    temperature: float
    max_tokens: int
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponseBuilder:
    """Builder for constructing fake LLM responses.

    Provides a fluent API for building complex LLM responses with
    content, thinking, tool calls, and metadata.

    Example:
        >>> response = (
        ...     LLMResponseBuilder()
        ...     .with_content("I'll help you with that.")
        ...     .with_thinking("The user wants to read a file...")
        ...     .with_tool_call("read_file", {"path": "test.py"})
        ...     .with_metadata({"model": "gpt-4"})
        ...     .build()
        ... )
    """

    _content: str = ""
    _thinking: str = ""
    _tool_calls: list[dict[str, Any]] = field(default_factory=list)
    _metadata: dict[str, Any] = field(default_factory=dict)
    _error: str | None = None
    _error_category: str | None = None
    _token_estimate: int = 0

    def with_content(self, content: str) -> LLMResponseBuilder:
        """Set the response content."""
        self._content = content
        return self

    def with_thinking(self, thinking: str) -> LLMResponseBuilder:
        """Set the thinking/reasoning content."""
        self._thinking = thinking
        return self

    def with_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        call_id: str | None = None,
    ) -> LLMResponseBuilder:
        """Add a tool call to the response.

        Args:
            tool: Tool name.
            args: Tool arguments.
            call_id: Optional unique call identifier.
        """
        call: dict[str, Any] = {
            "tool": tool,
            "args": args,
        }
        if call_id:
            call["call_id"] = call_id
        self._tool_calls.append(call)
        return self

    def with_metadata(self, metadata: dict[str, Any]) -> LLMResponseBuilder:
        """Merge metadata into the response."""
        self._metadata.update(metadata)
        return self

    def with_error(self, error: str, category: str = "unknown") -> LLMResponseBuilder:
        """Mark this response as an error."""
        self._error = error
        self._error_category = category
        return self

    def with_token_estimate(self, tokens: int) -> LLMResponseBuilder:
        """Set the token estimate for this response."""
        self._token_estimate = tokens
        return self

    def build(self) -> dict[str, Any]:
        """Build the response dictionary.

        Returns:
            A dictionary compatible with LLMResponse expectations.
        """
        result: dict[str, Any] = {
            "content": self._content,
            "thinking": self._thinking,
            "tool_calls": list(self._tool_calls),
            "metadata": dict(self._metadata),
            "token_estimate": self._token_estimate,
        }
        if self._error:
            result["error"] = self._error
            result["error_category"] = self._error_category
        return result


class FakeLLMInvoker:
    """Programmable fake LLM invoker for testing.

    This class implements LLMInvokerProtocol and allows tests to pre-program
    a sequence of responses that will be returned in order. It records all
    calls for later verification.

    Features:
        - Pre-programmed response sequences
        - Call recording and verification
        - Exception injection at specific call indices
        - Streaming and non-streaming support
        - Response builder integration

    Example:
        >>> fake_llm = FakeLLMInvoker()
        >>> fake_llm.enqueue_response(
        ...     LLMResponseBuilder()
        ...     .with_content("Hello!")
        ...     .build()
        ... )
        >>> fake_llm.enqueue_exception(ValueError("API Error"), at_call=1)
        >>>
        >>> # Use in test
        >>> response = await fake_llm.call(profile, system_prompt, context)
        >>> assert response.content == "Hello!"
        >>> assert fake_llm.call_count == 1
    """

    def __init__(self) -> None:
        """Initialize the fake LLM invoker."""
        self._responses: list[dict[str, Any] | Exception] = []
        self._call_records: list[LLMCallRecord] = []
        self._call_count: int = 0
        self._response_transformers: list[Callable[[dict[str, Any], LLMCallRecord], dict[str, Any]]] = []

    @property
    def call_count(self) -> int:
        """Number of calls made to this invoker."""
        return self._call_count

    @property
    def call_records(self) -> list[LLMCallRecord]:
        """Get a copy of all call records."""
        return copy.deepcopy(self._call_records)

    def enqueue_response(self, response: dict[str, Any]) -> FakeLLMInvoker:
        """Enqueue a response to be returned on the next call.

        Args:
            response: Response dictionary or LLMResponseBuilder.build() result.

        Returns:
            Self for method chaining.
        """
        self._responses.append(dict(response))
        return self

    def enqueue_responses(self, responses: list[dict[str, Any]]) -> FakeLLMInvoker:
        """Enqueue multiple responses at once.

        Args:
            responses: List of response dictionaries.

        Returns:
            Self for method chaining.
        """
        for response in responses:
            self._responses.append(dict(response))
        return self

    def enqueue_exception(self, exception: Exception, at_call: int | None = None) -> FakeLLMInvoker:
        """Enqueue an exception to be raised.

        Args:
            exception: Exception instance to raise.
            at_call: If specified, raise at this specific call index.
                     Otherwise, raise at the next call.

        Returns:
            Self for method chaining.
        """
        if at_call is not None:
            # Pad with None to reach the target index
            while len(self._responses) < at_call:
                self._responses.append({"content": ""})
            if len(self._responses) == at_call:
                self._responses.append(exception)
            else:
                self._responses[at_call] = exception
        else:
            self._responses.append(exception)
        return self

    def add_response_transformer(
        self,
        transformer: Callable[[dict[str, Any], LLMCallRecord], dict[str, Any]],
    ) -> FakeLLMInvoker:
        """Add a transformer function to modify responses.

        Transformers are applied in order after retrieving the pre-programmed
        response but before returning it. Useful for dynamic response modification.

        Args:
            transformer: Function that takes (response, call_record) and returns modified response.

        Returns:
            Self for method chaining.
        """
        self._response_transformers.append(transformer)
        return self

    def reset(self) -> FakeLLMInvoker:
        """Reset the invoker state, clearing all responses and records.

        Returns:
            Self for method chaining.
        """
        self._responses.clear()
        self._call_records.clear()
        self._call_count = 0
        self._response_transformers.clear()
        return self

    def _record_call(
        self,
        profile: Any,
        system_prompt: str,
        context: Any,
        response_model: type | None,
        temperature: float,
        max_tokens: int,
        **kwargs: Any,
    ) -> LLMCallRecord:
        """Record a call and return the record."""
        record = LLMCallRecord(
            call_index=self._call_count,
            profile=profile,
            system_prompt=system_prompt,
            context=context,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
            kwargs=dict(kwargs),
        )
        self._call_records.append(record)
        return record

    def _get_next_response(self, record: LLMCallRecord) -> dict[str, Any]:
        """Get the next pre-programmed response."""
        if self._call_count >= len(self._responses):
            raise FakeLLMExhaustedError(self._call_count)

        item = self._responses[self._call_count]

        # Increment call count before processing (even for exceptions)
        self._call_count += 1

        if isinstance(item, Exception):
            raise item

        response = dict(item)

        # Apply transformers
        for transformer in self._response_transformers:
            response = transformer(response, record)

        return response

    async def call(
        self,
        profile: Any,
        system_prompt: str,
        context: Any,
        response_model: type | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> Any:
        """Invoke LLM with non-streaming mode.

        Returns a mock LLMResponse object with attributes matching the real implementation.
        """
        record = self._record_call(profile, system_prompt, context, response_model, temperature, max_tokens, **kwargs)

        response_data = self._get_next_response(record)

        # Create a simple object with attributes from the response dict
        class MockLLMResponse:
            def __init__(self, data: dict[str, Any]) -> None:
                self.content: str = data.get("content", "")
                self.thinking: str | None = data.get("thinking") or None
                self.tool_calls: list[dict[str, Any]] = data.get("tool_calls", [])
                self.metadata: dict[str, Any] = data.get("metadata", {})
                self.error: str | None = data.get("error")
                self.error_category: str | None = data.get("error_category")
                self.token_estimate: int = data.get("token_estimate", 0)
                self.tool_call_provider: str = data.get("tool_call_provider", "auto")

            @property
            def is_success(self) -> bool:
                return self.error is None

            @property
            def has_tool_calls(self) -> bool:
                return len(self.tool_calls) > 0

        return MockLLMResponse(response_data)

    async def call_stream(
        self,
        profile: Any,
        system_prompt: str,
        context: Any,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Invoke LLM with streaming mode.

        Yields events that simulate a streaming LLM response.
        """
        record = self._record_call(profile, system_prompt, context, None, temperature, max_tokens, **kwargs)

        response_data = self._get_next_response(record)

        content = response_data.get("content", "")
        thinking = response_data.get("thinking", "")
        tool_calls = response_data.get("tool_calls", [])

        # Yield thinking chunks if present
        if thinking:
            # Split thinking into chunks for realism
            chunk_size = 50
            for i in range(0, len(thinking), chunk_size):
                chunk = thinking[i : i + chunk_size]
                yield {"type": "reasoning_chunk", "content": chunk}

        # Yield content chunks
        if content:
            chunk_size = 100
            for i in range(0, len(content), chunk_size):
                chunk = content[i : i + chunk_size]
                yield {"type": "chunk", "content": chunk}

        # Yield tool calls
        for call in tool_calls:
            yield {
                "type": "tool_call",
                "tool": call.get("tool"),
                "args": call.get("args", {}),
                "call_id": call.get("call_id", ""),
            }

        # Yield completion
        yield {"type": "complete"}

    def assert_call_count(self, expected: int) -> None:
        """Assert that the call count matches expected.

        Args:
            expected: Expected number of calls.

        Raises:
            AssertionError: If call count doesn't match.
        """
        if self._call_count != expected:
            raise AssertionError(f"Expected {expected} LLM calls, but got {self._call_count}")

    def assert_called_with(
        self,
        call_index: int,
        **expected_kwargs: Any,
    ) -> None:
        """Assert that a specific call was made with expected arguments.

        Args:
            call_index: Index of the call to check.
            **expected_kwargs: Expected argument values.

        Raises:
            AssertionError: If call doesn't match expectations.
            IndexError: If call_index is out of range.
        """
        if call_index >= len(self._call_records):
            raise IndexError(f"Call index {call_index} out of range (only {len(self._call_records)} calls made)")

        record = self._call_records[call_index]

        for key, expected_value in expected_kwargs.items():
            actual_value = getattr(record, key, None)
            if actual_value != expected_value:
                raise AssertionError(f"Call {call_index}: Expected {key}={expected_value!r}, got {actual_value!r}")


__all__ = [
    "FakeLLMExhaustedError",
    "FakeLLMInvoker",
    "LLMCallRecord",
    "LLMInvokerProtocol",
    "LLMResponseBuilder",
]
