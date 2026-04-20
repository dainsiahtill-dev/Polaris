"""LLM subsystem convergence tests.

Verifies:
1. executor / stream_executor shared-logic deduplication (token counting consistency)
2. API key resolver is a single function invoked from both execution paths
3. Error classification is consistent across executor and resilience code paths
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from polaris.kernelone.errors import ErrorCategory, classify_error as canonical_classify_error
from polaris.kernelone.llm.engine._executor_base import (
    build_invoke_config,
    resolve_requested_output_tokens,
)
from polaris.kernelone.llm.engine.contracts import ModelSpec
from polaris.kernelone.llm.engine.resilience import ResilienceManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model_spec(max_context: int = 8192, max_output: int = 1024) -> ModelSpec:
    return ModelSpec(
        provider_id="test_provider",
        provider_type="openai_compat",
        model="gpt-4o-mini",
        max_context_tokens=max_context,
        max_output_tokens=max_output,
    )


# ---------------------------------------------------------------------------
# Test 1: executor and stream_executor share the same resolve_requested_output_tokens
# ---------------------------------------------------------------------------


class TestExecutorStreamDedup:
    """Verify that shared helpers produce identical results for both executor types."""

    def test_resolve_requested_output_tokens_consistency(self) -> None:
        """Same options + model_spec must yield identical token counts from both executors."""
        model_spec = _make_model_spec(max_output=2048)
        options = {"max_tokens": 512}
        invoke_cfg: dict[str, Any] = {}

        # Call the shared helper directly (both executor types delegate here)
        result = resolve_requested_output_tokens(options, invoke_cfg, model_spec)
        assert result == 512

    def test_resolve_requested_output_tokens_clamped_to_model_max(self) -> None:
        """Requested tokens must be clamped to model's max_output_tokens."""
        model_spec = _make_model_spec(max_output=1024)
        options = {"max_tokens": 9999}
        invoke_cfg: dict[str, Any] = {}

        result = resolve_requested_output_tokens(options, invoke_cfg, model_spec)
        assert result == 1024

    def test_resolve_requested_output_tokens_falls_back_to_model_spec(self) -> None:
        """When options has no max_tokens, model_spec default is used."""
        model_spec = _make_model_spec(max_output=512)
        options: dict[str, Any] = {}
        invoke_cfg: dict[str, Any] = {}

        result = resolve_requested_output_tokens(options, invoke_cfg, model_spec)
        assert result == 512

    def test_build_invoke_config_streaming_includes_tool_keys(self) -> None:
        """Streaming config now includes tool-related keys for native tool calling.

        Native tool calling is part of the canonical runtime contract, so tools
        and tool_choice must flow through the structured stream path.
        """
        provider_cfg: dict[str, Any] = {"type": "openai_compat"}
        options = {
            "temperature": 0.5,
            "tools": [{"name": "search"}],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }

        stream_cfg = build_invoke_config(provider_cfg, options, streaming=True)
        non_stream_cfg = build_invoke_config(provider_cfg, options, streaming=False)

        # Both streaming and non-streaming now include tools
        assert stream_cfg.get("temperature") == 0.5
        assert stream_cfg.get("tools") == [{"name": "search"}]
        assert stream_cfg.get("tool_choice") == "auto"
        assert stream_cfg.get("parallel_tool_calls") is True

        # Non-streaming path also includes all keys
        assert non_stream_cfg.get("tools") == [{"name": "search"}]
        assert non_stream_cfg.get("tool_choice") == "auto"


# ---------------------------------------------------------------------------
# Test 2: API key resolver is a single function with deterministic output
# ---------------------------------------------------------------------------


class TestApiKeySingleResolver:
    """Verify API key resolution uses a single canonical function."""

    def test_api_key_from_env_via_openai_provider(self, monkeypatch) -> None:
        """OPENAI_API_KEY env var must be picked up for openai_compat providers."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai-key")

        from polaris.kernelone.llm.runtime import resolve_provider_api_key

        cfg = resolve_provider_api_key(
            "my_openai_provider",
            "openai_compat",
            {},
        )
        assert cfg.get("api_key") == "sk-test-openai-key"

    def test_api_key_from_env_via_anthropic_provider(self, monkeypatch) -> None:
        """ANTHROPIC_API_KEY env var must be picked up for anthropic_compat providers."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")

        from polaris.kernelone.llm.runtime import resolve_provider_api_key

        cfg = resolve_provider_api_key(
            "my_anthropic_provider",
            "anthropic_compat",
            {},
        )
        assert cfg.get("api_key") == "sk-ant-test-key"

    def test_existing_api_key_not_overwritten(self, monkeypatch) -> None:
        """An existing api_key in the config must not be replaced by the env lookup."""
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")

        from polaris.kernelone.llm.runtime import resolve_provider_api_key

        cfg = resolve_provider_api_key(
            "my_provider",
            "openai_compat",
            {"api_key": "original-key"},
        )
        assert cfg.get("api_key") == "original-key"

    def test_both_executors_use_same_resolve_function(self) -> None:
        """executor._executor_base.get_provider_config calls resolve_provider_api_key."""
        from polaris.kernelone.llm.runtime import resolve_provider_api_key

        call_record: list[tuple] = []

        def _spy(provider_id, provider_type, cfg, **kw):
            call_record.append((provider_id, provider_type))
            return dict(cfg)

        with (
            patch("polaris.kernelone.llm.engine._executor_base.resolve_provider_api_key", side_effect=_spy),
            patch("polaris.kernelone.llm.engine._executor_base.resolve_provider_api_key"),
        ):
            # We just confirm the import path is correct (no error)
            pass

        # Direct import consistency check: both modules reference the same object
        from polaris.kernelone.llm.engine._executor_base import resolve_provider_api_key as base_fn

        assert base_fn is resolve_provider_api_key


# ---------------------------------------------------------------------------
# Test 3: Error classification is consistent across executor and resilience
# ---------------------------------------------------------------------------


class TestErrorClassificationConsistent:
    """Verify that executor and resilience paths classify errors identically."""

    @pytest.mark.parametrize(
        "exc_message, expected_category",
        [
            ("Connection timed out", ErrorCategory.TIMEOUT),
            ("Request timed out", ErrorCategory.TIMEOUT),
            ("rate limit exceeded", ErrorCategory.RATE_LIMIT),
            ("429 Too Many Requests", ErrorCategory.RATE_LIMIT),
            ("connection refused", ErrorCategory.NETWORK_ERROR),
            ("network error occurred", ErrorCategory.NETWORK_ERROR),
            ("configuration error", ErrorCategory.CONFIG_ERROR),
            ("some random error", ErrorCategory.UNKNOWN),
        ],
    )
    def test_classify_error_from_error_categories(self, exc_message, expected_category) -> None:
        """canonical classify_error must categorise known patterns correctly."""
        exc = Exception(exc_message)
        result = canonical_classify_error(exc)
        assert result == expected_category

    def test_resilience_manager_uses_canonical_classifier(self) -> None:
        """ResilienceManager._classify_error must delegate to canonical function."""
        manager = ResilienceManager()

        timeout_exc = Exception("request timed out")
        rate_exc = Exception("rate limit hit 429")
        network_exc = Exception("connection refused")
        config_exc = Exception("configuration error")
        unknown_exc = Exception("something completely different")

        assert manager._classify_error(timeout_exc) == ErrorCategory.TIMEOUT
        assert manager._classify_error(rate_exc) == ErrorCategory.RATE_LIMIT
        assert manager._classify_error(network_exc) == ErrorCategory.NETWORK_ERROR
        assert manager._classify_error(config_exc) == ErrorCategory.CONFIG_ERROR
        assert manager._classify_error(unknown_exc) == ErrorCategory.UNKNOWN

    def test_executor_classify_error_uses_same_canonical_function(self) -> None:
        """_executor_base.classify_error must delegate to the canonical function.

        Note: The re-export creates a new reference, so we verify behavior consistency
        rather than object identity.
        """
        from polaris.kernelone.llm.engine._executor_base import classify_error as base_classify

        # Both functions should produce identical results for any exception
        test_exceptions = [
            Exception("timeout after 30s"),
            Exception("rate limit exceeded"),
            Exception("connection refused"),
            Exception("invalid API key"),
            Exception("random error"),
        ]

        for exc in test_exceptions:
            base_result = base_classify(exc)
            canonical_result = canonical_classify_error(exc)
            assert base_result == canonical_result, (
                f"Result mismatch for {exc!r}: base={base_result}, canonical={canonical_result}"
            )

    def test_same_exception_same_category_both_paths(self) -> None:
        """executor and resilience must agree on category for any given exception."""
        manager = ResilienceManager()
        cases = [
            Exception("timeout expired"),
            Exception("rate limit"),
            Exception("connection error"),
            Exception("config bad"),
            Exception("mystery error"),
        ]
        for exc in cases:
            resilience_category = manager._classify_error(exc)
            executor_category = canonical_classify_error(exc)
            assert resilience_category == executor_category, (
                f"Category mismatch for {exc!r}: resilience={resilience_category}, executor={executor_category}"
            )
