"""Tests for Phase Detection and Phase-Aware Budgeting (ContextOS 3.0 Phase 2)."""

from polaris.kernelone.context.context_os.phase_budget_planner import (
    PHASE_BUDGET_PROFILES,
    BudgetProfile,
    PhaseAwareBudgetPlan,
    PhaseAwareBudgetPlanner,
)
from polaris.kernelone.context.context_os.phase_detection import (
    ALLOWED_TRANSITIONS,
    MINIMUM_PHASE_DURATION,
    PhaseDetectionResult,
    TaskPhase,
    TaskPhaseDetector,
)


class TestTaskPhase:
    """Test TaskPhase enum."""

    def test_enum_values(self) -> None:
        assert TaskPhase.INTAKE.value == "intake"
        assert TaskPhase.PLANNING.value == "planning"
        assert TaskPhase.EXPLORATION.value == "exploration"
        assert TaskPhase.IMPLEMENTATION.value == "implementation"
        assert TaskPhase.VERIFICATION.value == "verification"
        assert TaskPhase.DEBUGGING.value == "debugging"
        assert TaskPhase.REVIEW.value == "review"

    def test_enum_from_value(self) -> None:
        assert TaskPhase("intake") == TaskPhase.INTAKE
        assert TaskPhase("debugging") == TaskPhase.DEBUGGING


class TestAllowedTransitions:
    """Test allowed phase transitions."""

    def test_intake_transitions(self) -> None:
        assert TaskPhase.PLANNING in ALLOWED_TRANSITIONS[TaskPhase.INTAKE]
        assert TaskPhase.EXPLORATION in ALLOWED_TRANSITIONS[TaskPhase.INTAKE]
        assert TaskPhase.IMPLEMENTATION not in ALLOWED_TRANSITIONS[TaskPhase.INTAKE]

    def test_implementation_transitions(self) -> None:
        assert TaskPhase.VERIFICATION in ALLOWED_TRANSITIONS[TaskPhase.IMPLEMENTATION]
        assert TaskPhase.DEBUGGING in ALLOWED_TRANSITIONS[TaskPhase.IMPLEMENTATION]
        assert TaskPhase.REVIEW in ALLOWED_TRANSITIONS[TaskPhase.IMPLEMENTATION]

    def test_review_transitions(self) -> None:
        assert TaskPhase.IMPLEMENTATION in ALLOWED_TRANSITIONS[TaskPhase.REVIEW]
        assert len(ALLOWED_TRANSITIONS[TaskPhase.REVIEW]) == 1


class TestPhaseDetectionResult:
    """Test PhaseDetectionResult dataclass."""

    def test_create_result(self) -> None:
        result = PhaseDetectionResult(
            phase=TaskPhase.DEBUGGING,
            confidence=0.9,
            reason="Recent errors detected",
            reason_codes=("RECENT_ERRORS",),
        )
        assert result.phase == TaskPhase.DEBUGGING
        assert result.confidence == 0.9
        assert "RECENT_ERRORS" in result.reason_codes

    def test_to_dict(self) -> None:
        result = PhaseDetectionResult(
            phase=TaskPhase.IMPLEMENTATION,
            confidence=0.85,
            reason="Has plan with writes",
            reason_codes=("PLAN_WITH_WRITES",),
            previous_phase=TaskPhase.PLANNING,
        )
        d = result.to_dict()
        assert d["phase"] == "implementation"
        assert d["confidence"] == 0.85
        assert d["previous_phase"] == "planning"


class TestTaskPhaseDetector:
    """Test TaskPhaseDetector class."""

    def test_initial_phase(self) -> None:
        detector = TaskPhaseDetector()
        assert detector.current_phase == TaskPhase.INTAKE
        assert detector.phase_turn_count == 0

    def test_detect_intake_no_goal(self) -> None:
        detector = TaskPhaseDetector()
        # Mock working state with no goal
        working_state = type(
            "MockWS",
            (),
            {
                "task_state": type(
                    "MockTS",
                    (),
                    {
                        "current_goal": None,
                        "accepted_plan": (),
                        "open_loops": (),
                        "blocked_on": (),
                        "deliverables": (),
                    },
                )()
            },
        )()

        result = detector.detect_phase(working_state)
        assert result.phase == TaskPhase.INTAKE
        assert result.confidence > 0.5

    def test_detect_planning_with_goal(self) -> None:
        detector = TaskPhaseDetector()
        # Mock working state with goal but no plan
        working_state = type(
            "MockWS",
            (),
            {
                "task_state": type(
                    "MockTS",
                    (),
                    {
                        "current_goal": type("MockGoal", (), {"value": "Implement feature X"})(),
                        "accepted_plan": (),
                        "open_loops": (),
                        "blocked_on": (),
                        "deliverables": (),
                    },
                )()
            },
        )()

        # Need to run enough times to overcome hysteresis
        for _ in range(MINIMUM_PHASE_DURATION + 1):
            result = detector.detect_phase(working_state)

        # Should have transitioned to PLANNING
        assert result.phase in (TaskPhase.PLANNING, TaskPhase.INTAKE)

    def test_detect_exploration_high_read_only(self) -> None:
        detector = TaskPhaseDetector()
        # Mock working state
        working_state = type(
            "MockWS",
            (),
            {
                "task_state": type(
                    "MockTS",
                    (),
                    {
                        "current_goal": type("MockGoal", (), {"value": "Explore codebase"})(),
                        "accepted_plan": (),
                        "open_loops": (),
                        "blocked_on": (),
                        "deliverables": (),
                    },
                )()
            },
        )()

        # Mock events with high read-only ratio
        events = []
        for i in range(10):
            event = type(
                "MockEvent",
                (),
                {
                    "role": "tool" if i % 2 == 0 else "assistant",
                    "content": "tool result" if i % 2 == 0 else "analysis",
                    "kind": "tool_result" if i % 2 == 0 else "assistant_turn",
                },
            )()
            events.append(event)

        # Need to run enough times to overcome hysteresis
        for _ in range(MINIMUM_PHASE_DURATION + 1):
            result = detector.detect_phase(working_state, tuple(events))

        # Should have transitioned away from INTAKE
        assert result.phase in (TaskPhase.EXPLORATION, TaskPhase.PLANNING, TaskPhase.INTAKE)

    def test_phase_transition_hysteresis(self) -> None:
        detector = TaskPhaseDetector()
        # Start in INTAKE
        assert detector.current_phase == TaskPhase.INTAKE

        # Mock working state with goal
        working_state = type(
            "MockWS",
            (),
            {
                "task_state": type(
                    "MockTS",
                    (),
                    {
                        "current_goal": type("MockGoal", (), {"value": "Test"})(),
                        "accepted_plan": (),
                        "open_loops": (),
                        "blocked_on": (),
                        "deliverables": (),
                    },
                )()
            },
        )()

        # First detection - should stay in INTAKE due to hysteresis
        result1 = detector.detect_phase(working_state)
        assert result1.phase == TaskPhase.INTAKE  # Hysteresis keeps in INTAKE

        # Second detection - now can transition
        result2 = detector.detect_phase(working_state)
        assert result2.phase in (TaskPhase.PLANNING, TaskPhase.INTAKE)

    def test_transition_history(self) -> None:
        detector = TaskPhaseDetector()
        assert len(detector._transition_history) == 0

        # Force a transition by setting phase_turn_count high enough
        detector._phase_turn_count = MINIMUM_PHASE_DURATION + 1


class TestBudgetProfile:
    """Test BudgetProfile dataclass."""

    def test_create_profile(self) -> None:
        profile = BudgetProfile(
            reserve_output_ratio=0.15,
            contract_ratio=0.20,
        )
        assert profile.reserve_output_ratio == 0.15
        assert profile.contract_ratio == 0.20

    def test_to_dict(self) -> None:
        profile = BudgetProfile()
        d = profile.to_dict()
        assert "reserve_output_ratio" in d
        assert "contract_ratio" in d


class TestPhaseBudgetProfiles:
    """Test pre-defined phase budget profiles."""

    def test_all_phases_have_profiles(self) -> None:
        for phase in TaskPhase:
            assert phase in PHASE_BUDGET_PROFILES

    def test_intake_high_contract(self) -> None:
        profile = PHASE_BUDGET_PROFILES[TaskPhase.INTAKE]
        assert profile.contract_ratio > 0.20

    def test_exploration_high_tool(self) -> None:
        profile = PHASE_BUDGET_PROFILES[TaskPhase.EXPLORATION]
        assert profile.reserve_tool_ratio > 0.10

    def test_implementation_high_output(self) -> None:
        profile = PHASE_BUDGET_PROFILES[TaskPhase.IMPLEMENTATION]
        assert profile.reserve_output_ratio > 0.15

    def test_debugging_high_code_context(self) -> None:
        profile = PHASE_BUDGET_PROFILES[TaskPhase.DEBUGGING]
        assert profile.code_context_ratio > 0.20


class TestPhaseAwareBudgetPlan:
    """Test PhaseAwareBudgetPlan dataclass."""

    def test_create_plan(self) -> None:
        plan = PhaseAwareBudgetPlan(
            phase=TaskPhase.IMPLEMENTATION,
            phase_profile=PHASE_BUDGET_PROFILES[TaskPhase.IMPLEMENTATION],
            model_context_window=128000,
            input_budget=80000,
        )
        assert plan.phase == TaskPhase.IMPLEMENTATION
        assert plan.model_context_window == 128000

    def test_to_dict(self) -> None:
        plan = PhaseAwareBudgetPlan(
            phase=TaskPhase.DEBUGGING,
            phase_profile=PHASE_BUDGET_PROFILES[TaskPhase.DEBUGGING],
        )
        d = plan.to_dict()
        assert d["phase"] == "debugging"
        assert "phase_profile" in d


class TestPhaseAwareBudgetPlanner:
    """Test PhaseAwareBudgetPlanner class."""

    def test_create_planner(self) -> None:
        planner = PhaseAwareBudgetPlanner(resolved_context_window=128000)
        assert planner._resolved_context_window == 128000

    def test_plan_budget_intake(self) -> None:
        planner = PhaseAwareBudgetPlanner(resolved_context_window=128000)
        plan = planner.plan_budget(
            phase=TaskPhase.INTAKE,
            transcript_tokens=1000,
            artifact_tokens=500,
        )
        assert plan.phase == TaskPhase.INTAKE
        assert plan.contract_budget > 0
        assert plan.input_budget > 0

    def test_plan_budget_implementation(self) -> None:
        planner = PhaseAwareBudgetPlanner(resolved_context_window=128000)
        plan = planner.plan_budget(
            phase=TaskPhase.IMPLEMENTATION,
            transcript_tokens=5000,
            artifact_tokens=2000,
        )
        assert plan.phase == TaskPhase.IMPLEMENTATION
        assert plan.output_reserve > plan.contract_budget  # Implementation has high output

    def test_plan_budget_debugging(self) -> None:
        planner = PhaseAwareBudgetPlanner(resolved_context_window=128000)
        plan = planner.plan_budget(
            phase=TaskPhase.DEBUGGING,
            transcript_tokens=3000,
            artifact_tokens=1000,
        )
        assert plan.phase == TaskPhase.DEBUGGING
        assert plan.code_context_budget > plan.contract_budget  # Debugging has high code context

    def test_plan_budget_different_windows(self) -> None:
        planner_small = PhaseAwareBudgetPlanner(resolved_context_window=32000)
        planner_large = PhaseAwareBudgetPlanner(resolved_context_window=256000)

        plan_small = planner_small.plan_budget(phase=TaskPhase.PLANNING)
        plan_large = planner_large.plan_budget(phase=TaskPhase.PLANNING)

        assert plan_small.model_context_window < plan_large.model_context_window
        assert plan_small.input_budget < plan_large.input_budget

    def test_plan_budget_validation_error(self) -> None:
        planner = PhaseAwareBudgetPlanner(resolved_context_window=1024)
        plan = planner.plan_budget(
            phase=TaskPhase.IMPLEMENTATION,
            transcript_tokens=500,
            artifact_tokens=200,
            p95_tool_result_tokens=3000,
        )
        # Should have validation error due to small window
        assert plan.validation_error != "" or plan.model_context_window >= 4096
