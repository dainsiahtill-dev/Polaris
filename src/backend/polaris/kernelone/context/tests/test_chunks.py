"""Tests for polaris.kernelone.context.chunks."""

from __future__ import annotations

import pytest
from polaris.kernelone.context.chunks import (
    AssemblyContext,
    CacheControl,
    ChunkBudget,
    ChunkBudgetTracker,
    ChunkMetadata,
    ChunkType,
    ContextOSReceipt,
    ContinuityDecision,
    FinalRequestReceipt,
    PromptChunk,
    PromptChunkAssembler,
    StrategyMetadata,
)


class TestChunkType:
    """Tests for ChunkType enum."""

    def test_all_chunk_types_defined(self) -> None:
        """All expected chunk types are defined."""
        expected = {
            "system",
            "examples",
            "continuity",
            "history_done",
            "repo_intelligence",
            "readonly_assets",
            "working_set",
            "current_turn",
            "reminder",
        }
        actual = {ct.value for ct in ChunkType}
        assert expected.issubset(actual)

    def test_eviction_priority_order(self) -> None:
        """System has highest priority (lowest number), repo_intelligence lowest."""
        assert ChunkType.SYSTEM.eviction_priority < ChunkType.CONTINUITY.eviction_priority
        assert ChunkType.CONTINUITY.eviction_priority < ChunkType.HISTORY_DONE.eviction_priority
        assert ChunkType.HISTORY_DONE.eviction_priority < ChunkType.EXAMPLES.eviction_priority
        assert ChunkType.EXAMPLES.eviction_priority < ChunkType.REPO_INTELLIGENCE.eviction_priority

    def test_cacheable_types(self) -> None:
        """Only specific types are cacheable."""
        assert ChunkType.SYSTEM.cacheable is True
        assert ChunkType.EXAMPLES.cacheable is True
        assert ChunkType.REPO_INTELLIGENCE.cacheable is True
        assert ChunkType.READONLY_ASSETS.cacheable is True
        assert ChunkType.CONTINUITY.cacheable is True
        assert ChunkType.CURRENT_TURN.cacheable is False

    def test_tier_order(self) -> None:
        """tier_order returns all types in eviction-safe order."""
        order = ChunkType.tier_order()
        assert len(order) == len(ChunkType)
        # System should be first
        assert order[0] == ChunkType.SYSTEM
        # Current turn should be second
        assert order[1] == ChunkType.CURRENT_TURN
        # Repo intelligence should be near the end
        assert order[-2] == ChunkType.REPO_INTELLIGENCE
        assert order[-1] == ChunkType.READONLY_ASSETS


class TestChunkMetadata:
    """Tests for ChunkMetadata."""

    def test_create_with_defaults(self) -> None:
        """Can create metadata with minimal required fields."""
        meta = ChunkMetadata(
            chunk_type=ChunkType.SYSTEM,
            source="role_profile",
        )
        assert meta.chunk_type == ChunkType.SYSTEM
        assert meta.source == "role_profile"
        assert meta.cache_control == CacheControl.EPHEMERAL
        assert meta.content_hash == ""
        assert meta.created_at == 0.0

    def test_to_dict(self) -> None:
        """Metadata serializes to dict correctly."""
        meta = ChunkMetadata(
            chunk_type=ChunkType.SYSTEM,
            source="test",
            cache_control=CacheControl.PERSISTENT,
            char_count=100,
            estimated_tokens=25,
        )
        d = meta.to_dict()
        assert d["chunk_type"] == "system"
        assert d["source"] == "test"
        assert d["cache_control"] == "persistent"
        assert d["char_count"] == 100
        assert d["estimated_tokens"] == 25


class TestPromptChunk:
    """Tests for PromptChunk."""

    def test_create_chunk(self) -> None:
        """Can create a basic chunk."""
        meta = ChunkMetadata(
            chunk_type=ChunkType.SYSTEM,
            source="test",
        )
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="You are Polaris.",
            metadata=meta,
        )
        assert chunk.chunk_type == ChunkType.SYSTEM
        assert chunk.content == "You are Polaris."
        assert chunk.tokens > 0
        assert chunk.chars == len("You are Polaris.")

    def test_auto_token_estimation(self) -> None:
        """Chunk auto-estimates tokens if not provided."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test")
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="x" * 400,
            metadata=meta,
        )
        # 400 chars / 4 = ~100 tokens
        assert chunk.tokens >= 99 and chunk.tokens <= 101

    def test_to_message(self) -> None:
        """Chunk converts to chat message format."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test")
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="You are Polaris.",
            metadata=meta,
        )
        msg = chunk.to_message()
        assert msg["role"] == "system"
        assert msg["content"] == "You are Polaris."


class TestChunkBudget:
    """Tests for ChunkBudget (frozen dataclass)."""

    def test_effective_limit(self) -> None:
        """effective_limit applies safety margin."""
        budget = ChunkBudget(
            total_tokens=0,
            admitted_chunks=0,
            evicted_chunks=0,
            model_window=100_000,
            safety_margin=0.80,
        )
        assert budget.effective_limit == 80_000

    def test_usage_ratio(self) -> None:
        """usage_ratio computes correctly."""
        budget = ChunkBudget(
            total_tokens=40_000,
            admitted_chunks=5,
            evicted_chunks=0,
            model_window=100_000,
            safety_margin=0.80,
        )
        assert budget.usage_ratio == pytest.approx(0.5)

    def test_headroom(self) -> None:
        """headroom is effective_limit minus used."""
        budget = ChunkBudget(
            total_tokens=30_000,
            admitted_chunks=5,
            evicted_chunks=0,
            model_window=100_000,
            safety_margin=0.80,
        )
        assert budget.headroom == 50_000

    def test_to_dict(self) -> None:
        """Budget serializes to dict."""
        budget = ChunkBudget(
            total_tokens=50_000,
            admitted_chunks=5,
            evicted_chunks=1,
            model_window=128_000,
            safety_margin=0.85,
        )
        d = budget.to_dict()
        assert d["total_tokens"] == 50_000
        assert d["admitted_chunks"] == 5
        assert d["evicted_chunks"] == 1
        assert d["effective_limit"] == 108_800  # 128k * 0.85


class TestChunkBudgetTracker:
    """Tests for ChunkBudgetTracker."""

    def test_construct_defaults(self) -> None:
        """Can construct with defaults."""
        tracker = ChunkBudgetTracker(model_window=128_000)
        assert tracker.model_window == 128_000
        assert tracker.safety_margin == 0.85
        budget = tracker.get_current_budget()
        assert budget.total_tokens == 0
        assert budget.admitted_chunks == 0

    def test_construct_invalid_window(self) -> None:
        """Rejects invalid model_window."""
        with pytest.raises(ValueError, match="positive int"):
            ChunkBudgetTracker(model_window=0)
        with pytest.raises(ValueError, match="positive int"):
            ChunkBudgetTracker(model_window=-1)

    def test_construct_invalid_margin(self) -> None:
        """Rejects invalid safety_margin."""
        with pytest.raises(ValueError, match="safety_margin"):
            ChunkBudgetTracker(model_window=128_000, safety_margin=0.0)
        with pytest.raises(ValueError, match="safety_margin"):
            ChunkBudgetTracker(model_window=128_000, safety_margin=1.5)

    def test_try_admit_single_chunk(self) -> None:
        """Can admit a single chunk."""
        tracker = ChunkBudgetTracker(model_window=128_000, safety_margin=0.80)
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=1000)
        chunk = PromptChunk(chunk_type=ChunkType.SYSTEM, content="x" * 4000, metadata=meta)

        ok, reason = tracker.try_admit(chunk)
        assert ok is True
        assert reason == ""
        assert tracker.get_current_budget().total_tokens > 0

    def test_try_admit_exceeds_budget(self) -> None:
        """Evicts chunk when budget exceeded."""
        # 128k * 0.85 = 108.8k effective limit
        tracker = ChunkBudgetTracker(model_window=128_000, safety_margin=0.85, initial_tokens=100_000)
        meta = ChunkMetadata(chunk_type=ChunkType.REPO_INTELLIGENCE, source="test", estimated_tokens=50_000)
        chunk = PromptChunk(chunk_type=ChunkType.REPO_INTELLIGENCE, content="x" * 200_000, metadata=meta)

        ok, reason = tracker.try_admit(chunk)
        assert ok is False
        assert "exceeds" in reason.lower() or "budget" in reason.lower()

    def test_try_admit_many(self) -> None:
        """Can admit multiple chunks with eviction."""
        tracker = ChunkBudgetTracker(model_window=128_000, safety_margin=0.50)

        # Create chunks
        system_meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=500)
        system_chunk = PromptChunk(ChunkType.SYSTEM, "System prompt", system_meta)

        repo_meta = ChunkMetadata(chunk_type=ChunkType.REPO_INTELLIGENCE, source="test", estimated_tokens=5000)
        repo_chunk = PromptChunk(ChunkType.REPO_INTELLIGENCE, "Repo info", repo_meta)

        current_meta = ChunkMetadata(chunk_type=ChunkType.CURRENT_TURN, source="test", estimated_tokens=100)
        current_chunk = PromptChunk(ChunkType.CURRENT_TURN, "Current turn", current_meta)

        # 128k * 0.5 = 64k limit, so 500 + 5000 + 100 = 5600 should fit
        admitted, evicted = tracker.try_admit_many([system_chunk, repo_chunk, current_chunk])

        assert len(admitted) == 3
        assert len(evicted) == 0

    def test_eviction_log(self) -> None:
        """Tracker records eviction decisions."""
        tracker = ChunkBudgetTracker(model_window=1000, safety_margin=0.50)

        # 1000 * 0.5 = 500 limit
        meta = ChunkMetadata(chunk_type=ChunkType.REPO_INTELLIGENCE, source="test", estimated_tokens=600)
        chunk = PromptChunk(ChunkType.REPO_INTELLIGENCE, "x" * 2400, meta)

        tracker.try_admit(chunk)
        assert len(tracker.get_eviction_log()) == 1

    def test_token_breakdown(self) -> None:
        """Tracker computes per-type breakdown."""
        tracker = ChunkBudgetTracker(model_window=128_000, safety_margin=0.85)

        meta1 = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk1 = PromptChunk(ChunkType.SYSTEM, "System", meta1)

        meta2 = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=200)
        chunk2 = PromptChunk(ChunkType.SYSTEM, "System 2", meta2)

        tracker.try_admit(chunk1)
        tracker.try_admit(chunk2)

        breakdown = tracker.get_token_breakdown()
        assert breakdown.get("system") == 300

    def test_reset(self) -> None:
        """reset clears all state."""
        tracker = ChunkBudgetTracker(model_window=128_000, initial_tokens=50_000)
        tracker.reset()
        budget = tracker.get_current_budget()
        assert budget.total_tokens == 0


class TestFinalRequestReceipt:
    """Tests for FinalRequestReceipt."""

    def test_build_minimal_receipt(self) -> None:
        """Can build a receipt with minimal fields."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk = PromptChunk(ChunkType.SYSTEM, "System", meta)

        receipt = FinalRequestReceipt.build(
            chunks=[chunk],
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200_000,
            safety_margin=0.85,
        )

        assert receipt.receipt_id
        assert receipt.timestamp
        assert receipt.model == "claude-opus-4-5"
        assert receipt.provider == "anthropic"
        assert receipt.total_tokens > 0
        assert len(receipt.token_breakdown) == 1

    def test_to_dict(self) -> None:
        """Receipt serializes to dict."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk = PromptChunk(ChunkType.SYSTEM, "System", meta)

        receipt = FinalRequestReceipt.build(
            chunks=[chunk],
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200_000,
            safety_margin=0.85,
        )

        d = receipt.to_dict()
        assert "receipt_id" in d
        assert "model" in d
        assert "content" in d
        assert "token_breakdown" in d

    def test_to_human_readable(self) -> None:
        """Receipt formats as human-readable text."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk = PromptChunk(ChunkType.SYSTEM, "System", meta)

        receipt = FinalRequestReceipt.build(
            chunks=[chunk],
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200_000,
            safety_margin=0.85,
        )

        text = receipt.to_human_readable()
        assert "FINAL REQUEST RECEIPT" in text
        assert "claude-opus-4-5" in text
        assert "anthropic" in text
        assert "Token Breakdown" in text

    def test_with_continuity(self) -> None:
        """Receipt includes continuity decision when provided."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk = PromptChunk(ChunkType.SYSTEM, "System", meta)

        continuity = ContinuityDecision(
            enabled=True,
            summary_tokens=50,
            summary_hash="abc123",
            source_messages=20,
        )

        receipt = FinalRequestReceipt.build(
            chunks=[chunk],
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200_000,
            safety_margin=0.85,
            continuity=continuity,
        )

        assert receipt.continuity is not None
        assert receipt.continuity.enabled is True
        assert receipt.continuity.source_messages == 20

    def test_with_strategy(self) -> None:
        """Receipt includes strategy metadata when provided."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk = PromptChunk(ChunkType.SYSTEM, "System", meta)

        strategy = StrategyMetadata(
            profile_id="canonical_balanced",
            profile_hash="hash123",
            strategy_bundle_hash="bundle456",
            continuity_policy_id="default",
            compaction_policy_id="default",
        )

        receipt = FinalRequestReceipt.build(
            chunks=[chunk],
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200_000,
            safety_margin=0.85,
            strategy=strategy,
        )

        assert receipt.strategy is not None
        assert receipt.strategy.profile_id == "canonical_balanced"

    def test_with_context_os(self) -> None:
        """Receipt includes Context OS projection summary when provided."""
        meta = ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test", estimated_tokens=100)
        chunk = PromptChunk(ChunkType.SYSTEM, "System", meta)

        context_os = ContextOSReceipt(
            adapter_id="code",
            current_goal="stabilize context runtime",
            next_action_hint="update receipt pipeline",
            pressure_level="soft",
            hard_constraint_count=2,
            open_loop_count=3,
            active_entity_count=4,
            active_artifact_count=1,
            episode_count=2,
            included_count=5,
            excluded_count=7,
        )

        receipt = FinalRequestReceipt.build(
            chunks=[chunk],
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200_000,
            safety_margin=0.85,
            context_os=context_os,
        )

        assert receipt.context_os is not None
        assert receipt.context_os.adapter_id == "code"
        assert receipt.to_dict()["context_os"]["pressure_level"] == "soft"


class TestPromptChunkAssembler:
    """Tests for PromptChunkAssembler."""

    def test_construct(self) -> None:
        """Can construct assembler."""
        assembler = PromptChunkAssembler(model_window=128_000)
        assert assembler._model_window == 128_000
        assert len(assembler.chunks) == 0

    def test_add_chunk(self) -> None:
        """Can add chunks to assembler."""
        assembler = PromptChunkAssembler(model_window=128_000)
        chunk = assembler.add_chunk(
            ChunkType.SYSTEM,
            "You are Polaris.",
            source="role_profile",
        )
        assert chunk.chunk_type == ChunkType.SYSTEM
        assert len(assembler.chunks) == 1

    def test_add_continuity(self) -> None:
        """Can add continuity summary."""
        assembler = PromptChunkAssembler(model_window=128_000)
        chunk = assembler.add_continuity(
            "Previous context summary...",
            source_messages=10,
        )
        assert chunk.chunk_type == ChunkType.CONTINUITY
        assert chunk.metadata.source == "session_continuity"

    def test_add_continuity_with_state_first_context_os(self) -> None:
        """Continuity chunk can render State-First Context OS working memory."""
        assembler = PromptChunkAssembler(model_window=128_000)
        chunk = assembler.add_continuity(
            "Previous context summary...",
            source_messages=10,
            context_os={
                "head_anchor": "Current goal: stabilize context runtime",
                "tail_anchor": "Last event: user -> continue",
                "active_entities": ["polaris/kernelone/context/session_continuity.py"],
                "artifact_stubs": [
                    {
                        "artifact_id": "art_1",
                        "type": "code_block",
                        "peek": "Session continuity traceback",
                    }
                ],
                "run_card": {
                    "current_goal": "stabilize context runtime",
                    "hard_constraints": ["do not replace context.engine"],
                    "next_action_hint": "wire receipt observability",
                },
                "context_slice_plan": {
                    "pressure_level": "soft",
                    "included": [{"type": "state", "ref": "task_state.current_goal", "reason": "root"}],
                    "excluded": [{"type": "event", "ref": "evt_old", "reason": "low_signal"}],
                },
            },
        )

        assert "State-First Context OS" in chunk.content
        assert "stabilize context runtime" in chunk.content
        assert "art_1<code_block>" in chunk.content
        assert "Pressure level: soft" in chunk.content

    def test_add_continuity_renders_run_card_current_goal_without_head_anchor(self) -> None:
        """Run-card current_goal must render even when legacy head_anchor is absent."""
        assembler = PromptChunkAssembler(model_window=128_000)
        chunk = assembler.add_continuity(
            "Previous context summary...",
            source_messages=10,
            context_os={
                "run_card": {
                    "current_goal": "stabilize context runtime",
                    "hard_constraints": ["do not replace context.engine"],
                    "open_loops": ["finish rollout"],
                    "active_entities": ["polaris/kernelone/context/session_continuity.py"],
                    "active_artifacts": ["art_1"],
                    "next_action_hint": "wire receipt observability",
                },
                "context_slice_plan": {
                    "pressure_level": "soft",
                },
            },
        )

        assert "Current goal: stabilize context runtime" in chunk.content
        assert "Active entities: polaris/kernelone/context/session_continuity.py" in chunk.content
        assert "art_1<active_artifact>" in chunk.content

    def test_assemble_basic(self) -> None:
        """Can assemble chunks into final messages."""
        assembler = PromptChunkAssembler(model_window=128_000)

        assembler.add_chunk(ChunkType.SYSTEM, "You are Polaris.", source="test")
        assembler.add_chunk(ChunkType.CURRENT_TURN, "Hello world.", source="test")

        context = AssemblyContext(
            role_id="director",
            session_id="sess_123",
            model="claude-opus-4-5",
            provider="anthropic",
        )

        result = assembler.assemble(context)

        assert len(result.messages) == 2
        assert result.receipt is not None
        assert result.total_tokens > 0
        assert result.usage_ratio > 0

    def test_assemble_with_strategy(self) -> None:
        """Assembly includes strategy metadata in receipt."""
        assembler = PromptChunkAssembler(model_window=128_000)

        assembler.add_chunk(ChunkType.SYSTEM, "You are Polaris.", source="test")

        context = AssemblyContext(
            role_id="director",
            session_id="sess_123",
            model="claude-opus-4-5",
            provider="anthropic",
            profile_id="canonical_balanced",
            profile_hash="abc123",
            domain="code",
        )

        result = assembler.assemble(context)

        assert result.receipt.strategy is not None
        assert result.receipt.strategy.profile_id == "canonical_balanced"
        assert result.receipt.strategy.domain == "code"

    def test_assemble_with_eviction(self) -> None:
        """Assembly evicts low-priority chunks on budget overflow."""
        # Very tight budget to force eviction
        assembler = PromptChunkAssembler(model_window=1000, safety_margin=0.10)

        # System (high priority)
        assembler.add_chunk(ChunkType.SYSTEM, "System prompt", source="test")

        # Repo intelligence (low priority)
        assembler.add_chunk(ChunkType.REPO_INTELLIGENCE, "x" * 500, source="test")

        context = AssemblyContext(
            role_id="director",
            session_id="sess_123",
            model="test",
            provider="test",
        )

        result = assembler.assemble(context)

        # System should be admitted, repo might be evicted
        assert result.total_tokens > 0

    def test_assemble_receipt_human_readable(self) -> None:
        """Assembly receipt is human-readable."""
        assembler = PromptChunkAssembler(model_window=128_000)

        assembler.add_chunk(ChunkType.SYSTEM, "You are Polaris.", source="test")
        assembler.add_continuity(
            "Previous context summary...",
            source_messages=8,
            context_os={
                "adapter_id": "code",
                "run_card": {
                    "current_goal": "finish context os",
                    "next_action_hint": "emit final receipt",
                    "hard_constraints": ["do not replace context.engine"],
                    "open_loops": ["finish context os"],
                },
                "active_entities": ["polaris/kernelone/context/context_os/runtime.py"],
                "active_artifacts": ["art_1"],
                "episode_cards": [{"episode_id": "ep_1", "digest_64": "finished phase 1"}],
                "context_slice_plan": {
                    "pressure_level": "soft",
                    "included": [{"type": "state", "ref": "task_state.current_goal", "reason": "root"}],
                    "excluded": [{"type": "event", "ref": "evt_old", "reason": "low_signal"}],
                },
            },
        )

        context = AssemblyContext(
            role_id="director",
            session_id="sess_123",
            model="claude-opus-4-5",
            provider="anthropic",
        )

        result = assembler.assemble(context)

        text = result.receipt.to_human_readable()
        assert "FINAL REQUEST RECEIPT" in text
        assert "Model:" in text
        assert "Content Stats" in text
        assert "Token Breakdown" in text
        assert "Context OS" in text
        assert "finish context os" in text

    def test_reset(self) -> None:
        """reset clears assembler state."""
        assembler = PromptChunkAssembler(model_window=128_000)
        assembler.add_chunk(ChunkType.SYSTEM, "Test", source="test")

        assembler.reset()

        assert len(assembler.chunks) == 0


class TestIntegration:
    """Integration tests for chunks subsystem."""

    def test_full_assembly_pipeline(self) -> None:
        """Complete pipeline from chunks to receipt."""
        # Create assembler
        assembler = PromptChunkAssembler(model_window=128_000, safety_margin=0.85)

        # Add system prompt
        assembler.add_chunk(
            ChunkType.SYSTEM,
            "You are Polaris, an AI coding assistant.",
            source="role_profile",
        )

        # Add continuity
        assembler.add_continuity(
            "Previous session: user was implementing feature X.",
            source_messages=15,
        )

        # Add history
        assembler.add_chunk(
            ChunkType.HISTORY_DONE,
            "User: Implement login\nAssistant: Done!",
            source="history",
        )

        # Add current turn
        assembler.add_chunk(
            ChunkType.CURRENT_TURN,
            "Add logout functionality.",
            source="user_input",
        )

        # Assemble
        context = AssemblyContext(
            role_id="director",
            session_id="sess_456",
            turn_index=5,
            model="claude-opus-4-5",
            provider="anthropic",
            profile_id="canonical_balanced",
            profile_hash="def456",
            strategy_bundle_hash="bundle789",
            continuity_enabled=True,
            continuity_summary="Session summary",
            continuity_summary_hash="hash999",
            continuity_source_messages=15,
        )

        result = assembler.assemble(context)

        # Verify messages
        assert len(result.messages) == 4
        roles = [m["role"] for m in result.messages]
        assert "system" in roles

        # Verify receipt
        assert result.receipt is not None
        assert result.receipt.role_id == "director"
        assert result.receipt.session_id == "sess_456"
        assert result.receipt.turn_index == 5
        assert result.receipt.continuity is not None
        assert result.receipt.strategy is not None

        # Verify token breakdown
        breakdown = {s.chunk_type: s for s in result.receipt.token_breakdown}
        assert "system" in breakdown
        assert "continuity" in breakdown
        assert "history_done" in breakdown
        assert "current_turn" in breakdown

        # Verify usage ratio
        assert 0 < result.usage_ratio < 1.0

        # Verify JSON serialization
        json_repr = result.receipt.to_dict()
        assert "receipt_id" in json_repr
        assert json_repr["provenance"]["role_id"] == "director"
