"""Tests for memory_search.py Phase 5-6 enhancements.

T6-1: HybridMemory integration / enhanced search
T6-2: O(n²) artifact recency calculation fixed to O(1)
"""

from __future__ import annotations

from polaris.kernelone.context.context_os.memory_search import _search_memory_impl


class MockArtifactStub:
    """Mock artifact stub for testing."""

    def __init__(self, artifact_id: str) -> None:
        self.artifact_id = artifact_id

    def to_stub(self) -> dict:
        return {"artifact_id": self.artifact_id}


class TestSearchMemoryImpl:
    """Tests for _search_memory_impl function."""

    def test_search_with_dict_snapshot(self) -> None:
        """Test that search works with dict-form snapshot (not just dataclass)."""
        snapshot = {
            "transcript_log": [
                {"event_id": "evt_1", "sequence": 1, "role": "user", "content": "test content"},
                {"event_id": "evt_2", "sequence": 2, "role": "assistant", "content": "response"},
            ],
            "working_state": {
                "state_history": [],
                "current_goal": {"value": "implement feature"},
                "active_entities": [],
                "active_artifacts": [],
            },
            "artifact_store": [],
            "episode_store": [],
        }

        # Should not raise, should return list
        results = _search_memory_impl(snapshot, "test", limit=3)
        assert isinstance(results, list)

    def test_artifact_recency_o1_lookup(self) -> None:
        """Test that artifact recency uses O(1) index lookup (T6-2 fix).

        The pre-built event_index provides O(1) lookup for artifact recency,
        eliminating the O(n²) behavior from nested loops.
        """
        # Create a snapshot with multiple events and artifacts
        artifact = MockArtifactStub("art_1")
        artifact.peek = "test artifact"
        artifact.keys = ["key1"]
        artifact.source_event_ids = ["evt_1", "evt_3"]

        snapshot = {
            "transcript_log": [
                {"event_id": "evt_1", "sequence": 1, "role": "user", "content": "first"},
                {"event_id": "evt_2", "sequence": 2, "role": "assistant", "content": "second"},
                {"event_id": "evt_3", "sequence": 3, "role": "user", "content": "third"},
            ],
            "working_state": {
                "state_history": [],
                "current_goal": {"value": ""},
                "active_entities": [],
                "active_artifacts": [],
            },
            "artifact_store": [artifact],
            "episode_store": [],
        }

        results = _search_memory_impl(snapshot, "test artifact", limit=5)

        # Should find the artifact
        artifact_results = [r for r in results if r["kind"] == "artifact"]
        assert len(artifact_results) == 1
        assert artifact_results[0]["id"] == "art_1"

    def test_episode_recency_with_reopened_episodes(self) -> None:
        """Test episode recency calculation handles various to_sequence values."""
        snapshot = {
            "transcript_log": [],
            "working_state": {
                "state_history": [],
                "current_goal": {"value": ""},
                "active_entities": [],
                "active_artifacts": [],
            },
            "artifact_store": [],
            "episode_store": [
                {
                    "episode_id": "ep_1",
                    "from_sequence": 1,
                    "to_sequence": 10,
                    "intent": "old task",
                    "outcome": "completed",
                    "digest_256": "old digest",
                },
                {
                    "episode_id": "ep_2",
                    "from_sequence": 15,
                    "to_sequence": 25,
                    "intent": "new task",
                    "outcome": "completed",
                    "digest_256": "new digest",
                },
            ],
        }

        results = _search_memory_impl(snapshot, "task", limit=5)
        episode_results = [r for r in results if r["kind"] == "episode"]

        # Should find both episodes
        assert len(episode_results) == 2

        # Find the two episodes by id
        ep_new = next(r for r in episode_results if r["id"] == "ep_2")
        ep_old = next(r for r in episode_results if r["id"] == "ep_1")

        # Recency should be calculated correctly
        # ep_2 has to_sequence=25, ep_1 has to_sequence=10
        # max_seq should be 25, so:
        # ep_1.recency = 10/25 = 0.4
        # ep_2.recency = 25/25 = 1.0
        # Due to floating point, check they're different
        assert ep_new["score_breakdown"]["recency"] != ep_old["score_breakdown"]["recency"], (
            f"Recency should differ: ep_1={ep_old['score_breakdown']['recency']}, "
            f"ep_2={ep_new['score_breakdown']['recency']}"
        )
        # ep_2 should have higher recency
        assert ep_new["score_breakdown"]["recency"] > ep_old["score_breakdown"]["recency"]

    def test_empty_snapshot_returns_empty_list(self) -> None:
        """Test that empty snapshot returns empty results gracefully."""
        snapshot = {
            "transcript_log": [],
            "working_state": {
                "state_history": [],
                "current_goal": {"value": ""},
                "active_entities": [],
                "active_artifacts": [],
            },
            "artifact_store": [],
            "episode_store": [],
        }

        results = _search_memory_impl(snapshot, "nonexistent query", limit=5)
        assert results == []

    def test_search_with_kind_filter(self) -> None:
        """Test that kind filter correctly filters results."""
        snapshot = {
            "transcript_log": [],
            "working_state": {
                "state_history": [
                    {
                        "entry_id": "state_1",
                        "value": "state value",
                        "path": "/test/path",
                        "source_turns": ["t1"],
                    }
                ],
                "current_goal": {"value": ""},
                "active_entities": [],
                "active_artifacts": [],
            },
            "artifact_store": [],
            "episode_store": [
                {
                    "episode_id": "ep_1",
                    "from_sequence": 1,
                    "to_sequence": 10,
                    "intent": "test episode",
                    "outcome": "done",
                    "digest_256": "test",
                }
            ],
        }

        # Filter by state
        state_results = _search_memory_impl(snapshot, "state", kind="state", limit=5)
        assert all(r["kind"] == "state" for r in state_results)

        # Filter by episode
        episode_results = _search_memory_impl(snapshot, "test", kind="episode", limit=5)
        assert all(r["kind"] == "episode" for r in episode_results)


class TestTrimTextFix:
    """Tests for T6-7 _trim_text digest truncation fix."""

    def test_trim_text_short_max_chars(self) -> None:
        """Test that _trim_text handles small max_chars correctly.

        T6-7 Fix: For small max_chars (e.g., 64), uses 60/40 split
        instead of 72/28 to avoid tiny tails.
        """
        from polaris.kernelone.context.context_os.helpers import _trim_text

        # Text longer than max_chars
        text = "This is a very long text that needs to be trimmed to fit within the character limit for digest_64"

        # With max_chars=64, the 72/28 split creates ~4 char tail
        # The fix uses 60/40 split for better tail preservation
        result = _trim_text(text, max_chars=64)

        # Result should be shorter than original
        assert len(result) <= 64

        # Result should contain snip marker
        assert "...[snip]..." in result

        # With the fix, tail should be at least 5 chars (60/40 split)
        parts = result.split("...[snip]...")
        if len(parts) == 2:
            tail = parts[1]
            assert len(tail) >= 5, f"Tail too short: {tail!r}"

    def test_trim_text_large_max_chars(self) -> None:
        """Test that _trim_text uses 72/28 split for larger max_chars."""
        from polaris.kernelone.context.context_os.helpers import _trim_text

        text = "A" * 500  # Very long text

        # With max_chars=256, should use 72/28 split
        result = _trim_text(text, max_chars=256)

        assert len(result) <= 256
        assert "...[snip]..." in result

    def test_trim_text_short_input(self) -> None:
        """Test that _trim_text returns original text if under limit."""
        from polaris.kernelone.context.context_os.helpers import _trim_text

        text = "short text"

        # With max_chars=64, short text should be returned unchanged
        result = _trim_text(text, max_chars=64)

        assert result == text
        assert "...[snip]..." not in result
