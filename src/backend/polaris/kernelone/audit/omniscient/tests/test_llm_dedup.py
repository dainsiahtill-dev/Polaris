"""Tests for LLMEventDeduplicator.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from polaris.kernelone.audit.omniscient.dedup import (
    LLMEventDeduplicator,
    _content_hash,
    get_global_llm_dedup,
    set_global_llm_dedup,
)

# ---------------------------------------------------------------------------
# _content_hash
# ---------------------------------------------------------------------------


class TestContentHash:
    def test_empty_data_returns_constant(self) -> None:
        assert _content_hash(None) == "empty"
        assert _content_hash({}) == "empty"

    def test_same_data_same_hash(self) -> None:
        data = {"event_type": "call_start", "model": "gpt-4", "attempt": 1}
        assert _content_hash(data) == _content_hash(data)

    def test_different_data_different_hash(self) -> None:
        a = {"event_type": "call_start", "model": "gpt-4"}
        b = {"event_type": "call_end", "model": "gpt-4"}
        assert _content_hash(a) != _content_hash(b)

    def test_volatile_fields_ignored(self) -> None:
        a = {"event_type": "call_start", "model": "gpt-4", "call_id": "x", "timestamp": 1000}
        b = {"event_type": "call_start", "model": "gpt-4", "call_id": "y", "timestamp": 2000}
        assert _content_hash(a) == _content_hash(b)


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — basic behaviour
# ---------------------------------------------------------------------------


class TestDeduplicatorBasic:
    def test_first_event_always_emitted(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        assert dedup.should_emit(
            session_id="s1",
            role="director",
            event_data={"event_type": "call_start", "model": "gpt-4"},
            call_id="c1",
        )

    def test_same_call_id_suppressed_within_window(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        assert not dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")

    def test_different_call_id_allowed_within_window(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c2")

    def test_same_call_id_allowed_after_window_expires(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=1)
        data = {"event_type": "call_start", "model": "gpt-4"}
        t0 = 1000.0
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1", now=t0)
        # Within window → suppressed
        assert not dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1", now=t0 + 0.5)
        # After window → allowed (window expired, entry reset)
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1", now=t0 + 2.0)

    def test_different_session_not_suppressed(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        assert dedup.should_emit(session_id="s2", role="director", event_data=data, call_id="c1")

    def test_different_role_not_suppressed(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        assert dedup.should_emit(session_id="s1", role="pm", event_data=data, call_id="c1")

    def test_different_content_not_suppressed(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        a = {"event_type": "call_start", "model": "gpt-4"}
        b = {"event_type": "call_end", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=a, call_id="c1")
        assert dedup.should_emit(session_id="s1", role="director", event_data=b, call_id="c1")


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — no call_id fallback
# ---------------------------------------------------------------------------


class TestDeduplicatorNoCallId:
    def test_no_call_id_suppressed_within_window(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data)
        assert not dedup.should_emit(session_id="s1", role="director", event_data=data)

    def test_no_call_id_allowed_after_window(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=5)
        data = {"event_type": "call_start", "model": "gpt-4"}
        t0 = 1000.0
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, now=t0)
        assert not dedup.should_emit(session_id="s1", role="director", event_data=data, now=t0 + 2)
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, now=t0 + 10)


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — multiple distinct calls
# ---------------------------------------------------------------------------


class TestDeduplicatorMultipleCalls:
    def test_multiple_distinct_call_ids_all_allowed(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        for i in range(5):
            assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id=f"c{i}")

    def test_repeated_call_ids_suppressed_after_first(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        for _ in range(10):
            assert not dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")

    def test_interleaved_calls(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c2")
        # c1 again → suppressed (same call_id, still in window)
        assert not dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        # c3 → new
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c3")


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — stats & reset
# ---------------------------------------------------------------------------


class TestDeduplicatorStats:
    def test_stats_reflect_state(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")  # suppressed
        stats = dedup.get_stats()
        assert stats["active_keys"] == 1
        assert stats["total_suppressed"] == 1
        assert stats["window_seconds"] == 10

    def test_reset_clears_state(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        dedup.reset()
        # After reset, same event should be allowed again
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — eviction
# ---------------------------------------------------------------------------


class TestDeduplicatorEviction:
    def test_eviction_on_overflow(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10, max_entries=100)
        for i in range(120):
            dedup.should_emit(
                session_id=f"s{i}",
                role="director",
                event_data={"event_type": "call_start", "model": "gpt-4"},
                call_id=f"c{i}",
            )
        stats = dedup.get_stats()
        # Should have evicted some entries
        assert stats["active_keys"] <= 100


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — edge cases
# ---------------------------------------------------------------------------


class TestDeduplicatorEdgeCases:
    def test_empty_session_id(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="", role="director", event_data=data, call_id="c1")
        assert not dedup.should_emit(session_id="", role="director", event_data=data, call_id="c1")

    def test_empty_role(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4"}
        assert dedup.should_emit(session_id="s1", role="", event_data=data, call_id="c1")
        assert not dedup.should_emit(session_id="s1", role="", event_data=data, call_id="c1")

    def test_none_event_data(self) -> None:
        dedup = LLMEventDeduplicator(window_seconds=10)
        assert dedup.should_emit(session_id="s1", role="director", event_data=None, call_id="c1")
        assert not dedup.should_emit(session_id="s1", role="director", event_data=None, call_id="c1")

    def test_concurrent_access(self) -> None:
        """Basic thread-safety smoke test."""
        import threading

        dedup = LLMEventDeduplicator(window_seconds=10)
        results: list[bool] = []
        lock = threading.Lock()

        def worker(call_id: str) -> None:
            r = dedup.should_emit(
                session_id="s1",
                role="director",
                event_data={"event_type": "call_start", "model": "gpt-4"},
                call_id=call_id,
            )
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(f"c{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All 10 distinct call_ids should be allowed
        assert all(results)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------


class TestGlobalSingleton:
    def setup_method(self) -> None:
        set_global_llm_dedup(LLMEventDeduplicator(window_seconds=10))

    def test_get_global_returns_instance(self) -> None:
        dedup = get_global_llm_dedup()
        assert isinstance(dedup, LLMEventDeduplicator)

    def test_set_global_replaces(self) -> None:
        custom = LLMEventDeduplicator(window_seconds=99)
        set_global_llm_dedup(custom)
        assert get_global_llm_dedup() is custom

    def test_singleton_is_same_object(self) -> None:
        a = get_global_llm_dedup()
        b = get_global_llm_dedup()
        assert a is b


# ---------------------------------------------------------------------------
# LLMEventDeduplicator — multi-provider / multi-model correctness
# ---------------------------------------------------------------------------


class TestDeduplicatorMultiProvider:
    """Verify dedup does NOT suppress events from different providers/models.

    In a multi-Director scenario, distinct provider+model combinations represent
    genuinely separate LLM invocations and must never be collapsed by dedup.
    """

    def test_same_model_different_provider_not_suppressed(self) -> None:
        """Two calls with same model but different provider must both emit."""
        dedup = LLMEventDeduplicator(window_seconds=10)
        data_openai = {"event_type": "call_start", "model": "gpt-4", "provider": "openai"}
        data_azure = {"event_type": "call_start", "model": "gpt-4", "provider": "azure"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data_openai, call_id="c1")
        assert dedup.should_emit(session_id="s1", role="director", event_data=data_azure, call_id="c2")

    def test_same_model_same_provider_suppressed(self) -> None:
        """Duplicate call (same model, same provider, same call_id) must be suppressed."""
        dedup = LLMEventDeduplicator(window_seconds=10)
        data = {"event_type": "call_start", "model": "gpt-4", "provider": "openai"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")
        assert not dedup.should_emit(session_id="s1", role="director", event_data=data, call_id="c1")

    def test_same_model_different_provider_no_call_id(self) -> None:
        """Without call_id, content_hash still differentiates providers."""
        dedup = LLMEventDeduplicator(window_seconds=10)
        data_openai = {"event_type": "call_start", "model": "gpt-4", "provider": "openai"}
        data_azure = {"event_type": "call_start", "model": "gpt-4", "provider": "azure"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data_openai)
        assert dedup.should_emit(session_id="s1", role="director", event_data=data_azure)

    def test_different_model_same_provider_not_suppressed(self) -> None:
        """Different models under the same provider must both emit."""
        dedup = LLMEventDeduplicator(window_seconds=10)
        data_gpt4 = {"event_type": "call_start", "model": "gpt-4", "provider": "openai"}
        data_gpt35 = {"event_type": "call_start", "model": "gpt-3.5-turbo", "provider": "openai"}
        assert dedup.should_emit(session_id="s1", role="director", event_data=data_gpt4, call_id="c1")
        assert dedup.should_emit(session_id="s1", role="director", event_data=data_gpt35, call_id="c2")

    def test_multi_provider_parallel_director_calls(self) -> None:
        """Simulate parallel Director calls from different providers — all must emit."""
        dedup = LLMEventDeduplicator(window_seconds=10)
        providers = ["openai", "azure", "anthropic", "ollama"]
        for i, prov in enumerate(providers):
            data = {"event_type": "call_start", "model": "gpt-4", "provider": prov}
            assert dedup.should_emit(
                session_id="s1",
                role="director",
                event_data=data,
                call_id=f"call_{prov}_{i}",
            ), f"Provider {prov} was incorrectly deduplicated!"

    def test_multi_model_multi_provider_matrix(self) -> None:
        """Full provider×model matrix: every unique combination must emit."""
        dedup = LLMEventDeduplicator(window_seconds=10)
        combos = [
            ("openai", "gpt-4"),
            ("openai", "gpt-3.5-turbo"),
            ("azure", "gpt-4"),
            ("azure", "gpt-3.5-turbo"),
            ("anthropic", "claude-3-opus"),
            ("anthropic", "claude-3-sonnet"),
        ]
        for i, (prov, model) in enumerate(combos):
            data = {"event_type": "call_start", "model": model, "provider": prov}
            assert dedup.should_emit(
                session_id="s1",
                role="director",
                event_data=data,
                call_id=f"call_{i}",
            ), f"({prov}, {model}) was incorrectly deduplicated!"
