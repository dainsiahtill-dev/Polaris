"""ADR-0090 W1.5b: structure-preserving budget compression for chat_messages.

Before this, budget compression rewrote only the flattened input and silently
dropped the structured array — weak local models lost chat-template role
anchoring at exactly the moment context was largest. The compressor keeps the
leading system block + final turn, back-fills recent turns, and marks the
elided span. Every output is guaranteed within the prompt budget.
"""

from __future__ import annotations

from polaris.kernelone.llm.engine.prompt_budget import (
    TokenEstimator,
    compress_chat_messages_to_budget,
)


def _tokens_of(messages: list[dict[str, str]]) -> int:
    return sum(TokenEstimator.estimate(m["content"]) + 8 for m in messages)


def _chat(*contents: tuple[str, str]) -> list[dict[str, object]]:
    return [{"role": role, "content": content} for role, content in contents]


class TestCompressChatMessagesToBudget:
    def test_fitting_conversation_kept_verbatim(self) -> None:
        chat = _chat(
            ("system", "SYS"),
            ("user", "question"),
            ("assistant", "answer"),
            ("user", "follow-up"),
        )

        result = compress_chat_messages_to_budget(chat, 10_000)

        assert result == [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow-up"},
        ]

    def test_middle_dropped_with_marker_and_recency_preserved(self) -> None:
        filler = "x" * 4000  # ~1000 tokens per middle turn
        chat = _chat(
            ("system", "SYS"),
            ("user", f"old-1 {filler}"),
            ("assistant", f"old-2 {filler}"),
            ("user", f"old-3 {filler}"),
            ("assistant", "recent analysis"),
            ("user", "latest intent"),
        )

        result = compress_chat_messages_to_budget(chat, 600)

        assert result is not None
        assert result[0] == {"role": "system", "content": "SYS"}
        assert result[-1] == {"role": "user", "content": "latest intent"}
        assert any("【上下文已压缩】" in m["content"] for m in result)
        # recent turn survives, oldest filler turns do not
        assert any(m["content"] == "recent analysis" for m in result)
        assert not any("old-1" in m["content"] for m in result)
        assert _tokens_of(result) <= 600

    def test_oversized_final_turn_is_trimmed_not_dropped(self) -> None:
        chat = _chat(
            ("system", "SYS"),
            ("user", "huge " * 3000),
        )

        result = compress_chat_messages_to_budget(chat, 500)

        assert result is not None
        assert result[-1]["role"] == "user"
        assert "compressed to fit model limit" in result[-1]["content"]
        assert _tokens_of(result) <= 500

    def test_oversized_lead_system_is_merged_and_capped(self) -> None:
        chat = _chat(
            ("system", "rule " * 2000),
            ("system", "more rules " * 2000),
            ("user", "intent"),
        )

        result = compress_chat_messages_to_budget(chat, 800)

        assert result is not None
        system_messages = [m for m in result if m["role"] == "system"]
        assert len(system_messages) == 1
        assert TokenEstimator.estimate(system_messages[0]["content"]) <= 400 + 8
        assert result[-1] == {"role": "user", "content": "intent"}
        assert _tokens_of(result) <= 800

    def test_impossible_budget_returns_none(self) -> None:
        chat = _chat(("system", "S" * 400), ("user", "U" * 400))

        assert compress_chat_messages_to_budget(chat, 10) is None
        assert compress_chat_messages_to_budget(chat, 0) is None
        assert compress_chat_messages_to_budget(chat, -5) is None

    def test_junk_and_empty_entries_filtered(self) -> None:
        chat: list[dict[str, object]] = [
            {"role": "user", "content": "   "},
            "not-a-dict",  # type: ignore[list-item]
            {"role": "user", "content": "real"},
        ]

        result = compress_chat_messages_to_budget(chat, 10_000)

        assert result == [{"role": "user", "content": "real"}]

    def test_none_and_empty_input_return_none(self) -> None:
        assert compress_chat_messages_to_budget(None, 1000) is None
        assert compress_chat_messages_to_budget([], 1000) is None

    def test_system_only_conversation_kept_when_fitting(self) -> None:
        result = compress_chat_messages_to_budget(_chat(("system", "SYS")), 1000)

        assert result == [{"role": "system", "content": "SYS"}]
