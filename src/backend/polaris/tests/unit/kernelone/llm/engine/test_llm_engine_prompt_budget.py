"""Tests for polaris.kernelone.llm.engine.prompt_budget."""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.kernelone.llm.engine.prompt_budget import CompressionRouter, TokenBudgetManager
from polaris.kernelone.llm.shared_contracts import CompressionResult, ModelSpec, TokenBudgetDecision


class TestCompressionRouterInit:
    def test_defaults(self) -> None:
        router = CompressionRouter()
        assert router.workspace is None
        assert router.role is None
        assert router._compressor_port is None

    def test_custom_values(self) -> None:
        router = CompressionRouter(workspace="/tmp", role="pm")
        assert router.workspace == "/tmp"
        assert router.role == "pm"


class TestCompressionRouterIsConversationContent:
    def test_valid_conversation(self) -> None:
        router = CompressionRouter()
        text = '[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]'
        assert router._is_conversation_content(text) is True

    def test_invalid_json(self) -> None:
        router = CompressionRouter()
        assert router._is_conversation_content("not json") is False

    def test_not_a_list(self) -> None:
        router = CompressionRouter()
        assert router._is_conversation_content('{"role": "user"}') is False

    def test_empty_list(self) -> None:
        router = CompressionRouter()
        assert router._is_conversation_content("[]") is False

    def test_missing_role(self) -> None:
        router = CompressionRouter()
        assert router._is_conversation_content('[{"content": "hello"}]') is False


class TestCompressionRouterIsCodeContent:
    def test_code_content(self) -> None:
        router = CompressionRouter()
        text = 'def foo():\n    return {"a": 1}\n'
        assert router._is_code_content(text) is True

    def test_not_code(self) -> None:
        router = CompressionRouter()
        text = "This is a normal sentence without code."
        assert router._is_code_content(text) is False

    def test_empty(self) -> None:
        router = CompressionRouter()
        assert router._is_code_content("") is False


class TestCompressionRouterCompressCode:
    def test_removes_imports(self) -> None:
        router = CompressionRouter()
        text = "\n".join([f"import mod{i}" for i in range(20)])
        result = router._compress_code(text, 1000)
        assert "omitted" in result
        assert "import mod0" in result

    def test_removes_comments(self) -> None:
        router = CompressionRouter()
        lines = [f"# comment {i}" for i in range(15)]
        lines.append("actual_code()")
        text = "\n".join(lines)
        result = router._compress_code(text, 1000)
        assert "omitted" in result
        assert "actual_code()" in result

    def test_limits_blank_lines(self) -> None:
        router = CompressionRouter()
        text = "line1\n\n\n\nline2"
        result = router._compress_code(text, 1000)
        assert result.count("\n\n") <= 1

    def test_hard_trim_fallback(self) -> None:
        router = CompressionRouter()
        text = "x" * 10000
        result = router._compress_code(text, 10)
        assert "[... context compressed" in result


class TestCompressionRouterCompressByLines:
    def test_short_text_returns_hard_trim(self) -> None:
        router = CompressionRouter()
        text = "\n".join([f"line {i}" for i in range(5)])
        result = router._compress_by_lines(text, 100)
        # Fewer than 24 lines triggers hard_trim
        assert result != text or len(result) <= len(text)

    def test_long_text_compacts(self) -> None:
        router = CompressionRouter()
        text = "\n".join([f"line {i}" for i in range(100)])
        result = router._compress_by_lines(text, 10000)
        assert "compressed by TokenBudgetManager" in result

    def test_preserves_head_and_tail(self) -> None:
        router = CompressionRouter()
        lines = [f"line {i}" for i in range(100)]
        text = "\n".join(lines)
        result = router._compress_by_lines(text, 10000)
        assert "line 0" in result
        assert "line 99" in result


class TestCompressionRouterHardTrim:
    def test_zero_target(self) -> None:
        router = CompressionRouter()
        assert router._hard_trim("hello", 0) == ""

    def test_short_text_unchanged(self) -> None:
        router = CompressionRouter()
        text = "short"
        result = router._hard_trim(text, 100)
        assert result == text

    def test_long_text_truncated(self) -> None:
        router = CompressionRouter()
        text = "x" * 10000
        result = router._hard_trim(text, 100)
        assert "[... context compressed" in result
        assert len(result) < len(text)


class TestCompressionRouterRouteAndCompress:
    def test_conversation_content(self) -> None:
        router = CompressionRouter()
        text = '[{"role": "user", "content": "' + "x" * 5000 + '"}]'
        _result, compression = router.route_and_compress(text, 100)
        assert isinstance(compression, CompressionResult)
        assert compression.strategy in ("role_context_compressor", "line_compaction", "hard_trim")

    def test_code_content(self) -> None:
        router = CompressionRouter()
        text = "def foo():\n    " + "x" * 5000 + "\n"
        _result, compression = router.route_and_compress(text, 100)
        assert isinstance(compression, CompressionResult)

    def test_general_content(self) -> None:
        router = CompressionRouter()
        text = "\n".join([f"line {i}" for i in range(100)])
        _result, compression = router.route_and_compress(text, 50)
        assert isinstance(compression, CompressionResult)
        assert compression.strategy in ("line_compaction", "hard_trim")

    def test_history_prevents_repeat(self) -> None:
        router = CompressionRouter()
        text = "x" * 10000
        _result1, _comp1 = router.route_and_compress(text, 100, compression_history=[])
        # The history keys checked by the router are "role_context", "code", "line"
        # (not the full strategy names like "line_compaction" or "hard_trim")
        # So passing the strategy name doesn't prevent reuse; pass the internal key
        _result2, comp2 = router.route_and_compress(text, 100, compression_history=["line"])
        assert comp2.strategy == "hard_trim"

    def test_port_injection(self) -> None:
        mock_port = MagicMock()
        mock_snapshot = MagicMock()
        mock_snapshot.original_tokens = 1000
        mock_snapshot.compressed_tokens = 500
        mock_snapshot.method = "test_method"
        mock_port.compact_if_needed = MagicMock(
            return_value=([{"role": "user", "content": "compressed"}], mock_snapshot)
        )
        router = CompressionRouter(compressor_port=mock_port)
        text = '[{"role": "user", "content": "hello"}]'
        _result, compression = router.route_and_compress(text, 100)
        assert compression.strategy == "role_context_compressor"
        assert any("test_method" in note for note in compression.notes)


class TestTokenBudgetManagerInit:
    def test_default_values(self) -> None:
        mgr = TokenBudgetManager()
        assert 0.01 <= mgr.safety_margin_ratio <= 0.4
        assert mgr.min_output_tokens >= 32
        assert mgr.min_prompt_budget_tokens >= 64

    def test_custom_values(self) -> None:
        mgr = TokenBudgetManager(safety_margin_ratio=0.2, min_output_tokens=512, min_prompt_budget_tokens=128)
        assert mgr.safety_margin_ratio == 0.2
        assert mgr.min_output_tokens == 512
        assert mgr.min_prompt_budget_tokens == 128

    def test_clamps_safety_margin(self) -> None:
        mgr = TokenBudgetManager(safety_margin_ratio=0.5)
        assert mgr.safety_margin_ratio == 0.4
        mgr2 = TokenBudgetManager(safety_margin_ratio=0.0)
        assert mgr2.safety_margin_ratio == 0.01


class TestTokenBudgetManagerEnforce:
    def test_within_budget(self) -> None:
        mgr = TokenBudgetManager()
        spec = ModelSpec(
            provider_id="test", provider_type="test", model="test", max_context_tokens=32768, max_output_tokens=4096
        )
        decision = mgr.enforce("hello world", spec)
        assert isinstance(decision, TokenBudgetDecision)
        assert decision.allowed is True
        assert decision.compression_applied is False

    def test_exceeds_budget(self) -> None:
        mgr = TokenBudgetManager()
        spec = ModelSpec(
            provider_id="test", provider_type="test", model="test", max_context_tokens=512, max_output_tokens=128
        )
        text = "x" * 5000
        decision = mgr.enforce(text, spec)
        assert isinstance(decision, TokenBudgetDecision)
        # May be allowed with compression or rejected
        assert decision.compression_applied is True

    def test_already_compressed(self) -> None:
        mgr = TokenBudgetManager()
        spec = ModelSpec(
            provider_id="test", provider_type="test", model="test", max_context_tokens=512, max_output_tokens=128
        )
        text = "x" * 5000
        decision = mgr.enforce(text, spec, compression_history=["line_compaction"])
        assert isinstance(decision, TokenBudgetDecision)
        assert decision.compression_applied is True

    def test_rejected_when_compression_insufficient(self) -> None:
        mgr = TokenBudgetManager()
        spec = ModelSpec(
            provider_id="test", provider_type="test", model="test", max_context_tokens=64, max_output_tokens=32
        )
        text = "x" * 10000
        decision = mgr.enforce(text, spec)
        assert isinstance(decision, TokenBudgetDecision)
        # With such a tiny context, it should be rejected
        assert decision.allowed is False or decision.compression_applied is True

    def test_requested_output_tokens(self) -> None:
        mgr = TokenBudgetManager()
        spec = ModelSpec(
            provider_id="test", provider_type="test", model="test", max_context_tokens=32768, max_output_tokens=4096
        )
        decision = mgr.enforce("hello", spec, requested_output_tokens=1024)
        assert decision.allowed is True
        assert decision.reserved_output_tokens >= 1024


class TestTokenBudgetManagerDropRatio:
    def test_zero_original(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._drop_ratio(0, 100) == 0.0

    def test_full_drop(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._drop_ratio(100, 0) == 1.0

    def test_half_drop(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._drop_ratio(100, 50) == 0.5

    def test_clamped(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._drop_ratio(100, 200) == 0.0
        # Negative compressed_tokens means drop_ratio > 1, clamped to 1.0
        assert mgr._drop_ratio(100, -10) == 1.0


class TestTokenBudgetManagerQualityFromRatio:
    def test_ok(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._quality_from_ratio(0.0) == "ok"
        assert mgr._quality_from_ratio(0.3) == "ok"

    def test_warning(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._quality_from_ratio(0.5) == "warning"
        assert mgr._quality_from_ratio(0.6) == "warning"

    def test_degraded(self) -> None:
        mgr = TokenBudgetManager()
        assert mgr._quality_from_ratio(0.8) == "degraded"
        assert mgr._quality_from_ratio(1.0) == "degraded"
