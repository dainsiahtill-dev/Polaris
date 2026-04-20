"""Tests for reasoning sanitizer."""

import pytest
from polaris.kernelone.llm.reasoning.sanitizer import (
    ReasoningSanitizer,
    SanitizationResult,
    is_standard_reasoning_tag,
    sanitize_reasoning_output,
)
from polaris.kernelone.llm.reasoning.tags import ReasoningTagSet, generate_session_tag


class TestReasoningSanitizer:
    """Tests for ReasoningSanitizer class."""

    @pytest.fixture
    def tag_set(self) -> ReasoningTagSet:
        """Create a test tag set."""
        return generate_session_tag(prefix="think", session_id="test-session")

    @pytest.fixture
    def sanitizer(self, tag_set: ReasoningTagSet) -> ReasoningSanitizer:
        """Create a test sanitizer."""
        return ReasoningSanitizer(tag_set)

    def test_rewrite_simple_think_tag(self, sanitizer: ReasoningSanitizer, tag_set: ReasoningTagSet) -> None:
        """Should rewrite simple think tags."""
        text = "<think>inner content</think>"
        result = sanitizer.rewrite(text)

        assert tag_set.open_tag in result.rewritten_text
        assert tag_set.close_tag in result.rewritten_text
        assert "inner content" in result.rewritten_text
        assert result.substitutions == 2  # open and close

    def test_rewrite_thinking_tag(self, sanitizer: ReasoningSanitizer, tag_set: ReasoningTagSet) -> None:
        """Should rewrite thinking tags."""
        text = "<thinking>analysis</thinking>"
        result = sanitizer.rewrite(text)

        assert tag_set.open_tag in result.rewritten_text
        assert tag_set.close_tag in result.rewritten_text

    def test_rewrite_multiple_blocks(self, sanitizer: ReasoningSanitizer, tag_set: ReasoningTagSet) -> None:
        """Should rewrite multiple reasoning blocks."""
        text = "<think>first</think> middle <think>second</think>"
        result = sanitizer.rewrite(text)

        assert result.rewritten_text.count(tag_set.open_tag) == 2
        assert result.substitutions == 4  # 2 open + 2 close

    def test_rewrite_preserves_content(self, sanitizer: ReasoningSanitizer) -> None:
        """Should preserve non-reasoning content."""
        text = "<think>reasoning</think> Answer: 42"
        result = sanitizer.rewrite(text)

        assert "Answer: 42" in result.rewritten_text

    def test_rewrite_empty_text(self, sanitizer: ReasoningSanitizer) -> None:
        """Should handle empty text."""
        result = sanitizer.rewrite("")
        assert result.rewritten_text == ""
        assert result.substitutions == 0

    def test_rewrite_no_reasoning_tags(self, sanitizer: ReasoningSanitizer) -> None:
        """Should return text unchanged if no reasoning tags."""
        text = "Just regular text without any reasoning tags."
        result = sanitizer.rewrite(text)

        assert result.rewritten_text == text
        assert result.substitutions == 0

    def test_rewrite_case_insensitive(self, sanitizer: ReasoningSanitizer) -> None:
        """Should match tags case-insensitively."""
        text = "<think>UPPER CASE</think>"
        result = sanitizer.rewrite(text)

        assert result.substitutions == 2

    def test_restore_standard(self, sanitizer: ReasoningSanitizer, tag_set: ReasoningTagSet) -> None:
        """Should restore standard tags from session tags."""
        text = f"{tag_set.open_tag}content{tag_set.close_tag}"
        restored = sanitizer.restore_standard(text)

        assert "<think>" in restored
        assert "</think>" in restored
        assert tag_set.open_tag not in restored


class TestSanitizeReasoningOutput:
    """Tests for sanitize_reasoning_output convenience function."""

    def test_basic_sanitization(self) -> None:
        """Should sanitize output with session tags."""
        tag_set = generate_session_tag(prefix="think")
        text = "<think>reasoning content</think>"

        result = sanitize_reasoning_output(text, tag_set)

        assert tag_set.open_tag in result.rewritten_text
        assert tag_set.close_tag in result.rewritten_text


class TestIsStandardReasoningTag:
    """Tests for is_standard_reasoning_tag function."""

    def test_valid_open_tags(self) -> None:
        """Should identify valid opening tags."""
        assert is_standard_reasoning_tag("<think>")
        assert is_standard_reasoning_tag("<thinking>")
        assert is_standard_reasoning_tag("<thought>")
        assert is_standard_reasoning_tag("<reasoning>")

    def test_valid_close_tags(self) -> None:
        """Should identify valid closing tags."""
        assert is_standard_reasoning_tag("</think>")
        assert is_standard_reasoning_tag("</thinking>")
        assert is_standard_reasoning_tag("</thought>")
        assert is_standard_reasoning_tag("</reasoning>")

    def test_case_insensitive(self) -> None:
        """Should match case-insensitively."""
        assert is_standard_reasoning_tag("<THINK>")
        assert is_standard_reasoning_tag("</THINKING>")

    def test_invalid_tags(self) -> None:
        """Should reject invalid tags."""
        assert not is_standard_reasoning_tag("<div>")
        assert not is_standard_reasoning_tag("<script>")
        assert not is_standard_reasoning_tag("")

    def test_session_tags_excluded(self) -> None:
        """Session-specific tags (with colons) should NOT be considered standard."""
        assert not is_standard_reasoning_tag("<think:a1b2c3d4>")
        assert not is_standard_reasoning_tag("<thinking:xyz123>")
        assert not is_standard_reasoning_tag("</think:abc>")

    def test_tag_with_attributes(self) -> None:
        """Should identify tags with attributes."""
        assert is_standard_reasoning_tag('<think style="compact">')
        assert is_standard_reasoning_tag('<thinking process="step1">')


class TestSanitizationResult:
    """Tests for SanitizationResult dataclass."""

    def test_result_fields(self) -> None:
        """Should have correct fields."""
        result = SanitizationResult(
            rewritten_text="<think:x>content</think:x>",
            substitutions=2,
            original_tags=("<think>", "</think>"),
        )

        assert result.rewritten_text == "<think:x>content</think:x>"
        assert result.substitutions == 2
        assert len(result.original_tags) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
