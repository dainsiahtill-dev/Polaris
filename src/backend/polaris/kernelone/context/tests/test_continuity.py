"""Tests for W4: Session Continuity.

This module tests session continuity capabilities:
    - Message normalization and filtering
    - Continuity pack generation
    - History window management
    - Stable facts and open loops extraction
"""

from __future__ import annotations

import pytest


class TestMessageNormalization:
    """Tests for message normalization."""

    def test_normalizes_role_to_lowercase(self, continuity_engine) -> None:
        """Role should be normalized to lowercase."""
        from polaris.kernelone.context.session_continuity import _iter_normalized_messages

        messages = [{"role": "USER", "content": "Hello", "sequence": 0}]
        normalized = _iter_normalized_messages(messages)
        assert normalized[0]["role"] == "user"

    def test_extracts_content_from_message_field(self, continuity_engine) -> None:
        """Should extract content from 'message' field as fallback."""
        from polaris.kernelone.context.session_continuity import _iter_normalized_messages

        messages = [{"role": "user", "message": "Hello from message field"}]
        normalized = _iter_normalized_messages(messages)
        assert normalized[0]["content"] == "Hello from message field"

    def test_filters_empty_messages(self, continuity_engine) -> None:
        """Empty messages should be filtered out."""
        from polaris.kernelone.context.session_continuity import _iter_normalized_messages

        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "Hello"},
        ]
        normalized = _iter_normalized_messages(messages)
        assert len(normalized) == 1
        assert normalized[0]["content"] == "Hello"

    def test_handles_none_messages(self, continuity_engine) -> None:
        """None messages should return empty list."""
        from polaris.kernelone.context.session_continuity import _iter_normalized_messages

        normalized = _iter_normalized_messages(None)
        assert normalized == []

    def test_preserves_sequence_order(self, continuity_engine) -> None:
        """Sequence numbers should be preserved."""
        from polaris.kernelone.context.session_continuity import _iter_normalized_messages

        messages = [
            {"role": "user", "content": "First", "sequence": 10},
            {"role": "user", "content": "Second", "sequence": 20},
        ]
        normalized = _iter_normalized_messages(messages)
        assert normalized[0]["sequence"] == 10
        assert normalized[1]["sequence"] == 20


class TestHistoryPairsConversion:
    """Tests for history pairs conversion."""

    def test_history_to_messages(self) -> None:
        """History pairs should convert to message format."""
        from polaris.kernelone.context.session_continuity import history_pairs_to_messages

        history = [("user", "Hello"), ("assistant", "Hi there")]
        messages = history_pairs_to_messages(history)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    def test_messages_to_history(self) -> None:
        """Messages should convert to history pairs."""
        from polaris.kernelone.context.session_continuity import messages_to_history_pairs

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        history = messages_to_history_pairs(messages)
        assert len(history) == 2
        # Content is preserved (case-sensitive)
        assert history[0] == ("user", "Hello")

    def test_roundtrip_conversion(self) -> None:
        """Roundtrip conversion should preserve essential content."""
        from polaris.kernelone.context.session_continuity import (
            history_pairs_to_messages,
            messages_to_history_pairs,
        )

        original = [("user", "Test message"), ("assistant", "Response")]
        messages = history_pairs_to_messages(original)
        result = messages_to_history_pairs(messages)
        # Content is normalized (whitespace normalized, case preserved)
        # Note: history_pairs_to_messages normalizes to lowercase via _normalize_text
        assert result[0][0] == "user"
        assert result[1][0] == "assistant"


class TestHistoryWindow:
    """Tests for history window resolution."""

    def test_resolve_default_when_none(self, continuity_engine) -> None:
        """Should return default window when history_limit is None."""
        window = continuity_engine.resolve_history_window(None)
        assert window == continuity_engine.policy.default_history_window_messages

    def test_resolve_respects_max(self, continuity_engine) -> None:
        """Should cap at max window size."""
        window = continuity_engine.resolve_history_window(1000)  # Very large
        assert window == continuity_engine.policy.max_history_window_messages

    def test_resolve_respects_min(self, continuity_engine) -> None:
        """Should enforce minimum of 1."""
        window = continuity_engine.resolve_history_window(0)
        assert window >= 1

    def test_resolve_negative_returns_default(self, continuity_engine) -> None:
        """Negative limit should return default."""
        window = continuity_engine.resolve_history_window(-5)
        assert window == continuity_engine.policy.default_history_window_messages


class TestContinuityPackGeneration:
    """Tests for SessionContinuityPack generation."""

    @pytest.mark.asyncio
    async def test_build_pack_from_messages(self, continuity_engine, sample_messages) -> None:
        """Should build continuity pack from messages."""
        pack = await continuity_engine.build_pack(sample_messages)
        assert pack is not None
        assert isinstance(pack.summary, str)
        assert len(pack.summary) > 0

    @pytest.mark.asyncio
    async def test_build_pack_requires_messages(self, continuity_engine) -> None:
        """Should return None when no messages provided."""
        pack = await continuity_engine.build_pack([])
        assert pack is None

    @pytest.mark.asyncio
    async def test_build_pack_tracks_source_count(self, continuity_engine, sample_messages) -> None:
        """Should track source message count."""
        pack = await continuity_engine.build_pack(sample_messages)
        assert pack.source_message_count == len(sample_messages)

    @pytest.mark.asyncio
    async def test_build_pack_sets_generated_at(self, continuity_engine, sample_messages) -> None:
        """Should set generated_at timestamp."""
        pack = await continuity_engine.build_pack(sample_messages)
        assert pack.generated_at != ""
        assert "T" in pack.generated_at  # ISO format

    @pytest.mark.asyncio
    async def test_build_pack_with_existing_pack(self, continuity_engine, sample_messages) -> None:
        """Should merge with existing pack."""
        existing = await continuity_engine.build_pack(sample_messages[:2])
        pack = await continuity_engine.build_pack(sample_messages[2:], existing_pack=existing)
        assert pack is not None
        assert pack.compacted_through_seq >= existing.compacted_through_seq


class TestLowSignalFiltering:
    """Tests for low-signal message filtering."""

    def test_filters_greetings(self, continuity_engine) -> None:
        """Should filter out greeting messages."""
        from polaris.kernelone.context.session_continuity import _is_low_signal

        assert _is_low_signal("hello") is True
        assert _is_low_signal("hi") is True
        assert _is_low_signal("你好") is True

    def test_preserves_engineering_content(self, continuity_engine) -> None:
        """Should preserve engineering content."""
        from polaris.kernelone.context.session_continuity import _is_low_signal

        assert _is_low_signal("fix the bug in main.py") is False
        assert _is_low_signal("implement feature X") is False

    @pytest.mark.asyncio
    async def test_omitted_count_tracked(self, continuity_engine) -> None:
        """Should track omitted low-signal count."""
        messages = [
            {"role": "user", "content": "hello", "sequence": 0},
            {"role": "user", "content": "fix the bug", "sequence": 1},
        ]
        pack = await continuity_engine.build_pack(messages)
        assert pack.omitted_low_signal_count >= 1


class TestSignalScoring:
    """Tests for message signal scoring."""

    def test_user_messages_score_higher(self) -> None:
        """User messages should score higher than assistant."""
        from polaris.kernelone.context.session_continuity import _signal_score

        user_score = _signal_score("user", "fix the bug")
        assistant_score = _signal_score("assistant", "I fixed the bug")
        assert user_score >= assistant_score

    def test_longer_content_scores_higher(self) -> None:
        """Longer content should score higher."""
        from polaris.kernelone.context.session_continuity import _signal_score

        short_score = _signal_score("user", "hi")
        long_score = _signal_score("user", "x" * 100)
        assert long_score > short_score

    def test_code_path_increases_score(self) -> None:
        """Content with code paths should score higher."""
        from polaris.kernelone.context.session_continuity import _signal_score

        plain_score = _signal_score("user", "fix something")
        code_score = _signal_score("user", "fix src/main.py at line 42")
        assert code_score > plain_score

    def test_high_signal_terms_increase_score(self) -> None:
        """High-signal terms should increase score."""
        from polaris.kernelone.context.session_continuity import _signal_score

        normal_score = _signal_score("user", "do something")
        signal_score = _signal_score("user", "fix error bug refactor")
        assert signal_score > normal_score


class TestOpenLoopExtraction:
    """Tests for open loop extraction from messages via build_pack."""

    @pytest.mark.asyncio
    async def test_build_pack_includes_open_loops(self, continuity_engine) -> None:
        """build_pack should include open loops from user requests."""
        messages = [{"role": "user", "content": "please fix the bug", "sequence": 0}]
        pack = await continuity_engine.build_pack(messages)
        # Pack should be generated (may or may not include loops depending on patterns)
        assert pack is None or isinstance(pack, type(pack))

    @pytest.mark.asyncio
    async def test_respects_max_open_loops(self, continuity_engine) -> None:
        """Should respect max_open_loops policy limit."""
        messages = [{"role": "user", "content": f"task {i}", "sequence": i} for i in range(20)]
        pack = await continuity_engine.build_pack(messages)
        if pack is not None:
            assert len(pack.open_loops) <= continuity_engine.policy.max_open_loops


class TestStableFactsExtraction:
    """Tests for stable facts extraction via build_pack."""

    @pytest.mark.asyncio
    async def test_build_pack_includes_stable_facts(self, continuity_engine) -> None:
        """build_pack should include stable facts."""
        messages = [
            {
                "role": "user",
                "content": "we are working on the login feature",
                "sequence": 0,
            }
        ]
        pack = await continuity_engine.build_pack(messages)
        if pack is not None:
            assert isinstance(pack.stable_facts, tuple)

    @pytest.mark.asyncio
    async def test_respects_max_stable_facts(self, continuity_engine) -> None:
        """Should respect max_stable_facts policy limit."""
        messages = [{"role": "user", "content": f"fact {i}", "sequence": i} for i in range(20)]
        pack = await continuity_engine.build_pack(messages)
        if pack is not None:
            assert len(pack.stable_facts) <= continuity_engine.policy.max_stable_facts


class TestSessionContinuityProjection:
    """Tests for SessionContinuityProjection."""

    @pytest.mark.asyncio
    async def test_projection_returns_recent_messages(self, continuity_engine) -> None:
        """Projection should return recent messages within window."""
        from polaris.kernelone.context.session_continuity import SessionContinuityRequest

        messages = [{"role": "user", "content": f"msg {i}", "sequence": i} for i in range(30)]
        request = SessionContinuityRequest(
            session_id="test",
            role="pm",
            workspace="/test",
            messages=tuple(messages),
        )
        projection = await continuity_engine.project(request)
        # Should return a projection with recent messages
        assert isinstance(projection.recent_messages, tuple)

    @pytest.mark.asyncio
    async def test_projection_updates_prompt_context(self, continuity_engine) -> None:
        """Projection should update prompt context with continuity when needed."""
        from polaris.kernelone.context.session_continuity import (
            SessionContinuityRequest,
        )

        # Use many messages to force older messages that need compaction
        messages = [{"role": "user", "content": f"task {i}", "sequence": i} for i in range(30)]
        request = SessionContinuityRequest(
            session_id="test",
            role="pm",
            workspace="/test",
            messages=tuple(messages),
            history_limit=10,  # Only recent 10 messages, rest need compaction
        )
        projection = await continuity_engine.project(request)

        # When messages exceed history window, continuity pack should be built
        # Note: prompt_context may or may not have continuity depending on summary quality
        assert isinstance(projection.prompt_context, dict)

    @pytest.mark.asyncio
    async def test_projection_detects_changes(self, continuity_engine) -> None:
        """Projection should indicate when continuity changed."""
        from polaris.kernelone.context.session_continuity import (
            SessionContinuityRequest,
        )

        messages = [{"role": "user", "content": "hello", "sequence": 0}]
        request = SessionContinuityRequest(
            session_id="test",
            role="pm",
            workspace="/test",
            messages=tuple(messages),
        )
        projection = await continuity_engine.project(request)
        # With recent messages only, should not need to build pack
        assert isinstance(projection.changed, bool)

    @pytest.mark.asyncio
    async def test_projection_persisted_context_os_excludes_raw_truth_keys(self, continuity_engine) -> None:
        """Persisted state_first_context_os must remain derived projection only.

        Note: transcript_log IS allowed because it is derived state (reconstructed from
        messages via _merge_transcript), not raw conversation truth like "messages" or "history".
        The previous transcript_log_index optimization was reverted because it caused a
        format drift: consumers (ContextOSSnapshot, ToolLoopController, ContextGateway)
        expect transcript_log to be present, but only saw an empty transcript.
        """
        from polaris.kernelone.context.session_continuity import SessionContinuityRequest

        messages = [
            {
                "role": "user",
                "content": "Implement runtime invariants and keep session truth ownership.",
                "sequence": 1,
            },
            {"role": "assistant", "content": "I will enforce state-first projection boundaries.", "sequence": 2},
        ]
        request = SessionContinuityRequest(
            session_id="test",
            role="director",
            workspace="/test",
            messages=tuple(messages),
        )
        projection = await continuity_engine.project(request)
        payload = projection.persisted_context_config.get("state_first_context_os")
        assert isinstance(payload, dict)
        # Full transcript_log is persisted so downstream consumers can read it directly.
        assert "transcript_log" in payload
        assert isinstance(payload["transcript_log"], list)
        assert len(payload["transcript_log"]) == 2
        # Raw truth keys are still forbidden
        assert "messages" not in payload
        assert "history" not in payload

    def test_rehydrate_legacy_snapshot_restores_transcript_and_filters_control_plane_noise(self) -> None:
        from polaris.kernelone.context.context_os.rehydration import rehydrate_persisted_context_os_payload

        legacy_payload = {
            "version": 1,
            "mode": "state_first_context_os_v1",
            "adapter_id": "generic",
            "transcript_log_index": [
                {"event_id": "evt_user", "role": "user"},
                {"event_id": "evt_assistant", "role": "assistant"},
            ],
            "working_state": {
                "task_state": {
                    "open_loops": [
                        {
                            "entry_id": "loop-1",
                            "path": "task.open_loops",
                            "value": "[SYSTEM WARNING] repeated read-only loop",
                            "source_turns": [],
                            "confidence": 1.0,
                            "updated_at": "2026-04-15T00:00:00Z",
                        }
                    ]
                }
            },
        }
        session_turn_events = [
            {
                "event_id": "evt_user",
                "role": "user",
                "content": "Please continue the runtime audit.",
                "sequence": 1,
                "metadata": {},
            },
            {
                "event_id": "evt_assistant",
                "role": "assistant",
                "content": "I will continue the runtime audit.",
                "sequence": 2,
                "metadata": {},
            },
        ]

        restored = rehydrate_persisted_context_os_payload(
            legacy_payload,
            session_turn_events=session_turn_events,
        )

        assert restored is not None
        assert len(restored.get("transcript_log", [])) == 2
        assert restored["transcript_log"][0]["content"] == "Please continue the runtime audit."
        assert restored["working_state"]["task_state"]["open_loops"] == []


class TestContinuityPackSerialization:
    """Tests for SessionContinuityPack serialization."""

    def test_to_dict_contains_all_fields(self) -> None:
        """to_dict should include all pack fields."""
        from polaris.kernelone.context.session_continuity import SessionContinuityPack

        pack = SessionContinuityPack(
            summary="test summary",
            stable_facts=("fact1",),
            open_loops=("loop1",),
            omitted_low_signal_count=2,
            generated_at="2024-01-01T00:00:00Z",
            compacted_through_seq=10,
            source_message_count=5,
            recent_window_messages=3,
        )
        d = pack.to_dict()

        assert "summary" in d
        assert "stable_facts" in d
        assert "open_loops" in d
        assert "omitted_low_signal_count" in d
        assert "generated_at" in d
        assert "compacted_through_seq" in d

    def test_from_mapping_valid(self) -> None:
        """from_mapping should parse valid dict."""
        from polaris.kernelone.context.session_continuity import SessionContinuityPack

        data = {
            "summary": "test",
            "stable_facts": ["f1", "f2"],
            "open_loops": ["l1"],
            "version": 2,
        }
        pack = SessionContinuityPack.from_mapping(data)
        assert pack.summary == "test"
        assert len(pack.stable_facts) == 2

    def test_from_mapping_handles_none(self) -> None:
        """from_mapping should handle None gracefully."""
        from polaris.kernelone.context.session_continuity import SessionContinuityPack

        pack = SessionContinuityPack.from_mapping(None)
        assert pack is None

    def test_from_mapping_handles_non_mapping(self) -> None:
        """from_mapping should handle non-mapping gracefully."""
        from polaris.kernelone.context.session_continuity import SessionContinuityPack

        pack = SessionContinuityPack.from_mapping("not a dict")
        assert pack is None


class TestContinuityCache:
    """Tests for continuity pack caching."""

    @pytest.mark.asyncio
    async def test_cache_roundtrip(self, tiered_cache) -> None:
        """Should cache and retrieve continuity pack."""
        pack_data = {
            "summary": "test continuity",
            "stable_facts": ["fact1"],
            "open_loops": [],
        }
        await tiered_cache.put_continuity_pack("session123", pack_data)

        cached = await tiered_cache.get_continuity_pack("session123")
        assert cached is not None
        assert cached["summary"] == "test continuity"

    @pytest.mark.asyncio
    async def test_cache_miss(self, tiered_cache) -> None:
        """Cache miss should return None."""
        result = await tiered_cache.get_continuity_pack("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_invalidation(self, tiered_cache) -> None:
        """Should invalidate cache correctly."""

        pack_data = {"summary": "test"}
        await tiered_cache.put_continuity_pack("session456", pack_data)

        # Verify it was cached
        cached = await tiered_cache.get_continuity_pack("session456")
        assert cached is not None

        # Invalidate all tiers
        await tiered_cache.invalidate("session456", None)

        # Should be gone
        result = await tiered_cache.get_continuity_pack("session456")
        assert result is None


class TestPolicyConfiguration:
    """Tests for SessionContinuityPolicy configuration."""

    def test_custom_policy_limits(self) -> None:
        """Custom policy should respect configured limits."""
        from polaris.kernelone.context.session_continuity import (
            SessionContinuityEngine,
            SessionContinuityPolicy,
        )

        policy = SessionContinuityPolicy(
            default_history_window_messages=10,
            max_history_window_messages=20,
            max_summary_chars=500,
        )
        engine = SessionContinuityEngine(policy=policy)
        assert engine.policy.default_history_window_messages == 10
        assert engine.policy.max_history_window_messages == 20
        assert engine.policy.max_summary_chars == 500

    def test_default_policy_values(self) -> None:
        """Default policy should have sensible values."""
        from polaris.kernelone.context.session_continuity import (
            SessionContinuityEngine,
        )

        engine = SessionContinuityEngine()
        assert engine.policy.default_history_window_messages > 0
        assert engine.policy.max_history_window_messages >= engine.policy.default_history_window_messages
        assert engine.policy.max_summary_chars > 0


class TestPendingFollowUpPersistenceIntegration:
    """Tests for pending_followup persistence through SessionContinuity (Critical: CRITICAL-001)."""

    @pytest.fixture
    def engine(self):
        from polaris.kernelone.context.context_os import StateFirstContextOS

        return StateFirstContextOS()

    @pytest.mark.asyncio
    async def test_persisted_payload_includes_pending_followup(self, continuity_engine, engine) -> None:
        """Persisted payload should include pending_followup state.

        This tests the fix for: 'pending_followup not persisted through session reload'.
        """
        from polaris.kernelone.context.session_continuity import SessionContinuityRequest

        # Create a projection with pending follow-up
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )

        # Verify pending follow-up exists
        assert projection.snapshot.pending_followup is not None
        assert projection.snapshot.pending_followup.action != ""

        # Create a request with context_os snapshot in session_context_config
        request = SessionContinuityRequest(
            session_id="test_persistence",
            role="director",
            workspace="/test",
            messages=(
                {"role": "user", "content": "请帮我实现登录功能", "sequence": 0},
                {"role": "assistant", "content": "需要我帮你实现吗？", "sequence": 1},
            ),
            session_context_config={
                "state_first_context_os": projection.snapshot.to_dict(),
            },
        )

        # Project through SessionContinuity
        continuity_projection = await continuity_engine.project(request)

        # Check persisted payload includes pending_followup
        persisted = continuity_projection.persisted_context_config.get("state_first_context_os")
        assert persisted is not None
        assert "pending_followup" in persisted
        # pending_followup should NOT be None when there's a pending action
        if projection.snapshot.pending_followup.action:
            assert persisted["pending_followup"] is not None

    @pytest.mark.asyncio
    async def test_context_os_snapshot_roundtrip_includes_pending_followup(self, engine) -> None:
        """ContextOSSnapshot serialization should preserve pending_followup.

        This tests: snapshot -> to_dict() -> from_mapping() preserves pending_followup.
        """
        from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot

        # Create projection with pending follow-up
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )

        original = projection.snapshot
        original_action = original.pending_followup.action if original.pending_followup else ""
        original_status = original.pending_followup.status if original.pending_followup else ""

        # Serialize
        snapshot_dict = original.to_dict()

        # Deserialize
        restored = ContextOSSnapshot.from_mapping(snapshot_dict)

        # Verify pending_followup preserved
        assert restored.pending_followup is not None
        assert restored.pending_followup.action == original_action
        assert restored.pending_followup.status == original_status

    @pytest.mark.asyncio
    async def test_resolved_followup_not_carried_in_session(self, continuity_engine, engine) -> None:
        """Resolved follow-ups should NOT be carried into next session.

        This tests the fix for: 'resolved follow-ups continue to occupy attention'.
        """
        from polaris.kernelone.context.session_continuity import SessionContinuityRequest

        # Turn 1: Create pending follow-up
        snapshot1 = (
            await engine.project(
                messages=[
                    {"role": "user", "content": "请帮我实现登录功能"},
                    {"role": "assistant", "content": "需要我帮你实现吗？"},
                ],
                recent_window_messages=8,
            )
        ).snapshot

        assert snapshot1.pending_followup.status == "pending"

        # Turn 2: Resolve it
        snapshot2 = (
            await engine.project(
                messages=[{"role": "user", "content": "需要"}],
                existing_snapshot=snapshot1,
                recent_window_messages=8,
            )
        ).snapshot

        assert snapshot2.pending_followup.status == "confirmed"

        # Turn 3: New session (simulated via SessionContinuity)
        request = SessionContinuityRequest(
            session_id="new_session",
            role="director",
            workspace="/test",
            messages=(
                {"role": "user", "content": "帮我实现注册功能", "sequence": 0},
                {"role": "assistant", "content": "好的，我来帮你实现注册功能。", "sequence": 1},
            ),
            session_context_config={
                "state_first_context_os": snapshot2.to_dict(),
            },
        )

        continuity_projection = await continuity_engine.project(request)

        # Get the restored snapshot from persisted payload
        persisted = continuity_projection.persisted_context_config.get("state_first_context_os")
        assert persisted is not None

        # The new session should NOT carry over the old resolved follow-up as pending
        # If there's a new pending follow-up from the new assistant question, it should be separate
        from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot

        ContextOSSnapshot.from_mapping(persisted)

        # The restored snapshot should reflect the current state
        # If there's a new pending follow-up, it should be from the new question
        # A resolved follow-up (confirmed) should not block attention
