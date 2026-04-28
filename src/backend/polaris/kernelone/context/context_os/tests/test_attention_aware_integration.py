"""Integration tests for Attention-Aware WindowCollector (ContextOS 3.0 Phase 3).

These tests verify that AttentionAwareWindowCollector integrates correctly
with the existing pipeline infrastructure.
"""

import pytest
from polaris.kernelone.context.context_os.attention.scorer import ScoringContext
from polaris.kernelone.context.context_os.decision_log import ContextDecisionLog
from polaris.kernelone.context.context_os.models_v2 import (
    BudgetPlanV2 as BudgetPlan,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.context_os.phase_detection import TaskPhase
from polaris.kernelone.context.context_os.pipeline.attention_aware_stages import (
    AttentionAwareWindowCollector,
)
from polaris.kernelone.context.context_os.pipeline.contracts import (
    BudgetPlannerOutput,
    CanonicalizerOutput,
    PipelineInput,
    StatePatcherOutput,
)
from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy


@pytest.fixture
def policy() -> StateFirstContextOSPolicy:
    """Create a test policy."""
    return StateFirstContextOSPolicy()


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
            content="I'll implement feature X by creating a new module",
            kind="assistant_turn",
            route="PATCH",
            source_turns=("t0",),
        ),
        TranscriptEvent(
            event_id="evt_003",
            sequence=2,
            role="tool",
            content="File created: feature_x.py",
            kind="tool_result",
            route="PATCH",
            source_turns=("t0",),
            artifact_id="art_001",
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
            accepted_plan=(),
            open_loops=(),
            blocked_on=(),
            deliverables=(),
        ),
        user_profile=UserProfileState(),
        active_entities=(),
        active_artifacts=("art_001",),
        decision_log=(),
    )


@pytest.fixture
def budget_plan() -> BudgetPlan:
    """Create a test budget plan."""
    return BudgetPlan(
        model_context_window=128000,
        output_reserve=23040,
        tool_reserve=12800,
        safety_margin=6400,
        input_budget=85760,
        soft_limit=47168,
        hard_limit=61747,
        emergency_limit=72896,
    )


class TestAttentionAwareWindowCollector:
    """Test AttentionAwareWindowCollector integration."""

    def test_create_collector(self, policy: StateFirstContextOSPolicy) -> None:
        """Test collector creation with attention scoring enabled."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=True,
        )
        assert collector._enable_attention_scoring is True
        assert collector._scorer is not None
        assert collector._ranker is not None

    def test_create_collector_disabled(self, policy: StateFirstContextOSPolicy) -> None:
        """Test collector creation with attention scoring disabled."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=False,
        )
        assert collector._enable_attention_scoring is False
        assert collector._scorer is None
        assert collector._ranker is None

    def test_process_with_attention_scoring(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        budget_plan: BudgetPlan,
    ) -> None:
        """Test processing with attention scoring enabled."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=True,
            current_phase=TaskPhase.IMPLEMENTATION,
        )

        # Create mock outputs
        budget_out = BudgetPlannerOutput(budget_plan=budget_plan)
        patcher_out = StatePatcherOutput(working_state=working_state)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )
        inp = PipelineInput(messages=[])

        # Process with decision log
        decision_log = ContextDecisionLog()
        window_out = collector.process(budget_out, patcher_out, canon_out, inp, decision_log)

        assert len(window_out.active_window) > 0
        assert decision_log.count > 0

    def test_process_without_attention_scoring(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        budget_plan: BudgetPlan,
    ) -> None:
        """Test processing with attention scoring disabled (fallback to static rules)."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=False,
        )

        # Create mock outputs
        budget_out = BudgetPlannerOutput(budget_plan=budget_plan)
        patcher_out = StatePatcherOutput(working_state=working_state)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )
        inp = PipelineInput(messages=[])

        # Process with decision log
        decision_log = ContextDecisionLog()
        window_out = collector.process(budget_out, patcher_out, canon_out, inp, decision_log)

        assert len(window_out.active_window) > 0
        assert decision_log.count > 0

    def test_process_records_attention_scores(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        budget_plan: BudgetPlan,
    ) -> None:
        """Test that attention scores are recorded in decision log."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=True,
            current_phase=TaskPhase.IMPLEMENTATION,
        )

        budget_out = BudgetPlannerOutput(budget_plan=budget_plan)
        patcher_out = StatePatcherOutput(working_state=working_state)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )
        inp = PipelineInput(messages=[])

        decision_log = ContextDecisionLog()
        collector.process(budget_out, patcher_out, canon_out, inp, decision_log)

        # Check that decisions have attention scores
        decisions = decision_log.get_decisions()
        included_decisions = [d for d in decisions if d.decision_type.value == "include_full"]
        assert len(included_decisions) > 0
        # At least one decision should have an attention score
        assert any(d.attention_score is not None for d in included_decisions)

    def test_build_scoring_context(
        self,
        policy: StateFirstContextOSPolicy,
        working_state: WorkingState,
    ) -> None:
        """Test scoring context building."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=True,
            current_phase=TaskPhase.IMPLEMENTATION,
        )

        context = collector._build_scoring_context(working_state)

        assert context.current_goal == "Implement feature X"
        assert context.current_phase == TaskPhase.IMPLEMENTATION
        assert "art_001" in str(working_state.active_artifacts)

    def test_current_phase_property(
        self,
        policy: StateFirstContextOSPolicy,
    ) -> None:
        """Test current_phase property."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=True,
            current_phase=TaskPhase.EXPLORATION,
        )
        assert collector.current_phase == TaskPhase.EXPLORATION

    def test_current_phase_setter(
        self,
        policy: StateFirstContextOSPolicy,
    ) -> None:
        """Test current_phase setter."""
        collector = AttentionAwareWindowCollector(
            policy=policy,
            enable_attention_scoring=True,
            current_phase=TaskPhase.INTAKE,
        )
        collector.current_phase = TaskPhase.DEBUGGING
        assert collector.current_phase == TaskPhase.DEBUGGING


class TestScoringContext:
    """Test ScoringContext dataclass."""

    def test_create_context(self) -> None:
        """Test context creation."""
        context = ScoringContext(
            current_intent="implement feature X",
            current_goal="implement feature X",
            acceptance_criteria=("must pass tests",),
            hard_constraints=("no breaking changes",),
            current_task_id="task_001",
            current_phase=TaskPhase.IMPLEMENTATION,
        )
        assert context.current_intent == "implement feature X"
        assert context.current_phase == TaskPhase.IMPLEMENTATION
        assert "must pass tests" in context.acceptance_criteria

    def test_default_values(self) -> None:
        """Test default values."""
        context = ScoringContext()
        assert context.current_intent == ""
        assert context.current_phase == TaskPhase.INTAKE
        assert context.current_time == 0.0
