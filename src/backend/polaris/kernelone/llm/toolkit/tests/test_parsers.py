"""Tests for tool call parsers.

P0-002: All parse methods now return list[ToolCall] (canonical type).
ParsedToolCall is now an alias to ToolCall from contracts.tool.
"""

from typing import Any

from polaris.kernelone.llm.contracts.tool import ToolCall
from polaris.kernelone.llm.toolkit.parsers import (
    CANONICAL_ARGUMENT_KEYS,
    CanonicalToolCallParser,
    NativeFunctionCallingParser,
    ParsedToolCall,  # Alias to ToolCall
    extract_arguments,
    extract_tool_calls_and_remainder,
    has_tool_calls,
    parse_tool_calls,
)


class TestCanonicalToolCallParser:
    """Test CanonicalToolCallParser - the unified parser entry point.

    P0-002: parse() now returns list[ToolCall].
    """

    def test_parse_openai_format(self) -> None:
        """Test parsing OpenAI format tool calls."""
        parser = CanonicalToolCallParser()
        tool_calls = [
            {
                "id": "call_123",
                "type": "function",
                "function": {"name": "repo_rg", "arguments": '{"pattern": "test", "path": "src"}'},
            }
        ]
        result = parser.parse(tool_calls, format_hint="openai")

        assert len(result) == 1
        # P0-002: ToolCall uses 'name' field (not 'tool_name')
        assert result[0].name == "repo_rg"
        assert result[0].source == "openai"
        assert result[0].arguments == {"pattern": "test", "path": "src"}
        assert result[0].id == "call_123"

    def test_parse_anthropic_format(self) -> None:
        """Test parsing Anthropic format tool calls."""
        parser = CanonicalToolCallParser()
        blocks = [{"type": "tool_use", "name": "repo_read_head", "input": '{"file": "test.py", "n": 10}'}]
        result = parser.parse(blocks, format_hint="anthropic")

        assert len(result) == 1
        # P0-002: ToolCall uses 'name' field
        assert result[0].name == "repo_read_head"
        assert result[0].source == "anthropic"
        assert result[0].arguments == {"file": "test.py", "n": 10}

    def test_parse_with_allowed_tools_filter(self) -> None:
        """Test that allowed_tools filter works."""
        parser = CanonicalToolCallParser()
        tool_calls = [
            {"id": "1", "type": "function", "function": {"name": "repo_rg", "arguments": "{}"}},
            {"id": "2", "type": "function", "function": {"name": "repo_read_head", "arguments": "{}"}},
        ]
        result = parser.parse(tool_calls, format_hint="openai", allowed_tools=["repo_rg"])

        assert len(result) == 1
        assert result[0].name == "repo_rg"

    def test_canonical_argument_keys(self) -> None:
        """Test that CANONICAL_ARGUMENT_KEYS is defined."""
        assert "arguments" in CANONICAL_ARGUMENT_KEYS
        assert "args" in CANONICAL_ARGUMENT_KEYS
        assert "params" in CANONICAL_ARGUMENT_KEYS
        assert "parameters" in CANONICAL_ARGUMENT_KEYS
        assert "input" in CANONICAL_ARGUMENT_KEYS

    def test_extract_arguments(self) -> None:
        """Test extract_arguments helper."""
        # Test arguments key
        data = {"arguments": {"a": 1}}
        assert extract_arguments(data) == {"a": 1}

        # Test args key
        data = {"args": {"b": 2}}
        assert extract_arguments(data) == {"b": 2}

        # Test fallback: when no canonical key found, filter known non-argument keys
        # If result would be empty, return original data unchanged
        fallback_data: dict[str, Any] = {"foo": "bar", "tool": "test"}  # no canonical keys, no filter keys
        result = extract_arguments(fallback_data)
        assert result == {"foo": "bar", "tool": "test"}

    def test_returns_tool_call_type(self) -> None:
        """Test that parse() returns ToolCall instances."""
        parser = CanonicalToolCallParser()
        tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": "{}"}}]
        result = parser.parse(tool_calls, format_hint="openai")

        # P0-002: Result should be list[ToolCall]
        assert len(result) == 1
        assert isinstance(result[0], ToolCall)


class TestToolCallUnified:
    """Test unified ToolCall type from contracts.tool."""

    def test_parsed_tool_call_is_tool_call(self) -> None:
        """Test that ParsedToolCall is now an alias to ToolCall."""
        # P0-002: ParsedToolCall = ToolCall
        assert ParsedToolCall is ToolCall

    def test_tool_call_fields(self) -> None:
        """Test ToolCall has canonical fields."""
        call = ToolCall(
            id="call_123",
            name="repo_rg",
            arguments={"pattern": "test"},
            source="openai",
            raw="{}",
            parse_error=None,
        )

        # Unified fields (P0-001 + P0-002)
        assert call.id == "call_123"
        assert call.name == "repo_rg"
        assert call.arguments == {"pattern": "test"}
        assert call.source == "openai"


class TestNativeFunctionCallingParser:
    """Test NativeFunctionCallingParser - existing parser."""

    def test_parse_openai(self) -> None:
        """Test parsing OpenAI format."""
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": '{"pattern": "test"}'}}
        ]
        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "repo_rg"

    def test_parse_openai_accepts_decoded_dict_arguments(self) -> None:
        """OpenAI-compatible adapters may already decode function arguments."""
        tool_calls = [
            {
                "id": "call_dict",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": {"file": "src/app.py", "start_line": 2},
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments["file"] == "src/app.py"
        assert result[0].arguments["start_line"] == 2

    def test_parse_openai_accepts_python_literal_string_arguments(self) -> None:
        """Weak/OpenAI-compatible adapters may return Python literal strings."""
        tool_calls = [
            {
                "id": "call_python_literal",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": "{'file': 'src/app.py', 'start_line': '2'}",
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "start_line": "2"}

    def test_parse_openai_accepts_single_object_array_arguments(self) -> None:
        """Weak/OpenAI-compatible adapters may wrap arguments in a one-item array."""
        tool_calls = [
            {
                "id": "call_array",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": [{"file": "src/app.py", "start_line": "2"}],
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "start_line": "2"}

    def test_parse_openai_accepts_json_string_single_object_array_arguments(self) -> None:
        """Weak/OpenAI-compatible adapters may stringify one-item argument arrays."""
        tool_calls = [
            {
                "id": "call_array_string",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '[{"file": "src/app.py", "start_line": "2"}]',
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "start_line": "2"}

    def test_parse_openai_accepts_function_parameters_arguments(self) -> None:
        """OpenAI-compatible adapters may put args under function.parameters."""
        tool_calls = [
            {
                "id": "call_parameters",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"file": "src/app.py", "start_line": "2"},
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "start_line": "2"}

    def test_parse_openai_canonicalizes_llm_tool_name_variants(self) -> None:
        """CamelCase/separator tool names should survive parser canonicalization."""
        tool_calls = [
            {
                "id": "call_read_file",
                "type": "function",
                "function": {
                    "name": "readFile",
                    "arguments": {"file": "src/app.py"},
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls, allowed_tool_names=["read_file"])

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py"}

    def test_parse_openai_uses_non_empty_function_args_when_arguments_is_empty(self) -> None:
        """Empty canonical arguments should not mask a non-empty function args alias."""
        tool_calls = [
            {
                "id": "call_empty_arguments_with_args",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": {},
                    "args": {"file": "src/app.py", "end_line": "3"},
                },
            }
        ]

        result = NativeFunctionCallingParser.parse_openai(tool_calls)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "end_line": "3"}

    def test_parse_deepseek_accepts_decoded_dict_arguments(self) -> None:
        """DeepSeek-compatible adapters may already decode function arguments."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "ds_dict",
                                "function": {
                                    "name": "write_file",
                                    "arguments": {"file": "src/app.py", "content": "print('ok')"},
                                },
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_deepseek(response)

        assert len(result) == 1
        assert result[0].name == "write_file"
        assert result[0].arguments == {"file": "src/app.py", "content": "print('ok')"}

    def test_parse_deepseek_accepts_function_input_arguments(self) -> None:
        """DeepSeek-compatible adapters may put args under function.input."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "ds_input",
                                "function": {
                                    "name": "read_file",
                                    "input": {"file": "src/app.py", "end_line": "4"},
                                },
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_deepseek(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "end_line": "4"}

    def test_parse_gemini_accepts_function_parameters_arguments(self) -> None:
        """Gemini-compatible adapters may put function-call args under parameters."""
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "read_file",
                                    "parameters": {"file": "src/gemini.py", "end_line": "7"},
                                }
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_gemini(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/gemini.py", "end_line": "7"}

    def test_parse_ollama_accepts_function_params_arguments(self) -> None:
        """Ollama-compatible adapters may put function args under params."""
        response = {
            "tool_calls": [
                {
                    "id": "ollama_params",
                    "function": {
                        "name": "read_file",
                        "params": {"file": "src/ollama.py", "start_line": "4"},
                    },
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_ollama(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/ollama.py", "start_line": "4"}

    def test_parse_anthropic_accepts_json_string_input(self) -> None:
        """Anthropic-compatible adapters may pass tool_use input as JSON text."""
        blocks = [
            {
                "id": "anthropic_json",
                "type": "tool_use",
                "name": "read_file",
                "input": '{"file": "src/app.py", "start_line": 3}',
            }
        ]

        result = NativeFunctionCallingParser.parse_anthropic(blocks)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/app.py", "start_line": 3}

    def test_parse_azure_accepts_function_input_arguments(self) -> None:
        """Azure/OpenAI-compatible adapters may put function args under input."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "azure_input",
                                "function": {
                                    "name": "read_file",
                                    "input": {"file": "src/azure.py", "end_line": "4"},
                                },
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_azure_openai(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/azure.py", "end_line": "4"}

    def test_parse_mistral_accepts_function_params_arguments(self) -> None:
        """Mistral-compatible adapters may put function args under params."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "mistral_params",
                                "function": {
                                    "name": "write_file",
                                    "params": '{"file": "src/mistral.py", "content": "print(1)"}',
                                },
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_mistral(response)

        assert len(result) == 1
        assert result[0].name == "write_file"
        assert result[0].arguments == {"file": "src/mistral.py", "content": "print(1)"}

    def test_parse_groq_uses_non_empty_args_when_arguments_is_empty(self) -> None:
        """Empty Groq function.arguments should not mask a non-empty args alias."""
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "groq_args",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{}",
                                    "args": {"file": "src/groq.py", "start_line": "2"},
                                },
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_groq(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/groq.py", "start_line": "2"}

    def test_parse_cohere_accepts_input_arguments(self) -> None:
        """Cohere-compatible adapters may put args under input instead of parameters."""
        response = {
            "tool_calls": [
                {
                    "id": "cohere_input",
                    "name": "read_file",
                    "input": {"file": "src/cohere.py", "end_line": "6"},
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_cohere(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/cohere.py", "end_line": "6"}

    def test_parse_bedrock_accepts_tool_use_parameters_arguments(self) -> None:
        """Bedrock-compatible adapters may put toolUse args under parameters."""
        response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "type": "toolUse",
                            "toolUse": {
                                "toolUseId": "bedrock_params",
                                "name": "read_file",
                                "parameters": {"file": "src/bedrock.py", "start_line": "3"},
                            },
                        }
                    ]
                }
            }
        }

        result = NativeFunctionCallingParser.parse_bedrock_claude(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/bedrock.py", "start_line": "3"}

    def test_parse_vertex_accepts_function_input_arguments(self) -> None:
        """Vertex-compatible adapters may put function-call args under input."""
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "function_call": {
                                    "name": "read_file",
                                    "input": {"file": "src/vertex.py", "end_line": "9"},
                                }
                            }
                        ]
                    }
                }
            ]
        }

        result = NativeFunctionCallingParser.parse_vertex_ai(response)

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments == {"file": "src/vertex.py", "end_line": "9"}


class TestCoreParsingFunctions:
    """Test core parsing functions from parsers module."""

    def test_extract_tool_calls_and_remainder_parses_tool_call_wrapper(self) -> None:
        """Text fallback should parse explicit TOOL_CALL wrappers and strip them from remainder."""
        text = (
            'Need a file.\n[TOOL_CALL]{"tool": "readFile", "arguments": {"path": "/workspace/src/app.py", '
            '"start_line": "2"}}[/TOOL_CALL]\nContinue.'
        )

        result, remainder = extract_tool_calls_and_remainder(text, allowed_tool_names=["read_file"])

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments["file"] == "src/app.py"
        assert result[0].arguments["start_line"] == 2
        assert "[TOOL_CALL]" not in remainder
        assert "Need a file." in remainder
        assert "Continue." in remainder

    def test_has_tool_calls_detects_recoverable_text_calls_only(self) -> None:
        """Detection should follow the same recoverable text fallback as parsing."""
        assert has_tool_calls('[TOOL_CALL]{"tool":"read_file","arguments":{"path":"README.md"}}[/TOOL_CALL]') is True
        assert has_tool_calls('{"name": "read_file", "arguments": {"path": "README.md"}}') is True
        assert has_tool_calls('{"name": "package", "version": "1.0.0"}') is False
        assert has_tool_calls("any text") is False

    def test_parse_tool_calls_falls_back_to_normalized_bare_json_text(self) -> None:
        """Bare JSON text should parse through the same canonical name/argument normalization."""
        text = '{"name": "readFile", "arguments": {"path": "/workspace/src/app.py", "end_line": "5"}}'

        result = parse_tool_calls(text=text, allowed_tool_names=["read_file"])

        assert len(result) == 1
        assert result[0].name == "read_file"
        assert result[0].arguments["file"] == "src/app.py"
        assert result[0].arguments["end_line"] == 5

    def test_parse_tool_calls_ignores_text_when_native_calls_exist(self) -> None:
        """Native tool calls remain authoritative over textual fallback."""
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": '{"pattern": "test"}'}}
        ]
        text = '{"name": "read_file", "arguments": {"path": "fallback.py"}}'

        result = parse_tool_calls(text=text, tool_calls=tool_calls, provider="openai")

        assert len(result) == 1
        assert result[0].name == "repo_rg"
        assert result[0].arguments == {"pattern": "test"}

    def test_parse_tool_calls_with_native_format(self) -> None:
        """Test parse_tool_calls with native format."""
        tool_calls = [
            {"id": "call_1", "type": "function", "function": {"name": "repo_rg", "arguments": '{"pattern": "test"}'}}
        ]
        result = parse_tool_calls(tool_calls=tool_calls, provider="openai")

        assert len(result) >= 1
        # P0-002: result is list[ToolCall]
        assert isinstance(result[0], ToolCall)
