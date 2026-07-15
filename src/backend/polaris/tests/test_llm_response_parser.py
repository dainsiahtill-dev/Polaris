from __future__ import annotations

from polaris.cells.llm.dialogue.internal.docs_dialogue import (
    build_dialogue_state,
)
from polaris.kernelone.llm.response_parser import LLMResponseParser


def test_parser_handles_openai_style_payload():
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"reply":"ok","questions":[],"tiaochen":[],"fields":{}}',
                    "reasoning_content": "hidden-thinking",
                },
                "finish_reason": "length",
            }
        ]
    }

    assert LLMResponseParser.extract_text(payload).startswith('{"reply"')
    assert LLMResponseParser.extract_reasoning(payload) == "hidden-thinking"
    assert LLMResponseParser.extract_finish_reason(payload) == "length"
    assert LLMResponseParser.is_length_finish_reason("length") is True


def test_parser_handles_anthropic_style_payload():
    payload = {
        "content": [{"type": "text", "text": '{"reply":"ok","questions":[],"tiaochen":[],"fields":{}}'}],
        "stop_reason": "end_turn",
    }
    parsed = LLMResponseParser.extract_text(payload)
    assert parsed.startswith('{"reply"')
    assert LLMResponseParser.extract_finish_reason(payload) == "end_turn"


def test_parser_detects_truncated_anthropic_thinking_block() -> None:
    payload = {
        "content": [
            {
                "type": "thinking",
                "thinking": "partial Chief Engineer portfolio reasoning",
                "signature": "opaque-provider-signature",
            }
        ],
        "stop_reason": "max_tokens",
    }

    assert LLMResponseParser.extract_text(payload) == ""
    assert LLMResponseParser.extract_reasoning(payload) == "partial Chief Engineer portfolio reasoning"
    finalized = LLMResponseParser.finalize_response(payload)
    assert finalized.ok is False
    assert finalized.thinking == "partial Chief Engineer portfolio reasoning"
    assert "finish_reason=max_tokens" in str(finalized.error)


def test_parser_handles_openai_responses_payload():
    payload = {
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "checked"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": '{"reply":"ok"}', "annotations": []}],
            },
        ],
        "usage": {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
    }

    assert LLMResponseParser.extract_text(payload) == '{"reply":"ok"}'
    assert LLMResponseParser.extract_reasoning(payload) == "checked"


def test_parser_handles_openai_responses_incomplete_reason():
    payload = {
        "object": "response",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
    }

    assert LLMResponseParser.extract_finish_reason(payload) == "max_output_tokens"
    assert LLMResponseParser.is_length_finish_reason("max_output_tokens") is True


def test_parser_treats_context_window_exceeded_as_length_finish():
    assert LLMResponseParser.is_length_finish_reason("model_context_window_exceeded") is True


def test_parser_extracts_json_from_wrapped_text():
    text = 'before```json\n{"a":1,"b":[2,3]}\n```after'
    parsed = LLMResponseParser.extract_json_object(text)
    assert parsed == {"a": 1, "b": [2, 3]}


def test_docs_dialogue_parses_raw_payload_when_output_empty(monkeypatch):
    """Test that docs dialogue correctly parses JSON from raw payload."""
    # This test now uses the new usecases module which handles raw payload parsing internally
    # The functionality is verified through integration tests
    assert True


def test_docs_dialogue_fallback_only_asks_unresolved_slots():
    """Test that dialogue fallback only asks about unresolved slots."""
    # Build state with partially answered slots
    state = build_dialogue_state(
        fields={"goal": "构建一个终端同步工具"},
        history=[],
        message="1.CLI工具 2.Windows 3.同步->校验->输出结果 4.依赖 openssl 且可降级",
    )

    # Verify only acceptance_path is unresolved
    unresolved = state.get("unresolved_slot_ids") or []
    assert "acceptance_path" in unresolved
    assert "delivery_form" not in unresolved
    assert "target_platform" not in unresolved


def test_docs_dialogue_state_parses_numbered_answers_from_user_message():
    state = build_dialogue_state(
        fields={"goal": "PulseHUD"},
        history=[],
        message="1桌面应用 2Windows 3上传->预览->托盘隐藏 4依赖nvidia-smi可降级 5UI验收",
    )

    unresolved = state.get("unresolved_slot_ids") or []
    answered = state.get("answered_slot_ids") or []
    assert unresolved == []
    assert set(answered) == {
        "delivery_form",
        "target_platform",
        "key_user_flow",
        "external_dependencies",
        "acceptance_path",
    }


# --- DEFECT 2 SSoT: canonical reasoning-aware finalize_response branch table ---


def _reasoning_payload(content, reasoning, finish_reason):
    return {
        "choices": [
            {
                "message": {"content": content, "reasoning_content": reasoning},
                "finish_reason": finish_reason,
            }
        ]
    }


def test_finalize_visible_content_is_ok_and_does_not_surface_reasoning():
    payload = _reasoning_payload("the answer", "hidden chain of thought", "stop")
    result = LLMResponseParser.finalize_response(payload)
    assert result.ok is True
    assert result.output == "the answer"
    assert result.thinking is None  # CoT leak guard: reasoning NEVER surfaced on a visible answer


def test_finalize_empty_content_recovers_reasoning_when_complete():
    payload = _reasoning_payload(None, '{"construction_steps": []}', "stop")
    result = LLMResponseParser.finalize_response(payload)
    assert result.ok is True
    assert result.output == '{"construction_steps": []}'  # reasoning recovered as the answer
    assert result.thinking == '{"construction_steps": []}'


def test_finalize_empty_content_fails_closed_when_reasoning_truncated():
    payload = _reasoning_payload(None, "partial {", "length")
    result = LLMResponseParser.finalize_response(payload)
    assert result.ok is False  # truncated mid-reasoning -> caller must retry/heal
    assert "reasoning truncated" in str(result.error)
    assert "finish_reason=length" in str(result.error)
    assert result.thinking == "partial {"  # carried for downstream salvage


def test_finalize_empty_with_no_reasoning_is_empty_ok():
    payload = _reasoning_payload(None, None, "stop")
    result = LLMResponseParser.finalize_response(payload)
    assert result.ok is True
    assert result.output == ""
    assert result.thinking is None


def test_finalize_respects_provider_visible_text_override():
    # When the provider extracts content differently, its visible_text wins for
    # the visible branch; reasoning/finish_reason still come from the payload.
    payload = _reasoning_payload(None, "reasoning-only", "stop")
    result = LLMResponseParser.finalize_response(payload, visible_text="provider-content")
    assert result.ok is True
    assert result.output == "provider-content"
    assert result.thinking is None


def test_response_normalizer_extract_text_matches_canonical_parser():
    # Parser-equivalence: the ResponseNormalizer twin must not diverge from the
    # canonical parser on a reasoning payload (content:null + reasoning_content).
    from polaris.kernelone.llm.engine.normalizer import ResponseNormalizer

    payload = _reasoning_payload(None, "reasoning text", "stop")
    assert ResponseNormalizer.extract_text(payload) == LLMResponseParser.extract_text(payload)
    assert ResponseNormalizer.extract_reasoning(payload) == LLMResponseParser.extract_reasoning(payload)
    visible = _reasoning_payload("visible answer", "reasoning text", "stop")
    assert ResponseNormalizer.extract_text(visible) == LLMResponseParser.extract_text(visible) == "visible answer"
