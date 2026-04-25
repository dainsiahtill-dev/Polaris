"""Tests for polaris.kernelone.llm.engine.token_estimator."""

from __future__ import annotations

from unittest.mock import patch

from polaris.kernelone.llm.engine.token_estimator import (
    TokenEstimator,
    TokenEstimatorAdapter,
    ensure_token_estimator_registered,
    estimate_tokens,
)


class TestTokenEstimatorEstimate:
    def test_empty_text(self) -> None:
        assert TokenEstimator.estimate("") == 0

    def test_none_text(self) -> None:
        assert TokenEstimator.estimate(None) == 0  # type: ignore[arg-type]

    def test_general_text(self) -> None:
        text = "a" * 100
        result = TokenEstimator.estimate(text)
        assert result == 25  # 100 / 4

    def test_code_content_type(self) -> None:
        text = "a" * 100
        result = TokenEstimator.estimate(text, content_type="code")
        assert result == 34  # 100 / 3

    def test_cjk_content_type(self) -> None:
        text = "a" * 100
        result = TokenEstimator.estimate(text, content_type="cjk")
        assert result == 50  # 100 / 2

    def test_cjk_ratio_above_threshold(self) -> None:
        text = "中" * 40 + "a" * 60
        result = TokenEstimator.estimate(text)
        assert result == 50  # 100 / 2 due to cjk_ratio > 0.3

    def test_cjk_ratio_mixed(self) -> None:
        text = "中" * 10 + "a" * 90
        result = TokenEstimator.estimate(text)
        # 0.05 < cjk_ratio=0.1 < 0.3, weighted average
        expected_chars_per_token = 0.1 * 2 + 0.9 * 4
        expected = max(1, int(100 / expected_chars_per_token))
        assert result == expected

    def test_low_cjk_ratio_uses_general(self) -> None:
        text = "中" * 2 + "a" * 98
        result = TokenEstimator.estimate(text)
        assert result == 25  # 100 / 4


class TestTokenEstimatorWithRealTokenizer:
    def test_cl100k_base_hint_no_tiktoken(self) -> None:
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = TokenEstimator.estimate("hello world", tokenizer_hint="cl100k_base")
            # Falls back to heuristic if tiktoken not installed
            assert isinstance(result, int)
            assert result > 0

    def test_o200k_base_hint_no_tiktoken(self) -> None:
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = TokenEstimator.estimate("hello world", tokenizer_hint="o200k_base")
            assert isinstance(result, int)
            assert result > 0

    def test_gpt4_alias(self) -> None:
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = TokenEstimator.estimate("hello", tokenizer_hint="gpt-4")
            assert isinstance(result, int)

    def test_gpt4o_alias(self) -> None:
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = TokenEstimator.estimate("hello", tokenizer_hint="gpt-4o")
            assert isinstance(result, int)

    def test_unknown_tokenizer_hint_fallback(self) -> None:
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = TokenEstimator.estimate("hello" * 100, tokenizer_hint="unknown_tokenizer")
            # Falls back to heuristic
            assert isinstance(result, int)
            assert result > 0


class TestTokenEstimatorEstimateMessages:
    def test_empty_messages(self) -> None:
        assert TokenEstimator.estimate_messages([]) == 0

    def test_none_messages(self) -> None:
        assert TokenEstimator.estimate_messages(None) == 0  # type: ignore[arg-type]

    def test_single_message(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        result = TokenEstimator.estimate_messages(messages)
        assert result > 0
        # Should include format overhead
        assert result >= 4

    def test_multiple_messages(self) -> None:
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = TokenEstimator.estimate_messages(messages)
        assert result > 0
        assert result >= 8  # 2 * 4 overhead


class TestTokenEstimatorGetStats:
    def test_empty_text(self) -> None:
        stats = TokenEstimator.get_stats("")
        assert stats["char_count"] == 0
        assert stats["cjk_count"] == 0
        assert stats["cjk_ratio"] == 0
        assert stats["code_indicators"] == 0
        assert stats["code_ratio"] == 0

    def test_mixed_content(self) -> None:
        text = "abc中{def}"
        stats = TokenEstimator.get_stats(text)
        assert stats["char_count"] == 10
        assert stats["cjk_count"] == 1
        assert stats["code_indicators"] == 2  # { and }
        assert stats["cjk_ratio"] == 0.1
        assert stats["code_ratio"] == 0.2
        assert "estimate_general" in stats
        assert "estimate_code" in stats
        assert "estimate_cjk" in stats


class TestEstimateTokensFunction:
    def test_convenience_function(self) -> None:
        result = estimate_tokens("hello world")
        assert isinstance(result, int)
        assert result > 0


class TestTokenEstimatorAdapter:
    def test_estimate_tokens_no_model(self) -> None:
        adapter = TokenEstimatorAdapter()
        result = adapter.estimate_tokens("hello world")
        assert isinstance(result, int)
        assert result > 0

    def test_estimate_tokens_gpt4o(self) -> None:
        adapter = TokenEstimatorAdapter()
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = adapter.estimate_tokens("hello", model="gpt-4o")
            assert isinstance(result, int)

    def test_estimate_tokens_gpt4(self) -> None:
        adapter = TokenEstimatorAdapter()
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = adapter.estimate_tokens("hello", model="gpt-4")
            assert isinstance(result, int)

    def test_estimate_tokens_claude(self) -> None:
        adapter = TokenEstimatorAdapter()
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = adapter.estimate_tokens("hello", model="claude-3")
            assert isinstance(result, int)

    def test_estimate_messages_tokens(self) -> None:
        adapter = TokenEstimatorAdapter()
        messages = [{"role": "user", "content": "hello"}]
        result = adapter.estimate_messages_tokens(messages)
        assert isinstance(result, int)
        assert result > 0

    def test_estimate_messages_tokens_with_model(self) -> None:
        adapter = TokenEstimatorAdapter()
        messages = [{"role": "user", "content": "hello"}]
        with patch("polaris.kernelone.llm.engine.token_estimator._logger"):
            result = adapter.estimate_messages_tokens(messages, model="gpt-4o")
            assert isinstance(result, int)


class TestEnsureTokenEstimatorRegistered:
    def test_registration(self) -> None:
        from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

        # Reset first
        ServiceLocator._token_estimator = None  # type: ignore[attr-defined]
        ensure_token_estimator_registered()
        assert ServiceLocator.get_token_estimator() is not None

    def test_idempotent(self) -> None:
        from polaris.kernelone.llm.toolkit.contracts import ServiceLocator

        ServiceLocator._token_estimator = None  # type: ignore[attr-defined]
        ensure_token_estimator_registered()
        first = ServiceLocator.get_token_estimator()
        ensure_token_estimator_registered()
        second = ServiceLocator.get_token_estimator()
        assert first is second
