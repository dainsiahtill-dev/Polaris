"""Prompt budget edge-case coverage."""

from __future__ import annotations

from polaris.kernelone.llm.engine.prompt_budget import (
    _estimate_message_tokens,
    compress_chat_messages_to_budget,
)


def test_compress_chat_messages_noops_when_messages_fit_exact_budget() -> None:
    messages = [
        {"role": "system", "content": "system anchor"},
        {"role": "user", "content": "first turn"},
        {"role": "assistant", "content": "ack"},
        {"role": "user", "content": "current request"},
    ]
    exact_budget = sum(_estimate_message_tokens(str(item["content"]), "general") for item in messages)

    result = compress_chat_messages_to_budget(messages, exact_budget)

    assert result == messages
