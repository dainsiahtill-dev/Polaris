"""Tests for reasoning stripper."""

import pytest
from polaris.kernelone.llm.reasoning.stripper import (
    ReasoningStripper,
    StripResult,
    extract_reasoning_blocks,
    has_reasoning_content,
    strip_reasoning_from_history,
    strip_reasoning_tags,
)
from polaris.kernelone.llm.reasoning.tags import generate_session_tag


class TestReasoningStripper:
    """Tests for ReasoningStripper class."""

    @pytest.fixture
    def stripper(self) -> ReasoningStripper:
        """Create a test stripper without session tags."""
        return ReasoningStripper()

    @pytest.fixture
    def stripper_with_tags(self) -> ReasoningStripper:
        """Create a test stripper with session tags."""
        tag_set = generate_session_tag(prefix="think", session_id="test-session")
        return ReasoningStripper(tag_set=tag_set)

    def test_strip_simple_think_tag(self, stripper: ReasoningStripper) -> None:
        """Should strip simple think tags."""
        text = "<think>inner content</think>"
        result = stripper.strip(text)

        assert result.cleaned_text == ""
        assert result.removed_blocks >= 1

    def test_strip_thinking_tag(self, stripper: ReasoningStripper) -> None:
        """Should strip thinking tags."""
        text = "<thinking>analysis</thinking>"
        result = stripper.strip(text)

        assert result.cleaned_text == ""
        assert result.removed_blocks >= 1

    def test_strip_preserves_non_reasoning_content(self, stripper: ReasoningStripper) -> None:
        """Should preserve non-reasoning content."""
        text = "<think>reasoning</think> Answer: 42"
        result = stripper.strip(text)

        assert "Answer: 42" in result.cleaned_text
        assert "<think>" not in result.cleaned_text

    def test_strip_multiple_blocks(self, stripper: ReasoningStripper) -> None:
        """Should strip multiple reasoning blocks."""
        text = "<think>first</think> middle <think>second</think> end"
        result = stripper.strip(text)

        assert "<think>" not in result.cleaned_text
        assert "middle" in result.cleaned_text
        assert "end" in result.cleaned_text

    def test_strip_empty_text(self, stripper: ReasoningStripper) -> None:
        """Should handle empty text."""
        result = stripper.strip("")

        assert result.cleaned_text == ""
        assert result.removed_blocks == 0

    def test_strip_no_reasoning_tags(self, stripper: ReasoningStripper) -> None:
        """Should return text unchanged if no reasoning tags."""
        text = "Just regular text."
        result = stripper.strip(text)

        assert result.cleaned_text == text
        assert result.removed_blocks == 0

    def test_strip_with_session_tags(
        self,
        stripper_with_tags: ReasoningStripper,
    ) -> None:
        """Should strip session-specific tags."""
        tag_set = stripper_with_tags.tag_set
        assert tag_set is not None, "tag_set should not be None"
        text = f"{tag_set.open_tag}session reasoning{tag_set.close_tag}"
        result = stripper_with_tags.strip(text)

        # Tags should be stripped
        assert tag_set.open_tag not in result.cleaned_text
        assert tag_set.close_tag not in result.cleaned_text
        # Content should be preserved (that's the expected behavior)
        assert "session reasoning" in result.cleaned_text

    def test_strip_from_history_entry(self, stripper: ReasoningStripper) -> None:
        """Should strip from a single history entry."""
        entry = {
            "role": "assistant",
            "content": "<think>thinking content</think> Answer here",
            "thinking": "I was thinking...",
        }
        result = stripper.strip_from_history_entry(entry)

        assert "<think>" not in result["content"]
        assert "Answer here" in result["content"]
        assert result.get("thinking") is None

    def test_strip_from_history(self, stripper: ReasoningStripper) -> None:
        """Should strip from entire history."""
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "<think>thinking1</think> Response"},
            {"role": "user", "content": "<think>thinking2</think> User message"},
        ]
        result = stripper.strip_from_history(history)

        assert "<think>" not in result[0]["content"]
        assert "<think>" not in result[1]["content"]
        assert "<think>" not in result[2]["content"]
        assert "Response" in result[1]["content"]
        assert "User message" in result[2]["content"]


class TestStripReasoningFromHistory:
    """Tests for strip_reasoning_from_history convenience function."""

    def test_basic_stripping(self) -> None:
        """Should strip reasoning from history."""
        history = [
            {"role": "assistant", "content": "<think>thoughts</think> answer"},
        ]
        result = strip_reasoning_from_history(history)

        assert "<think>" not in result[0]["content"]
        assert "answer" in result[0]["content"]

    def test_with_session_tags(self) -> None:
        """Should strip session-specific tags."""
        tag_set = generate_session_tag()
        history = [
            {"role": "assistant", "content": f"{tag_set.open_tag}reasoning{tag_set.close_tag} answer"},
        ]
        result = strip_reasoning_from_history(history, tag_set=tag_set)

        # Tags should be stripped
        assert tag_set.open_tag not in result[0]["content"]
        assert tag_set.close_tag not in result[0]["content"]
        # Content should be preserved
        assert "reasoning" in result[0]["content"]
        assert "answer" in result[0]["content"]


class TestStripReasoningTags:
    """Tests for strip_reasoning_tags convenience function."""

    def test_strip_reasoning_tags(self) -> None:
        """Should strip reasoning tags from text."""
        content = "Some text <think>inner</think> more text <think>outer</think>"
        result = strip_reasoning_tags(content)
        assert "inner" not in result
        assert "outer" not in result

    def test_strip_reasoning_tags_with_multiple_blocks(self) -> None:
        """Should strip multiple reasoning blocks."""
        content = "<think>first</think>text1<think>second</think>text2<think>third</think>"
        result = strip_reasoning_tags(content)
        assert "first" not in result
        assert "second" not in result
        assert "third" not in result
        assert "<think>" not in result
        assert "text1" in result
        assert "text2" in result

    def test_strip_reasoning_tags_preserves_non_reasoning(self) -> None:
        """Should preserve non-reasoning content."""
        content = "<think>thinking</think> Answer: 42 <think>more thinking</think>"
        result = strip_reasoning_tags(content)
        assert "Answer: 42" in result
        assert "<think>" not in result

    def test_strip_reasoning_tags_empty_string(self) -> None:
        """Should handle empty string."""
        result = strip_reasoning_tags("")
        assert result == ""

    def test_strip_reasoning_tags_no_tags(self) -> None:
        """Should return text unchanged if no reasoning tags."""
        content = "Just regular text without any reasoning."
        result = strip_reasoning_tags(content)
        assert result == content


class TestExtractReasoningBlocks:
    """Tests for extract_reasoning_blocks function."""

    def test_extract_simple_blocks(self) -> None:
        """Should extract reasoning block contents."""
        text = "<think>first block</think> middle <think>second block</think>"
        blocks = extract_reasoning_blocks(text)

        assert len(blocks) >= 1

    def test_extract_empty_if_no_blocks(self) -> None:
        """Should return empty list if no blocks."""
        blocks = extract_reasoning_blocks("Just text")
        assert blocks == []


class TestHasReasoningContent:
    """Tests for has_reasoning_content function."""

    def test_has_reasoning_tags(self) -> None:
        """Should return True if reasoning tags present."""
        assert has_reasoning_content("<think>thinking</think>")
        assert has_reasoning_content("<thinking>content</thinking>")

    def test_no_reasoning_tags(self) -> None:
        """Should return False if no reasoning tags."""
        assert not has_reasoning_content("Just regular text")
        assert not has_reasoning_content("")

    def test_empty_string(self) -> None:
        """Should return False for empty string."""
        assert not has_reasoning_content("")


class TestStripResult:
    """Tests for StripResult dataclass."""

    def test_result_fields(self) -> None:
        """Should have correct fields."""
        result = StripResult(
            cleaned_text="cleaned text",
            removed_blocks=2,
            removed_content="<think>removed1</think><think>removed2</think>",
        )

        assert result.cleaned_text == "cleaned text"
        assert result.removed_blocks == 2
        assert "<think>" in result.removed_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
