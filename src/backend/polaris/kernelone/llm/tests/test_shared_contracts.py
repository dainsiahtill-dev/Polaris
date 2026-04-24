"""Tests for polaris.kernelone.llm.shared_contracts module.

Covers:
- TaskType enum values
- StreamEventType enum and from_string method
- ModelSpec dataclass and to_dict
- CompressionResult dataclass and to_dict
- TokenBudgetDecision dataclass and to_dict
- AIRequest dataclass with factory methods
- Usage dataclass with factory methods
- AIResponse dataclass with factory methods
- ProviderFormatter protocol
"""

from __future__ import annotations

from polaris.kernelone.errors import ErrorCategory
from polaris.kernelone.llm.shared_contracts import (
    AIRequest,
    AIResponse,
    CompressionResult,
    ModelSpec,
    ProviderFormatter,
    StreamEventType,
    TaskType,
    TokenBudgetDecision,
    Usage,
)


class TestTaskType:
    """Tests for TaskType enum."""

    def test_task_type_values(self) -> None:
        """Verify all expected TaskType values exist."""
        assert TaskType.DIALOGUE.value == "dialogue"
        assert TaskType.INTERVIEW.value == "interview"
        assert TaskType.EVALUATION.value == "evaluation"
        assert TaskType.READINESS.value == "readiness"
        assert TaskType.GENERATION.value == "generation"
        assert TaskType.CLASSIFICATION.value == "classification"

    def test_task_type_is_string_enum(self) -> None:
        """Verify TaskType is a string enum."""
        assert isinstance(TaskType.DIALOGUE, str)
        assert TaskType.DIALOGUE == "dialogue"


class TestStreamEventType:
    """Tests for StreamEventType enum."""

    def test_event_type_values(self) -> None:
        """Verify all expected StreamEventType values exist."""
        assert StreamEventType.CHUNK.value == "chunk"
        assert StreamEventType.REASONING_CHUNK.value == "reasoning_chunk"
        assert StreamEventType.TOOL_START.value == "tool_start"
        assert StreamEventType.TOOL_CALL.value == "tool_call"
        assert StreamEventType.TOOL_END.value == "tool_end"
        assert StreamEventType.TOOL_RESULT.value == "tool_result"
        assert StreamEventType.META.value == "meta"
        assert StreamEventType.COMPLETE.value == "complete"
        assert StreamEventType.ERROR.value == "error"

    def test_from_string_valid(self) -> None:
        """Verify from_string converts valid strings."""
        assert StreamEventType.from_string("chunk") == StreamEventType.CHUNK
        assert StreamEventType.from_string("tool_call") == StreamEventType.TOOL_CALL
        assert StreamEventType.from_string("complete") == StreamEventType.COMPLETE

    def test_from_string_invalid_returns_error(self) -> None:
        """Verify from_string returns ERROR for invalid strings."""
        assert StreamEventType.from_string("invalid") == StreamEventType.ERROR
        assert StreamEventType.from_string("") == StreamEventType.ERROR
        assert StreamEventType.from_string("unknown_type") == StreamEventType.ERROR


class TestModelSpec:
    """Tests for ModelSpec dataclass."""

    def test_default_initialization(self) -> None:
        """Verify ModelSpec with default values."""
        spec = ModelSpec(provider_id="openai", provider_type="openai", model="gpt-4")

        assert spec.provider_id == "openai"
        assert spec.provider_type == "openai"
        assert spec.model == "gpt-4"
        assert spec.max_context_tokens == 32768
        assert spec.max_output_tokens == 4096
        assert spec.tokenizer == "char_estimate"
        assert spec.supports_tools is False
        assert spec.supports_json_schema is False
        assert spec.supports_vision is False
        assert spec.cost_hint is None

    def test_custom_initialization(self) -> None:
        """Verify ModelSpec with custom values."""
        spec = ModelSpec(
            provider_id="anthropic",
            provider_type="anthropic",
            model="claude-3-opus",
            max_context_tokens=200000,
            max_output_tokens=8192,
            supports_tools=True,
            supports_json_schema=True,
            cost_hint="$0.015/1K tokens",
        )

        assert spec.max_context_tokens == 200000
        assert spec.max_output_tokens == 8192
        assert spec.supports_tools is True
        assert spec.supports_json_schema is True
        assert spec.cost_hint == "$0.015/1K tokens"

    def test_to_dict(self) -> None:
        """Verify ModelSpec.to_dict produces correct dictionary."""
        spec = ModelSpec(
            provider_id="test",
            provider_type="test",
            model="test-model",
            supports_tools=True,
        )
        result = spec.to_dict()

        assert isinstance(result, dict)
        assert result["provider_id"] == "test"
        assert result["provider_type"] == "test"
        assert result["model"] == "test-model"
        assert result["supports_tools"] is True


class TestCompressionResult:
    """Tests for CompressionResult dataclass."""

    def test_default_initialization(self) -> None:
        """Verify CompressionResult with default values."""
        result = CompressionResult(
            compressed_input="compressed text",
            original_tokens=1000,
            compressed_tokens=500,
        )

        assert result.compressed_input == "compressed text"
        assert result.original_tokens == 1000
        assert result.compressed_tokens == 500
        assert result.strategy == "none"
        assert result.quality_flag == "ok"
        assert result.drop_ratio == 0.0
        assert result.notes == []

    def test_to_dict(self) -> None:
        """Verify CompressionResult.to_dict produces correct dictionary."""
        result = CompressionResult(
            compressed_input="compressed",
            original_tokens=100,
            compressed_tokens=50,
            strategy="truncation",
            quality_flag="good",
            drop_ratio=0.5,
            notes=["note1", "note2"],
        )
        dict_result = result.to_dict()

        assert isinstance(dict_result, dict)
        assert dict_result["compressed_input"] == "compressed"
        assert dict_result["original_tokens"] == 100
        assert dict_result["compressed_tokens"] == 50
        assert dict_result["strategy"] == "truncation"
        assert dict_result["quality_flag"] == "good"
        assert dict_result["drop_ratio"] == 0.5
        assert dict_result["notes"] == ["note1", "note2"]


class TestTokenBudgetDecision:
    """Tests for TokenBudgetDecision dataclass."""

    def test_allowed_decision(self) -> None:
        """Verify TokenBudgetDecision for allowed request."""
        decision = TokenBudgetDecision(
            allowed=True,
            max_context_tokens=32768,
            allowed_prompt_tokens=28000,
            requested_prompt_tokens=25000,
            reserved_output_tokens=4096,
            safety_margin_tokens=172,
        )

        assert decision.allowed is True
        assert decision.compression_applied is False
        assert decision.compression is None
        assert decision.error is None

    def test_denied_decision(self) -> None:
        """Verify TokenBudgetDecision for denied request."""
        decision = TokenBudgetDecision(
            allowed=False,
            max_context_tokens=32768,
            allowed_prompt_tokens=0,
            requested_prompt_tokens=50000,
            reserved_output_tokens=4096,
            safety_margin_tokens=172,
            error="exceeds context limit",
        )

        assert decision.allowed is False
        assert decision.error == "exceeds context limit"

    def test_decision_with_compression(self) -> None:
        """Verify TokenBudgetDecision with compression applied."""
        compression = CompressionResult(
            compressed_input="compressed",
            original_tokens=1000,
            compressed_tokens=500,
            strategy="smart",
        )
        decision = TokenBudgetDecision(
            allowed=True,
            max_context_tokens=32768,
            allowed_prompt_tokens=28000,
            requested_prompt_tokens=25000,
            reserved_output_tokens=4096,
            safety_margin_tokens=172,
            compression_applied=True,
            compression=compression,
        )

        assert decision.compression_applied is True
        assert decision.compression is not None
        assert decision.compression.strategy == "smart"

    def test_to_dict_without_compression(self) -> None:
        """Verify to_dict without compression."""
        decision = TokenBudgetDecision(
            allowed=True,
            max_context_tokens=32768,
            allowed_prompt_tokens=28000,
            requested_prompt_tokens=25000,
            reserved_output_tokens=4096,
            safety_margin_tokens=172,
        )
        result = decision.to_dict()

        assert isinstance(result, dict)
        assert result["allowed"] is True
        assert result["max_context_tokens"] == 32768
        assert "compression" not in result
        assert "error" not in result

    def test_to_dict_with_compression(self) -> None:
        """Verify to_dict includes compression when present."""
        compression = CompressionResult(
            compressed_input="compressed",
            original_tokens=100,
            compressed_tokens=50,
        )
        decision = TokenBudgetDecision(
            allowed=True,
            max_context_tokens=32768,
            allowed_prompt_tokens=28000,
            requested_prompt_tokens=25000,
            reserved_output_tokens=4096,
            safety_margin_tokens=172,
            compression_applied=True,
            compression=compression,
        )
        result = decision.to_dict()

        assert "compression" in result
        assert result["compression"]["compressed_input"] == "compressed"

    def test_to_dict_with_error(self) -> None:
        """Verify to_dict includes error when present."""
        decision = TokenBudgetDecision(
            allowed=False,
            max_context_tokens=32768,
            allowed_prompt_tokens=0,
            requested_prompt_tokens=50000,
            reserved_output_tokens=4096,
            safety_margin_tokens=172,
            error="exceeds limit",
        )
        result = decision.to_dict()

        assert result["error"] == "exceeds limit"


class TestAIRequest:
    """Tests for AIRequest dataclass."""

    def test_required_fields_only(self) -> None:
        """Verify AIRequest with only required fields."""
        request = AIRequest(task_type=TaskType.GENERATION, role="user")

        assert request.task_type == TaskType.GENERATION
        assert request.role == "user"
        assert request.provider_id is None
        assert request.model is None
        assert request.input == ""
        assert request.options == {}
        assert request.context == {}

    def test_full_initialization(self) -> None:
        """Verify AIRequest with all fields."""
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="assistant",
            provider_id="openai",
            model="gpt-4",
            input="Hello, world!",
            options={"temperature": 0.7},
            context={"session_id": "123"},
        )

        assert request.task_type == TaskType.DIALOGUE
        assert request.provider_id == "openai"
        assert request.model == "gpt-4"
        assert request.input == "Hello, world!"
        assert request.options == {"temperature": 0.7}
        assert request.context == {"session_id": "123"}

    def test_to_dict(self) -> None:
        """Verify AIRequest.to_dict produces correct dictionary."""
        request = AIRequest(
            task_type=TaskType.CLASSIFICATION,
            role="system",
            input="classify this",
            options={"temperature": 0.0},
        )
        result = request.to_dict()

        assert isinstance(result, dict)
        assert result["task_type"] == "classification"
        assert result["role"] == "system"
        assert result["input"] == "classify this"
        assert result["options"] == {"temperature": 0.0}

    def test_from_dict(self) -> None:
        """Verify AIRequest.from_dict creates correct instance."""
        data = {
            "task_type": "dialogue",
            "role": "user",
            "provider_id": "anthropic",
            "model": "claude-3",
            "input": "test input",
            "options": {"max_tokens": 100},
            "context": {"key": "value"},
        }
        request = AIRequest.from_dict(data)

        assert request.task_type == TaskType.DIALOGUE
        assert request.role == "user"
        assert request.provider_id == "anthropic"
        assert request.model == "claude-3"
        assert request.input == "test input"

    def test_from_dict_with_defaults(self) -> None:
        """Verify AIRequest.from_dict handles missing fields."""
        data: dict[str, object] = {}
        request = AIRequest.from_dict(data)

        assert request.task_type == TaskType.GENERATION
        assert request.role == ""
        assert request.provider_id is None
        assert request.model is None


class TestUsage:
    """Tests for Usage dataclass."""

    def test_default_initialization(self) -> None:
        """Verify Usage with default values."""
        usage = Usage()

        assert usage.cached_tokens == 0
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.estimated is False
        assert usage.prompt_chars == 0
        assert usage.completion_chars == 0

    def test_full_initialization(self) -> None:
        """Verify Usage with all values."""
        usage = Usage(
            cached_tokens=100,
            prompt_tokens=500,
            completion_tokens=300,
            total_tokens=900,
            estimated=True,
            prompt_chars=2000,
            completion_chars=1200,
        )

        assert usage.cached_tokens == 100
        assert usage.prompt_tokens == 500
        assert usage.completion_tokens == 300
        assert usage.total_tokens == 900
        assert usage.estimated is True

    def test_to_dict(self) -> None:
        """Verify Usage.to_dict produces correct dictionary."""
        usage = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        result = usage.to_dict()

        assert isinstance(result, dict)
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 50
        assert result["total_tokens"] == 150

    def test_from_dict(self) -> None:
        """Verify Usage.from_dict creates correct instance."""
        data = {
            "cached_tokens": 50,
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_tokens": 350,
            "estimated": True,
            "prompt_chars": 800,
            "completion_chars": 400,
        }
        usage = Usage.from_dict(data)

        assert usage.cached_tokens == 50
        assert usage.prompt_tokens == 200
        assert usage.completion_tokens == 100
        assert usage.total_tokens == 350
        assert usage.estimated is True

    def test_from_dict_with_none(self) -> None:
        """Verify Usage.from_dict handles None."""
        usage = Usage.from_dict(None)

        assert usage.cached_tokens == 0
        assert usage.prompt_tokens == 0

    def test_estimate(self) -> None:
        """Verify Usage.estimate creates estimated usage."""
        usage = Usage.estimate(prompt="Hello world test", output="Hi there")

        assert usage.estimated is True
        assert usage.prompt_tokens > 0
        assert usage.completion_tokens > 0
        assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens

    def test_estimate_empty_strings(self) -> None:
        """Verify Usage.estimate handles empty strings."""
        usage = Usage.estimate(prompt="", output="")

        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0


class TestAIResponse:
    """Tests for AIResponse dataclass."""

    def test_success_response(self) -> None:
        """Verify AIResponse.success creates success response."""
        response = AIResponse.success(
            output="Hello!",
            model="gpt-4",
            provider_id="openai",
            latency_ms=150,
        )

        assert response.ok is True
        assert response.output == "Hello!"
        assert response.model == "gpt-4"
        assert response.provider_id == "openai"
        assert response.error is None
        assert response.error_category is None

    def test_failure_response(self) -> None:
        """Verify AIResponse.failure creates failure response."""
        response = AIResponse.failure(
            error="Something went wrong",
            category=ErrorCategory.PROVIDER_ERROR,
            latency_ms=50,
            model="gpt-4",
        )

        assert response.ok is False
        assert response.output == ""
        assert response.error == "Something went wrong"
        assert response.error_category == ErrorCategory.PROVIDER_ERROR

    def test_to_dict_success(self) -> None:
        """Verify to_dict for success response."""
        usage = Usage(prompt_tokens=100, completion_tokens=50)
        response = AIResponse.success(
            output="test output",
            usage=usage,
            latency_ms=100,
            model="gpt-4",
        )
        result = response.to_dict()

        assert result["ok"] is True
        assert result["output"] == "test output"
        assert result["model"] == "gpt-4"
        assert "usage" in result

    def test_to_dict_failure(self) -> None:
        """Verify to_dict for failure response."""
        response = AIResponse.failure(
            error="error message",
            category=ErrorCategory.TIMEOUT,
        )
        result = response.to_dict()

        assert result["ok"] is False
        assert result["error"] == "error message"
        assert result["error_category"] == "timeout"

    def test_to_dict_with_optional_fields(self) -> None:
        """Verify to_dict includes optional fields when present."""
        response = AIResponse(
            ok=True,
            output="test",
            structured={"key": "value"},
            thinking="I am thinking",
            trace_id="trace-123",
            metadata={"extra": "data"},
            platform_retry_count=2,
            platform_retry_exhausted=True,
        )
        result = response.to_dict()

        assert result["structured"] == {"key": "value"}
        assert result["thinking"] == "I am thinking"
        assert result["trace_id"] == "trace-123"
        assert result["platform_retry_count"] == 2
        assert result["platform_retry_exhausted"] is True

    def test_from_dict(self) -> None:
        """Verify from_dict creates correct instance."""
        data = {
            "ok": True,
            "output": "result",
            "model": "claude-3",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "latency_ms": 200,
        }
        response = AIResponse.from_dict(data)

        assert response.ok is True
        assert response.output == "result"
        assert response.model == "claude-3"
        assert response.usage.prompt_tokens == 100

    def test_from_dict_with_error_category_string(self) -> None:
        """Verify from_dict converts error_category string to enum."""
        data = {
            "ok": False,
            "error": "timeout",
            "error_category": "timeout",
        }
        response = AIResponse.from_dict(data)

        assert response.error_category == ErrorCategory.TIMEOUT

    def test_from_dict_with_none(self) -> None:
        """Verify from_dict handles None."""
        response = AIResponse.from_dict(None)

        assert response.ok is True
        assert response.output == ""
        assert response.error is None


class TestProviderFormatter:
    """Tests for ProviderFormatter protocol."""

    def test_protocol_exists(self) -> None:
        """Verify ProviderFormatter protocol is defined."""
        # Protocol is a type hint construct, we just verify it exists
        assert ProviderFormatter is not None

    def test_protocol_methods_exist(self) -> None:
        """Verify protocol defines required methods."""
        # Check that format_tools and format_messages are in the protocol
        assert hasattr(ProviderFormatter, "format_tools")
        assert hasattr(ProviderFormatter, "format_messages")
