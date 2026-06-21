"""Tests for LLMMetricsStore — time-windowed LLM call metrics aggregation.

Covers:
- Empty data (no events recorded)
- Normal data (multiple events, window filtering)
- Error data (error events, error_rate computation)
- Binding dimensions (role / provider / model aggregation)
- Ring buffer eviction (max_events bound)
- Singleton accessor
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest
from polaris.kernelone.audit.omniscient.interceptors.llm_metrics import (
    LLMMetricsStore,
    get_llm_metrics_store,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> LLMMetricsStore:
    """Fresh store per test."""
    return LLMMetricsStore(max_events=100, default_window_seconds=60)


# ---------------------------------------------------------------------------
# Empty data
# ---------------------------------------------------------------------------


class TestEmptyData:
    def test_query_returns_empty_snapshot(self, store: LLMMetricsStore) -> None:
        result = store.query()
        assert result["total"]["total_calls"] == 0
        assert result["total"]["error_calls"] == 0
        assert result["total"]["error_rate"] == 0.0
        assert result["total"]["avg_latency_ms"] == 0.0
        assert result["dimensions"] == []
        assert result["window_seconds"] == 60

    def test_query_with_custom_window(self, store: LLMMetricsStore) -> None:
        result = store.query(window_seconds=10)
        assert result["window_seconds"] == 10
        assert result["total"]["total_calls"] == 0

    def test_event_count_initially_zero(self, store: LLMMetricsStore) -> None:
        assert store.event_count == 0


# ---------------------------------------------------------------------------
# Normal data
# ---------------------------------------------------------------------------


class TestNormalData:
    def test_single_event(self, store: LLMMetricsStore) -> None:
        store.record(
            role="director",
            provider="openai",
            model="gpt-4",
            latency_ms=500.0,
            prompt_tokens=100,
            completion_tokens=50,
        )
        result = store.query()
        assert result["total"]["total_calls"] == 1
        assert result["total"]["avg_latency_ms"] == 500.0
        assert result["total"]["prompt_tokens_sum"] == 100
        assert result["total"]["completion_tokens_sum"] == 50
        assert len(result["dimensions"]) == 1
        dim = result["dimensions"][0]
        assert dim["role"] == "director"
        assert dim["provider"] == "openai"
        assert dim["model"] == "gpt-4"

    def test_multiple_events_same_dimension(self, store: LLMMetricsStore) -> None:
        for i in range(5):
            store.record(
                role="pm",
                provider="anthropic",
                model="claude-3",
                latency_ms=100.0 * (i + 1),
                prompt_tokens=50,
                completion_tokens=25,
            )
        result = store.query()
        assert result["total"]["total_calls"] == 5
        assert result["total"]["avg_latency_ms"] == 300.0  # (100+200+300+400+500)/5
        assert result["total"]["prompt_tokens_sum"] == 250
        assert result["total"]["completion_tokens_sum"] == 125
        assert len(result["dimensions"]) == 1
        assert result["dimensions"][0]["total_calls"] == 5

    def test_window_filtering_excludes_old_events(self, store: LLMMetricsStore) -> None:
        # Record an event "now"
        store.record(role="r1", provider="p1", model="m1", latency_ms=100.0)
        # Record an event 120 seconds ago (outside default 60s window)
        with patch("polaris.kernelone.audit.omniscient.interceptors.llm_metrics.time") as mock_time:
            mock_time.time.return_value = time.time() - 120
            store.record(role="r2", provider="p2", model="m2", latency_ms=200.0)

        result = store.query(window_seconds=60)
        # Only the "now" event should be in the window
        assert result["total"]["total_calls"] == 1
        assert result["dimensions"][0]["role"] == "r1"

    def test_min_max_latency(self, store: LLMMetricsStore) -> None:
        store.record(role="r", provider="p", model="m", latency_ms=100.0)
        store.record(role="r", provider="p", model="m", latency_ms=300.0)
        store.record(role="r", provider="p", model="m", latency_ms=200.0)
        result = store.query()
        dim = result["dimensions"][0]
        assert dim["min_latency_ms"] == 100.0
        assert dim["max_latency_ms"] == 300.0


# ---------------------------------------------------------------------------
# Error data
# ---------------------------------------------------------------------------


class TestErrorData:
    def test_single_error_event(self, store: LLMMetricsStore) -> None:
        store.record(
            role="director",
            provider="openai",
            model="gpt-4",
            latency_ms=50.0,
            is_error=True,
        )
        result = store.query()
        assert result["total"]["total_calls"] == 1
        assert result["total"]["error_calls"] == 1
        assert result["total"]["error_rate"] == 1.0

    def test_mixed_success_and_error(self, store: LLMMetricsStore) -> None:
        for _ in range(3):
            store.record(role="r", provider="p", model="m", latency_ms=100.0)
        for _ in range(2):
            store.record(role="r", provider="p", model="m", latency_ms=50.0, is_error=True)
        result = store.query()
        total = result["total"]
        assert total["total_calls"] == 5
        assert total["error_calls"] == 2
        assert total["error_rate"] == 0.4

    def test_error_rate_per_dimension(self, store: LLMMetricsStore) -> None:
        # Model A: 1 success, 1 error
        store.record(role="r", provider="p", model="m1", latency_ms=100.0)
        store.record(role="r", provider="p", model="m1", latency_ms=50.0, is_error=True)
        # Model B: 2 success
        store.record(role="r", provider="p", model="m2", latency_ms=200.0)
        store.record(role="r", provider="p", model="m2", latency_ms=300.0)

        result = store.query()
        dims_by_model = {d["model"]: d for d in result["dimensions"]}
        assert dims_by_model["m1"]["error_rate"] == 0.5
        assert dims_by_model["m2"]["error_rate"] == 0.0


# ---------------------------------------------------------------------------
# Binding dimensions (role / provider / model)
# ---------------------------------------------------------------------------


class TestBindingDimensions:
    def test_multi_role_aggregation(self, store: LLMMetricsStore) -> None:
        store.record(role="pm", provider="openai", model="gpt-4", latency_ms=100.0)
        store.record(role="director", provider="anthropic", model="claude-3", latency_ms=200.0)
        store.record(role="architect", provider="openai", model="gpt-4o", latency_ms=300.0)

        result = store.query()
        assert result["total"]["total_calls"] == 3
        assert len(result["dimensions"]) == 3

        roles = {d["role"] for d in result["dimensions"]}
        assert roles == {"pm", "director", "architect"}

    def test_multi_provider_aggregation(self, store: LLMMetricsStore) -> None:
        store.record(role="r", provider="openai", model="gpt-4", latency_ms=100.0)
        store.record(role="r", provider="anthropic", model="claude-3", latency_ms=200.0)
        store.record(role="r", provider="openai", model="gpt-4o", latency_ms=150.0)

        result = store.query()
        dims_by_provider: dict[str, list[dict[str, Any]]] = {}
        for d in result["dimensions"]:
            dims_by_provider.setdefault(d["provider"], []).append(d)

        assert "openai" in dims_by_provider
        assert "anthropic" in dims_by_provider
        assert len(dims_by_provider["openai"]) == 2

    def test_same_role_different_bindings(self, store: LLMMetricsStore) -> None:
        store.record(role="director", provider="openai", model="gpt-4", latency_ms=100.0)
        store.record(role="director", provider="anthropic", model="claude-3", latency_ms=200.0)

        result = store.query()
        assert result["total"]["total_calls"] == 2
        assert len(result["dimensions"]) == 2
        models = {d["model"] for d in result["dimensions"]}
        assert models == {"gpt-4", "claude-3"}

    def test_dimensions_sorted_by_tuple(self, store: LLMMetricsStore) -> None:
        store.record(role="z", provider="a", model="m", latency_ms=10.0)
        store.record(role="a", provider="z", model="m", latency_ms=20.0)
        store.record(role="m", provider="m", model="m", latency_ms=30.0)

        result = store.query()
        roles = [d["role"] for d in result["dimensions"]]
        assert roles == sorted(roles)


# ---------------------------------------------------------------------------
# Ring buffer eviction
# ---------------------------------------------------------------------------


class TestRingBufferEviction:
    def test_eviction_respects_max_events(self) -> None:
        s = LLMMetricsStore(max_events=5, default_window_seconds=3600)
        for i in range(10):
            s.record(role=f"r{i}", provider="p", model="m", latency_ms=float(i * 10))
        assert s.event_count == 5
        result = s.query()
        assert result["total"]["total_calls"] == 5
        # Only events 5..9 should remain
        assert result["dimensions"][0]["role"] == "r5"

    def test_eviction_preserves_recent(self) -> None:
        s = LLMMetricsStore(max_events=3, default_window_seconds=3600)
        s.record(role="old1", provider="p", model="m", latency_ms=10.0)
        s.record(role="old2", provider="p", model="m", latency_ms=20.0)
        s.record(role="new1", provider="p", model="m", latency_ms=30.0)
        s.record(role="new2", provider="p", model="m", latency_ms=40.0)
        assert s.event_count == 3
        result = s.query()
        roles = {d["role"] for d in result["dimensions"]}
        assert "old1" not in roles
        assert "new1" in roles
        assert "new2" in roles


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_events(self, store: LLMMetricsStore) -> None:
        store.record(role="r", provider="p", model="m", latency_ms=100.0)
        assert store.event_count == 1
        store.reset()
        assert store.event_count == 0
        result = store.query()
        assert result["total"]["total_calls"] == 0


# ---------------------------------------------------------------------------
# Default values / edge cases
# ---------------------------------------------------------------------------


class TestDefaultValues:
    def test_defaults_when_empty_strings(self, store: LLMMetricsStore) -> None:
        store.record(role="", provider="", model="", latency_ms=0.0)
        result = store.query()
        dim = result["dimensions"][0]
        assert dim["role"] == "unknown"
        assert dim["provider"] == "unknown"
        assert dim["model"] == "unknown"

    def test_negative_latency_clamped_to_zero(self, store: LLMMetricsStore) -> None:
        store.record(role="r", provider="p", model="m", latency_ms=-50.0)
        result = store.query()
        assert result["total"]["avg_latency_ms"] == 0.0

    def test_negative_tokens_clamped_to_zero(self, store: LLMMetricsStore) -> None:
        store.record(role="r", provider="p", model="m", latency_ms=10.0, prompt_tokens=-5, completion_tokens=-10)
        result = store.query()
        assert result["total"]["prompt_tokens_sum"] == 0
        assert result["total"]["completion_tokens_sum"] == 0


# ---------------------------------------------------------------------------
# Percentile computation
# ---------------------------------------------------------------------------


class TestPercentileComputation:
    def test_empty_store_percentiles_zero(self, store: LLMMetricsStore) -> None:
        result = store.query()
        assert result["total"]["p50_latency_ms"] == 0.0
        assert result["total"]["p95_latency_ms"] == 0.0
        assert result["total"]["p99_latency_ms"] == 0.0

    def test_single_event_percentiles_equal_latency(self, store: LLMMetricsStore) -> None:
        store.record(role="r", provider="p", model="m", latency_ms=123.0)
        result = store.query()
        total = result["total"]
        assert total["p50_latency_ms"] == 123.0
        assert total["p95_latency_ms"] == 123.0
        assert total["p99_latency_ms"] == 123.0
        dim = result["dimensions"][0]
        assert dim["p50_latency_ms"] == 123.0
        assert dim["p95_latency_ms"] == 123.0
        assert dim["p99_latency_ms"] == 123.0

    def test_multiple_events_percentiles_nonzero(self, store: LLMMetricsStore) -> None:
        latencies = [100.0, 200.0, 300.0, 400.0, 500.0]
        for lat in latencies:
            store.record(role="r", provider="p", model="m", latency_ms=lat)
        result = store.query()
        total = result["total"]
        # p50 of [100,200,300,400,500] at rank 2.0 -> 300
        assert total["p50_latency_ms"] == 300.0
        # p95 and p99 should be >= p50
        assert total["p95_latency_ms"] >= total["p50_latency_ms"]
        assert total["p99_latency_ms"] >= total["p95_latency_ms"]

    def test_percentiles_per_dimension(self, store: LLMMetricsStore) -> None:
        # Model A: latencies 100, 200, 300
        for lat in [100.0, 200.0, 300.0]:
            store.record(role="r", provider="p", model="m1", latency_ms=lat)
        # Model B: latencies 500, 600, 700
        for lat in [500.0, 600.0, 700.0]:
            store.record(role="r", provider="p", model="m2", latency_ms=lat)
        result = store.query()
        dims_by_model = {d["model"]: d for d in result["dimensions"]}
        # m1 p50 = 200
        assert dims_by_model["m1"]["p50_latency_ms"] == 200.0
        # m2 p50 = 600
        assert dims_by_model["m2"]["p50_latency_ms"] == 600.0

    def test_p95_reflects_tail_latency(self, store: LLMMetricsStore) -> None:
        # 99 events at 100ms, 1 event at 1000ms
        for _ in range(99):
            store.record(role="r", provider="p", model="m", latency_ms=100.0)
        store.record(role="r", provider="p", model="m", latency_ms=1000.0)
        result = store.query()
        total = result["total"]
        # p95 should be 100 (95th percentile is still in the 100ms cluster)
        assert total["p95_latency_ms"] == 100.0
        # p99 rank = 0.99*99 = 98.01 -> interpolates between idx 98 (100) and 99 (1000)
        # = 100*(1-0.01) + 1000*0.01 = 99 + 10 = 109
        assert total["p99_latency_ms"] == 109.0


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


class TestSingletonAccessor:
    def test_get_llm_metrics_store_returns_same_instance(self) -> None:
        s1 = get_llm_metrics_store()
        s2 = get_llm_metrics_store()
        assert s1 is s2

    def test_singleton_is_llm_metrics_store(self) -> None:
        s = get_llm_metrics_store()
        assert isinstance(s, LLMMetricsStore)
