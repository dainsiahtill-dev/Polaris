"""ADR-0090 I3: decode failures feed a corrective re-ask instead of dying silently.

Weak models emit tool calls whose arguments fail strict JSON parsing, or fully
empty responses. The decoder must capture the exact parse errors, and
``evaluate_decode_corrective`` must request exactly one corrective re-ask for
the two degraded shapes (all-calls-unparseable / empty response) while leaving
healthy decisions untouched.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.internal.transaction.decode_corrective import (
    build_corrective_context,
    evaluate_decode_corrective,
)
from polaris.cells.roles.kernel.internal.turn_decision_decoder import TurnDecisionDecoder
from polaris.cells.roles.kernel.public.turn_contracts import (
    RawLLMResponse,
    TurnDecisionKind,
    TurnId,
)

TURN_ID = TurnId("turn-decode-feedback")


def _native_call(name: str, arguments: Any, call_id: str = "c1") -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _decode(response: RawLLMResponse):
    return TurnDecisionDecoder().decode(response, TURN_ID)


class TestDecoderCapturesParseFailures:
    def test_all_calls_unparseable_marks_decode_failure_source(self) -> None:
        response = RawLLMResponse(
            content="",
            native_tool_calls=[_native_call("repo_rg", "{broken beyond repair", call_id="c1")],
        )

        decision = _decode(response)

        assert decision.get("kind") == TurnDecisionKind.ASK_USER
        metadata = decision.get("metadata") or {}
        assert metadata.get("source") == "tool_call_decode_failure"
        failures = metadata.get("decode_failures")
        assert failures and failures[0]["tool"] == "repo_rg"
        assert "JSONDecodeError" in failures[0]["error"]

    def test_repairable_arguments_recovered_into_tool_batch(self) -> None:
        """ADR-0090 W1.3: almost-valid JSON (unterminated string) is repaired at
        decode time instead of becoming a decode failure."""
        response = RawLLMResponse(
            content="",
            native_tool_calls=[_native_call("repo_rg", '{"pattern": "class X', call_id="c1")],
        )

        decision = _decode(response)

        assert decision.get("kind") == TurnDecisionKind.TOOL_BATCH
        metadata = decision.get("metadata") or {}
        assert not metadata.get("decode_failures")

    def test_mixed_batch_keeps_good_call_and_records_failure(self) -> None:
        response = RawLLMResponse(
            content="",
            native_tool_calls=[
                _native_call("repo_rg", '{"pattern": "good"}', call_id="c1"),
                _native_call("read_file", "{broken", call_id="c2"),
            ],
        )

        decision = _decode(response)

        assert decision.get("kind") == TurnDecisionKind.TOOL_BATCH
        batch = decision.get("tool_batch")
        assert batch is not None and len(batch.get("invocations") or batch.get("tools") or []) >= 1
        metadata = decision.get("metadata") or {}
        assert len(metadata.get("decode_failures") or []) == 1
        assert metadata["decode_failures"][0]["tool"] == "read_file"

    def test_prose_with_failed_calls_keeps_final_answer_but_surfaces_failures(self) -> None:
        response = RawLLMResponse(
            content="The fix is to handle memoryview in HttpResponse.__init__.",
            native_tool_calls=[_native_call("edit_blocks", "{not json", call_id="c1")],
        )

        decision = _decode(response)

        assert decision.get("kind") == TurnDecisionKind.FINAL_ANSWER
        metadata = decision.get("metadata") or {}
        assert metadata.get("decode_failures")


class TestEvaluateDecodeCorrective:
    def test_all_failed_calls_trigger_corrective_with_quoted_errors(self) -> None:
        response = RawLLMResponse(
            content="",
            native_tool_calls=[_native_call("repo_rg", "{broken beyond repair", call_id="c1")],
        )
        decision = _decode(response)

        ask = evaluate_decode_corrective(decision, response)

        assert ask is not None
        assert ask.reason == "tool_call_decode_failure"
        assert "repo_rg" in ask.content
        assert "JSONDecodeError" in ask.content

    def test_prose_final_answer_with_failed_calls_triggers_corrective(self) -> None:
        response = RawLLMResponse(
            content="Here is my analysis of the bug...",
            native_tool_calls=[_native_call("edit_blocks", "{broken", call_id="c1")],
        )
        decision = _decode(response)

        ask = evaluate_decode_corrective(decision, response)

        assert ask is not None
        assert ask.reason == "tool_call_decode_failure"

    def test_empty_response_triggers_single_reask(self) -> None:
        response = RawLLMResponse(content="", native_tool_calls=[])
        decision = _decode(response)

        ask = evaluate_decode_corrective(decision, response)

        assert ask is not None
        assert ask.reason == "empty_response"

    def test_empty_response_without_tools_requests_direct_answer_only(self) -> None:
        response = RawLLMResponse(content="", native_tool_calls=[])
        decision = _decode(response)

        ask = evaluate_decode_corrective(decision, response, tool_definitions=[])

        assert ask is not None
        assert ask.reason == "empty_response"
        assert "No tools are available" in ask.content
        assert "call exactly one appropriate tool" not in ask.content

    def test_empty_response_with_tools_keeps_tool_or_answer_retry(self) -> None:
        response = RawLLMResponse(content="", native_tool_calls=[])
        decision = _decode(response)

        ask = evaluate_decode_corrective(
            decision,
            response,
            tool_definitions=[{"name": "write_file"}],
        )

        assert ask is not None
        assert ask.reason == "empty_response"
        assert "call exactly one appropriate tool" in ask.content

    def test_healthy_tool_batch_not_disturbed(self) -> None:
        response = RawLLMResponse(
            content="",
            native_tool_calls=[_native_call("repo_rg", '{"pattern": "good"}', call_id="c1")],
        )
        decision = _decode(response)

        assert decision.get("kind") == TurnDecisionKind.TOOL_BATCH
        assert evaluate_decode_corrective(decision, response) is None

    def test_mixed_batch_proceeds_without_corrective(self) -> None:
        response = RawLLMResponse(
            content="",
            native_tool_calls=[
                _native_call("repo_rg", '{"pattern": "good"}', call_id="c1"),
                _native_call("read_file", "{broken", call_id="c2"),
            ],
        )
        decision = _decode(response)

        assert evaluate_decode_corrective(decision, response) is None

    def test_plain_prose_answer_not_disturbed(self) -> None:
        response = RawLLMResponse(content="Done — the answer is 42.", native_tool_calls=[])
        decision = _decode(response)

        assert decision.get("kind") == TurnDecisionKind.FINAL_ANSWER
        assert evaluate_decode_corrective(decision, response) is None

    def test_thinking_only_response_is_not_empty(self) -> None:
        response = RawLLMResponse(content="", thinking="let me reason...", native_tool_calls=[])
        decision = _decode(response)

        assert evaluate_decode_corrective(decision, response) is None


class TestBuildCorrectiveContext:
    def test_appends_system_message_without_mutating_original(self) -> None:
        response = RawLLMResponse(content="", native_tool_calls=[])
        decision = _decode(response)
        ask = evaluate_decode_corrective(decision, response)
        assert ask is not None

        original: list[dict[str, Any]] = [{"role": "user", "content": "fix the bug"}]
        corrected = build_corrective_context(original, ask)

        assert len(original) == 1
        assert corrected[-1]["role"] == "system"
        assert "[EMPTY RESPONSE]" in corrected[-1]["content"]
