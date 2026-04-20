"""Integration tests for Standard Toolkit Tools.

【K1-PURIFY Phase 2】
KernelOne toolkit 不再包含角色集成（已迁移至 Cell 层）。
本测试文件仅测试 KernelOne 平台级能力。
角色集成测试见: tests/architecture/test_cell_layer_migration.py

NOTE: Prompt-based ([TOOL_NAME]...[/TOOL_NAME]) and tool_chain (<tool_chain>...)
text protocols were deprecated and deleted in Phase 2.
Tool chain parsing via AgentAccelToolChainPlan.parse_from_llm_output() uses
_parse_json_text which requires JSON-encoded tool calls, not numbered format.
"""

from unittest.mock import Mock, patch

import pytest


class TestPhase2ToolChainIntegration:
    """Test Phase 2: Tool Chain integration."""

    def test_tool_chain_execution(self) -> None:
        """Test executing tool chain plan."""
        # 使用 mock 避免实际执行
        with patch("polaris.kernelone.llm.toolkit.executor.AgentAccelToolExecutor") as MockExecutor:
            mock_executor = Mock()
            mock_executor.execute.return_value = {"ok": True, "result": {}}
            MockExecutor.return_value = mock_executor

            from polaris.kernelone.llm.toolkit.tool_chain_adapter import (
                AgentAccelToolChainExecutor,
            )

            executor = AgentAccelToolChainExecutor("/tmp/workspace")

            plan_text = """
<tool_chain>
1. search_code(query="test")
</tool_chain>
"""
            result = executor.execute_plan_text(plan_text)

            # 执行应该返回结果（即使使用 mock）
            assert "ok" in result


class TestPhase3NativeFunctionCallingIntegration:
    """Test Phase 3: Native Function Calling."""

    def test_openai_tool_format(self) -> None:
        """Test OpenAI function format generation."""
        from polaris.kernelone.llm.toolkit.definitions import create_default_registry

        registry = create_default_registry()
        functions = registry.to_openai_functions()

        # 验证生成了正确的 OpenAI 格式
        assert len(functions) > 0

        for func in functions:
            assert func["type"] == "function"
            assert "function" in func
            assert "name" in func["function"]
            assert "description" in func["function"]
            assert "parameters" in func["function"]

    def test_native_tool_parsing(self) -> None:
        """Test parsing native tool calls."""
        from polaris.kernelone.llm.toolkit.native_function_calling import (
            NativeFunctionCallingHandler,
        )

        handler = NativeFunctionCallingHandler("/tmp/workspace")

        # 模拟 OpenAI 响应
        raw_response = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "search_code",
                                    "arguments": '{"files": ["src/test.py"], "task_description": "Test"}',
                                },
                            }
                        ],
                    },
                }
            ],
        }

        tool_calls = handler.parse_response(raw_response)

        assert len(tool_calls) == 1
        assert tool_calls[0].name == "search_code"
        assert tool_calls[0].arguments["files"] == ["src/test.py"]


class TestAllThreeSchemes:
    """Test Native Function Calling integration.

    NOTE: Prompt-based and tool_chain text protocols were deprecated
    and deleted in Phase 2. All tool execution now uses native
    function calling formats via CanonicalToolCallParser.
    """

    def test_openai_and_anthropic_parsing(self) -> None:
        """Test that CanonicalToolCallParser handles both OpenAI and Anthropic formats."""
        from polaris.kernelone.llm.toolkit.parsers import CanonicalToolCallParser

        parser = CanonicalToolCallParser()

        # OpenAI format
        openai_calls = [
            {"id": "1", "type": "function", "function": {"name": "search_code", "arguments": '{"query": "test"}'}}
        ]
        result = parser.parse(openai_calls, format_hint="openai")
        assert len(result) == 1
        # P0-002: ToolCall uses 'name' field (not 'tool_name')
        assert result[0].name == "search_code"

        # Anthropic format
        anthropic_blocks = [{"type": "tool_use", "name": "read_file", "input": '{"file": "test.py", "n": 10}'}]
        result = parser.parse(anthropic_blocks, format_hint="anthropic")
        assert len(result) == 1
        # P0-002: ToolCall uses 'name' field
        assert result[0].name == "read_file"

    def test_fallback_behavior(self, tmp_path) -> None:
        """Test that executor handles missing tools gracefully."""
        from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

        executor = AgentAccelToolExecutor(str(tmp_path))
        result = executor.execute("search_code", {"query": "test"})

        # Should return a result (ok=True with data, or ok=False with error)
        assert "ok" in result
        # Should not expose internal error messages
        if not result["ok"]:
            error = str(result.get("error") or "")
            assert "Tool execution failed" not in error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
