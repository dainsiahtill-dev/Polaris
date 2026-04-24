"""Tests for LLM Caller module.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.response_types import (
    LLMResponse,
    NormalizedStreamEvent,
    PreparedLLMRequest,
    StructuredLLMResponse,
)


class TestLLMResponse:
    """Test suite for LLMResponse dataclass."""

    @pytest.fixture
    def success_response(self) -> LLMResponse:
        """Create a successful LLM response."""
        return LLMResponse(
            content="Hello, world!",
            token_estimate=10,
            error=None,
            error_category=None,
            tool_calls=[],
            tool_call_provider="openai",
            metadata={"model": "gpt-4"},
        )

    @pytest.fixture
    def error_response(self) -> LLMResponse:
        """Create an error LLM response."""
        return LLMResponse(
            content="",
            token_estimate=0,
            error="Rate limit exceeded",
            error_category="rate_limit",
            tool_calls=[],
            tool_call_provider="openai",
            metadata={"model": "gpt-4"},
        )

    def test_success_response_is_success(self, success_response: LLMResponse) -> None:
        """Test that successful response returns is_success=True."""
        assert success_response.is_success is True

    def test_error_response_is_not_success(self, error_response: LLMResponse) -> None:
        """Test that error response returns is_success=False."""
        assert error_response.is_success is False

    def test_has_tool_calls_with_calls(self) -> None:
        """Test has_tool_calls returns True when tool_calls present."""
        response = LLMResponse(
            content="test",
            tool_calls=[{"tool": "search", "args": {}}],
        )
        assert response.has_tool_calls is True

    def test_has_tool_calls_without_calls(self, success_response: LLMResponse) -> None:
        """Test has_tool_calls returns False when no tool_calls."""
        assert success_response.has_tool_calls is False

    def test_default_values(self) -> None:
        """Test LLMResponse default values."""
        response = LLMResponse(content="test")
        assert response.token_estimate == 0
        assert response.error is None
        assert response.error_category is None
        assert response.tool_calls == []
        assert response.tool_call_provider == "auto"
        assert response.metadata == {}

    def test_empty_content(self) -> None:
        """Test LLMResponse with empty content."""
        response = LLMResponse(content="")
        assert response.content == ""
        assert response.is_success is True


class TestStructuredLLMResponse:
    """Test suite for StructuredLLMResponse dataclass."""

    @pytest.fixture
    def success_structured_response(self) -> StructuredLLMResponse:
        """Create a successful structured response."""
        return StructuredLLMResponse(
            data={"name": "test", "value": 42},
            raw_content='{"name": "test", "value": 42}',
            token_estimate=15,
            error=None,
            error_category=None,
            validation_errors=[],
            metadata={"model": "gpt-4"},
        )

    @pytest.fixture
    def error_structured_response(self) -> StructuredLLMResponse:
        """Create an error structured response."""
        return StructuredLLMResponse(
            data={},
            raw_content="invalid json",
            token_estimate=0,
            error="Parsing failed",
            error_category="validation_fail",
            validation_errors=["Invalid JSON format"],
            metadata={"model": "gpt-4"},
        )

    def test_success_structured_is_success(self, success_structured_response: StructuredLLMResponse) -> None:
        """Test that successful structured response returns is_success=True."""
        assert success_structured_response.is_success is True

    def test_error_structured_is_not_success(self, error_structured_response: StructuredLLMResponse) -> None:
        """Test that error structured response returns is_success=False."""
        assert error_structured_response.is_success is False

    def test_has_data_with_data(self, success_structured_response: StructuredLLMResponse) -> None:
        """Test has_data returns True when data present."""
        assert success_structured_response.has_data is True

    def test_has_data_without_data(self, error_structured_response: StructuredLLMResponse) -> None:
        """Test has_data returns False when data empty."""
        assert error_structured_response.has_data is False

    def test_validation_errors_trigger_not_success(self) -> None:
        """Test that validation_errors make is_success False even without error."""
        response = StructuredLLMResponse(
            data={"valid": True},
            validation_errors=["Field 'x' is required"],
        )
        assert response.is_success is False

    def test_default_values(self) -> None:
        """Test StructuredLLMResponse default values."""
        response = StructuredLLMResponse()
        assert response.data == {}
        assert response.raw_content == ""
        assert response.token_estimate == 0
        assert response.error is None
        assert response.error_category is None
        assert response.validation_errors == []
        assert response.metadata == {}


class TestNormalizedStreamEvent:
    """Test suite for NormalizedStreamEvent dataclass."""

    @pytest.fixture
    def chunk_event(self) -> NormalizedStreamEvent:
        """Create a chunk stream event."""
        return NormalizedStreamEvent(
            event_type="chunk",
            content="Hello",
            metadata={"model": "gpt-4"},
        )

    @pytest.fixture
    def tool_call_event(self) -> NormalizedStreamEvent:
        """Create a tool_call stream event."""
        return NormalizedStreamEvent(
            event_type="tool_call",
            content="",
            tool_name="search",
            tool_args={"query": "test"},
            tool_call_id="call_123",
        )

    @pytest.fixture
    def error_event(self) -> NormalizedStreamEvent:
        """Create an error stream event."""
        return NormalizedStreamEvent(
            event_type="error",
            error="Connection lost",
        )

    @pytest.fixture
    def complete_event(self) -> NormalizedStreamEvent:
        """Create a complete stream event."""
        return NormalizedStreamEvent(
            event_type="complete",
            content="Done",
        )

    def test_is_chunk(self, chunk_event: NormalizedStreamEvent) -> None:
        """Test is_chunk property for chunk events."""
        assert chunk_event.is_chunk is True
        assert chunk_event.is_tool_call is False
        assert chunk_event.is_error is False
        assert chunk_event.is_complete is False

    def test_is_tool_call(self, tool_call_event: NormalizedStreamEvent) -> None:
        """Test is_tool_call property for tool_call events."""
        assert tool_call_event.is_tool_call is True
        assert tool_call_event.is_chunk is False
        assert tool_call_event.is_error is False
        assert tool_call_event.is_complete is False

    def test_is_error(self, error_event: NormalizedStreamEvent) -> None:
        """Test is_error property for error events."""
        assert error_event.is_error is True
        assert error_event.is_chunk is False
        assert error_event.is_tool_call is False
        assert error_event.is_complete is False

    def test_is_complete(self, complete_event: NormalizedStreamEvent) -> None:
        """Test is_complete property for complete events."""
        assert complete_event.is_complete is True
        assert complete_event.is_chunk is False
        assert complete_event.is_tool_call is False
        assert complete_event.is_error is False

    def test_unknown_event_type(self) -> None:
        """Test unknown event type returns False for all properties."""
        event = NormalizedStreamEvent(event_type="unknown")
        assert event.is_chunk is False
        assert event.is_tool_call is False
        assert event.is_error is False
        assert event.is_complete is False

    def test_default_values(self) -> None:
        """Test NormalizedStreamEvent default values."""
        event = NormalizedStreamEvent(event_type="chunk")
        assert event.content == ""
        assert event.metadata == {}
        assert event.error == ""
        assert event.tool_name == ""
        assert event.tool_args == {}
        assert event.tool_call_id == ""
        assert event.tool_result == {}


class TestPreparedLLMRequest:
    """Test suite for PreparedLLMRequest dataclass."""

    def test_default_values(self) -> None:
        """Test PreparedLLMRequest default values."""
        request = PreparedLLMRequest(
            messages=[{"role": "user", "content": "test"}],
            input_text="test",
            context_result=None,
            context_summary="hash123",
            request_options={},
            ai_request=None,
        )
        assert request.native_tool_schemas == []
        assert request.native_tool_mode == "disabled"
        assert request.response_model is None
        assert request.native_response_format is None
        assert request.response_format_mode == "plain_text"

    def test_with_tool_schemas(self) -> None:
        """Test PreparedLLMRequest with tool schemas."""
        tool_schemas = [{"type": "function", "function": {"name": "search"}}]
        request = PreparedLLMRequest(
            messages=[{"role": "user", "content": "test"}],
            input_text="test",
            context_result=None,
            context_summary="hash123",
            request_options={},
            ai_request=None,
            native_tool_schemas=tool_schemas,
            native_tool_mode="native_tools",
        )
        assert request.native_tool_schemas == tool_schemas
        assert request.native_tool_mode == "native_tools"

    def test_with_response_format(self) -> None:
        """Test PreparedLLMRequest with response format."""
        response_format = {"type": "json_object"}
        request = PreparedLLMRequest(
            messages=[{"role": "user", "content": "test"}],
            input_text="test",
            context_result=None,
            context_summary="hash123",
            request_options={},
            ai_request=None,
            native_response_format=response_format,
            response_format_mode="json",
        )
        assert request.native_response_format == response_format
        assert request.response_format_mode == "json"


class TestLLMResponseEdgeCases:
    """Test edge cases for LLMResponse."""

    def test_none_error_category(self) -> None:
        """Test LLMResponse with None error_category."""
        response = LLMResponse(content="test", error=None, error_category=None)
        assert response.is_success is True

    def test_empty_error_string(self) -> None:
        """Test LLMResponse with empty error string."""
        response = LLMResponse(content="test", error="", error_category=None)
        # Empty string is falsy but not None, so is_success should be False
        assert response.is_success is False

    def test_whitespace_error(self) -> None:
        """Test LLMResponse with whitespace-only error."""
        response = LLMResponse(content="test", error="   ", error_category=None)
        # Whitespace is truthy string, so is_success should be False
        assert response.is_success is False

    def test_large_content(self) -> None:
        """Test LLMResponse with large content."""
        large_content = "x" * 100000
        response = LLMResponse(content=large_content, token_estimate=25000)
        assert response.content == large_content
        assert response.token_estimate == 25000

    def test_unicode_content(self) -> None:
        """Test LLMResponse with unicode content."""
        unicode_content = "你好世界 🌍"
        response = LLMResponse(content=unicode_content)
        assert response.content == unicode_content


class TestStructuredLLMResponseEdgeCases:
    """Test edge cases for StructuredLLMResponse."""

    def test_partial_data(self) -> None:
        """Test StructuredLLMResponse with partial data."""
        response = StructuredLLMResponse(
            data={"name": "test"},
            raw_content='{"name": "test"}',
        )
        assert response.has_data is True
        assert response.is_success is True

    def test_nested_data(self) -> None:
        """Test StructuredLLMResponse with nested data."""
        response = StructuredLLMResponse(
            data={"user": {"name": "test", "scores": [1, 2, 3]}},
        )
        assert response.has_data is True

    def test_multiple_validation_errors(self) -> None:
        """Test StructuredLLMResponse with multiple validation errors."""
        response = StructuredLLMResponse(
            data={},
            validation_errors=["Error 1", "Error 2", "Error 3"],
        )
        assert len(response.validation_errors) == 3
        assert response.is_success is False


class TestNormalizedStreamEventEdgeCases:
    """Test edge cases for NormalizedStreamEvent."""

    def test_empty_tool_args(self) -> None:
        """Test NormalizedStreamEvent with empty tool args."""
        event = NormalizedStreamEvent(
            event_type="tool_call",
            tool_name="search",
            tool_args={},
        )
        assert event.is_tool_call is True
        assert event.tool_args == {}

    def test_complex_tool_result(self) -> None:
        """Test NormalizedStreamEvent with complex tool result."""
        complex_result = {"data": {"items": [1, 2, 3]}, "status": "ok"}
        event = NormalizedStreamEvent(
            event_type="tool_result",
            tool_name="search",
            tool_result=complex_result,
        )
        assert event.tool_result == complex_result

    def test_error_with_content(self) -> None:
        """Test error event with both error and content."""
        event = NormalizedStreamEvent(
            event_type="error",
            content="Partial response",
            error="Connection timeout",
        )
        assert event.is_error is True
        assert event.content == "Partial response"
        assert event.error == "Connection timeout"
