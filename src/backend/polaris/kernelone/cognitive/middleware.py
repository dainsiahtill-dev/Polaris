"""Cognitive Middleware - Integrates Cognitive Life Form with Role Dialogue.

This middleware provides optional cognitive enhancement for role processing:
- Intent detection before role processing
- Uncertainty assessment
- Risk level evaluation
- Cognitive context injection into prompts
"""

from __future__ import annotations

import os
from typing import Any

from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator


class CognitiveMiddleware:
    """
    Middleware that integrates CognitiveOrchestrator with role dialogue.

    When enabled, it preprocesses messages through the cognitive pipeline
    to provide enhanced context for role processing.

    Usage:
        middleware = CognitiveMiddleware(workspace=".", enabled=True)
        cognitive_context = await middleware.process(
            message="Create a new API endpoint",
            role_id="director",
            session_id="session_123",
        )
        # cognitive_context contains:
        # - intent_type
        # - confidence
        # - uncertainty_score
        # - execution_path
        # - cognitive_analysis
    """

    def __init__(
        self,
        workspace: str | None = None,
        enabled: bool | None = None,
    ):
        self._workspace = workspace or "."
        self._enabled = self._resolve_enabled(enabled)
        self._orchestrator: CognitiveOrchestrator | None = None

    def _resolve_enabled(self, enabled: bool | None) -> bool:
        """Resolve whether cognitive middleware is enabled."""
        if enabled is not None:
            return enabled
        # Check environment variable
        env_value = os.environ.get("KERNELONE_ENABLE_COGNITIVE_MIDDLEWARE", "").strip().lower()
        if env_value in ("1", "true", "yes", "on"):
            return True
        # Default: cognitive middleware is ENABLED for unified cognitive + role system
        return env_value not in ("0", "false", "no", "off")

    def _get_orchestrator(self) -> CognitiveOrchestrator | None:
        """Lazy initialization of orchestrator.

        All enable_* flags are passed as None so the orchestrator reads
        from environment variables (COGNITIVE_ENABLE_*).
        This allows users to control full cognitive pipeline via env vars.
        """
        if not self._enabled:
            return None
        if self._orchestrator is None:
            try:
                self._orchestrator = CognitiveOrchestrator(
                    workspace=self._workspace,
                    enable_evolution=None,  # Read from COGNITIVE_ENABLE_EVOLUTION
                    enable_personality=None,  # Read from COGNITIVE_ENABLE_PERSONALITY
                    enable_value_alignment=None,  # Read from COGNITIVE_ENABLE_VALUE_ALIGNMENT
                    enable_governance=None,  # Read from COGNITIVE_ENABLE_GOVERNANCE
                    use_llm=None,  # Read from COGNITIVE_USE_LLM
                )
            except (RuntimeError, ValueError):
                return None
        return self._orchestrator

    async def process(
        self,
        message: str,
        role_id: str = "director",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Process message through cognitive pipeline.

        Args:
            message: User message to analyze
            role_id: Role processing the message
            session_id: Optional session ID for context

        Returns:
            Cognitive context dict with keys:
            - enabled: Whether cognitive processing was applied
            - intent_type: Detected intent type
            - confidence: Confidence score (0.0-1.0)
            - uncertainty_score: Uncertainty score (0.0-1.0)
            - execution_path: Recommended execution path
            - cognitive_analysis: Pre-computed cognitive analysis
            - blocked: Whether the message was blocked
        """
        orchestrator = self._get_orchestrator()

        if orchestrator is None:
            return {
                "enabled": False,
                "intent_type": "unknown",
                "confidence": 0.0,
                "uncertainty_score": 0.0,
                "execution_path": "unknown",
                "cognitive_analysis": None,
                "blocked": False,
                "block_reason": None,
            }

        try:
            result = await orchestrator.process(
                message=message,
                session_id=session_id or f"middleware_{role_id}",
                role_id=role_id,
                workspace=self._workspace,
            )

            return {
                "enabled": True,
                "intent_type": result.intent_type,
                "confidence": result.confidence,
                "uncertainty_score": result.uncertainty_score,
                "execution_path": result.execution_path.value,
                "cognitive_analysis": {
                    "content": result.content,
                    "clarity_level": result.clarity_level.value,
                    "actions_taken": result.actions_taken,
                    "verification_needed": result.verification_needed,
                },
                "blocked": result.blocked,
                "block_reason": result.block_reason,
            }

        except (RuntimeError, ValueError):
            return {
                "enabled": False,
                "intent_type": "unknown",
                "confidence": 0.0,
                "uncertainty_score": 0.0,
                "execution_path": "unknown",
                "cognitive_analysis": None,
                "blocked": False,
                "block_reason": None,
            }

    def inject_into_context(
        self,
        cognitive_context: dict[str, Any],
        existing_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Inject cognitive context into role dialogue context.

        Args:
            cognitive_context: Output from process()
            existing_context: Existing context dict to merge with

        Returns:
            Merged context with cognitive enhancements
        """
        context = dict(existing_context) if existing_context else {}

        if not cognitive_context.get("enabled"):
            return context

        # Inject cognitive metadata
        context["cognitive"] = {
            "intent_type": cognitive_context.get("intent_type"),
            "confidence": cognitive_context.get("confidence"),
            "uncertainty_score": cognitive_context.get("uncertainty_score"),
            "execution_path": cognitive_context.get("execution_path"),
        }

        # Inject cognitive analysis as additional context
        analysis = cognitive_context.get("cognitive_analysis")
        if analysis:
            context["cognitive"]["analysis"] = analysis

        # If blocked, mark in context
        if cognitive_context.get("blocked"):
            context["cognitive"]["blocked"] = True
            context["cognitive"]["block_reason"] = cognitive_context.get("block_reason")

        return context

    def get_prompt_appendix(self, cognitive_context: dict[str, Any]) -> str | None:
        """
        Generate a prompt appendix based on cognitive context.

        This can be appended to the role prompt to provide cognitive guidance.

        Args:
            cognitive_context: Output from process()

        Returns:
            Prompt appendix string or None
        """
        if not cognitive_context.get("enabled"):
            return None

        intent_type = cognitive_context.get("intent_type", "unknown")
        confidence = cognitive_context.get("confidence", 0.0)
        uncertainty = cognitive_context.get("uncertainty_score", 0.0)
        execution_path = cognitive_context.get("execution_path", "unknown")

        parts = [
            f"[Cognitive Analysis] Intent: {intent_type}",
            f"Confidence: {confidence:.2f}",
            f"Uncertainty: {uncertainty:.2f}",
            f"Execution Path: {execution_path}",
        ]

        if cognitive_context.get("blocked"):
            parts.append(f"BLOCKED: {cognitive_context.get('block_reason')}")

        return " | ".join(parts)


# Global middleware instance
_cognitive_middleware: CognitiveMiddleware | None = None


def get_cognitive_middleware(
    workspace: str | None = None,
    enabled: bool | None = None,
) -> CognitiveMiddleware:
    """Get or create the global cognitive middleware instance."""
    global _cognitive_middleware

    if _cognitive_middleware is None:
        _cognitive_middleware = CognitiveMiddleware(workspace=workspace, enabled=enabled)

    return _cognitive_middleware


def reset_cognitive_middleware() -> None:
    """Reset the global cognitive middleware (for testing)."""
    global _cognitive_middleware
    _cognitive_middleware = None
