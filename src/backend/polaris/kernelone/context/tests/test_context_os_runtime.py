"""Tests for StateFirstContextOS runtime methods - P0 coverage.

These tests cover the untested P0 methods:
- read_artifact: Artifact lookup with span parameters
- read_episode: Episode lookup by ID
- get_state: State path resolution
"""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os.models_v2 import (
    ArtifactRecordV2 as ArtifactRecord,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    EpisodeCardV2 as EpisodeCard,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.context_os.policies import AttentionRuntimePolicy, StateFirstContextOSPolicy
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS


@pytest.fixture
def sample_snapshot() -> ContextOSSnapshot:
    """Create a sample ContextOSSnapshot for testing."""
    return ContextOSSnapshot(
        version=1,
        mode="state_first_context_os_v1",
        adapter_id="generic",
        transcript_log=(),
        working_state=WorkingState(
            task_state=TaskStateView(
                current_goal=StateEntry(
                    entry_id="goal_1",
                    path="task_state.current_goal",
                    value="Implement login feature",
                    source_turns=("t0", "t1"),
                    confidence=0.96,
                    updated_at="2024-01-01T00:00:00Z",
                ),
            ),
        ),
        artifact_store=(
            ArtifactRecord(
                artifact_id="art_abc123",
                artifact_type="code",
                mime_type="text/x-python",
                token_count=100,
                char_count=400,
                peek="def login():",
                content="def login():\n    pass\n\ndef logout():\n    pass\n\ndef verify():\n    pass\n",
            ),
            ArtifactRecord(
                artifact_id="art_def456",
                artifact_type="markup",
                mime_type="text/html",
                token_count=50,
                char_count=200,
                peek="<html>",
                content="<html>\n<head></head>\n<body></body>\n</html>",
            ),
        ),
        episode_store=(
            EpisodeCard(
                episode_id="ep_1",
                from_sequence=0,
                to_sequence=3,
                intent="Setup project structure",
                outcome="Created basic files",
            ),
            EpisodeCard(
                episode_id="ep_2",
                from_sequence=4,
                to_sequence=7,
                intent="Implement core features",
                outcome="Features working",
            ),
        ),
    )


@pytest.fixture
def context_os() -> StateFirstContextOS:
    """Create a StateFirstContextOS instance for testing."""
    return StateFirstContextOS(policy=StateFirstContextOSPolicy())


class TestStateFirstContextOSReadArtifact:
    """Tests for read_artifact method."""

    def test_read_artifact_normal(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test normal artifact read."""
        result = context_os.read_artifact(sample_snapshot, "art_abc123")
        assert result is not None
        assert result["artifact_id"] == "art_abc123"
        assert result["artifact_type"] == "code"
        assert "def login" in result["content"]

    def test_read_artifact_with_valid_span(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test artifact read with valid line span."""
        result = context_os.read_artifact(sample_snapshot, "art_abc123", span=(1, 2))
        assert result is not None
        assert result["artifact_id"] == "art_abc123"
        # Should only contain lines 1-2 (first two lines)
        lines = result["content"].split("\n")
        assert len(lines) <= 2

    def test_read_artifact_with_invalid_span_start_greater_than_end(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test artifact read with invalid span (start > end)."""
        # This should gracefully handle by using start as the index
        result = context_os.read_artifact(sample_snapshot, "art_abc123", span=(5, 2))
        assert result is not None
        # Should return empty or partial content due to invalid span
        assert result["artifact_id"] == "art_abc123"

    def test_read_artifact_non_existent(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test reading non-existent artifact returns None."""
        result = context_os.read_artifact(sample_snapshot, "art_nonexistent")
        assert result is None

    def test_read_artifact_out_of_bounds_span(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test artifact read with out-of-bounds line span."""
        result = context_os.read_artifact(sample_snapshot, "art_abc123", span=(100, 200))
        assert result is not None
        # Should handle gracefully, returning empty or available lines
        assert result["artifact_id"] == "art_abc123"

    def test_read_artifact_empty_id(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test reading artifact with empty ID returns None."""
        result = context_os.read_artifact(sample_snapshot, "")
        assert result is None

    def test_read_artifact_none_snapshot(self, context_os: StateFirstContextOS) -> None:
        """Test reading artifact with None snapshot returns None."""
        result = context_os.read_artifact(None, "art_abc123")
        assert result is None


class TestStateFirstContextOSReadEpisode:
    """Tests for read_episode method."""

    def test_read_episode_existing(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test reading existing episode."""
        result = context_os.read_episode(sample_snapshot, "ep_1")
        assert result is not None
        assert result["episode_id"] == "ep_1"
        assert result["intent"] == "Setup project structure"
        assert result["outcome"] == "Created basic files"

    def test_read_episode_second(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test reading second episode."""
        result = context_os.read_episode(sample_snapshot, "ep_2")
        assert result is not None
        assert result["episode_id"] == "ep_2"
        assert result["intent"] == "Implement core features"

    def test_read_episode_non_existent(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test reading non-existent episode returns None."""
        result = context_os.read_episode(sample_snapshot, "ep_nonexistent")
        assert result is None

    def test_read_episode_empty_id(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test reading episode with empty ID returns None."""
        result = context_os.read_episode(sample_snapshot, "")
        assert result is None

    def test_read_episode_none_snapshot(self, context_os: StateFirstContextOS) -> None:
        """Test reading episode with None snapshot returns None."""
        result = context_os.read_episode(None, "ep_1")
        assert result is None


class TestStateFirstContextOSGetState:
    """Tests for get_state method."""

    def test_get_state_run_card(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test getting run_card state."""
        result = context_os.get_state(sample_snapshot, "run_card")
        assert result is not None
        assert "current_goal" in result

    def test_get_state_context_slice_plan(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test getting context_slice_plan state."""
        result = context_os.get_state(sample_snapshot, "context_slice_plan")
        assert result is not None
        assert "plan_id" in result

    def test_get_state_task_state_current_goal(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test getting task_state.current_goal."""
        result = context_os.get_state(sample_snapshot, "task_state.current_goal")
        assert result is not None
        assert result["value"] == "Implement login feature"
        assert result["path"] == "task_state.current_goal"

    def test_get_state_non_existent_path(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test getting non-existent path returns None."""
        result = context_os.get_state(sample_snapshot, "nonexistent.path")
        assert result is None

    def test_get_state_empty_path(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test getting empty path returns None."""
        result = context_os.get_state(sample_snapshot, "")
        assert result is None

    def test_get_state_none_snapshot(self, context_os: StateFirstContextOS) -> None:
        """Test getting state with None snapshot returns None."""
        result = context_os.get_state(None, "run_card")
        assert result is None

    def test_get_state_task_state_open_loops(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test getting task_state.open_loops."""
        result = context_os.get_state(sample_snapshot, "task_state.open_loops")
        assert result is not None
        assert isinstance(result, list)

    def test_get_state_decision_log(self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot) -> None:
        """Test getting decision_log."""
        result = context_os.get_state(sample_snapshot, "decision_log")
        assert result is not None
        assert isinstance(result, list)

    def test_get_state_active_artifacts(
        self, context_os: StateFirstContextOS, sample_snapshot: ContextOSSnapshot
    ) -> None:
        """Test getting active_artifacts."""
        result = context_os.get_state(sample_snapshot, "active_artifacts")
        assert result is not None
        assert isinstance(result, list)


class TestStateFirstContextOSLifecycle:
    """Tests for StateFirstContextOS lifecycle management methods."""

    def test_context_manager_entry_exit(self) -> None:
        """Test sync context manager __enter__ and __exit__."""
        policy = StateFirstContextOSPolicy(
            attention_runtime=AttentionRuntimePolicy(enable_dialog_act=True)
        )
        context_os = StateFirstContextOS(policy=policy)

        # Access classifier to trigger creation
        _ = context_os.dialog_act_classifier
        assert context_os._dialog_act_classifier is not None

        # Use context manager
        with context_os as os_instance:
            assert os_instance is context_os

        # After __exit__, classifier should be released
        assert context_os._dialog_act_classifier is None

    def test_context_manager_with_dialog_act_classifier(self) -> None:
        """Test context manager when dialog_act_classifier is enabled."""
        policy = StateFirstContextOSPolicy(
            attention_runtime=AttentionRuntimePolicy(enable_dialog_act=True)
        )
        context_os = StateFirstContextOS(policy=policy)

        # Access classifier to trigger creation
        _ = context_os.dialog_act_classifier
        assert context_os._dialog_act_classifier is not None

        # Use context manager
        with context_os as os_instance:
            assert os_instance is context_os

        # After __exit__, classifier should be released
        assert context_os._dialog_act_classifier is None

    @pytest.mark.asyncio
    async def test_cleanup_releases_resources(self) -> None:
        """Test async cleanup() releases DialogActClassifier."""
        policy = StateFirstContextOSPolicy(
            attention_runtime=AttentionRuntimePolicy(enable_dialog_act=True)
        )
        context_os = StateFirstContextOS(policy=policy)

        # Access classifier to trigger creation
        _ = context_os.dialog_act_classifier
        assert context_os._dialog_act_classifier is not None

        # Call cleanup
        await context_os.cleanup()

        # Classifier should be released after cleanup
        assert context_os._dialog_act_classifier is None

    @pytest.mark.asyncio
    async def test_close_calls_cleanup(self) -> None:
        """Test async close() calls cleanup() to release resources."""
        policy = StateFirstContextOSPolicy(
            attention_runtime=AttentionRuntimePolicy(enable_dialog_act=True)
        )
        context_os = StateFirstContextOS(policy=policy)

        # Access classifier to trigger creation
        _ = context_os.dialog_act_classifier
        assert context_os._dialog_act_classifier is not None

        # Call close
        await context_os.close()

        # Classifier should be released after close
        assert context_os._dialog_act_classifier is None

    def test_cleanup_lock_is_lazy(self) -> None:
        """Test that _cleanup_lock is lazily initialized."""
        context_os = StateFirstContextOS()

        # Lock should be None initially
        assert context_os._cleanup_lock is None

        # Get lock via _get_cleanup_lock()
        lock1 = context_os._get_cleanup_lock()
        assert lock1 is not None

        # Same lock should be returned on subsequent calls
        lock2 = context_os._get_cleanup_lock()
        assert lock1 is lock2
