"""LLM Response Types.

Defines the canonical response structures for LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMResponse:
    """LLM Response result.

    Attributes:
        content: The response text content
        token_estimate: Estimated token count
        error: Error message if call failed
        error_category: Error category (timeout, network, rate_limit, provider, unknown)
        tool_calls: List of native tool calls extracted from response
        tool_call_provider: Provider hint for tool call format
        metadata: Additional metadata about the response
    """

    content: str
    token_estimate: int = 0
    error: str | None = None
    error_category: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_provider: str = "auto"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Check if response was successful."""
        return self.error is None and self.error_category is None

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


@dataclass
class StructuredLLMResponse:
    """Structured LLM Response with validated output.

    Attributes:
        data: Parsed and validated data structure
        raw_content: Raw response content before parsing
        token_estimate: Estimated token count
        error: Error message if parsing failed
        error_category: Error category
        validation_errors: List of validation error messages
        metadata: Additional metadata
    """

    data: dict[str, Any] = field(default_factory=dict)
    raw_content: str = ""
    token_estimate: int = 0
    error: str | None = None
    error_category: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Check if response was successful."""
        return self.error is None and not self.validation_errors

    @property
    def has_data(self) -> bool:
        """Check if response contains valid data."""
        return bool(self.data)


@dataclass
class PreparedLLMRequest:
    """Shared request bundle for sync and streaming LLM calls.

    This encapsulates all prepared data needed for an LLM call,
    including messages, context results, and request options.

    Attributes:
        messages: List of formatted messages
        input_text: Formatted input string for legacy providers
        context_result: Context gateway result
        context_summary: Hash summary of context
        request_options: Provider-specific request options
        ai_request: AIRequest for engine invocation
        native_tool_schemas: OpenAI-format tool schemas
        native_tool_mode: Tool calling mode indicator
        response_model: Pydantic model for structured output
        native_response_format: OpenAI-format response_format payload
        response_format_mode: Response format mode indicator
    """

    messages: list[dict[str, str]]
    input_text: str
    context_result: Any
    context_summary: str
    request_options: dict[str, Any]
    ai_request: Any  # AIRequest from kernelone.llm.engine.contracts
    native_tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    native_tool_mode: str = "disabled"
    response_model: type | None = None
    native_response_format: dict[str, Any] | None = None
    response_format_mode: str = "plain_text"


@dataclass
class NormalizedStreamEvent:
    """Canonical stream event after provider-shape normalization.

    This represents a normalized streaming event that abstracts away
    provider-specific event formats.

    Attributes:
        event_type: Event type (chunk, tool_call, tool_result, complete, error)
        content: Text content for chunk events
        metadata: Provider-specific metadata
        error: Error message for error events
        tool_name: Tool name for tool_call events
        tool_args: Tool arguments for tool_call events
        tool_call_id: Unique call identifier for tool_call events
        tool_result: Tool result payload for tool_result events
    """

    event_type: str
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    tool_result: dict[str, Any] = field(default_factory=dict)

    @property
    def is_chunk(self) -> bool:
        """Check if this is a text chunk event."""
        return self.event_type == "chunk"

    @property
    def is_tool_call(self) -> bool:
        """Check if this is a tool call event."""
        return self.event_type == "tool_call"

    @property
    def is_error(self) -> bool:
        """Check if this is an error event."""
        return self.event_type == "error"

    @property
    def is_complete(self) -> bool:
        """Check if this is a completion event."""
        return self.event_type == "complete"


__all__ = [
    "LLMResponse",
    "NormalizedStreamEvent",
    "PreparedLLMRequest",
    "StructuredLLMResponse",
]
