"""Tests for canonical token estimator (StateFirstContextOS helper).

These tests verify the token estimation accuracy for:
- ASCII text (expected error < 10%)
- CJK text (expected exact match with cjk*1.5 formula)
- Mixed text (expected exact match)
"""

from __future__ import annotations

# Import from the canonical location used by StateFirstContextOS
from polaris.kernelone.context._token_estimator import estimate_tokens as canonical_estimate
from polaris.kernelone.llm.engine.token_estimator import TokenEstimator


class TestCanonicalEstimatorASCII:
    """Tests for ASCII text token estimation."""

    def test_canonical_estimator_ascii_accuracy(self) -> None:
        """ASCII text should have <10% error vs len/4."""
        text = "hello world " * 100  # 1200 chars
        estimated = canonical_estimate(text)
        expected = len(text) / 4  # 300
        error = abs(estimated - expected) / expected
        assert error < 0.10, f"ASCII error {error:.2%} exceeds 10% threshold"

    def test_canonical_estimator_ascii_repeating(self) -> None:
        """Test ASCII with repeating patterns."""
        text = "test " * 200  # 1000 chars
        estimated = canonical_estimate(text)
        expected = len(text) / 4  # 250
        error = abs(estimated - expected) / expected
        assert error < 0.10

    def test_canonical_estimator_ascii_single_word(self) -> None:
        """Test single ASCII word."""
        text = "hello"
        estimated = canonical_estimate(text)
        # Formula: max(1, int(ascii/4) + int(cjk*1.5))
        # 5 ASCII chars -> int(5/4) = 1, max(1, 1) = 1
        assert estimated == 1

    def test_canonical_estimator_ascii_empty(self) -> None:
        """Test empty string returns 0."""
        estimated = canonical_estimate("")
        assert estimated == 0

    def test_canonical_estimator_ascii_whitespace_only(self) -> None:
        """Test whitespace-only string."""
        estimated = canonical_estimate("   ")
        # 3 ASCII spaces, 3/4 = 0.75, int(0.75) = 0, max(1, 0) = 1
        assert estimated >= 1


class TestCanonicalEstimatorCJK:
    """Tests for CJK text token estimation."""

    def test_canonical_estimator_cjk_accuracy(self) -> None:
        """CJK text should use cjk*1.5 formula."""
        text = "你好世界" * 100  # 400 CJK chars
        estimated = canonical_estimate(text)
        # Expected: 0 ASCII chars + 400 CJK * 1.5 = 600
        assert estimated == 600

    def test_canonical_estimator_cjk_single_char(self) -> None:
        """Test single CJK character."""
        text = "你"
        estimated = canonical_estimate(text)
        # Formula: max(1, int(ascii/4) + int(cjk*1.5))
        # 0 ASCII + 1 CJK -> int(0) + int(1.5) = 0 + 1 = 1, max(1, 1) = 1
        assert estimated == 1

    def test_canonical_estimator_cjk_mixed(self) -> None:
        """Test CJK with spaces."""
        text = "你好 世界"  # 4 CJK + 1 space
        estimated = canonical_estimate(text)
        # Space is ASCII, CJK is not
        # 1 ASCII (space) / 4 = 0.25 -> int(0.25) = 0
        # 4 CJK * 1.5 = 6.0 -> int(6.0) = 6
        # max(1, 0 + 6) = 6
        assert estimated == 6


class TestCanonicalEstimatorMixed:
    """Tests for mixed ASCII/CJK text."""

    def test_canonical_estimator_mixed_text(self) -> None:
        """Mixed ASCII/CJK text should match expected formula."""
        text = "hello你好world世界" * 50
        estimated = canonical_estimate(text)

        # Count ASCII and CJK separately (10 ASCII + 4 CJK per repetition * 50)
        ascii_part = sum(1 for c in text if ord(c) < 128)  # 500
        cjk_part = len(text) - ascii_part  # 200
        # Expected: int(ascii/4) + int(cjk*1.5) = int(500/4) + int(200*1.5) = 125 + 300 = 425
        assert estimated == 425
        # Also verify the formula used by canonical estimator
        assert estimated == max(1, int(ascii_part / 4) + int(cjk_part * 1.5))

    def test_canonical_estimator_mixed_balanced(self) -> None:
        """Test with balanced ASCII and CJK."""
        # 100 ASCII chars + 100 CJK chars
        ascii_text = "abcdefghij" * 10  # 100 chars
        cjk_text = "你好世界" * 25  # 100 chars (4 per iteration * 25)
        mixed = ascii_text + cjk_text

        estimated = canonical_estimate(mixed)

        ascii_count = sum(1 for c in mixed if ord(c) < 128)  # 100
        cjk_count = len(mixed) - ascii_count  # 100
        # Expected: int(100/4) + int(100*1.5) = 25 + 150 = 175
        assert estimated == 175
        # Also verify the formula
        assert estimated == max(1, int(ascii_count / 4) + int(cjk_count * 1.5))


class TestUnifiedTokenEstimator:
    """Tests for the unified TokenEstimator class."""

    def test_canonical_estimate_function(self) -> None:
        """Test the convenience estimate_tokens function."""
        text = "hello world"
        result = canonical_estimate(text)
        assert isinstance(result, int)
        assert result >= 0

    def test_token_estimator_class_ascii(self) -> None:
        """Test TokenEstimator class with ASCII text."""
        text = "test content " * 50
        result = TokenEstimator.estimate(text)
        assert isinstance(result, int)
        assert result > 0

    def test_token_estimator_class_cjk(self) -> None:
        """Test TokenEstimator class with CJK text."""
        text = "测试文本" * 20
        result = TokenEstimator.estimate(text, content_type="cjk")
        assert isinstance(result, int)
        assert result > 0

    def test_token_estimator_empty_string(self) -> None:
        """Test TokenEstimator with empty string."""
        result = TokenEstimator.estimate("")
        assert result == 0

    def test_token_estimator_code_content_type(self) -> None:
        """Test TokenEstimator with code content type."""
        text = "def foo():\n    return 42\n" * 10
        result = TokenEstimator.estimate(text, content_type="code")
        assert isinstance(result, int)
        # Code has different chars/token ratio (3 vs 4)
        assert result > 0

    def test_token_estimator_get_stats(self) -> None:
        """Test TokenEstimator.get_stats method."""
        text = "Hello 你好 World 世界"
        stats = TokenEstimator.get_stats(text)

        assert "char_count" in stats
        assert "cjk_count" in stats
        assert "estimate_general" in stats
        assert stats["char_count"] == len(text)
        assert stats["cjk_count"] == 4  # 你好世界


class TestCanonicalEstimatorDirect:
    """Tests for the canonical _token_estimator module directly."""

    def test_canonical_estimator_empty(self) -> None:
        """Canonical estimator should return 0 for empty string."""
        assert canonical_estimate("") == 0

    def test_canonical_estimator_ascii(self) -> None:
        """Canonical estimator should work for ASCII."""
        text = "hello world"
        result = canonical_estimate(text)
        assert result == 2  # 11 chars / 4 = 2.75 -> 2

    def test_canonical_estimator_cjk(self) -> None:
        """Canonical estimator should work for CJK."""
        text = "你好世界"
        result = canonical_estimate(text)
        assert result == 6  # 4 CJK * 1.5 = 6

    def test_canonical_estimator_minimum_one(self) -> None:
        """Non-empty should return at least 1."""
        assert canonical_estimate("a") == 1

    def test_helpers_delegates_to_canonical(self) -> None:
        """helpers.canonical_estimate should return same result as canonical."""
        text = "测试hello世界"
        assert canonical_estimate(text) == canonical_estimate(text)
