"""Adaptive dialogue strategy selection based on conversation context.

This module implements the S2-3 Adaptive Dialogue Strategy component of
Polaris Phase 2 (协作强化).

Strategy selection is based on context analysis including:
- Task phase and complexity
- User expertise level
- Budget constraints
- Presence of blockers
- Conversation history patterns
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.dialogue.turn_context import TurnContext


@dataclass(frozen=True)
class StrategySelection:
    """Result of strategy selection with confidence and reasoning."""

    strategy_name: str
    confidence: float  # 0.0 to 1.0
    reasoning: str
    alternative_strategies: tuple[str, ...] = field(default_factory=tuple)


class DialogueStrategy(ABC):
    """Abstract base class for dialogue strategies."""

    @abstractmethod
    async def select_next_action(self, context: TurnContext) -> str:
        """Select the next action based on context.

        Args:
            context: The current turn context

        Returns:
            Action identifier for the next step
        """
        ...

    @abstractmethod
    def get_strategy_name(self) -> str:
        """Return the strategy name."""
        ...


class ExplorationStrategy(DialogueStrategy):
    """Strategy for exploring alternative approaches.

    Use when:
    - Task phase is exploration
    - User expertise is novice/intermediate
    - There are blockers present
    - Previous solutions failed
    - Task complexity is high
    """

    async def select_next_action(self, context: TurnContext) -> str:
        """Select an exploratory action."""
        # Explore alternative approaches
        if context.has_blockers:
            return "analyze_blocker"
        if context.task_state.task_complexity > 0.7:
            return "decompose_task"
        if context.message_count < 3:
            return "gather_requirements"
        return "propose_alternatives"

    def get_strategy_name(self) -> str:
        return "exploration"


class ExploitationStrategy(DialogueStrategy):
    """Strategy for exploiting known good solutions.

    Use when:
    - Task phase is implementation
    - User expertise is expert
    - No blockers present
    - Previous solutions worked well
    - Task complexity is low to medium
    """

    async def select_next_action(self, context: TurnContext) -> str:
        """Select a direct action."""
        if context.is_low_budget:
            return "execute_direct"
        if context.task_state.phase.value == "implementation":
            return "implement_solution"
        return "apply_best_practice"

    def get_strategy_name(self) -> str:
        return "exploitation"


class NegotiationStrategy(DialogueStrategy):
    """Strategy for negotiating and finding common ground.

    Use when:
    - Task phase is negotiation
    - Multiple stakeholders involved
    - Conflicting requirements
    - User preferences conflict with constraints
    """

    async def select_next_action(self, context: TurnContext) -> str:
        """Select a negotiation action."""
        return "find_common_ground"

    def get_strategy_name(self) -> str:
        return "negotiation"


class TutorialStrategy(DialogueStrategy):
    """Strategy for guiding users through learning.

    Use when:
    - User expertise is novice
    - Task phase is tutorial
    - User is learning new concepts
    - Interaction count is low
    """

    async def select_next_action(self, context: TurnContext) -> str:
        """Select a tutorial action."""
        if context.user_profile.interaction_count < 2:
            return "explain_concept"
        if context.message_count > 5:
            return "summarize_progress"
        return "guide_next_step"

    def get_strategy_name(self) -> str:
        return "tutorial"


class AdaptiveDialogueStrategy:
    """Selects the best dialogue strategy based on context analysis.

    This is the main entry point for S2-3 Adaptive Dialogue Strategy.
    It analyzes the turn context and selects the most appropriate strategy.

    Strategy selection rules (in priority order):
    1. If user expertise is novice -> TutorialStrategy
    2. If task phase is negotiation -> NegotiationStrategy
    3. If task phase is exploration OR has blockers -> ExplorationStrategy
    4. If task phase is implementation AND no blockers -> ExploitationStrategy
    5. Default -> ExplorationStrategy
    """

    STRATEGIES: dict[str, type[DialogueStrategy]] = {
        "exploration": ExplorationStrategy,
        "exploitation": ExploitationStrategy,
        "negotiation": NegotiationStrategy,
        "tutorial": TutorialStrategy,
    }

    def __init__(self) -> None:
        """Initialize the adaptive strategy selector."""
        self._strategy_instances: dict[str, DialogueStrategy] = {name: cls() for name, cls in self.STRATEGIES.items()}

    async def select_strategy(self, context: TurnContext) -> StrategySelection:
        """Select the best strategy based on context.

        Args:
            context: The current turn context

        Returns:
            StrategySelection with the chosen strategy and alternatives
        """
        analysis = await self._analyze_context(context)
        strategy_name, confidence, reasoning = self._choose_strategy(context, analysis)

        # Get alternative strategies (all except the chosen one)
        alternatives = tuple(name for name in self.STRATEGIES if name != strategy_name)

        return StrategySelection(
            strategy_name=strategy_name,
            confidence=confidence,
            reasoning=reasoning,
            alternative_strategies=alternatives,
        )

    async def _analyze_context(self, context: TurnContext) -> dict[str, Any]:
        """Analyze context and return metrics for strategy selection.

        Args:
            context: The current turn context

        Returns:
            Dict of analysis metrics
        """
        # Compute derived metrics
        is_novice = context.user_profile.expertise.value == "novice"
        is_expert = context.user_profile.expertise.value == "expert"
        is_exploration_phase = context.task_state.phase.value == "exploration"
        is_implementation_phase = context.task_state.phase.value == "implementation"
        is_negotiation_phase = context.task_state.phase.value == "negotiation"
        is_tutorial_phase = context.task_state.phase.value == "tutorial"
        has_blockers = context.has_blockers
        is_low_budget = context.is_low_budget
        is_complex = context.task_state.task_complexity > 0.6

        # Short conversation indicator
        is_early_conversation = context.message_count < 4

        return {
            "is_novice": is_novice,
            "is_expert": is_expert,
            "is_exploration_phase": is_exploration_phase,
            "is_implementation_phase": is_implementation_phase,
            "is_negotiation_phase": is_negotiation_phase,
            "is_tutorial_phase": is_tutorial_phase,
            "has_blockers": has_blockers,
            "is_low_budget": is_low_budget,
            "is_complex": is_complex,
            "is_early_conversation": is_early_conversation,
            "message_count": context.message_count,
            "task_complexity": context.task_state.task_complexity,
        }

    def _choose_strategy(
        self,
        context: TurnContext,
        analysis: dict[str, Any],
    ) -> tuple[str, float, str]:
        """Choose the best strategy based on analysis.

        Returns:
            Tuple of (strategy_name, confidence, reasoning)
        """
        # Priority 1: Tutorial for novices
        if analysis["is_novice"] and analysis["is_early_conversation"]:
            return (
                "tutorial",
                0.85,
                "User is novice and early in conversation; tutorial guidance is most effective.",
            )

        # Priority 2: Negotiation for negotiation phase
        if analysis["is_negotiation_phase"]:
            return (
                "negotiation",
                0.80,
                "Task phase is negotiation; finding common ground is the priority.",
            )

        # Priority 3: Exploration for exploration phase or with blockers
        if analysis["is_exploration_phase"] or analysis["has_blockers"]:
            confidence = 0.90 if analysis["has_blockers"] else 0.75
            reason = (
                "Task has blockers; exploration strategy needed to analyze and resolve."
                if analysis["has_blockers"]
                else "Task phase is exploration; exploring alternatives is appropriate."
            )
            return ("exploration", confidence, reason)

        # Priority 4: Exploitation for implementation with no blockers
        if analysis["is_implementation_phase"] and not analysis["has_blockers"]:
            confidence = 0.80 if analysis["is_expert"] else 0.70
            expertise_note = (
                "User is expert; direct exploitation is most efficient."
                if analysis["is_expert"]
                else "Implementation phase with no blockers; exploit known good approaches."
            )
            return ("exploitation", confidence, expertise_note)

        # Priority 5: Tutorial for complex tasks with novices
        if analysis["is_complex"] and analysis["is_novice"]:
            return (
                "tutorial",
                0.75,
                "Complex task with novice user; tutorial guidance helps avoid blockers.",
            )

        # Default fallback: Exploration
        return (
            "exploration",
            0.60,
            "Default fallback; no specific strategy indicators found.",
        )

    def get_strategy(self, name: str) -> DialogueStrategy:
        """Get a strategy instance by name.

        Args:
            name: Strategy name (exploration, exploitation, negotiation, tutorial)

        Returns:
            Strategy instance

        Raises:
            ValueError: If strategy name is unknown
        """
        if name not in self._strategy_instances:
            available = ", ".join(self.STRATEGIES.keys())
            msg = f"Unknown strategy: {name}. Available: {available}"
            raise ValueError(msg)
        return self._strategy_instances[name]


__all__ = [
    "AdaptiveDialogueStrategy",
    "DialogueStrategy",
    "ExploitationStrategy",
    "ExplorationStrategy",
    "NegotiationStrategy",
    "StrategySelection",
    "TutorialStrategy",
]
