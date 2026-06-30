"""ADR-0090 W1.5 (ollama): structured chat_messages pass-through.

The openai_compat provider already consumes the caller-supplied
``chat_messages`` array (real role anchoring for weak local models); the
ollama provider used to flatten everything into one user message. Both now
share ``provider_helpers.build_chat_messages_payload``.
"""

from __future__ import annotations

from polaris.infrastructure.llm.providers.ollama_provider import _extract_messages
from polaris.infrastructure.llm.providers.provider_helpers import build_chat_messages_payload


class TestOllamaChatMessagesPassThrough:
    def test_chat_messages_win_over_flattened_prompt(self) -> None:
        chat = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "analysis"},
            {"role": "user", "content": "follow-up"},
        ]

        messages = _extract_messages("flattened prompt", {"chat_messages": chat})

        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert messages[0]["content"] == "SYS"
        assert "flattened prompt" not in str(messages)

    def test_chat_messages_win_over_adapter_messages(self) -> None:
        chat = [{"role": "user", "content": "structured"}]
        config = {
            "chat_messages": chat,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "adapter"}]}],
        }

        messages = _extract_messages("prompt", config)

        assert messages == [{"role": "user", "content": "structured"}]

    def test_tool_turns_downgrade_to_marked_user(self) -> None:
        chat = [
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "calling tool"},
            {"role": "tool", "content": "tool output"},
        ]

        messages = _extract_messages("prompt", {"chat_messages": chat})

        assert messages[-1]["role"] == "user"
        assert "【工具结果】" in messages[-1]["content"]

    def test_mid_conversation_system_downgrades(self) -> None:
        chat = [
            {"role": "system", "content": "LEAD"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "late hint"},
        ]

        messages = _extract_messages("prompt", {"chat_messages": chat})

        assert messages[0] == {"role": "system", "content": "LEAD"}
        assert messages[-1]["role"] == "user"
        assert "【系统提示】" in messages[-1]["content"]

    def test_no_chat_messages_keeps_legacy_fallback(self) -> None:
        messages = _extract_messages("prompt", {"system_prompt": "SYS"})

        assert messages == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "prompt"},
        ]

    def test_empty_chat_messages_keeps_legacy_fallback(self) -> None:
        messages = _extract_messages("prompt", {"chat_messages": []})

        assert messages == [{"role": "user", "content": "prompt"}]

    def test_adapter_messages_still_honored_without_chat_messages(self) -> None:
        config = {"messages": [{"role": "user", "content": [{"type": "text", "text": "adapter"}]}]}

        messages = _extract_messages("prompt", config)

        assert messages == [{"role": "user", "content": [{"type": "text", "text": "adapter"}]}]


class TestSharedBuilderParity:
    def test_shared_helper_is_the_openai_compat_implementation(self) -> None:
        from polaris.infrastructure.llm.providers.openai_provider import (
            _build_chat_messages_payload,
        )

        assert _build_chat_messages_payload is build_chat_messages_payload


class TestUserTurnGuarantee:
    """factory-bench 2026-06-12 live regression: an all-system chat_messages
    array must never reach a strict chat template without a user turn
    (vLLM qwen3: 400 'No user query found in messages')."""

    def test_all_system_array_gets_prompt_appended_as_user(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import build_chat_messages_payload

        messages = build_chat_messages_payload(
            [{"role": "system", "content": "你是项目经理。"}],
            "生成 AGENTS.md",
        )
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "生成 AGENTS.md"

    def test_all_system_with_empty_prompt_gets_placeholder_user(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import build_chat_messages_payload

        messages = build_chat_messages_payload(
            [{"role": "system", "content": "instructions"}],
            "",
        )
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "(continue)"

    def test_empty_user_content_stripped_then_guarded(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import build_chat_messages_payload

        messages = build_chat_messages_payload(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "   "}],
            "real question",
        )
        roles = [m["role"] for m in messages]
        assert "user" in roles

    def test_normal_conversation_unchanged(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import build_chat_messages_payload

        messages = build_chat_messages_payload(
            [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}],
            "q",
        )
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[-1]["content"] == "q"


class TestContextOverflowSelfHeal:
    """factory-bench 2026-06-12: server-truth self-heal for prompt+output>window 400s."""

    _BODY = (
        '{"error":{"message":"This model\'s maximum context length is 16384 tokens. '
        "However, you requested 8192 output tokens and your prompt contains at least 8193 input tokens, "
        'for a total of at least 16385 tokens.","type":"BadRequestError","code":400}}'
    )

    def test_shrinks_to_server_reported_headroom(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import (
            shrink_max_tokens_for_context_overflow,
        )

        payload = {"max_tokens": 8192}
        assert shrink_max_tokens_for_context_overflow(payload, self._BODY) is True
        assert payload["max_tokens"] == 16384 - 8193 - 16

    def test_non_overflow_body_untouched(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import (
            shrink_max_tokens_for_context_overflow,
        )

        payload = {"max_tokens": 8192}
        assert shrink_max_tokens_for_context_overflow(payload, '{"error":"No user query"}') is False
        assert payload["max_tokens"] == 8192

    def test_no_headroom_left_returns_false(self) -> None:
        from polaris.infrastructure.llm.providers.provider_helpers import (
            shrink_max_tokens_for_context_overflow,
        )

        body = (
            '"maximum context length is 16384 tokens. However, you requested 100 output tokens '
            'and your prompt contains at least 16380 input tokens"'
        )
        payload = {"max_tokens": 100}
        assert shrink_max_tokens_for_context_overflow(payload, body) is False
