"""Tests for adaptive dialogue strategy selection.

These tests verify the S2-3 Adaptive Dialogue Strategy implementation
according to the blueprint requirements.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.dialogue.adaptive_strategy import (
    AdaptiveDialogueStrategy,
    ExploitationStrategy,
    ExplorationStrategy,
    NegotiationStrategy,
    StrategySelection,
    TutorialStrategy,
)
from polaris.kernelone.dialogue.turn_context import (
    Message,
    TaskPhase,
    TaskState,
    TurnContext,
    UserExpertise,
    UserProfile,
)


def make_context(
    *,
    task_phase: TaskPhase = TaskPhase.IMPLEMENTATION,
    user_expertise: UserExpertise = UserExpertise.INTERMEDIATE,
    has_blockers: bool = False,
    budget_remaining: float = 1.0,
    message_count: int = 5,
    complexity: float = 0.5,
) -> TurnContext:
    """Helper to create a TurnContext for testing."""
    messages = tuple(
        Message(role="user" if i % 2 == 0 else "assistant", content=f"Message {i}") for i in range(message_count)
    )
    return TurnContext(
        messages=messages,
        user_profile=UserProfile(
            expertise=user_expertise,
            interaction_count=message_count,
        ),
        task_state=TaskState(
            phase=task_phase,
            task_complexity=complexity,
            budget_remaining_pct=budget_remaining,
            blocker_indicators=("blocker1",) if has_blockers else (),
        ),
        turn_index=0,
        session_id="test-session",
        workspace="/test",
    )


class TestDialogueStrategy:
    """Test the DialogueStrategy abstract class and implementations."""

    def test_exploration_strategy_get_name(self) -> None:
        """ExplorationStrategy returns correct name."""
        strategy = ExplorationStrategy()
        assert strategy.get_strategy_name() == "exploration"

    def test_exploitation_strategy_get_name(self) -> None:
        """ExploitationStrategy returns correct name."""
        strategy = ExploitationStrategy()
        assert strategy.get_strategy_name() == "exploitation"

    def test_negotiation_strategy_get_name(self) -> None:
        """NegotiationStrategy returns correct name."""
        strategy = NegotiationStrategy()
        assert strategy.get_strategy_name() == "negotiation"

    def test_tutorial_strategy_get_name(self) -> None:
        """TutorialStrategy returns correct name."""
        strategy = TutorialStrategy()
        assert strategy.get_strategy_name() == "tutorial"

    @pytest.mark.asyncio
    async def test_exploration_strategy_select_action_with_blockers(self) -> None:
        """ExplorationStrategy selects analyze_blocker when blockers present."""
        strategy = ExplorationStrategy()
        context = make_context(has_blockers=True)
        action = await strategy.select_next_action(context)
        assert action == "analyze_blocker"

    @pytest.mark.asyncio
    async def test_exploration_strategy_select_action_complex_task(self) -> None:
        """ExplorationStrategy selects decompose_task for complex tasks."""
        strategy = ExplorationStrategy()
        context = make_context(complexity=0.8)
        action = await strategy.select_next_action(context)
        assert action == "decompose_task"

    @pytest.mark.asyncio
    async def test_exploration_strategy_select_action_early_conversation(self) -> None:
        """ExplorationStrategy selects gather_requirements early."""
        strategy = ExplorationStrategy()
        context = make_context(message_count=2)
        action = await strategy.select_next_action(context)
        assert action == "gather_requirements"

    @pytest.mark.asyncio
    async def test_exploitation_strategy_select_action_low_budget(self) -> None:
        """ExploitationStrategy selects execute_direct when low budget."""
        strategy = ExploitationStrategy()
        context = make_context(budget_remaining=0.1)
        action = await strategy.select_next_action(context)
        assert action == "execute_direct"

    @pytest.mark.asyncio
    async def test_exploitation_strategy_select_action_implementation(self) -> None:
        """ExploitationStrategy selects implement_solution in implementation phase."""
        strategy = ExploitationStrategy()
        context = make_context(task_phase=TaskPhase.IMPLEMENTATION)
        action = await strategy.select_next_action(context)
        assert action == "implement_solution"

    @pytest.mark.asyncio
    async def test_negotiation_strategy_select_action(self) -> None:
        """NegotiationStrategy selects find_common_ground."""
        strategy = NegotiationStrategy()
        context = make_context()
        action = await strategy.select_next_action(context)
        assert action == "find_common_ground"

    @pytest.mark.asyncio
    async def test_tutorial_strategy_select_action_novice_first_interaction(self) -> None:
        """TutorialStrategy selects explain_concept for novice first interaction."""
        strategy = TutorialStrategy()
        context = make_context(
            user_expertise=UserExpertise.NOVICE,
            message_count=1,
        )
        action = await strategy.select_next_action(context)
        assert action == "explain_concept"

    @pytest.mark.asyncio
    async def test_tutorial_strategy_select_action_later_conversation(self) -> None:
        """TutorialStrategy selects summarize_progress later in conversation."""
        strategy = TutorialStrategy()
        context = make_context(message_count=10)
        action = await strategy.select_next_action(context)
        assert action == "summarize_progress"


class TestStrategySelection:
    """Test the StrategySelection dataclass."""

    def test_strategy_selection_creation(self) -> None:
        """StrategySelection can be created with required fields."""
        selection = StrategySelection(
            strategy_name="exploration",
            confidence=0.85,
            reasoning="Test reasoning",
        )
        assert selection.strategy_name == "exploration"
        assert selection.confidence == 0.85
        assert selection.reasoning == "Test reasoning"
        assert selection.alternative_strategies == ()

    def test_strategy_selection_with_alternatives(self) -> None:
        """StrategySelection can include alternative strategies."""
        selection = StrategySelection(
            strategy_name="exploitation",
            confidence=0.75,
            reasoning="Test",
            alternative_strategies=("exploration", "negotiation", "tutorial"),
        )
        assert len(selection.alternative_strategies) == 3


class TestAdaptiveDialogueStrategy:
    """Test the AdaptiveDialogueStrategy selector."""

    @pytest.fixture
    def selector(self) -> AdaptiveDialogueStrategy:
        """Create an AdaptiveDialogueStrategy instance."""
        return AdaptiveDialogueStrategy()

    @pytest.mark.asyncio
    async def test_select_tutorial_for_novice(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects tutorial when user is novice and early conversation."""
        context = make_context(
            user_expertise=UserExpertise.NOVICE,
            message_count=2,
        )
        result = await selector.select_strategy(context)
        assert result.strategy_name == "tutorial"
        assert result.confidence == 0.85

    @pytest.mark.asyncio
    async def test_select_negotiation_for_negotiation_phase(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects negotiation when task phase is negotiation."""
        context = make_context(task_phase=TaskPhase.NEGOTIATION)
        result = await selector.select_strategy(context)
        assert result.strategy_name == "negotiation"
        assert result.confidence == 0.80

    @pytest.mark.asyncio
    async def test_select_exploration_for_exploration_phase(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects exploration when task phase is exploration."""
        context = make_context(task_phase=TaskPhase.EXPLORATION)
        result = await selector.select_strategy(context)
        assert result.strategy_name == "exploration"
        assert result.confidence == 0.75

    @pytest.mark.asyncio
    async def test_select_exploration_with_blockers(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects exploration with high confidence when blockers present."""
        context = make_context(has_blockers=True)
        result = await selector.select_strategy(context)
        assert result.strategy_name == "exploration"
        assert result.confidence == 0.90

    @pytest.mark.asyncio
    async def test_select_exploitation_for_implementation_no_blockers(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects exploitation for implementation without blockers."""
        context = make_context(
            task_phase=TaskPhase.IMPLEMENTATION,
            has_blockers=False,
        )
        result = await selector.select_strategy(context)
        assert result.strategy_name == "exploitation"
        assert result.confidence == 0.70

    @pytest.mark.asyncio
    async def test_select_exploitation_expert_user(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects exploitation with higher confidence for expert users."""
        context = make_context(
            task_phase=TaskPhase.IMPLEMENTATION,
            user_expertise=UserExpertise.EXPERT,
        )
        result = await selector.select_strategy(context)
        assert result.strategy_name == "exploitation"
        assert result.confidence == 0.80

    @pytest.mark.asyncio
    async def test_select_tutorial_complex_novice(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive selects tutorial for complex tasks with novice users.

        Priority 5 applies when no higher priority matches.
        Using VERIFICATION phase avoids Priority 4 (implementation + no blockers).
        """
        context = make_context(
            user_expertise=UserExpertise.NOVICE,
            complexity=0.8,
            task_phase=TaskPhase.VERIFICATION,  # Not IMPLEMENTATION to avoid Priority 4
        )
        result = await selector.select_strategy(context)
        assert result.strategy_name == "tutorial"
        assert result.confidence == 0.75

    @pytest.mark.asyncio
    async def test_default_fallback_to_exploration(self, selector: AdaptiveDialogueStrategy) -> None:
        """Adaptive falls back to exploration when no specific indicators match."""
        context = make_context(
            task_phase=TaskPhase.VERIFICATION,
            user_expertise=UserExpertise.INTERMEDIATE,
            has_blockers=False,
            complexity=0.4,
        )
        result = await selector.select_strategy(context)
        assert result.strategy_name == "exploration"
        assert result.confidence == 0.60

    @pytest.mark.asyncio
    async def test_alternative_strategies_included(self, selector: AdaptiveDialogueStrategy) -> None:
        """StrategySelection includes alternative strategies."""
        context = make_context()
        result = await selector.select_strategy(context)
        assert len(result.alternative_strategies) == 3
        assert "tutorial" in result.alternative_strategies
        assert "negotiation" in result.alternative_strategies
        assert "exploration" in result.alternative_strategies

    def test_get_strategy_valid(self, selector: AdaptiveDialogueStrategy) -> None:
        """get_strategy returns correct strategy instance."""
        strategy = selector.get_strategy("exploration")
        assert isinstance(strategy, ExplorationStrategy)

    def test_get_strategy_invalid(self, selector: AdaptiveDialogueStrategy) -> None:
        """get_strategy raises ValueError for invalid strategy name."""
        with pytest.raises(ValueError, match="Unknown strategy"):
            selector.get_strategy("invalid_strategy")

    @pytest.mark.asyncio
    async def test_analyze_context_metrics(self, selector: AdaptiveDialogueStrategy) -> None:
        """_analyze_context returns correct metrics."""
        context = make_context(
            user_expertise=UserExpertise.EXPERT,
            task_phase=TaskPhase.EXPLORATION,
            has_blockers=True,
            complexity=0.8,
        )
        analysis = await selector._analyze_context(context)

        assert analysis["is_expert"] is True
        assert analysis["is_exploration_phase"] is True
        assert analysis["has_blockers"] is True
        assert analysis["is_complex"] is True
        assert analysis["is_novice"] is False
        assert analysis["is_low_budget"] is False


class TestTurnContext:
    """Test the TurnContext dataclass."""

    def test_turn_context_message_count(self) -> None:
        """message_count returns correct count."""
        context = make_context(message_count=7)
        assert context.message_count == 7

    def test_turn_context_last_user_message(self) -> None:
        """last_user_message returns content of last user message."""
        messages = (
            Message(role="assistant", content="Assistant 1"),
            Message(role="user", content="User question"),
            Message(role="assistant", content="Assistant 2"),
        )
        context = TurnContext(
            messages=messages,
            user_profile=UserProfile(),
            task_state=TaskState(),
        )
        assert context.last_user_message == "User question"

    def test_turn_context_last_user_message_empty(self) -> None:
        """last_user_message returns empty string when no user messages."""
        messages = (Message(role="assistant", content="Assistant only"),)
        context = TurnContext(
            messages=messages,
            user_profile=UserProfile(),
            task_state=TaskState(),
        )
        assert context.last_user_message == ""

    def test_turn_context_has_blockers(self) -> None:
        """has_blockers returns True when blockers present."""
        context = make_context(has_blockers=True)
        assert context.has_blockers is True

    def test_turn_context_no_blockers(self) -> None:
        """has_blockers returns False when no blockers."""
        context = make_context(has_blockers=False)
        assert context.has_blockers is False

    def test_turn_context_is_low_budget(self) -> None:
        """is_low_budget returns True when budget below 20%."""
        context = make_context(budget_remaining=0.15)
        assert context.is_low_budget is True

    def test_turn_context_not_low_budget(self) -> None:
        """is_low_budget returns False when budget above 20%."""
        context = make_context(budget_remaining=0.5)
        assert context.is_low_budget is False

    def test_turn_context_to_dict(self) -> None:
        """to_dict returns correct serialization."""
        context = make_context(message_count=3)
        d = context.to_dict()
        assert d["message_count"] == 3
        assert d["has_blockers"] is False
        assert d["is_low_budget"] is False
