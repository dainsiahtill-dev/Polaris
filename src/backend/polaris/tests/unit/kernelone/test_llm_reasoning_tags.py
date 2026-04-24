"""Tests for polaris.kernelone.llm.reasoning.tags."""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.reasoning.tags import (
    ReasoningTagGenerator,
    ReasoningTagSet,
    _build_tags,
    _generate_random_suffix,
    generate_session_tag,
)


class TestGenerateRandomSuffix:
    """Tests for _generate_random_suffix."""

    def test_default_length(self) -> None:
        suffix = _generate_random_suffix()
        assert len(suffix) == 8
        assert suffix.isalnum()
        assert suffix.islower()

    def test_custom_length(self) -> None:
        suffix = _generate_random_suffix(length=16)
        assert len(suffix) == 16

    def test_deterministic_positive_length(self) -> None:
        # Each call should produce a string (not crash)
        for length in [1, 4, 12, 32]:
            suffix = _generate_random_suffix(length=length)
            assert len(suffix) == length


class TestBuildTags:
    """Tests for _build_tags."""

    def test_format(self) -> None:
        open_tag, close_tag = _build_tags("think", "a1b2c3d4")
        assert open_tag == "<think:a1b2c3d4>"
        assert close_tag == "</think:a1b2c3d4>"

    def test_different_prefix(self) -> None:
        open_tag, close_tag = _build_tags("reasoning", "xyz999")
        assert open_tag == "<reasoning:xyz999>"
        assert close_tag == "</reasoning:xyz999>"


class TestReasoningTagSet:
    """Tests for ReasoningTagSet dataclass."""

    def test_fields(self) -> None:
        tag_set = ReasoningTagSet(
            open_tag="<think:abc123>",
            close_tag="</think:abc123>",
            prefix="think",
            session_id="session-42",
            raw_suffix="abc123",
        )
        assert tag_set.open_tag == "<think:abc123>"
        assert tag_set.close_tag == "</think:abc123>"
        assert tag_set.prefix == "think"
        assert tag_set.session_id == "session-42"
        assert tag_set.raw_suffix == "abc123"

    def test_immutable(self) -> None:
        tag_set = ReasoningTagSet(
            open_tag="<think:abc>",
            close_tag="</think:abc>",
            prefix="think",
            session_id="s1",
            raw_suffix="abc",
        )
        with pytest.raises(AttributeError):
            tag_set.open_tag = "<think:xyz>"  # type: ignore[misc]


class TestReasoningTagGenerator:
    """Tests for ReasoningTagGenerator."""

    def test_default_prefix(self) -> None:
        gen = ReasoningTagGenerator()
        assert gen.prefix == "think"

    def test_custom_prefix_normalized(self) -> None:
        gen = ReasoningTagGenerator(prefix="THINK")
        assert gen.prefix == "think"

    def test_empty_prefix_defaults_to_think(self) -> None:
        gen = ReasoningTagGenerator(prefix="")
        assert gen.prefix == "think"

    def test_whitespace_prefix_normalized(self) -> None:
        gen = ReasoningTagGenerator(prefix="  reasoning  ")
        assert gen.prefix == "reasoning"

    def test_generate_returns_unique_tags(self) -> None:
        gen = ReasoningTagGenerator()
        tag1 = gen.generate()
        tag2 = gen.generate()
        assert tag1.open_tag != tag2.open_tag
        assert tag1.raw_suffix != tag2.raw_suffix

    def test_generate_with_session_id(self) -> None:
        gen = ReasoningTagGenerator()
        tag = gen.generate(session_id="my-session")
        assert tag.session_id == "my-session"

    def test_generate_without_session_id(self) -> None:
        gen = ReasoningTagGenerator()
        tag = gen.generate()
        assert tag.session_id is not None
        assert len(tag.session_id) > 0

    def test_generate_tags_well_formed(self) -> None:
        gen = ReasoningTagGenerator()
        tag = gen.generate()
        assert tag.open_tag.startswith("<think:")
        assert tag.close_tag.startswith("</think:")
        assert tag.open_tag.endswith(">")
        assert tag.close_tag.endswith(">")
        # Suffix should be embedded
        suffix = tag.raw_suffix
        assert suffix in tag.open_tag
        assert suffix in tag.close_tag

    def test_standard_prefixes(self) -> None:
        prefixes = ReasoningTagGenerator.standard_prefixes()
        assert "think" in prefixes
        assert "thinking" in prefixes
        assert "reasoning" in prefixes
        assert "answer" in prefixes
        assert isinstance(prefixes, tuple)

    def test_for_standard_prefix_valid(self) -> None:
        tag = ReasoningTagGenerator.for_standard_prefix("thinking", session_id="s1")
        assert tag.prefix == "thinking"
        assert "thinking" in tag.open_tag

    def test_for_standard_prefix_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown prefix"):
            ReasoningTagGenerator.for_standard_prefix("not_a_real_prefix")

    def test_for_standard_prefix_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="prefix cannot be empty"):
            ReasoningTagGenerator.for_standard_prefix("")

    def test_suffix_length_custom(self) -> None:
        gen = ReasoningTagGenerator(suffix_length=4)
        tag = gen.generate()
        # suffix length is 4, but the suffix itself (without colon) is 4 chars
        assert len(tag.raw_suffix) == 4


class TestGenerateSessionTag:
    """Tests for the generate_session_tag convenience function."""

    def test_default_call(self) -> None:
        tag = generate_session_tag()
        assert isinstance(tag, ReasoningTagSet)
        assert tag.prefix == "think"

    def test_custom_prefix(self) -> None:
        tag = generate_session_tag(prefix="reasoning")
        assert tag.prefix == "reasoning"

    def test_with_session_id(self) -> None:
        tag = generate_session_tag(session_id="my-session")
        assert tag.session_id == "my-session"

    def test_custom_suffix_length(self) -> None:
        tag = generate_session_tag(suffix_length=16)
        assert len(tag.raw_suffix) == 16
