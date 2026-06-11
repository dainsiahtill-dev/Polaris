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
        from polaris.infrastructure.llm.providers.openai_compat_provider import (
            _build_chat_messages_payload,
        )

        assert _build_chat_messages_payload is build_chat_messages_payload
