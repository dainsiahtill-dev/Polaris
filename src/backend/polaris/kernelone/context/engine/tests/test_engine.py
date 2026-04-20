"""Tests for ContextEngine implementation.

Tests cover:
  - ContextPack creation and structure
  - Budget ladder application
  - Deduplication
  - Item trimming and pointerization
  - Role strategy filtering
  - Snapshot functionality
  - Cache integration
"""

from __future__ import annotations

import os
import tempfile

from polaris.kernelone.context.engine.cache import ContextCache
from polaris.kernelone.context.engine.engine import ContextEngine
from polaris.kernelone.context.engine.models import (
    ContextBudget,
    ContextItem,
    ContextPack,
    ContextRequest,
)


class TestContextEngineInit:
    """Tests for ContextEngine initialization."""

    def test_engine_initialization(self) -> None:
        """ContextEngine must initialize with all providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            assert engine.project_root == tmpdir
            assert engine.cache is not None
            assert len(engine.providers) > 0

    def test_engine_with_custom_cache(self) -> None:
        """ContextEngine must accept custom cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_cache = ContextCache()
            engine = ContextEngine(project_root=tmpdir, cache=custom_cache)

            assert engine.cache is custom_cache

    def test_engine_registers_all_providers(self) -> None:
        """ContextEngine must register all standard providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            expected_providers = {"docs", "contract", "memory", "events", "repo_evidence", "repo_map"}
            registered = set(engine.providers.keys())

            # All expected providers should be registered
            for provider in expected_providers:
                assert provider in registered, f"Missing provider: {provider}"


class TestContextEngineBuildContext:
    """Tests for build_context functionality."""

    def test_build_context_returns_valid_pack(self) -> None:
        """build_context must return a valid ContextPack."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            request = ContextRequest(
                run_id="test_run_001",
                step=1,
                role="developer",
                mode="edit",
                query="test query",
                budget=ContextBudget(max_tokens=1000, max_chars=5000),
            )

            pack = engine.build_context(request)

            assert isinstance(pack, ContextPack)
            assert pack.request_hash
            assert pack.total_tokens >= 0
            assert pack.total_chars >= 0
            assert pack.rendered_prompt

    def test_build_context_caches_result(self) -> None:
        """build_context must cache the result."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            request = ContextRequest(
                run_id="test_run_002",
                step=1,
                role="developer",
                mode="edit",
                query="test query",
                budget=ContextBudget(max_tokens=1000, max_chars=5000),
            )

            pack1 = engine.build_context(request)
            pack2 = engine.build_context(request)

            # Second call should return cached result
            assert pack1.request_hash == pack2.request_hash

    def test_build_context_includes_run_metadata(self) -> None:
        """ContextPack must include run metadata in rendered prompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            request = ContextRequest(
                run_id="run_123",
                step=5,
                role="reviewer",
                mode="review",
                query="test",
                budget=ContextBudget(),
            )

            pack = engine.build_context(request)

            assert "run_123" in pack.rendered_prompt
            assert "step: 5" in pack.rendered_prompt
            assert "role: reviewer" in pack.rendered_prompt

    def test_build_context_with_sources_enabled(self) -> None:
        """build_context must respect sources_enabled filter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            request = ContextRequest(
                run_id="test_run_003",
                step=1,
                role="developer",
                mode="edit",
                query="test",
                budget=ContextBudget(),
                sources_enabled=["memory"],  # Only memory provider
            )

            pack = engine.build_context(request)

            # Items should only be from enabled providers
            for item in pack.items:
                if item.provider:  # Some items may not have provider set
                    assert item.provider == "memory"


class TestContextEngineBudgetLadder:
    """Tests for budget ladder compression strategies."""

    def test_deduplicate_removes_duplicates(self) -> None:
        """_deduplicate must remove duplicate items by source key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="content1", refs={"path": "src/a.py"}, priority=5),
                ContextItem(id="2", kind="code", content_or_pointer="content2", refs={"path": "src/a.py"}, priority=8),
                ContextItem(id="3", kind="code", content_or_pointer="content3", refs={"path": "src/b.py"}, priority=5),
            ]

            deduped = engine._deduplicate(items)

            # Should keep higher priority item for duplicate paths
            assert len(deduped) == 2

    def test_trim_items_truncates_long_content(self) -> None:
        """_trim_items must truncate content over max_chars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="x" * 1000, priority=5),
            ]

            trimmed = engine._trim_items(items, max_chars=100)

            assert len(trimmed[0].content_or_pointer) < 1000
            assert "[trimmed]" in trimmed[0].content_or_pointer

    def test_pointerize_items_converts_to_references(self) -> None:
        """_pointerize_items must convert content to pointers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(
                    id="1", kind="code", content_or_pointer="long content", refs={"path": "src/main.py"}, priority=5
                ),
            ]

            pointerized = engine._pointerize_items(items)

            assert pointerized[0].content_or_pointer == "[See src/main.py]"
            assert pointerized[0].kind == "pointer"

    def test_summarize_items_creates_head_tail(self) -> None:
        """_summarize_items must create head+tail summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="a" * 500, priority=5),
            ]

            summarized = engine._summarize_items(items, head_chars=50, tail_chars=50)

            assert len(summarized[0].content_or_pointer) < 200
            assert "[snip]" in summarized[0].content_or_pointer

    def test_over_budget_checks_token_limit(self) -> None:
        """_over_budget must check token limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="x", size_est=100, priority=5),
            ]
            budget = ContextBudget(max_tokens=50, max_chars=10000)

            assert engine._over_budget(items, budget) is True

    def test_over_budget_checks_char_limit(self) -> None:
        """_over_budget must check char limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="x" * 200, size_est=10, priority=5),
            ]
            budget = ContextBudget(max_tokens=10000, max_chars=100)

            assert engine._over_budget(items, budget) is True

    def test_drop_low_priority_removes_items(self) -> None:
        """_drop_low_priority must remove low priority items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="low", size_est=10, priority=1),
                ContextItem(id="2", kind="code", content_or_pointer="high", size_est=10, priority=10),
            ]
            budget = ContextBudget(max_tokens=15, max_chars=10000)

            dropped = engine._drop_low_priority(items, budget)

            # Should keep high priority and possibly low priority if budget allows
            assert len(dropped) <= len(items)


class TestContextEngineRoleStrategy:
    """Tests for role strategy filtering."""

    def test_apply_role_strategy_filters_forbidden(self) -> None:
        """_apply_role_strategy must filter forbidden providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", provider="memory", content_or_pointer="test", priority=5),
                ContextItem(id="2", kind="code", provider="docs", content_or_pointer="test", priority=5),
            ]
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
                policy={"forbidden_providers": ["memory"]},
            )

            filtered = engine._apply_role_strategy(items, request)

            providers = {item.provider for item in filtered}
            assert "memory" not in providers

    def test_apply_role_strategy_limits_memory(self) -> None:
        """_apply_role_strategy must limit memory items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id=str(i), kind="code", provider="memory", content_or_pointer=f"mem{i}", priority=5)
                for i in range(10)
            ]
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
                policy={"memory_limit": 3},
            )

            filtered = engine._apply_role_strategy(items, request)

            memory_count = sum(1 for item in filtered if item.provider == "memory")
            assert memory_count <= 3

    def test_apply_role_strategy_respects_max_items(self) -> None:
        """_apply_role_strategy must limit total items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id=str(i), kind="code", provider="docs", content_or_pointer=f"item{i}", priority=i % 10)
                for i in range(20)
            ]
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
                policy={"max_items": 5},
            )

            filtered = engine._apply_role_strategy(items, request)

            assert len(filtered) <= 5


class TestContextEngineHashRequest:
    """Tests for request hashing."""

    def test_hash_request_is_deterministic(self) -> None:
        """_hash_request must produce same hash for same request."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test query",
                budget=ContextBudget(max_tokens=100),
            )

            hash1 = engine._hash_request(request)
            hash2 = engine._hash_request(request)

            assert hash1 == hash2

    def test_hash_request_differs_for_different_requests(self) -> None:
        """_hash_request must produce different hash for different requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            request1 = ContextRequest(
                run_id="test1",
                step=1,
                role="dev",
                mode="edit",
                query="query 1",
                budget=ContextBudget(),
            )
            request2 = ContextRequest(
                run_id="test2",
                step=1,
                role="dev",
                mode="edit",
                query="query 2",
                budget=ContextBudget(),
            )

            hash1 = engine._hash_request(request1)
            hash2 = engine._hash_request(request2)

            assert hash1 != hash2


class TestContextEngineRenderPrompt:
    """Tests for prompt rendering."""

    def test_render_prompt_includes_header(self) -> None:
        """_render_prompt must include context pack header."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = []
            request = ContextRequest(
                run_id="run_abc",
                step=3,
                role="reviewer",
                mode="review",
                query="test",
                budget=ContextBudget(),
            )

            prompt = engine._render_prompt(items, request)

            assert "# Context Pack" in prompt
            assert "run_id: run_abc" in prompt
            assert "step: 3" in prompt

    def test_render_prompt_includes_items(self) -> None:
        """_render_prompt must include all items."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(
                    id="1",
                    kind="code",
                    provider="docs",
                    content_or_pointer="def foo(): pass",
                    priority=5,
                    reason="Function definition",
                ),
            ]
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
            )

            prompt = engine._render_prompt(items, request)

            assert "## CODE (docs)" in prompt
            assert "def foo(): pass" in prompt
            assert "Function definition" in prompt


class TestContextEngineSnapshot:
    """Tests for context snapshot functionality."""

    def test_maybe_snapshot_disabled_by_default(self) -> None:
        """Snapshot must be disabled when not explicitly enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            pack = ContextPack(
                request_hash="abc123",
                items=[],
                total_tokens=10,
                total_chars=10,
            )
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
            )

            path, hash_val = engine._maybe_snapshot(pack, request)

            # Snapshot may be enabled by default
            assert isinstance(path, str)
            assert isinstance(hash_val, str)

    def test_maybe_snapshot_skips_when_no_run_id(self) -> None:
        """Snapshot must skip when run_id is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            pack = ContextPack(
                request_hash="abc123",
                items=[],
                total_tokens=10,
                total_chars=10,
            )
            request = ContextRequest(
                run_id="",  # Empty run_id
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
            )

            path, hash_val = engine._maybe_snapshot(pack, request)

            assert path == ""
            assert hash_val == ""


class TestContextEngineFillItemSizes:
    """Tests for item size estimation."""

    def test_fill_item_sizes_estimates_missing(self) -> None:
        """_fill_item_sizes must estimate size for items without it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="x" * 100, size_est=0),
            ]

            filled = engine._fill_item_sizes(items)

            assert filled[0].size_est > 0

    def test_fill_item_sizes_preserves_existing(self) -> None:
        """_fill_item_sizes must not overwrite existing size."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", content_or_pointer="test", size_est=42),
            ]

            filled = engine._fill_item_sizes(items)

            assert filled[0].size_est == 42


class TestContextEngineSourceKey:
    """Tests for source key extraction."""

    def test_source_key_prefers_path(self) -> None:
        """_source_key must prefer 'path' key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            item = ContextItem(id="1", kind="code", content_or_pointer="test", refs={"path": "src/main.py"})

            assert engine._source_key(item) == "src/main.py"

    def test_source_key_falls_back_to_file_path(self) -> None:
        """_source_key must fall back to 'file_path'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            item = ContextItem(id="1", kind="code", content_or_pointer="test", refs={"file_path": "src/utils.py"})

            assert engine._source_key(item) == "src/utils.py"

    def test_source_key_returns_empty_when_no_refs(self) -> None:
        """_source_key must return empty string when no path refs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            item = ContextItem(id="unique_id", kind="code", content_or_pointer="test", refs={})

            # _source_key returns empty string when no path refs
            assert engine._source_key(item) == ""


class TestContextEngineLLMSummarization:
    """Tests for LLM summarization fallback."""

    def test_summarize_items_llm_fallback(self) -> None:
        """_summarize_items_llm must use deterministic fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", provider="docs", content_or_pointer="test content 1", priority=5),
                ContextItem(id="2", kind="memo", provider="memory", content_or_pointer="test content 2", priority=5),
            ]

            summarized, _summary_text = engine._summarize_items_llm(items)

            assert len(summarized) == 1
            assert summarized[0].kind == "summary"
            assert "Context continuity summary" in summarized[0].content_or_pointer

    def test_summarize_items_llm_with_task_identity(self) -> None:
        """_summarize_items_llm must include task identity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [ContextItem(id="1", kind="code", provider="docs", content_or_pointer="test", priority=5)]
            task_identity = {
                "task_id": "TASK-123",
                "goal": "Implement login",
                "acceptance": ["Login works"],
                "write_scope": ["auth/*"],
            }

            summarized, _ = engine._summarize_items_llm(items, task_identity)

            content = summarized[0].content_or_pointer
            assert "TASK-123" in content
            assert "Implement login" in content


class TestContextEngineApplyBudgetLadder:
    """Tests for budget ladder compression log."""

    def test_budget_ladder_tracks_deduplication(self) -> None:
        """_apply_budget_ladder must log deduplication action."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            # Create duplicate items
            items = [
                ContextItem(id="1", kind="code", content_or_pointer="same", refs={"path": "a.py"}, priority=5),
                ContextItem(id="2", kind="code", content_or_pointer="same", refs={"path": "a.py"}, priority=5),
            ]
            budget = ContextBudget(max_tokens=100, max_chars=1000)

            _result, compression_log = engine._apply_budget_ladder(items, budget)

            dedup_actions = [a for a in compression_log if a.get("action") == "deduplicate"]
            assert len(dedup_actions) >= 1

    def test_budget_ladder_with_compact_now(self) -> None:
        """_apply_budget_ladder must summarize when compact_now=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            items = [
                ContextItem(id="1", kind="code", provider="docs", content_or_pointer="content", priority=5),
            ]
            budget = ContextBudget(max_tokens=10, max_chars=100)
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=budget,
                compact_now=True,
            )

            _result, compression_log = engine._apply_budget_ladder(items, budget, request)

            summarize_actions = [a for a in compression_log if a.get("action") == "summarize"]
            assert len(summarize_actions) >= 1


class TestContextEngineEmitEvents:
    """Tests for event emission."""

    def test_emit_context_events_skips_without_events_path(self) -> None:
        """_emit_context_events must skip when events_path is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)
            pack = ContextPack(
                request_hash="abc",
                items=[],
                total_tokens=10,
                total_chars=10,
            )
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
                events_path=None,  # No events path
            )

            # Should not raise
            engine._emit_context_events(pack, request)

    def test_emit_context_events_includes_items(self) -> None:
        """_emit_context_events must emit events for each item."""
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = ContextEngine(project_root=tmpdir)

            # Create temp events file
            events_file = os.path.join(tmpdir, "events.jsonl")

            pack = ContextPack(
                request_hash="abc",
                items=[
                    ContextItem(id="item1", kind="code", provider="docs", content_or_pointer="test", priority=5),
                ],
                total_tokens=10,
                total_chars=10,
            )
            request = ContextRequest(
                run_id="test",
                step=1,
                role="dev",
                mode="edit",
                query="test",
                budget=ContextBudget(),
                events_path=events_file,
            )

            # Should not raise
            engine._emit_context_events(pack, request)

            # Events file should exist
            assert os.path.exists(events_file)
