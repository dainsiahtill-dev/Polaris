"""Tests for TokenEstimator."""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.engine import TokenEstimator, estimate_tokens


class TestTokenEstimator:
    """Test TokenEstimator functionality."""

    def test_estimate_empty_text(self) -> None:
        """Empty text returns 0."""
        assert TokenEstimator.estimate("") == 0
        assert TokenEstimator.estimate(None or "") == 0

    def test_estimate_simple_text(self) -> None:
        """Simple English text uses chars/4."""
        text = "hello world"
        expected = max(1, len(text) // 4)
        assert TokenEstimator.estimate(text) == expected

    def test_estimate_cjk_text(self) -> None:
        """CJK text uses chars/2 by content type hint."""
        text = "你好世界"
        # With content_type=cjk
        result = TokenEstimator.estimate(text, content_type="cjk")
        expected = max(1, len(text) // 2)
        assert result == expected

    def test_estimate_code_content(self) -> None:
        """Code content uses chars/3."""
        code = "def hello():\n    return 'world'"
        result = TokenEstimator.estimate(code, content_type="code")
        expected = max(1, len(code) // 3)
        assert result == expected

    def test_estimate_auto_detects_cjk(self) -> None:
        """Auto detection of high CJK ratio."""
        # >30% CJK should use CJK ratio (chars/2 vs chars/4)
        text = "这是一段中文文本 with some english"
        result = TokenEstimator.estimate(text)
        # CJK estimate should be higher than general (since chars/2 > chars/4)
        general = len(text) // 4
        assert result >= general

    def test_estimate_messages(self) -> None:
        """Message list estimation includes format overhead."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = TokenEstimator.estimate_messages(messages)
        # Should include format overhead (2 messages * 4 tokens)
        assert result > 0

    def test_get_stats(self) -> None:
        """Stats include CJK and code detection."""
        text = "Hello 世界 {code}"
        stats = TokenEstimator.get_stats(text)
        assert "char_count" in stats
        assert "cjk_count" in stats
        assert "cjk_ratio" in stats
        assert stats["cjk_count"] == 2  # 世界

    def test_convenience_function(self) -> None:
        """estimate_tokens convenience function works."""
        text = "test"
        assert estimate_tokens(text) == TokenEstimator.estimate(text)


class TestTokenEstimatorWithRealTokenizer:
    """Test with real tokenizer if available."""

    def test_cl100k_base_if_available(self) -> None:
        """Use cl100k_base if tiktoken available."""
        try:
            import tiktoken

            text = "Hello world"
            result = TokenEstimator.estimate(text, tokenizer_hint="cl100k_base")
            enc = tiktoken.get_encoding("cl100k_base")
            expected = len(enc.encode(text))
            assert result == expected
        except ImportError:
            pytest.skip("tiktoken not installed")

    def test_fallback_when_tokenizer_unavailable(self) -> None:
        """Fallback to heuristic when tokenizer_hint unavailable."""
        text = "Hello world"
        result = TokenEstimator.estimate(text, tokenizer_hint="nonexistent_tokenizer")
        # Should fallback to heuristic
        assert result == max(1, len(text) // 4)
