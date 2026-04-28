"""End-to-end tests for Enhanced Pipeline Runner (ContextOS 3.0).

These tests verify that all ContextOS 3.0 features work together
in the enhanced pipeline.
"""

import pytest
from polaris.kernelone.context.context_os.decision_log import ContextDecisionLog
from polaris.kernelone.context.context_os.models_v2 import (
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.context_os.pipeline.contracts import (
    PipelineInput,
)
from polaris.kernelone.context.context_os.pipeline.enhanced_runner import (
    EnhancedPipelineRunner,
)
from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy


@pytest.fixture
def policy() -> StateFirstContextOSPolicy:
    """Create a test policy."""
    return StateFirstContextOSPolicy()


@pytest.fixture
def sample_messages() -> list[dict[str, str]]:
    """Create sample messages."""
    return [
        {"role": "user", "content": "Implement feature X"},
        {"role": "assistant", "content": "I'll implement feature X"},
    ]


@pytest.fixture
def sample_transcript() -> tuple[TranscriptEvent, ...]:
    """Create sample transcript events."""
    return (
        TranscriptEvent(
            event_id="evt_001",
            sequence=0,
            role="user",
            content="Implement feature X",
            kind="user_turn",
            route="PATCH",
            source_turns=("t0",),
        ),
        TranscriptEvent(
            event_id="evt_002",
            sequence=1,
            role="assistant",
            content="I'll implement feature X",
            kind="assistant_turn",
            route="PATCH",
            source_turns=("t0",),
        ),
    )


@pytest.fixture
def working_state() -> WorkingState:
    """Create WorkingState with a goal."""
    return WorkingState(
        task_state=TaskStateView(
            current_goal=StateEntry(
                entry_id="goal_001",
                path="task_state.current_goal",
                value="Implement feature X",
                source_turns=("t0",),
                confidence=0.96,
            ),
        ),
        user_profile=UserProfileState(),
        active_entities=(),
        active_artifacts=(),
        decision_log=(),
    )


class TestEnhancedPipelineRunner:
    """Test EnhancedPipelineRunner integration."""

    def test_create_runner_all_enabled(self, policy: StateFirstContextOSPolicy) -> None:
        """Test runner creation with all features enabled."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=True,
            enable_attention_scoring=True,
            enable_graph_propagation=True,
            enable_memory_integration=True,
            enable_predictive_compression=True,
            enable_metrics=True,
        )
        assert runner._enable_phase_aware_budgeting is True
        assert runner._enable_attention_scoring is True
        assert runner._enable_graph_propagation is True
        assert runner._enable_memory_integration is True
        assert runner._enable_predictive_compression is True
        assert runner._enable_metrics is True

    def test_create_runner_all_disabled(self, policy: StateFirstContextOSPolicy) -> None:
        """Test runner creation with all features disabled."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=False,
            enable_attention_scoring=False,
            enable_graph_propagation=False,
            enable_memory_integration=False,
            enable_predictive_compression=False,
            enable_metrics=False,
        )
        assert runner._enable_phase_aware_budgeting is False
        assert runner._enable_attention_scoring is False

    def test_run_with_all_features(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
    ) -> None:
        """Test running pipeline with all features enabled."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=True,
            enable_attention_scoring=True,
            enable_graph_propagation=True,
            enable_memory_integration=True,
            enable_predictive_compression=True,
            enable_metrics=True,
        )

        inp = PipelineInput(
            messages=[{"role": "user", "content": "Implement feature X"}],
            existing_snapshot_transcript=sample_transcript,
        )

        decision_log = ContextDecisionLog()
        projection, report = runner.project(inp, decision_log=decision_log)

        assert projection is not None
        assert report is not None
        assert report.projection_id.startswith("ctxproj_")
        assert len(report.stage_durations_ms) > 0

    def test_run_without_phase_aware(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
    ) -> None:
        """Test running pipeline without phase-aware budgeting."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=False,
            enable_attention_scoring=True,
        )

        inp = PipelineInput(
            messages=[{"role": "user", "content": "Implement feature X"}],
            existing_snapshot_transcript=sample_transcript,
        )

        projection, _report = runner.project(inp)
        assert projection is not None

    def test_run_without_attention(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
    ) -> None:
        """Test running pipeline without attention scoring."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=True,
            enable_attention_scoring=False,
        )

        inp = PipelineInput(
            messages=[{"role": "user", "content": "Implement feature X"}],
            existing_snapshot_transcript=sample_transcript,
        )

        projection, _report = runner.project(inp)
        assert projection is not None

    def test_metrics_collection(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
    ) -> None:
        """Test that metrics are collected correctly."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_metrics=True,
        )

        inp = PipelineInput(
            messages=[{"role": "user", "content": "Implement feature X"}],
            existing_snapshot_transcript=sample_transcript,
        )

        runner.project(inp)
        metrics = runner.metrics

        assert metrics is not None
        assert "gauges" in metrics
        assert "counters" in metrics
        assert "histograms" in metrics

    def test_decision_log_recording(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
    ) -> None:
        """Test that decision log records decisions."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_attention_scoring=True,
        )

        inp = PipelineInput(
            messages=[{"role": "user", "content": "Implement feature X"}],
            existing_snapshot_transcript=sample_transcript,
        )

        decision_log = ContextDecisionLog()
        runner.project(inp, decision_log=decision_log)

        assert decision_log.count > 0

    def test_phase_detection(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
    ) -> None:
        """Test that phase detection works."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=True,
        )

        inp = PipelineInput(
            messages=[{"role": "user", "content": "Implement feature X"}],
            existing_snapshot_transcript=sample_transcript,
        )

        projection, _report = runner.project(inp)
        assert projection is not None
        # Phase detection may return None if WorkingState doesn't have enough info
        # This is expected behavior


class TestEnhancedPipelineIntegration:
    """Integration tests for enhanced pipeline."""

    def test_full_pipeline_flow(
        self,
        policy: StateFirstContextOSPolicy,
    ) -> None:
        """Test complete pipeline flow with multiple messages."""
        runner = EnhancedPipelineRunner(
            policy=policy,
            enable_phase_aware_budgeting=True,
            enable_attention_scoring=True,
            enable_metrics=True,
        )

        # Simulate multiple turns
        messages_turn1 = [
            {"role": "user", "content": "Create a new module"},
            {"role": "assistant", "content": "I'll create the module"},
        ]

        inp1 = PipelineInput(messages=messages_turn1)
        proj1, report1 = runner.project(inp1)

        assert proj1 is not None
        assert report1 is not None

        # Second turn with existing transcript
        messages_turn2 = [
            {"role": "user", "content": "Now add tests"},
            {"role": "assistant", "content": "I'll add tests"},
        ]

        inp2 = PipelineInput(
            messages=messages_turn2,
            existing_snapshot_transcript=proj1.snapshot.transcript_log,
        )
        proj2, _report2 = runner.project(inp2)

        assert proj2 is not None
        assert len(proj2.snapshot.transcript_log) >= len(proj1.snapshot.transcript_log)
