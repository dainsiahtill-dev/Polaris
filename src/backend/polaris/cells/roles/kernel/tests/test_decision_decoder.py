"""
Tests for Turn Decision Decoder.

验证：
1. thinking 永不产生可执行工具
2. native tool calls 是唯一执行来源
3. finalize_mode 正确确定
4. handoff_workflow 正确触发
"""

from __future__ import annotations

import json

from polaris.cells.roles.kernel.internal.turn_decision_decoder import (
    DecodeConfig,
    RawLLMResponse,
    TurnDecisionDecoder,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    FinalizeMode,
    ToolExecutionMode,
    TurnDecisionKind,
    TurnId,
)


def _native_tool(
    name: str,
    arguments: dict[str, object],
    *,
    call_id: str = "call_1",
) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


class TestThinkingNeverExecutable:
    """验证：thinking 内容永远不会产生工具调用。"""

    def test_thinking_with_tool_syntax_not_executed(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="我将帮您查找文件。",
            thinking="""
            让我先思考一下。我需要使用工具：
            [TOOL_CALL]{"name": "read_file", "arguments": {"path": "secret.txt"}}[/TOOL_CALL]
            """,
            native_tool_calls=[],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_1"))

        assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER
        assert decision["tool_batch"] is None

    def test_thinking_reasoning_not_executed(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="这是最终答案。",
            thinking="""
            <thinking>
            用户需要我分析代码。我应该：
            1. 先读取main.py
            2. 然后搜索相关函数
            [TOOL_CALL]{"name": "read_file", "args": {"path": "main.py"}}[/TOOL_CALL]
            </thinking>
            """,
            native_tool_calls=[],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_2"))

        assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER


class TestNativeToolExecutionSource:
    """验证：只有 native tool calls 会进入执行决策。"""

    def test_native_tools_execute_even_if_content_contains_wrapper_example(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content='示例：[TOOL_CALL]{"name": "read_file", "arguments": {"path": "main.py"}}[/TOOL_CALL]',
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "main.py"}),
            ],
            model="gpt-4",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_3"))

        assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
        assert decision["tool_batch"] is not None
        assert len(decision["tool_batch"]["invocations"]) == 1
        assert decision["tool_batch"]["invocations"][0]["tool_name"] == "read_file"

    def test_different_native_tools_all_execute(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="先列目录，再读文件。",
            thinking=None,
            native_tool_calls=[
                _native_tool("list_directory", {"path": "."}, call_id="call_ls"),
                _native_tool("read_file", {"path": "main.py"}, call_id="call_read"),
            ],
            model="gpt-4",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_4"))

        assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
        assert decision["tool_batch"] is not None
        assert len(decision["tool_batch"]["invocations"]) == 2

    def test_repeated_same_tool_with_different_call_ids_is_preserved(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="read -> edit -> read verification",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "server.py"}, call_id="call_read_before"),
                _native_tool("edit_file", {"path": "server.py", "old": "8080", "new": "9090"}, call_id="call_edit"),
                _native_tool("read_file", {"path": "server.py"}, call_id="call_read_after"),
            ],
            model="gpt-4",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_repeat_read"))

        assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
        assert decision["tool_batch"] is not None
        invocations = decision["tool_batch"]["invocations"]
        assert [item["tool_name"] for item in invocations] == ["read_file", "edit_file", "read_file"]


class TestFinalizeModeDetermination:
    """验证：finalize_mode 正确确定。"""

    def test_write_tools_default_none(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="请应用改动。",
            thinking=None,
            native_tool_calls=[
                _native_tool("write_file", {"path": "test.py", "content": "x"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_5"))
        assert decision["finalize_mode"] == FinalizeMode.NONE

    def test_explicit_llm_once_respected(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="code"))

        response = RawLLMResponse(
            content="[finalize_mode:llm_once]",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "main.py"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_6"))
        assert decision["finalize_mode"] == FinalizeMode.LLM_ONCE

    def test_readonly_tools_domain_default(self) -> None:
        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "main.py"}),
            ],
            model="claude",
            usage={},
        )

        decoder_doc = TurnDecisionDecoder(config=DecodeConfig(domain="document"))
        decision_doc = decoder_doc.decode(response, TurnId("turn_7"))
        assert decision_doc["finalize_mode"] == FinalizeMode.LLM_ONCE

        decoder_code = TurnDecisionDecoder(config=DecodeConfig(domain="code"))
        decision_code = decoder_code.decode(response, TurnId("turn_8"))
        assert decision_code["finalize_mode"] == FinalizeMode.LLM_ONCE

    def test_explicit_local_mode(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="[finalize_mode:local]",
            thinking=None,
            native_tool_calls=[
                _native_tool("grep", {"pattern": "test"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_9"))
        assert decision["finalize_mode"] == FinalizeMode.LOCAL


class TestToolClassification:
    """验证：工具正确分类执行模式。"""

    def test_readonly_tools_parallel(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "a.py"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_10"))

        assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
        assert decision["tool_batch"] is not None
        tool = decision["tool_batch"]["invocations"][0]
        assert tool["execution_mode"] == ToolExecutionMode.READONLY_PARALLEL
        assert len(decision["tool_batch"]["parallel_readonly"]) == 1

    def test_write_tools_serial(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("write_file", {"path": "x.py", "content": "1"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_11"))

        assert decision["tool_batch"] is not None
        tool = decision["tool_batch"]["invocations"][0]
        assert tool["execution_mode"] == ToolExecutionMode.WRITE_SERIAL
        assert len(decision["tool_batch"]["serial_writes"]) == 1

    def test_unknown_tools_safe_default(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("unknown_custom_tool", {}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_12"))

        assert decision["tool_batch"] is not None
        tool = decision["tool_batch"]["invocations"][0]
        assert tool["execution_mode"] == ToolExecutionMode.WRITE_SERIAL

    def test_repo_tree_classified_readonly(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="code"))

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "package.json"}),
                _native_tool("repo_tree", {"path": "src"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_repo_tree"))

        assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
        assert decision["tool_batch"] is not None
        assert decision["finalize_mode"] == FinalizeMode.LLM_ONCE
        invocations = decision["tool_batch"]["invocations"]
        assert all(t["execution_mode"] == ToolExecutionMode.READONLY_PARALLEL for t in invocations), (
            f"Expected all readonly, got {[t['execution_mode'] for t in invocations]}"
        )


class TestHandoffWorkflow:
    """验证：handoff_workflow 正确触发。"""

    def test_explicit_handoff_marker(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="[handoff_workflow]",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": "main.py"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_13"))

        assert decision["kind"] == TurnDecisionKind.HANDOFF_WORKFLOW
        assert decision["metadata"]["handoff_reason"] == "complex_exploration"

    def test_async_tools_trigger_handoff(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("create_pull_request", {"title": "test"}),
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_14"))

        assert decision["kind"] == TurnDecisionKind.HANDOFF_WORKFLOW
        assert decision["tool_batch"] is not None

    def test_many_reads_trigger_handoff(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=[
                _native_tool("read_file", {"path": f"file{i}.py"}, call_id=f"call_{i}") for i in range(5)
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_15"))
        assert decision["kind"] in {
            TurnDecisionKind.TOOL_BATCH,
            TurnDecisionKind.HANDOFF_WORKFLOW,
        }


class TestFinalAnswerDetection:
    """验证：直接回答正确识别。"""

    def test_empty_response_is_final_answer(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="这是直接回答，不需要工具。",
            thinking=None,
            native_tool_calls=[],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_16"))
        assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER

    def test_explicit_final_answer_marker(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="[final_answer] 这是最终答案。",
            thinking=None,
            native_tool_calls=[],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_17"))
        assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER


class TestEdgeCases:
    """边界情况处理。"""

    def test_empty_native_tools(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="直接回答。",
            thinking=None,
            native_tool_calls=[],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_18"))
        assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER

    def test_malformed_native_tool_is_skipped(self) -> None:
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document"))

        response = RawLLMResponse(
            content="这是直接回答。",
            thinking=None,
            native_tool_calls=[
                {
                    "id": "bad_call",
                    "type": "function",
                }
            ],
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_19"))
        assert decision["kind"] == TurnDecisionKind.FINAL_ANSWER

    def test_max_tools_threshold(self) -> None:
        """已移除 too_many_tools 硬阈值 handoff；纯读取批次应走 TOOL_BATCH + LLM_ONCE。"""
        decoder = TurnDecisionDecoder(config=DecodeConfig(domain="document", max_tools_per_turn=3))

        native_tools = [_native_tool("read_file", {"path": f"file{i}.py"}, call_id=f"call_{i}") for i in range(5)]

        response = RawLLMResponse(
            content="",
            thinking=None,
            native_tool_calls=native_tools,
            model="claude",
            usage={},
        )

        decision = decoder.decode(response, TurnId("turn_20"))

        assert decision["kind"] == TurnDecisionKind.TOOL_BATCH
        assert decision["finalize_mode"] == FinalizeMode.LLM_ONCE
