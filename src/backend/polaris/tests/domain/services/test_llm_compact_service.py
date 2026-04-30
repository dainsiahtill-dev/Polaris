# ruff: noqa: E402
"""Tests for polaris.domain.services.llm_compact_service.

Covers:
- CompactResult dataclass and compression_ratio property
- LLMCompactService initialization
- compact() happy path, fallback, and edge cases
- _format_messages() with various content types
- create_identity_anchor() formatting
- compact_with_anchor() integration
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from polaris.domain.services.llm_compact_service import (
    CompactResult,
    LLMClient,
    LLMCompactService,
)

# =============================================================================
# CompactResult
# =============================================================================


class TestCompactResult:
    def test_basic_construction(self) -> None:
        result = CompactResult(
            summary="test",
            key_decisions=["d1"],
            action_items=["a1"],
            original_token_estimate=100,
            compressed_token_estimate=50,
        )
        assert result.summary == "test"
        assert result.key_decisions == ["d1"]
        assert result.action_items == ["a1"]

    def test_compression_ratio(self) -> None:
        result = CompactResult(
            summary="test",
            key_decisions=[],
            action_items=[],
            original_token_estimate=100,
            compressed_token_estimate=25,
        )
        assert result.compression_ratio == 0.25

    def test_compression_ratio_zero_original(self) -> None:
        result = CompactResult(
            summary="test",
            key_decisions=[],
            action_items=[],
            original_token_estimate=0,
            compressed_token_estimate=0,
        )
        assert result.compression_ratio == 1.0

    def test_compression_ratio_original_zero_nonzero_compressed(self) -> None:
        result = CompactResult(
            summary="test",
            key_decisions=[],
            action_items=[],
            original_token_estimate=0,
            compressed_token_estimate=10,
        )
        assert result.compression_ratio == 1.0

    def test_compression_ratio_greater_than_one(self) -> None:
        result = CompactResult(
            summary="test",
            key_decisions=[],
            action_items=[],
            original_token_estimate=10,
            compressed_token_estimate=20,
        )
        assert result.compression_ratio == 2.0

    def test_empty_lists_default(self) -> None:
        result = CompactResult(
            summary="test",
            key_decisions=[],
            action_items=[],
            original_token_estimate=0,
            compressed_token_estimate=0,
        )
        assert result.key_decisions == []
        assert result.action_items == []


# =============================================================================
# LLMCompactService init
# =============================================================================


class TestLLMCompactServiceInit:
    def test_init_with_none(self) -> None:
        service = LLMCompactService()
        assert service._llm is None

    def test_init_with_mock_client(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        service = LLMCompactService(mock_client)
        assert service._llm is mock_client


# =============================================================================
# compact()
# =============================================================================


@pytest.mark.asyncio
class TestCompact:
    async def test_compact_without_client_raises(self) -> None:
        service = LLMCompactService()
        with pytest.raises(RuntimeError, match="LLM client not configured"):
            await service.compact([{"role": "user", "content": "hello"}])

    async def test_compact_happy_path(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps(
            {
                "summary": "A summary",
                "key_decisions": ["decide A"],
                "action_items": ["do B"],
                "current_context": "ctx",
            }
        )
        service = LLMCompactService(mock_client)
        messages = [{"role": "user", "content": "hello"}]
        result = await service.compact(messages)

        assert result.summary == "A summary"
        assert result.key_decisions == ["decide A"]
        assert result.action_items == ["do B"]
        assert result.original_token_estimate > 0
        assert result.compressed_token_estimate > 0
        mock_client.complete.assert_awaited_once()

    async def test_compact_preserve_recent_zero(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps({"summary": "all"})
        service = LLMCompactService(mock_client)
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
        ]
        await service.compact(messages, preserve_recent=0)
        prompt = mock_client.complete.call_args[0][0]
        assert "msg1" in prompt
        assert "msg2" in prompt

    async def test_compact_preserve_recent_two(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps({"summary": "old"})
        service = LLMCompactService(mock_client)
        messages = [
            {"role": "user", "content": "old1"},
            {"role": "assistant", "content": "old2"},
            {"role": "user", "content": "recent1"},
            {"role": "assistant", "content": "recent2"},
        ]
        await service.compact(messages, preserve_recent=2)
        prompt = mock_client.complete.call_args[0][0]
        # old1 and old2 should be in prompt; recent ones are preserved verbatim
        # and therefore excluded from the summarization prompt
        assert "old1" in prompt
        assert "old2" in prompt
        assert "recent1" not in prompt
        assert "recent2" not in prompt

    async def test_compact_fallback_on_invalid_json(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = "This is not JSON but a summary"
        service = LLMCompactService(mock_client)
        messages = [{"role": "user", "content": "hello"}]
        result = await service.compact(messages)

        assert "This is not JSON" in result.summary
        assert result.key_decisions == []
        assert result.action_items == []

    async def test_compact_empty_messages(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps({"summary": "empty"})
        service = LLMCompactService(mock_client)
        result = await service.compact([])
        assert result.summary == "empty"
        assert result.original_token_estimate == 0  # len('[]') // 4 == 2 // 4 == 0

    async def test_compact_llm_receives_max_tokens(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps({"summary": "ok"})
        service = LLMCompactService(mock_client)
        await service.compact([{"role": "user", "content": "x"}])
        kwargs = mock_client.complete.call_args[1]
        assert kwargs.get("max_tokens") == 2000

    async def test_compact_missing_fields_in_json(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps({"summary": "partial"})
        service = LLMCompactService(mock_client)
        result = await service.compact([{"role": "user", "content": "x"}])
        assert result.summary == "partial"
        assert result.key_decisions == []
        assert result.action_items == []

    async def test_compact_json_with_extra_fields(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps(
            {
                "summary": "extra",
                "key_decisions": ["d1"],
                "action_items": ["a1"],
                "current_context": "ctx",
                "extra_field": "ignored",
            }
        )
        service = LLMCompactService(mock_client)
        result = await service.compact([{"role": "user", "content": "x"}])
        assert result.summary == "extra"
        assert result.key_decisions == ["d1"]

    async def test_compact_large_messages(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps({"summary": "large"})
        service = LLMCompactService(mock_client)
        messages = [{"role": "user", "content": "x" * 10_000}]
        result = await service.compact(messages)
        assert result.original_token_estimate > 0


# =============================================================================
# _format_messages
# =============================================================================


class TestFormatMessages:
    def test_format_simple_text(self) -> None:
        service = LLMCompactService()
        text = service._format_messages([{"role": "user", "content": "hello"}])
        assert "[USER] hello" in text

    def test_format_multiple_messages(self) -> None:
        service = LLMCompactService()
        text = service._format_messages(
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hey"},
            ]
        )
        assert "[USER] hi" in text
        assert "[ASSISTANT] hey" in text

    def test_format_missing_role(self) -> None:
        service = LLMCompactService()
        text = service._format_messages([{"content": "no role"}])
        assert "[UNKNOWN] no role" in text

    def test_format_missing_content(self) -> None:
        service = LLMCompactService()
        text = service._format_messages([{"role": "user"}])
        assert "[USER] " in text

    def test_format_tool_result_content(self) -> None:
        service = LLMCompactService()
        text = service._format_messages(
            [
                {
                    "role": "tool",
                    "content": [{"type": "tool_result", "name": "ls", "content": "file.txt"}],
                }
            ]
        )
        assert "[TOOL]" in text
        assert "[Tool ls: file.txt]" in text

    def test_format_text_part_content(self) -> None:
        service = LLMCompactService()
        text = service._format_messages(
            [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello world"}],
                }
            ]
        )
        assert "hello world" in text

    def test_format_mixed_content_parts(self) -> None:
        service = LLMCompactService()
        text = service._format_messages(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "intro"},
                        {"type": "tool_result", "name": "cat", "content": "data"},
                    ],
                }
            ]
        )
        assert "intro" in text
        assert "[Tool cat: data]" in text

    def test_format_long_content_truncated(self) -> None:
        service = LLMCompactService()
        long_content = "x" * 600
        text = service._format_messages([{"role": "user", "content": long_content}])
        assert len(text.split("\n")[0]) < 600
        assert "[USER]" in text

    def test_format_empty_messages(self) -> None:
        service = LLMCompactService()
        text = service._format_messages([])
        assert text == ""

    def test_format_unknown_part_type_ignored(self) -> None:
        service = LLMCompactService()
        text = service._format_messages(
            [
                {
                    "role": "user",
                    "content": [{"type": "image", "url": "http://x"}],
                }
            ]
        )
        # Unknown dict type falls through without adding text
        assert "[USER]" in text


# =============================================================================
# create_identity_anchor
# =============================================================================


class TestCreateIdentityAnchor:
    def test_basic_anchor(self) -> None:
        service = LLMCompactService()
        anchor = service.create_identity_anchor(
            task="fix bug",
            constraints=["no breaking changes"],
            working_directory="/tmp/ws",
            key_decisions=["use pytest"],
        )
        assert "TASK: fix bug" in anchor
        assert "WORKING DIRECTORY: /tmp/ws" in anchor
        assert "CONSTRAINTS:" in anchor
        assert "no breaking changes" in anchor
        assert "KEY DECISIONS:" in anchor
        assert "use pytest" in anchor

    def test_no_constraints(self) -> None:
        service = LLMCompactService()
        anchor = service.create_identity_anchor(
            task="task",
            constraints=[],
            working_directory="/ws",
            key_decisions=["d1"],
        )
        assert "CONSTRAINTS:" not in anchor
        assert "KEY DECISIONS:" in anchor

    def test_no_key_decisions(self) -> None:
        service = LLMCompactService()
        anchor = service.create_identity_anchor(
            task="task",
            constraints=["c1"],
            working_directory="/ws",
            key_decisions=[],
        )
        assert "CONSTRAINTS:" in anchor
        assert "KEY DECISIONS:" not in anchor

    def test_empty_all(self) -> None:
        service = LLMCompactService()
        anchor = service.create_identity_anchor(
            task="task",
            constraints=[],
            working_directory="/ws",
            key_decisions=[],
        )
        assert "TASK: task" in anchor
        assert "CONSTRAINTS:" not in anchor
        assert "KEY DECISIONS:" not in anchor
        assert "==========================" in anchor


# =============================================================================
# compact_with_anchor
# =============================================================================


@pytest.mark.asyncio
class TestCompactWithAnchor:
    async def test_returns_anchor_and_result(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps(
            {
                "summary": "summary",
                "key_decisions": ["kd1"],
                "action_items": ["ai1"],
            }
        )
        service = LLMCompactService(mock_client)
        anchor, result = await service.compact_with_anchor(
            messages=[{"role": "user", "content": "hello"}],
            task="my task",
            constraints=["c1"],
            working_directory="/ws",
        )
        assert "TASK: my task" in anchor
        assert "kd1" in anchor
        assert result.summary == "summary"

    async def test_anchor_reflects_key_decisions_from_llm(self) -> None:
        mock_client = AsyncMock(spec=LLMClient)
        mock_client.complete.return_value = json.dumps(
            {
                "summary": "s",
                "key_decisions": ["decision A", "decision B"],
                "action_items": [],
            }
        )
        service = LLMCompactService(mock_client)
        anchor, _result = await service.compact_with_anchor(
            messages=[],
            task="t",
            constraints=[],
            working_directory="/ws",
        )
        assert "decision A" in anchor
        assert "decision B" in anchor
