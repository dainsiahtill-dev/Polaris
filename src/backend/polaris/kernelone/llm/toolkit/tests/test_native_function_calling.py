"""Tests for polaris.kernelone.llm.toolkit.native_function_calling module.

Covers:
- _serialize_tool_output function
- _parse_tool_arguments function
- ToolResult dataclass
- ToolEnabledAIRequest class
- ToolEnabledAIResponse class
- NativeFunctionCallingHandler class
- ToolEnabledProviderMixin class
- create_tool_request function
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSerializeToolOutput:
    """Tests for _serialize_tool_output function."""

    def test_serializes_simple_dict(self) -> None:
        """Verify simple dict is serialized correctly."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _serialize_tool_output,
        )

        output = {"status": "ok", "data": [1, 2, 3]}
        result = _serialize_tool_output(output)
        assert result == output

    def test_serializes_complex_object(self) -> None:
        """Verify complex objects are serialized using default handler."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _serialize_tool_output,
        )

        class CustomObject:
            def __str__(self) -> str:
                return "custom"

        output = {"obj": CustomObject()}
        result = _serialize_tool_output(output)
        assert result["obj"] == "custom"

    def test_non_serializable_returns_value_wrapped(self) -> None:
        """Verify non-serializable objects are handled with default=str handler.

        The function uses json.dumps(..., default=str) which converts
        non-serializable objects to strings. NonSerializable objects
        without __str__ are converted to their repr() form.
        """
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _serialize_tool_output,
        )

        class NonSerializable:
            pass

        output = NonSerializable()
        result = _serialize_tool_output(output)
        # The result is a dict with "value" key containing the string representation
        assert isinstance(result, dict)
        assert "value" in result

    def test_non_dict_return_value(self) -> None:
        """Verify non-dict return values are wrapped."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _serialize_tool_output,
        )

        result = _serialize_tool_output("just a string")
        assert result == {"value": "just a string"}

        result = _serialize_tool_output([1, 2, 3])
        assert result == {"value": [1, 2, 3]}


class TestParseToolArguments:
    """Tests for _parse_tool_arguments function."""

    def test_parses_valid_json(self) -> None:
        """Verify valid JSON is parsed correctly."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _parse_tool_arguments,
        )

        args, error = _parse_tool_arguments('{"path": "/tmp/test"}')
        assert args == {"path": "/tmp/test"}
        assert error is None

    def test_empty_string_returns_empty_dict(self) -> None:
        """Verify empty string returns empty dict."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _parse_tool_arguments,
        )

        args, error = _parse_tool_arguments("")
        assert args == {}
        assert error is None

    def test_none_returns_empty_dict(self) -> None:
        """Verify None returns empty dict."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _parse_tool_arguments,
        )

        args, error = _parse_tool_arguments(None)  # type: ignore
        assert args == {}
        assert error is None

    def test_invalid_json_returns_error(self) -> None:
        """Verify invalid JSON returns error."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _parse_tool_arguments,
        )

        args, error = _parse_tool_arguments("not valid json {")
        assert args == {}
        assert error is not None
        assert "invalid JSON" in error

    def test_non_dict_json_returns_error(self) -> None:
        """Verify non-dict JSON returns error."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            _parse_tool_arguments,
        )

        args, error = _parse_tool_arguments("[1, 2, 3]")
        assert args == {}
        assert error == "tool arguments must decode to a JSON object"


class TestToolResult:
    """Tests for ToolResult dataclass."""

    def test_basic_initialization(self) -> None:
        """Verify ToolResult basic initialization."""
        from polaris.kernelone.llm.toolkit.native_function_calling import ToolResult

        result = ToolResult(
            tool_call_id="call_123",
            name="ReadFile",
            output={"content": "file contents"},
        )

        assert result.tool_call_id == "call_123"
        assert result.name == "ReadFile"
        assert result.output == {"content": "file contents"}
        assert result.is_error is False

    def test_error_initialization(self) -> None:
        """Verify ToolResult with error flag."""
        from polaris.kernelone.llm.toolkit.native_function_calling import ToolResult

        result = ToolResult(
            tool_call_id="call_456",
            name="WriteFile",
            output={"error": "permission denied"},
            is_error=True,
        )

        assert result.is_error is True

    def test_to_openai_format(self) -> None:
        """Verify ToolResult.to_openai_format produces correct structure."""
        from polaris.kernelone.llm.toolkit.native_function_calling import ToolResult

        result = ToolResult(
            tool_call_id="call_789",
            name="SearchCode",
            output={"matches": 5},
        )
        openai = result.to_openai_format()

        assert openai["role"] == "tool"
        assert openai["tool_call_id"] == "call_789"
        assert openai["name"] == "SearchCode"
        assert "content" in openai


class TestToolEnabledAIRequest:
    """Tests for ToolEnabledAIRequest class."""

    def test_initialization_with_tools(self) -> None:
        """Verify ToolEnabledAIRequest with tools."""
        from polaris.kernelone.llm.shared_contracts import TaskType
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIRequest,
        )

        request = ToolEnabledAIRequest(
            task_type=TaskType.GENERATION,
            role="user",
            input="search for foo",
            tools=[{"type": "function", "name": "search"}],
            tool_choice="auto",
        )

        assert len(request.tools) == 1
        assert request.tool_choice == "auto"

    def test_initialization_without_tools(self) -> None:
        """Verify ToolEnabledAIRequest without tools defaults to empty list."""
        from polaris.kernelone.llm.shared_contracts import TaskType
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIRequest,
        )

        request = ToolEnabledAIRequest(
            task_type=TaskType.DIALOGUE,
            role="assistant",
            input="hello",
        )

        assert request.tools == []
        assert request.tool_choice == "auto"

    def test_to_dict_includes_tools(self) -> None:
        """Verify to_dict includes tools when present."""
        from polaris.kernelone.llm.shared_contracts import TaskType
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIRequest,
        )

        request = ToolEnabledAIRequest(
            task_type=TaskType.CLASSIFICATION,
            role="system",
            input="classify",
            tools=[{"type": "function", "name": "classify"}],
        )
        data = request.to_dict()

        assert "tools" in data
        assert data["tools"] == [{"type": "function", "name": "classify"}]

    def test_to_dict_excludes_empty_tools(self) -> None:
        """Verify to_dict excludes tools when empty."""
        from polaris.kernelone.llm.shared_contracts import TaskType
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIRequest,
        )

        request = ToolEnabledAIRequest(
            task_type=TaskType.GENERATION,
            role="user",
            input="hello",
        )
        data = request.to_dict()

        assert "tools" not in data or data["tools"] == []

    def test_to_dict_includes_tool_choice(self) -> None:
        """Verify to_dict includes tool_choice."""
        from polaris.kernelone.llm.shared_contracts import TaskType
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIRequest,
        )

        request = ToolEnabledAIRequest(
            task_type=TaskType.GENERATION,
            role="user",
            input="search",
            tool_choice="required",
        )
        data = request.to_dict()

        assert data["tool_choice"] == "required"


class TestToolEnabledAIResponse:
    """Tests for ToolEnabledAIResponse class."""

    def test_initialization_with_tool_calls(self) -> None:
        """Verify ToolEnabledAIResponse with tool_calls."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIResponse,
        )

        tool_calls = [
            ToolCall(id="call_1", name="read", arguments={"path": "/a.txt"}),
        ]
        response = ToolEnabledAIResponse(
            ok=True,
            output="tool result",
            tool_calls=tool_calls,
        )

        assert len(response.tool_calls) == 1
        assert response.has_tool_calls is True

    def test_initialization_without_tool_calls(self) -> None:
        """Verify ToolEnabledAIResponse without tool_calls defaults to empty."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIResponse,
        )

        response = ToolEnabledAIResponse(ok=True, output="hello")
        assert response.tool_calls == []
        assert response.has_tool_calls is False

    def test_has_tool_calls_property(self) -> None:
        """Verify has_tool_calls property works correctly."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIResponse,
        )

        response = ToolEnabledAIResponse(
            ok=True,
            tool_calls=[ToolCall(id="call_1", name="test", arguments={})],
        )
        assert response.has_tool_calls is True

        empty_response = ToolEnabledAIResponse(ok=True, tool_calls=[])
        assert empty_response.has_tool_calls is False

    def test_to_dict_includes_tool_calls(self) -> None:
        """Verify to_dict includes tool_calls."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledAIResponse,
        )

        tool_calls = [
            ToolCall(id="call_1", name="read", arguments={"path": "/a.txt"}),
        ]
        response = ToolEnabledAIResponse(ok=True, tool_calls=tool_calls)
        data = response.to_dict()

        assert "tool_calls" in data
        assert len(data["tool_calls"]) == 1


class TestNativeFunctionCallingHandler:
    """Tests for NativeFunctionCallingHandler class."""

    def test_initialization(self) -> None:
        """Verify NativeFunctionCallingHandler initializes correctly."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        handler = NativeFunctionCallingHandler(workspace="/tmp")
        assert handler.workspace == "/tmp"

    @patch("polaris.kernelone.llm.toolkit.native_function_calling.create_default_registry")
    @patch("polaris.kernelone.llm.toolkit.native_function_calling.AgentAccelToolExecutor")
    def test_get_available_tools(self, mock_executor_class, mock_registry) -> None:
        """Verify get_available_tools returns OpenAI format."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        mock_registry.return_value.to_openai_functions.return_value = [{"type": "function", "name": "test_tool"}]

        handler = NativeFunctionCallingHandler(workspace="/tmp")
        tools = handler.get_available_tools()

        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    def test_parse_response_openai_format(self) -> None:
        """Verify parse_response handles OpenAI format."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        handler = NativeFunctionCallingHandler(workspace="/tmp")

        raw_response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "ReadFile",
                                    "arguments": '{"path": "/tmp/test.txt"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

        tool_calls = handler.parse_response(raw_response)
        assert len(tool_calls) == 1
        assert tool_calls[0].id == "call_123"
        assert tool_calls[0].name == "ReadFile"
        assert tool_calls[0].arguments == {"path": "/tmp/test.txt"}

    def test_parse_response_anthropic_format(self) -> None:
        """Verify parse_response handles Anthropic format."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        handler = NativeFunctionCallingHandler(workspace="/tmp")

        raw_response = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool_use_1",
                    "name": "SearchCode",
                    "input": {"query": "foo", "path": "/src"},
                }
            ]
        }

        tool_calls = handler.parse_response(raw_response)
        assert len(tool_calls) == 1
        assert tool_calls[0].id == "tool_use_1"
        assert tool_calls[0].name == "SearchCode"
        assert tool_calls[0].arguments == {"query": "foo", "path": "/src"}

    def test_parse_response_invalid_json_arguments(self) -> None:
        """Verify parse_response handles invalid JSON arguments."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        handler = NativeFunctionCallingHandler(workspace="/tmp")

        raw_response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_456",
                                "type": "function",
                                "function": {
                                    "name": "WriteFile",
                                    "arguments": "not valid json",
                                },
                            }
                        ]
                    }
                }
            ]
        }

        tool_calls = handler.parse_response(raw_response)
        assert len(tool_calls) == 1
        assert tool_calls[0].parse_error is not None
        assert "invalid JSON" in tool_calls[0].parse_error

    @patch("polaris.kernelone.llm.toolkit.native_function_calling.AgentAccelToolExecutor")
    def test_execute_tool_calls(self, mock_executor_class) -> None:
        """Verify execute_tool_calls executes tools."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        mock_executor = MagicMock()
        mock_executor.execute.return_value = {"ok": True, "content": "file contents"}
        mock_executor_class.return_value = mock_executor

        handler = NativeFunctionCallingHandler(workspace="/tmp")
        tool_calls = [ToolCall(id="call_1", name="ReadFile", arguments={"path": "/tmp/test.txt"})]

        results = handler.execute_tool_calls(tool_calls)
        assert len(results) == 1
        assert results[0].tool_call_id == "call_1"
        assert results[0].is_error is False

    @patch("polaris.kernelone.llm.toolkit.native_function_calling.AgentAccelToolExecutor")
    def test_execute_tool_calls_with_parse_error(self, mock_executor_class) -> None:
        """Verify execute_tool_calls handles parse errors gracefully."""
        from polaris.kernelone.llm.contracts.tool import ToolCall
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        handler = NativeFunctionCallingHandler(workspace="/tmp")
        tool_calls = [
            ToolCall(
                id="call_2",
                name="WriteFile",
                arguments={},
                parse_error="invalid JSON",
            )
        ]

        results = handler.execute_tool_calls(tool_calls)
        assert len(results) == 1
        assert results[0].is_error is True
        assert "invalid JSON" in results[0].output["error"]

    def test_build_tool_response_message(self) -> None:
        """Verify build_tool_response_message produces OpenAI format."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
            ToolResult,
        )

        handler = NativeFunctionCallingHandler(workspace="/tmp")
        tool_results = [
            ToolResult(
                tool_call_id="call_1",
                name="ReadFile",
                output={"content": "hello"},
            )
        ]

        messages = handler.build_tool_response_message(tool_results)
        assert len(messages) == 1
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "call_1"


class TestToolEnabledProviderMixin:
    """Tests for ToolEnabledProviderMixin class."""

    def test_build_payload_with_tools_auto(self) -> None:
        """Verify build_payload_with_tools with auto choice."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        payload = mixin.build_payload_with_tools(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "name": "search"}],
            tool_choice="auto",
        )

        assert "messages" in payload
        assert "tools" in payload
        assert payload["tool_choice"] == "auto"

    def test_build_payload_with_tools_none(self) -> None:
        """Verify build_payload_with_tools with none choice."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        payload = mixin.build_payload_with_tools(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "name": "search"}],
            tool_choice="none",
        )

        assert payload["tool_choice"] == "none"

    def test_build_payload_with_tools_required(self) -> None:
        """Verify build_payload_with_tools with required choice."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        payload = mixin.build_payload_with_tools(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "name": "search"}],
            tool_choice="required",
        )

        assert payload["tool_choice"] == "required"

    def test_build_payload_with_specific_tool(self) -> None:
        """Verify build_payload_with_tools with specific tool name."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        payload = mixin.build_payload_with_tools(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "name": "search"}],
            tool_choice="search",
        )

        assert payload["tool_choice"]["type"] == "function"
        assert payload["tool_choice"]["function"]["name"] == "search"

    def test_build_payload_without_tools(self) -> None:
        """Verify build_payload_with_tools without tools."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        payload = mixin.build_payload_with_tools(
            messages=[{"role": "user", "content": "hello"}],
        )

        assert "messages" in payload
        assert "tools" not in payload

    def test_parse_tool_calls_from_response(self) -> None:
        """Verify parse_tool_calls_from_response extracts tool calls."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "ReadFile",
                                    "arguments": '{"path": "/tmp/test"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

        tool_calls = mixin.parse_tool_calls_from_response(response)
        assert len(tool_calls) == 1
        assert tool_calls[0].id == "call_1"
        assert tool_calls[0].name == "ReadFile"

    def test_parse_tool_calls_empty_response(self) -> None:
        """Verify parse_tool_calls_from_response handles empty response."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            ToolEnabledProviderMixin,
        )

        mixin = ToolEnabledProviderMixin()
        tool_calls = mixin.parse_tool_calls_from_response({})
        assert len(tool_calls) == 0

        tool_calls = mixin.parse_tool_calls_from_response({"choices": []})
        assert len(tool_calls) == 0


class TestCreateToolRequest:
    """Tests for create_tool_request function."""

    @patch("polaris.kernelone.llm.toolkit.native_function_calling.create_default_registry")
    def test_create_tool_request(self, mock_registry) -> None:
        """Verify create_tool_request creates request with tools."""
        from polaris.kernelone.llm.shared_contracts import TaskType
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            create_tool_request,
        )

        mock_registry.return_value.to_openai_functions.return_value = [{"type": "function", "name": "test_tool"}]

        request = create_tool_request(
            task_type=TaskType.GENERATION,
            role="user",
            input_text="do something",
            workspace="/tmp",
            tool_choice="auto",
        )

        assert request.task_type == TaskType.GENERATION
        assert request.role == "user"
        assert request.input == "do something"
        assert len(request.tools) == 1
        assert request.tool_choice == "auto"
