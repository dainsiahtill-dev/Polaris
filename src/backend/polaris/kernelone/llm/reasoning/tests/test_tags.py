"""Tests for reasoning tag generation."""

import pytest
from polaris.kernelone.llm.reasoning.tags import (
    ReasoningTagGenerator,
    ReasoningTagSet,
    _generate_random_suffix,
    generate_session_tag,
)


class TestReasoningTagGenerator:
    """Tests for ReasoningTagGenerator class."""

    def test_generate_returns_unique_tags(self) -> None:
        """Each generated tag set should be unique."""
        gen = ReasoningTagGenerator(prefix="think")
        tag1 = gen.generate()
        tag2 = gen.generate()

        assert tag1.open_tag != tag2.open_tag
        assert tag1.close_tag != tag2.close_tag
        assert tag1.session_id != tag2.session_id
        assert tag1.raw_suffix != tag2.raw_suffix

    def test_generate_tags_have_correct_format(self) -> None:
        """Generated tags should have the format <prefix:suffix> ... </prefix:suffix>."""
        gen = ReasoningTagGenerator(prefix="think")
        tag_set = gen.generate()

        assert tag_set.open_tag.startswith("<think:")
        assert tag_set.close_tag.startswith("</think:")
        assert tag_set.raw_suffix in tag_set.open_tag
        assert tag_set.prefix == "think"

    def test_generate_with_custom_session_id(self) -> None:
        """Should respect custom session ID."""
        gen = ReasoningTagGenerator(prefix="thinking")
        tag_set = gen.generate(session_id="my-session-123")

        assert tag_set.session_id == "my-session-123"

    def test_different_prefixes(self) -> None:
        """Should work with different standard prefixes."""
        for prefix in ["think", "thinking", "thought", "reasoning", "answer"]:
            gen = ReasoningTagGenerator(prefix=prefix)
            tag_set = gen.generate()
            assert tag_set.prefix == prefix
            assert tag_set.open_tag.startswith(f"<{prefix}:")
            assert tag_set.close_tag.startswith(f"</{prefix}:")

    def test_for_standard_prefix_valid(self) -> None:
        """Should generate tags for valid standard prefixes."""
        tag_set = ReasoningTagGenerator.for_standard_prefix("think")
        assert tag_set.prefix == "think"

        tag_set = ReasoningTagGenerator.for_standard_prefix("reasoning")
        assert tag_set.prefix == "reasoning"

    def test_for_standard_prefix_invalid(self) -> None:
        """Should raise ValueError for invalid prefixes."""
        with pytest.raises(ValueError, match="unknown prefix"):
            ReasoningTagGenerator.for_standard_prefix("invalid_prefix")

        with pytest.raises(ValueError, match="prefix cannot be empty"):
            ReasoningTagGenerator.for_standard_prefix("")

    def test_standard_prefixes(self) -> None:
        """Should return all standard prefixes."""
        prefixes = ReasoningTagGenerator.standard_prefixes()
        assert "think" in prefixes
        assert "thinking" in prefixes
        assert "reasoning" in prefixes
        assert "answer" in prefixes


class TestGenerateSessionTag:
    """Tests for generate_session_tag convenience function."""

    def test_basic_generation(self) -> None:
        """Should generate a valid tag set."""
        tag_set = generate_session_tag()
        assert isinstance(tag_set, ReasoningTagSet)
        assert tag_set.open_tag.startswith("<think:")
        assert tag_set.close_tag.startswith("</think:")

    def test_custom_prefix(self) -> None:
        """Should respect custom prefix."""
        tag_set = generate_session_tag(prefix="reasoning")
        assert tag_set.prefix == "reasoning"
        assert tag_set.open_tag.startswith("<reasoning:")

    def test_custom_session_id(self) -> None:
        """Should respect custom session ID."""
        tag_set = generate_session_tag(session_id="test-123")
        assert tag_set.session_id == "test-123"


class TestRandomSuffix:
    """Tests for random suffix generation."""

    def test_generate_random_suffix_length(self) -> None:
        """Should generate suffix of specified length."""
        suffix = _generate_random_suffix(8)
        assert len(suffix) == 8

        suffix = _generate_random_suffix(16)
        assert len(suffix) == 16

    def test_generate_random_suffix_alphanumeric(self) -> None:
        """Should generate alphanumeric suffix."""
        suffix = _generate_random_suffix(20)
        assert suffix.isalnum()
        assert suffix.islower()

    def test_generate_random_suffix_unique(self) -> None:
        """Should generate unique suffixes."""
        suffixes = [_generate_random_suffix(8) for _ in range(100)]
        assert len(set(suffixes)) == 100  # All unique


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
