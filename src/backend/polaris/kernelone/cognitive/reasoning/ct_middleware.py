"""Critical Thinking Engine Middleware - Implements ContextOS HOOKs.

This module provides CTEngineMiddleware which integrates CriticalThinkingEngine
with ContextOS lifecycle events via Pluggy hooks.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.kernelone.cognitive.hooks import (
    ContextOSHooksSpec,
    hookimpl,
)
from polaris.kernelone.cognitive.reasoning.engine import CriticalThinkingEngine
from polaris.kernelone.context.context_os.models_v2 import (
    TranscriptEventV2 as TranscriptEvent,
    WorkingStateV2 as WorkingState,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class CTEngineMiddleware(ContextOSHooksSpec):
    """Middleware that integrates Critical Thinking Engine with ContextOS hooks.

    This class implements the ContextOSHooksSpec to provide critical thinking
    capabilities at key ContextOS lifecycle points:
    - on_context_patched: Analyze working state after patch
    - on_before_episode_sealed: Validate episode before sealing
    - on_thinking_phase_started/completed: Track reasoning phases

    Usage:
        # Create and register the middleware
        ct_middleware = CTEngineMiddleware()
        from polaris.kernelone.cognitive.hooks import register_plugin
        register_plugin(ct_middleware, name="ct_engine")

        # Or use the convenience function
        ct_middleware = create_and_register_ct_middleware()
    """

    def __init__(
        self,
        ct_engine: CriticalThinkingEngine | None = None,
        enabled: bool = True,
        min_confidence_threshold: float = 0.6,
        enable_episode_validation: bool = True,
        enable_state_analysis: bool = True,
    ) -> None:
        """Initialize the CT Engine Middleware.

        Args:
            ct_engine: Optional CriticalThinkingEngine instance.
            enabled: Whether the middleware is enabled.
            min_confidence_threshold: Minimum confidence for accepting conclusions.
            enable_episode_validation: Whether to validate episodes before sealing.
            enable_state_analysis: Whether to analyze working state on patch.
        """
        self._ct_engine = ct_engine or CriticalThinkingEngine()
        self._enabled = enabled
        self._min_confidence_threshold = min_confidence_threshold
        self._enable_episode_validation = enable_episode_validation
        self._enable_state_analysis = enable_state_analysis

        # Track reasoning history
        self._reasoning_history: list[dict[str, Any]] = []
        self._last_analysis: dict[str, Any] | None = None

    @hookimpl
    def on_context_patched(
        self,
        working_state: WorkingState,
        transcript: tuple[TranscriptEvent, ...],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Analyze working state after context patch.

        Performs critical thinking analysis on the current goal and task state
        to identify assumptions, risks, and verification steps.

        Args:
            working_state: The newly patched working state.
            transcript: Current transcript log.
            **kwargs: Additional context data.

        Returns:
            Analysis results including assumptions, risks, and recommendations.
        """
        if not self._enabled or not self._enable_state_analysis:
            return None

        try:
            # Extract current goal for analysis
            current_goal = working_state.task_state.current_goal.value if working_state.task_state.current_goal else ""

            if not current_goal:
                logger.debug("No current goal to analyze in on_context_patched")
                return None

            # Build context from working state
            context = self._build_context_from_working_state(working_state)

            # Perform critical thinking analysis
            # Note: This is synchronous; for async, use analyze_with_llm in async context
            import asyncio

            reasoning_chain = asyncio.run(
                self._ct_engine.analyze(
                    conclusion=current_goal,
                    intent_chain=None,  # Could extract from transcript
                    context=context,
                )
            )

            result = {
                "hook": "on_context_patched",
                "goal": current_goal,
                "confidence": reasoning_chain.confidence_level,
                "should_proceed": reasoning_chain.should_proceed,
                "blockers": list(reasoning_chain.blockers),
                "assumptions": [
                    {"text": a.text, "confidence": a.confidence} for a in reasoning_chain.six_questions.assumptions
                ],
                "failure_conditions": list(reasoning_chain.six_questions.failure_conditions),
                "verification_steps": list(reasoning_chain.six_questions.verification_steps),
                "cost_of_error": reasoning_chain.six_questions.cost_of_error,
                "severity": reasoning_chain.six_questions.severity,
            }

            self._last_analysis = result
            self._reasoning_history.append(result)

            logger.debug(
                "CT analysis complete: confidence=%s, should_proceed=%s",
                reasoning_chain.confidence_level,
                reasoning_chain.should_proceed,
            )

            return result

        except (RuntimeError, ValueError) as e:
            logger.exception("Error in on_context_patched CT analysis: %s", e)
            return {"hook": "on_context_patched", "error": str(e)}

    @hookimpl
    def on_before_episode_sealed(
        self,
        episode_events: tuple[TranscriptEvent, ...],
        working_state: WorkingState,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Validate episode before sealing.

        Performs critical thinking analysis on the episode intent and outcome
        to validate whether the episode should be sealed.

        Args:
            episode_events: Events that will be sealed into the episode.
            working_state: Current working state.
            **kwargs: Additional context data.

        Returns:
            Validation results including veto recommendation if applicable.
        """
        if not self._enabled or not self._enable_episode_validation:
            return None

        try:
            if not episode_events:
                return None

            # Extract episode intent and outcome
            intent = working_state.task_state.current_goal.value if working_state.task_state.current_goal else ""

            # Build combined content from episode events
            combined_content = "\n".join(e.content for e in episode_events if e.content)

            if not intent and not combined_content:
                logger.debug("No content to analyze for episode validation")
                return None

            conclusion = intent or f"Episode with {len(episode_events)} events"
            context = f"Episode content summary:\n{combined_content[:500]}"

            # Perform critical thinking analysis
            import asyncio

            reasoning_chain = asyncio.run(
                self._ct_engine.analyze(
                    conclusion=conclusion,
                    intent_chain=None,
                    context=context,
                )
            )

            # Determine if episode sealing should be vetoed
            should_veto = (
                reasoning_chain.six_questions.severity == "critical"
                or reasoning_chain.six_questions.conclusion_probability < 0.3
            )

            result = {
                "hook": "on_before_episode_sealed",
                "episode_event_count": len(episode_events),
                "intent": intent,
                "confidence": reasoning_chain.confidence_level,
                "should_proceed": reasoning_chain.should_proceed,
                "should_veto": should_veto,
                "veto_reason": ("Critical severity or low confidence detected" if should_veto else None),
                "blockers": list(reasoning_chain.blockers),
                "assumptions_count": len(reasoning_chain.six_questions.assumptions),
                "verification_steps": list(reasoning_chain.six_questions.verification_steps),
            }

            self._reasoning_history.append(result)

            logger.debug(
                "Episode validation complete: should_veto=%s, confidence=%s",
                should_veto,
                reasoning_chain.confidence_level,
            )

            return result

        except (RuntimeError, ValueError) as e:
            logger.exception("Error in on_before_episode_sealed validation: %s", e)
            return {"hook": "on_before_episode_sealed", "error": str(e)}

    @hookimpl
    def on_thinking_phase_started(
        self,
        phase_name: str,
        context: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Track thinking phase start.

        Args:
            phase_name: Name of the thinking phase.
            context: Context data for the thinking phase.
            **kwargs: Additional arguments.

        Returns:
            Phase tracking metadata.
        """
        if not self._enabled:
            return None

        result = {
            "hook": "on_thinking_phase_started",
            "phase_name": phase_name,
            "context_keys": list(context.keys()) if context else [],
            "status": "started",
        }

        logger.debug("Thinking phase started: %s", phase_name)
        return result

    @hookimpl
    def on_thinking_phase_completed(
        self,
        phase_name: str,
        result: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Track thinking phase completion.

        Args:
            phase_name: Name of the thinking phase.
            result: Result data from the thinking phase.
            **kwargs: Additional arguments.

        Returns:
            Phase completion metadata.
        """
        if not self._enabled:
            return None

        completion_result = {
            "hook": "on_thinking_phase_completed",
            "phase_name": phase_name,
            "result_summary": self._summarize_result(result),
            "status": "completed",
        }

        logger.debug("Thinking phase completed: %s", phase_name)
        return completion_result

    def _build_context_from_working_state(self, working_state: WorkingState) -> str:
        """Build analysis context from working state.

        Args:
            working_state: Current working state.

        Returns:
            Context string for CT analysis.
        """
        parts: list[str] = []

        # Add open loops
        if working_state.task_state.open_loops:
            loops = [loop.value for loop in working_state.task_state.open_loops]
            parts.append(f"Open loops: {', '.join(loops)}")

        # Add blocked items
        if working_state.task_state.blocked_on:
            blocked = [b.value for b in working_state.task_state.blocked_on]
            parts.append(f"Blocked on: {', '.join(blocked)}")

        # Add recent decisions
        if working_state.decision_log:
            decisions = [d.summary for d in working_state.decision_log[-3:]]
            parts.append(f"Recent decisions: {', '.join(decisions)}")

        # Add active entities
        if working_state.active_entities:
            entities = [e.value for e in working_state.active_entities[:5]]
            parts.append(f"Active entities: {', '.join(entities)}")

        return "\n".join(parts) if parts else "No additional context available"

    def _summarize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Create a summary of result for logging.

        Args:
            result: The full result dict.

        Returns:
            Summarized version with key fields.
        """
        if not isinstance(result, dict):
            return {"type": type(result).__name__}

        summary = {}
        for key in ["confidence", "status", "should_proceed", "blocked"]:
            if key in result:
                summary[key] = result[key]
        return summary

    def get_reasoning_history(self) -> list[dict[str, Any]]:
        """Get the history of all reasoning operations.

        Returns:
            List of reasoning results from all hook calls.
        """
        return list(self._reasoning_history)

    def get_last_analysis(self) -> dict[str, Any] | None:
        """Get the most recent analysis result.

        Returns:
            The last analysis result or None.
        """
        return self._last_analysis

    def clear_history(self) -> None:
        """Clear the reasoning history."""
        self._reasoning_history.clear()


# Global middleware instance
_global_ct_middleware: CTEngineMiddleware | None = None


def create_and_register_ct_middleware(
    ct_engine: CriticalThinkingEngine | None = None,
    enabled: bool = True,
    **kwargs: Any,
) -> CTEngineMiddleware:
    """Create and register the CT Engine Middleware with the global hook manager.

    This is a convenience function for the common case of setting up
    the middleware with default configuration.

    Args:
        ct_engine: Optional CriticalThinkingEngine instance.
        enabled: Whether the middleware is enabled.
        **kwargs: Additional arguments passed to CTEngineMiddleware.

    Returns:
        The created and registered CTEngineMiddleware instance.
    """
    global _global_ct_middleware

    if _global_ct_middleware is not None:
        logger.debug("CTEngineMiddleware already exists, returning existing instance")
        return _global_ct_middleware

    from polaris.kernelone.cognitive.hooks import get_hook_manager

    middleware = CTEngineMiddleware(ct_engine=ct_engine, enabled=enabled, **kwargs)
    manager = get_hook_manager()
    manager.register_plugin(middleware, name="ct_engine_middleware")

    _global_ct_middleware = middleware
    logger.info("CTEngineMiddleware created and registered")

    return middleware


def get_ct_middleware() -> CTEngineMiddleware | None:
    """Get the global CT Engine Middleware instance.

    Returns:
        The global middleware instance or None if not created.
    """
    return _global_ct_middleware


def reset_ct_middleware() -> None:
    """Reset the global CT Engine Middleware (useful for testing)."""
    global _global_ct_middleware
    _global_ct_middleware = None
