"""ADR-0090 W1.5: structured chat messages preserve role anchoring.

The openai_compat provider used to flatten the entire transcript into ONE user
message, bypassing the model's chat template — the single biggest structural
handicap for weak local models. With ``chat_messages`` supplied, real
system/user/assistant roles reach the provider payload.
"""

from __future__ import annotations

from polaris.infrastructure.llm.providers.openai_provider import (
    _build_chat_messages_payload,
)


class TestStructuredChatMessages:
    def test_roles_pass_through(self) -> None:
        chat = [
            {"role": "system", "content": "You are the Director."},
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "looking into it"},
            {"role": "user", "content": "go on"},
        ]

        payload = _build_chat_messages_payload(chat, "fallback prompt")

        assert [m["role"] for m in payload] == ["system", "user", "assistant", "user"]
        assert payload[0]["content"] == "You are the Director."

    def test_tool_role_becomes_marked_user_turn(self) -> None:
        chat = [
            {"role": "user", "content": "read the file"},
            {"role": "tool", "content": "def main(): ..."},
        ]

        payload = _build_chat_messages_payload(chat, "fallback")

        assert payload[-1]["role"] == "user"
        assert payload[-1]["content"].startswith("read the file")
        assert "【工具结果】" in payload[-1]["content"]

    def test_consecutive_same_role_merged(self) -> None:
        chat = [
            {"role": "system", "content": "rules"},
            {"role": "system", "content": "more rules"},
            {"role": "user", "content": "hi"},
        ]

        payload = _build_chat_messages_payload(chat, "fallback")

        assert len(payload) == 2
        assert payload[0]["content"] == "rules\n\nmore rules"

    def test_unknown_role_coerced_to_user(self) -> None:
        chat = [{"role": "narrator", "content": "scene"}]

        payload = _build_chat_messages_payload(chat, "fallback")

        assert payload == [{"role": "user", "content": "scene"}]

    def test_empty_chat_falls_back_to_flattened_prompt(self) -> None:
        payload = _build_chat_messages_payload(None, "the flattened prompt", system_prompt="SYS")

        assert payload == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "the flattened prompt"},
        ]

    def test_all_blank_content_falls_back(self) -> None:
        payload = _build_chat_messages_payload([{"role": "user", "content": "   "}], "fallback")

        assert payload == [{"role": "user", "content": "fallback"}]

    def test_mid_conversation_system_downgraded_to_marked_user(self) -> None:
        """vLLM strict templates reject non-leading system messages — supplemental
        system turns (role signals, tail hints) must ride as marked user turns."""
        chat = [
            {"role": "system", "content": "You are the Director."},
            {"role": "user", "content": "fix the bug"},
            {"role": "system", "content": "【项目结构】src/..."},
            {"role": "user", "content": "go"},
        ]

        payload = _build_chat_messages_payload(chat, "fallback")

        assert payload[0]["role"] == "system"
        assert all(m["role"] != "system" for m in payload[1:])
        joined_users = "\n".join(m["content"] for m in payload if m["role"] == "user")
        assert "【系统提示】" in joined_users
        assert "【项目结构】" in joined_users

    def test_leading_system_block_merges_and_stays_system(self) -> None:
        chat = [
            {"role": "system", "content": "rules"},
            {"role": "system", "content": "【项目结构】src/"},
            {"role": "user", "content": "hi"},
        ]

        payload = _build_chat_messages_payload(chat, "fallback")

        assert payload[0]["role"] == "system"
        assert "rules" in payload[0]["content"] and "【项目结构】" in payload[0]["content"]
        assert [m["role"] for m in payload] == ["system", "user"]
