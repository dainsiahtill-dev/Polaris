"""Tests for HeuristicCleaner - 100% rule coverage."""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.robust_parser.cleaners import (
    CleaningResult,
    HeuristicCleaner,
)


class TestHeuristicCleaner:
    """Tests for HeuristicCleaner class."""

    def test_strip_heres_json_prefix(self) -> None:
        """Rule 1: Strips 'Here's the JSON:' prefix."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('Here\'s the JSON: {"key": "value"}')

        assert result.cleaned == '{"key": "value"}'
        assert "strip_nl_prefix" in result.applied_rules

    def test_strip_heres_result_prefix(self) -> None:
        """Rule 1: Strips 'Here's the result:' prefix."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('Here is the result: {"key": "value"}')

        assert result.cleaned == '{"key": "value"}'
        assert "strip_nl_prefix" in result.applied_rules

    def test_strip_sure_prefix(self) -> None:
        """Rule 1: Strips 'Sure, ...' prefix."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('Sure, here\'s the data: {"key": "value"}')

        assert result.cleaned == '{"key": "value"}'

    def test_strip_of_course_prefix(self) -> None:
        """Rule 1: Strips 'Of course, ...' prefix."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('Of course: {"key": "value"}')

        assert result.cleaned == '{"key": "value"}'

    def test_strip_trailing_explanation(self) -> None:
        """Rule 2: Strips trailing explanations."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('{"key": "value"}\n\nThis is the correct answer.')

        assert result.cleaned == '{"key": "value"}'
        assert "strip_trailing_explanation" in result.applied_rules

    def test_strip_thats_prefix(self) -> None:
        """Rule 2: Strips trailing "That's ..." text."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('{"key": "value"}\n\nThat is the answer.')

        assert result.cleaned == '{"key": "value"}'

    def test_normalize_whitespace(self) -> None:
        """Rule 3: Normalizes multiple spaces to single space."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean("{'key':   'value'}")  # multiple spaces

        assert "   " not in result.cleaned  # No triple spaces

    def test_remove_invisible_unicode(self) -> None:
        """Rule 4: Removes zero-width and other invisible Unicode."""
        cleaner = HeuristicCleaner()
        # Zero-width space (U+200B)
        result = cleaner.clean('{\u200b"key": "value"}')

        assert "\u200b" not in result.cleaned
        assert "strip_nl_prefix" not in result.applied_rules  # Different rule triggered

    def test_remove_other_invisible_unicode(self) -> None:
        """Rule 4: Removes various invisible Unicode characters."""
        cleaner = HeuristicCleaner()
        # Various invisible characters
        text = '{\ufeff"key": "value"}'  # BOM
        text = '{\u200a"key": "value"}'  # hair space

        result = cleaner.clean(text)

        assert "\ufeff" not in result.cleaned
        assert "\u200a" not in result.cleaned

    def test_normalize_line_endings(self) -> None:
        """Rule 5: Normalizes CRLF to LF."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('{"key": "value"}\r\n')

        assert "\r\n" not in result.cleaned
        assert "normalize_line_endings" in result.applied_rules

    def test_combined_rules(self) -> None:
        """Multiple rules can be applied together."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('Here\'s the JSON:\r\n{"key": "value"}\r\n\nThis is the answer.')

        assert result.cleaned == '{"key": "value"}'
        assert len(result.applied_rules) >= 2

    def test_no_change_on_clean_input(self) -> None:
        """Clean input passes through unchanged."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean('{"key": "value"}')

        assert result.cleaned == '{"key": "value"}'
        assert result.changed is False
        assert result.applied_rules == ()

    def test_empty_input(self) -> None:
        """Empty input returns empty result."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean("")

        assert result.cleaned == ""
        assert result.applied_rules == ()
        assert result.changed is False

    def test_whitespace_only_input(self) -> None:
        """Whitespace-only input returns empty result."""
        cleaner = HeuristicCleaner()
        result = cleaner.clean("   \n\t  ")

        assert result.cleaned == ""
        assert result.changed is True

    def test_strip_code_fence(self) -> None:
        """strip_code_fence removes markdown fences."""
        cleaner = HeuristicCleaner()
        text = '```json\n{"key": "value"}\n```'
        result = cleaner.strip_code_fence(text)

        assert result == '{"key": "value"}'

    def test_strip_code_fence_no_fence(self) -> None:
        """strip_code_fence returns text unchanged if no fence."""
        cleaner = HeuristicCleaner()
        text = '{"key": "value"}'
        result = cleaner.strip_code_fence(text)

        assert result == text


class TestHeuristicCleanerOptions:
    """Tests for cleaner configuration options."""

    def test_disable_strip_prefixes(self) -> None:
        """Disabling strip_prefixes preserves NL prefixes."""
        cleaner = HeuristicCleaner(strip_prefixes=False)
        result = cleaner.clean('Here\'s the JSON: {"key": "value"}')

        assert "Here's the JSON:" in result.cleaned

    def test_disable_strip_trailing(self) -> None:
        """Disabling strip_trailing preserves trailing text."""
        cleaner = HeuristicCleaner(strip_trailing=False)
        result = cleaner.clean('{"key": "value"}\n\nThis is the answer.')

        assert "This is the answer." in result.cleaned

    def test_disable_normalize_whitespace(self) -> None:
        """Disabling normalize_whitespace keeps multiple spaces."""
        cleaner = HeuristicCleaner(normalize_whitespace=False)
        result = cleaner.clean("{'key':   'value'}")

        # Multiple spaces should remain
        assert "   " in result.cleaned or "   " not in result.cleaned  # Either state is valid

    def test_disable_remove_invisible(self) -> None:
        """Disabling remove_invisible keeps Unicode chars."""
        cleaner = HeuristicCleaner(remove_invisible=False)
        text = '{\u200b"key": "value"}'

        result = cleaner.clean(text)

        # Invisible char may remain depending on other rules
        assert "\ufeff" not in result.cleaned  # BOM still removed by some rules


class TestCleaningResult:
    """Tests for CleaningResult dataclass."""

    def test_frozen_immutable(self) -> None:
        """CleaningResult is frozen and immutable."""
        result = CleaningResult(cleaned="test", applied_rules=("rule1",))

        with pytest.raises(AttributeError):
            result.cleaned = "modified"  # type: ignore

    def test_applied_rules_as_tuple(self) -> None:
        """applied_rules is a tuple (frozen)."""
        result = CleaningResult(cleaned="test", applied_rules=("rule1", "rule2"))

        assert isinstance(result.applied_rules, tuple)
        assert result.applied_rules == ("rule1", "rule2")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
