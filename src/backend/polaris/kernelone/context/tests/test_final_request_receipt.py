"""Tests for FinalRequestReceipt and related receipt types.

Covers ChunkTokenStats, CompressionDecision, ContinuityDecision,
StrategyMetadata, ContextOSReceipt, and FinalRequestReceipt.
"""

from __future__ import annotations

from polaris.kernelone.context.chunks.receipt import (
    ChunkTokenStats,
    CompressionDecision,
    ContextOSReceipt,
    ContinuityDecision,
    FinalRequestReceipt,
    StrategyMetadata,
)

# ---------------------------------------------------------------------------
# ChunkTokenStats Tests
# ---------------------------------------------------------------------------


class TestChunkTokenStats:
    """Test ChunkTokenStats dataclass."""

    def test_required_fields(self) -> None:
        """ChunkTokenStats should require all fields."""
        stats = ChunkTokenStats(
            chunk_type="system",
            token_count=100,
            char_count=400,
            chunk_count=5,
        )
        assert stats.chunk_type == "system"
        assert stats.token_count == 100
        assert stats.char_count == 400
        assert stats.chunk_count == 5

    def test_to_dict(self) -> None:
        """ChunkTokenStats should serialize to dict."""
        stats = ChunkTokenStats(
            chunk_type="system",
            token_count=100,
            char_count=400,
            chunk_count=5,
        )
        d = stats.to_dict()
        assert d["chunk_type"] == "system"
        assert d["token_count"] == 100
        assert d["char_count"] == 400
        assert d["chunk_count"] == 5

    def test_multiple_chunk_types(self) -> None:
        """Should handle multiple chunk types in breakdown."""
        system_stats = ChunkTokenStats(
            chunk_type="system",
            token_count=50,
            char_count=200,
            chunk_count=1,
        )
        user_stats = ChunkTokenStats(
            chunk_type="user",
            token_count=100,
            char_count=400,
            chunk_count=3,
        )
        assert system_stats.to_dict()["chunk_type"] == "system"
        assert user_stats.to_dict()["chunk_type"] == "user"


# ---------------------------------------------------------------------------
# CompressionDecision Tests
# ---------------------------------------------------------------------------


class TestCompressionDecision:
    """Test CompressionDecision dataclass."""

    def test_eviction_method(self) -> None:
        """CompressionDecision should record eviction decisions."""
        decision = CompressionDecision(
            chunk_type="history_done",
            reason="Budget exceeded",
            tokens_freed=500,
            method="evicted",
        )
        assert decision.chunk_type == "history_done"
        assert decision.tokens_freed == 500
        assert decision.method == "evicted"

    def test_truncated_method(self) -> None:
        """CompressionDecision should handle truncation."""
        decision = CompressionDecision(
            chunk_type="readonly_assets",
            reason="Content too large",
            tokens_freed=200,
            method="truncated",
        )
        assert decision.method == "truncated"

    def test_summarized_method(self) -> None:
        """CompressionDecision should handle summarization."""
        decision = CompressionDecision(
            chunk_type="repo_intelligence",
            reason="LLM summarization",
            tokens_freed=1000,
            method="summarized",
        )
        assert decision.method == "summarized"

    def test_to_dict(self) -> None:
        """CompressionDecision should serialize to dict."""
        decision = CompressionDecision(
            chunk_type="history",
            reason="over_budget",
            tokens_freed=300,
            method="evicted",
        )
        d = decision.to_dict()
        assert d["chunk_type"] == "history"
        assert d["tokens_freed"] == 300
        assert d["method"] == "evicted"


# ---------------------------------------------------------------------------
# ContinuityDecision Tests
# ---------------------------------------------------------------------------


class TestContinuityDecision:
    """Test ContinuityDecision dataclass."""

    def test_enabled_decision(self) -> None:
        """ContinuityDecision should record enabled state."""
        decision = ContinuityDecision(
            enabled=True,
            summary_tokens=500,
            summary_hash="abc123",
            source_messages=10,
        )
        assert decision.enabled is True
        assert decision.summary_tokens == 500
        assert decision.source_messages == 10

    def test_disabled_decision(self) -> None:
        """ContinuityDecision should handle disabled state."""
        decision = ContinuityDecision(
            enabled=False,
            summary_tokens=0,
            summary_hash="",
            source_messages=0,
        )
        assert decision.enabled is False
        assert decision.summary_tokens == 0

    def test_to_dict(self) -> None:
        """ContinuityDecision should serialize to dict."""
        decision = ContinuityDecision(
            enabled=True,
            summary_tokens=500,
            summary_hash="hash123",
            source_messages=10,
        )
        d = decision.to_dict()
        assert d["enabled"] is True
        assert d["summary_tokens"] == 500
        assert d["source_messages"] == 10


# ---------------------------------------------------------------------------
# StrategyMetadata Tests
# ---------------------------------------------------------------------------


class TestStrategyMetadata:
    """Test StrategyMetadata dataclass."""

    def test_required_fields(self) -> None:
        """StrategyMetadata should require identity fields."""
        metadata = StrategyMetadata(
            profile_id="canonical_balanced",
            profile_hash="hash123",
            strategy_bundle_hash="bundle_hash",
            continuity_policy_id="default",
            compaction_policy_id="standard",
        )
        assert metadata.profile_id == "canonical_balanced"
        assert metadata.continuity_policy_id == "default"

    def test_with_domain(self) -> None:
        """StrategyMetadata should accept optional domain."""
        metadata = StrategyMetadata(
            profile_id="test",
            profile_hash="hash",
            strategy_bundle_hash="bundle",
            continuity_policy_id="cont",
            compaction_policy_id="comp",
            domain="code",
        )
        assert metadata.domain == "code"

    def test_to_dict(self) -> None:
        """StrategyMetadata should serialize to dict."""
        metadata = StrategyMetadata(
            profile_id="test",
            profile_hash="abc",
            strategy_bundle_hash="def",
            continuity_policy_id="cont",
            compaction_policy_id="comp",
        )
        d = metadata.to_dict()
        assert d["profile_id"] == "test"
        assert d["profile_hash"] == "abc"


# ---------------------------------------------------------------------------
# ContextOSReceipt Tests
# ---------------------------------------------------------------------------


class TestContextOSReceipt:
    """Test ContextOSReceipt dataclass."""

    def test_required_fields(self) -> None:
        """ContextOSReceipt should have all fields."""
        receipt = ContextOSReceipt(
            adapter_id="generic",
            current_goal="Implement feature X",
            next_action_hint="Write tests",
            pressure_level="normal",
            hard_constraint_count=5,
            open_loop_count=2,
            active_entity_count=10,
            active_artifact_count=3,
            episode_count=1,
            included_count=100,
            excluded_count=20,
        )
        assert receipt.adapter_id == "generic"
        assert receipt.current_goal == "Implement feature X"
        assert receipt.pressure_level == "normal"
        assert receipt.hard_constraint_count == 5

    def test_to_dict(self) -> None:
        """ContextOSReceipt should serialize to dict."""
        receipt = ContextOSReceipt(
            adapter_id="code",
            current_goal="Fix bug",
            next_action_hint="Add test",
            pressure_level="low",
            hard_constraint_count=3,
            open_loop_count=1,
            active_entity_count=5,
            active_artifact_count=2,
            episode_count=1,
            included_count=50,
            excluded_count=10,
        )
        d = receipt.to_dict()
        assert d["adapter_id"] == "code"
        assert d["current_goal"] == "Fix bug"
        assert d["pressure_level"] == "low"
        assert "hard_constraint_count" in d
        assert "open_loop_count" in d


# ---------------------------------------------------------------------------
# FinalRequestReceipt Tests
# ---------------------------------------------------------------------------


class TestFinalRequestReceipt:
    """Test FinalRequestReceipt dataclass and build method."""

    def test_required_fields(self) -> None:
        """FinalRequestReceipt should require identity and model fields."""
        receipt = FinalRequestReceipt(
            receipt_id="test_receipt",
            timestamp="2026-04-24T00:00:00Z",
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200000,
            effective_limit=180000,
            total_tokens=5000,
            total_chars=20000,
            chunk_count=10,
            token_breakdown=(),
            eviction_summary=(),
            continuity=None,
            context_os=None,
            strategy=None,
            assembly_start="2026-04-24T00:00:00Z",
            assembly_duration_ms=100,
            role_id="developer",
            session_id="session_001",
            turn_index=1,
        )
        assert receipt.receipt_id == "test_receipt"
        assert receipt.model == "claude-opus-4-5"
        assert receipt.total_tokens == 5000

    def test_build_basic(self) -> None:
        """FinalRequestReceipt.build should create a valid receipt."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="You are a helpful assistant.",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="role_profile",
                    estimated_tokens=50,
                    char_count=200,
                ),
            ),
            PromptChunk(
                chunk_type=ChunkType.CURRENT_TURN,
                content="Hello, how are you?",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.CURRENT_TURN,
                    source="user_input",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200000,
            safety_margin=0.9,
            role_id="developer",
            session_id="session_001",
            turn_index=1,
        )

        assert receipt.receipt_id is not None
        assert len(receipt.receipt_id) == 16
        assert receipt.model == "claude-opus-4-5"
        assert receipt.provider == "anthropic"
        assert receipt.model_window == 200000
        assert receipt.effective_limit == 180000  # 200000 * 0.9
        assert receipt.total_tokens == 60  # 50 + 10
        assert receipt.total_chars == 240  # 200 + 40
        assert receipt.chunk_count == 2

    def test_build_with_breakdown(self) -> None:
        """FinalRequestReceipt.build should create token breakdown."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="System prompt",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="role",
                    estimated_tokens=20,
                    char_count=80,
                ),
            ),
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="Another system message",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="rules",
                    estimated_tokens=15,
                    char_count=60,
                ),
            ),
            PromptChunk(
                chunk_type=ChunkType.CURRENT_TURN,
                content="User message",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.CURRENT_TURN,
                    source="input",
                    estimated_tokens=5,
                    char_count=20,
                ),
            ),
        ]

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="claude-sonnet",
            provider="anthropic",
            model_window=100000,
            safety_margin=0.95,
        )

        # Should have breakdown by chunk type
        breakdown_map = {s.chunk_type: s for s in receipt.token_breakdown}
        assert "system" in breakdown_map
        assert "current_turn" in breakdown_map
        assert breakdown_map["system"].token_count == 35  # 20 + 15
        assert breakdown_map["current_turn"].token_count == 5

    def test_build_with_eviction_decisions(self) -> None:
        """FinalRequestReceipt.build should record eviction decisions."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.REPO_INTELLIGENCE,
                content="Repo info",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.REPO_INTELLIGENCE,
                    source="repo_map",
                    estimated_tokens=100,
                    char_count=400,
                ),
            ),
        ]

        eviction_decisions = [
            CompressionDecision(
                chunk_type="history_done",
                reason="Budget exceeded",
                tokens_freed=500,
                method="evicted",
            ),
        ]

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="test",
            provider="test",
            model_window=50000,
            safety_margin=0.9,
            eviction_decisions=eviction_decisions,
        )

        assert len(receipt.eviction_summary) == 1
        assert receipt.eviction_summary[0].tokens_freed == 500

    def test_build_with_continuity(self) -> None:
        """FinalRequestReceipt.build should include continuity decision."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="System",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="role",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        continuity = ContinuityDecision(
            enabled=True,
            summary_tokens=100,
            summary_hash="summary_hash_123",
            source_messages=5,
        )

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="test",
            provider="test",
            model_window=100000,
            safety_margin=0.9,
            continuity=continuity,
        )

        assert receipt.continuity is not None
        assert receipt.continuity.enabled is True
        assert receipt.continuity.summary_tokens == 100

    def test_build_with_context_os(self) -> None:
        """FinalRequestReceipt.build should include ContextOS receipt."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="System",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="role",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        context_os = ContextOSReceipt(
            adapter_id="generic",
            current_goal="Task X",
            pressure_level="normal",
            hard_constraint_count=3,
            open_loop_count=1,
        )

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="test",
            provider="test",
            model_window=100000,
            safety_margin=0.9,
            context_os=context_os,
        )

        assert receipt.context_os is not None
        assert receipt.context_os.adapter_id == "generic"
        assert receipt.context_os.current_goal == "Task X"

    def test_build_with_strategy(self) -> None:
        """FinalRequestReceipt.build should include strategy metadata."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="System",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="role",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        strategy = StrategyMetadata(
            profile_id="canonical_balanced",
            profile_hash="abc123",
            strategy_bundle_hash="bundle_xyz",
            continuity_policy_id="default",
            compaction_policy_id="standard",
        )

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="test",
            provider="test",
            model_window=100000,
            safety_margin=0.9,
            strategy=strategy,
        )

        assert receipt.strategy is not None
        assert receipt.strategy.profile_id == "canonical_balanced"

    def test_build_with_cache_control(self) -> None:
        """FinalRequestReceipt.build should track cache control applied."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="System",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="role",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="test",
            provider="test",
            model_window=100000,
            safety_margin=0.9,
            cache_control_applied=["system", "continuity"],
        )

        assert "system" in receipt.cache_control_applied
        assert "continuity" in receipt.cache_control_applied

    def test_build_empty_chunks(self) -> None:
        """FinalRequestReceipt.build should handle empty chunks."""
        receipt = FinalRequestReceipt.build(
            chunks=[],
            model="test",
            provider="test",
            model_window=100000,
            safety_margin=0.9,
        )

        assert receipt.total_tokens == 0
        assert receipt.total_chars == 0
        assert receipt.chunk_count == 0
        assert len(receipt.token_breakdown) == 0

    def test_to_dict(self) -> None:
        """FinalRequestReceipt.to_dict should serialize correctly."""
        receipt = FinalRequestReceipt(
            receipt_id="test_123",
            timestamp="2026-04-24T00:00:00Z",
            model="claude-opus",
            provider="anthropic",
            model_window=200000,
            effective_limit=180000,
            total_tokens=1000,
            total_chars=4000,
            chunk_count=5,
            token_breakdown=(
                ChunkTokenStats(
                    chunk_type="system",
                    token_count=100,
                    char_count=400,
                    chunk_count=1,
                ),
            ),
            eviction_summary=(),
            continuity=None,
            context_os=None,
            strategy=None,
            assembly_start="2026-04-24T00:00:00Z",
            assembly_duration_ms=50,
            role_id="dev",
            session_id="sess_001",
            turn_index=1,
        )

        d = receipt.to_dict()

        assert d["receipt_id"] == "test_123"
        assert d["model"]["model"] == "claude-opus"
        assert d["model"]["provider"] == "anthropic"
        assert d["content"]["total_tokens"] == 1000
        assert d["provenance"]["role_id"] == "dev"
        assert d["provenance"]["session_id"] == "sess_001"

    def test_to_human_readable(self) -> None:
        """FinalRequestReceipt.to_human_readable should format correctly."""
        receipt = FinalRequestReceipt(
            receipt_id="test_abc",
            timestamp="2026-04-24T00:00:00Z",
            model="claude-opus-4-5",
            provider="anthropic",
            model_window=200000,
            effective_limit=180000,
            total_tokens=5000,
            total_chars=20000,
            chunk_count=10,
            token_breakdown=(
                ChunkTokenStats(
                    chunk_type="system",
                    token_count=1000,
                    char_count=4000,
                    chunk_count=2,
                ),
                ChunkTokenStats(
                    chunk_type="current_turn",
                    token_count=100,
                    char_count=400,
                    chunk_count=1,
                ),
            ),
            eviction_summary=(),
            continuity=ContinuityDecision(
                enabled=True,
                summary_tokens=500,
                summary_hash="hash123",
                source_messages=5,
            ),
            context_os=ContextOSReceipt(
                adapter_id="generic",
                current_goal="Test task",
                next_action_hint="Continue coding",
                pressure_level="normal",
                hard_constraint_count=3,
                open_loop_count=1,
                active_entity_count=5,
                active_artifact_count=2,
                episode_count=1,
                included_count=100,
                excluded_count=10,
            ),
            strategy=StrategyMetadata(
                profile_id="canonical_balanced",
                profile_hash="abc123def456",
                strategy_bundle_hash="bundle_xyz789",
                continuity_policy_id="default",
                compaction_policy_id="standard",
            ),
            assembly_start="2026-04-24T00:00:00Z",
            assembly_duration_ms=150,
            role_id="developer",
            session_id="session_001",
            turn_index=5,
        )

        output = receipt.to_human_readable()

        # Should contain key information
        assert "FINAL REQUEST RECEIPT" in output
        assert "test_abc" in output
        assert "claude-opus-4-5" in output
        assert "anthropic" in output
        assert "5,000" in output  # total tokens with comma
        assert "system" in output
        assert "canonical_balanced" in output
        assert "Test task" in output
        assert "150ms" in output or "150" in output

    def test_to_human_readable_empty_breakdown(self) -> None:
        """to_human_readable should handle empty token breakdown."""
        receipt = FinalRequestReceipt(
            receipt_id="empty_test",
            timestamp="2026-04-24T00:00:00Z",
            model="test",
            provider="test",
            model_window=100000,
            effective_limit=90000,
            total_tokens=0,
            total_chars=0,
            chunk_count=0,
            token_breakdown=(),
            eviction_summary=(),
            continuity=None,
            context_os=None,
            strategy=None,
            assembly_start="2026-04-24T00:00:00Z",
            assembly_duration_ms=0,
            role_id="test",
            session_id="test",
            turn_index=0,
        )

        output = receipt.to_human_readable()
        assert "FINAL REQUEST RECEIPT" in output
        assert "0 tokens" in output

    def test_receipt_id_format(self) -> None:
        """Receipt ID should be a valid SHA256 hash truncated to 16 characters."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="Same content",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="test",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        receipt = FinalRequestReceipt.build(
            chunks=chunks,
            model="test_model",
            provider="test_provider",
            model_window=100000,
            safety_margin=0.9,
        )

        # Receipt ID should be 16 characters (SHA256 truncated)
        assert len(receipt.receipt_id) == 16
        # Should be valid hex
        assert all(c in "0123456789abcdef" for c in receipt.receipt_id)

    def test_different_inputs_produce_different_receipt_ids(self) -> None:
        """Different inputs should produce different receipt IDs."""
        from polaris.kernelone.context.chunks.taxonomy import ChunkMetadata, ChunkType, PromptChunk

        chunks1 = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="Content A",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="test",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]
        chunks2 = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM,
                content="Content B",
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="test",
                    estimated_tokens=10,
                    char_count=40,
                ),
            ),
        ]

        receipt1 = FinalRequestReceipt.build(
            chunks=chunks1,
            model="test_model",
            provider="test_provider",
            model_window=100000,
            safety_margin=0.9,
        )

        receipt2 = FinalRequestReceipt.build(
            chunks=chunks2,
            model="test_model",
            provider="test_provider",
            model_window=100000,
            safety_margin=0.9,
        )

        # Different content should produce different receipt IDs
        assert receipt1.receipt_id != receipt2.receipt_id
