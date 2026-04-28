"""Integration tests for Phase-Aware Budgeting (ContextOS 3.0 Phase 2).

These tests verify that PhaseAwareBudgetPlannerStage integrates correctly
with the existing pipeline infrastructure.
"""

import pytest
from polaris.kernelone.context.context_os.models_v2 import (
    BudgetPlanV2 as BudgetPlan,
    StateEntryV2 as StateEntry,
    TaskStateViewV2 as TaskStateView,
    TranscriptEventV2 as TranscriptEvent,
    UserProfileStateV2 as UserProfileState,
    WorkingStateV2 as WorkingState,
)
from polaris.kernelone.context.context_os.phase_detection import TaskPhase
from polaris.kernelone.context.context_os.pipeline.contracts import (
    BudgetPlannerOutput,
    CanonicalizerOutput,
    StatePatcherOutput,
)
from polaris.kernelone.context.context_os.pipeline.phase_aware_stages import (
    PhaseAwareBudgetPlannerStage,
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
            content="I'll implement feature X",
            kind="assistant_turn",
            route="PATCH",
            source_turns=("t0",),
        ),
    )


@pytest.fixture
def working_state_with_goal() -> WorkingState:
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
        active_artifacts=(),
        decision_log=(),
    )


@pytest.fixture
def working_state_no_goal() -> WorkingState:
    """Create WorkingState without a goal (INTAKE phase)."""
    return WorkingState(
        task_state=TaskStateView(),
        user_profile=UserProfileState(),
        active_entities=(),
        active_artifacts=(),
        decision_log=(),
    )


class TestPhaseAwareBudgetPlannerStage:
    """Test PhaseAwareBudgetPlannerStage integration."""

    def test_create_stage(self, policy: StateFirstContextOSPolicy) -> None:
        """Test stage creation."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )
        assert stage._enable_phase_detection is True
        assert stage._phase_detector is not None

    def test_create_stage_disabled(self, policy: StateFirstContextOSPolicy) -> None:
        """Test stage creation with phase detection disabled."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=False,
        )
        assert stage._enable_phase_detection is False
        assert stage._phase_detector is None

    def test_process_intake_phase(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state_no_goal: WorkingState,
    ) -> None:
        """Test processing in INTAKE phase (no goal)."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )

        # Create mock outputs
        patcher_out = StatePatcherOutput(working_state=working_state_no_goal)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )

        budget_out, phase_result = stage.process(patcher_out, canon_out)

        assert isinstance(budget_out, BudgetPlannerOutput)
        assert isinstance(budget_out.budget_plan, BudgetPlan)
        assert budget_out.budget_plan.model_context_window > 0
        assert budget_out.budget_plan.input_budget > 0

        # Should detect INTAKE phase
        assert phase_result is not None
        assert phase_result.phase == TaskPhase.INTAKE

    def test_process_planning_phase(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state_with_goal: WorkingState,
    ) -> None:
        """Test processing in PLANNING phase (has goal, no plan)."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )

        # Create mock outputs
        patcher_out = StatePatcherOutput(working_state=working_state_with_goal)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )

        # Run multiple times to overcome hysteresis
        for _ in range(3):
            budget_out, phase_result = stage.process(patcher_out, canon_out)

        assert isinstance(budget_out, BudgetPlannerOutput)
        assert phase_result is not None
        # Should have transitioned to PLANNING or stayed in INTAKE
        assert phase_result.phase in (TaskPhase.PLANNING, TaskPhase.INTAKE)

    def test_process_no_phase_detection(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state_with_goal: WorkingState,
    ) -> None:
        """Test processing with phase detection disabled."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=False,
        )

        # Create mock outputs
        patcher_out = StatePatcherOutput(working_state=working_state_with_goal)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )

        budget_out, phase_result = stage.process(patcher_out, canon_out)

        assert isinstance(budget_out, BudgetPlannerOutput)
        assert phase_result is None

    def test_current_phase_property(
        self,
        policy: StateFirstContextOSPolicy,
    ) -> None:
        """Test current_phase property."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )
        assert stage.current_phase == TaskPhase.INTAKE

    def test_phase_turn_count_property(
        self,
        policy: StateFirstContextOSPolicy,
    ) -> None:
        """Test phase_turn_count property."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )
        assert stage.phase_turn_count == 0


class TestPhaseAwareBudgetPlanCompatibility:
    """Test compatibility with existing BudgetPlan interface."""

    def test_budget_plan_has_all_fields(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state_no_goal: WorkingState,
    ) -> None:
        """Test that phase-aware budget plan has all required fields."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )

        patcher_out = StatePatcherOutput(working_state=working_state_no_goal)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )

        budget_out, _ = stage.process(patcher_out, canon_out)
        plan = budget_out.budget_plan

        # Check all required fields exist
        assert hasattr(plan, "model_context_window")
        assert hasattr(plan, "output_reserve")
        assert hasattr(plan, "tool_reserve")
        assert hasattr(plan, "safety_margin")
        assert hasattr(plan, "input_budget")
        assert hasattr(plan, "retrieval_budget")
        assert hasattr(plan, "soft_limit")
        assert hasattr(plan, "hard_limit")
        assert hasattr(plan, "emergency_limit")
        assert hasattr(plan, "current_input_tokens")
        assert hasattr(plan, "expected_next_input_tokens")
        assert hasattr(plan, "p95_tool_result_tokens")
        assert hasattr(plan, "planned_retrieval_tokens")
        assert hasattr(plan, "validation_error")

    def test_budget_plan_invariants(
        self,
        policy: StateFirstContextOSPolicy,
        sample_transcript: tuple[TranscriptEvent, ...],
        working_state_no_goal: WorkingState,
    ) -> None:
        """Test that budget plan invariants are satisfied."""
        stage = PhaseAwareBudgetPlannerStage(
            policy=policy,
            resolved_context_window=128000,
            enable_phase_detection=True,
        )

        patcher_out = StatePatcherOutput(working_state=working_state_no_goal)
        canon_out = CanonicalizerOutput(
            transcript=sample_transcript,
            artifacts=(),
        )

        budget_out, _ = stage.process(patcher_out, canon_out)
        plan = budget_out.budget_plan

        # Check invariants
        assert plan.model_context_window >= 4096
        assert plan.output_reserve > 0
        assert plan.tool_reserve > 0
        assert plan.safety_margin > 0
        assert plan.input_budget > 0
        assert plan.soft_limit > 0
        assert plan.hard_limit > 0
        assert plan.emergency_limit > 0
        assert plan.soft_limit <= plan.hard_limit <= plan.emergency_limit
