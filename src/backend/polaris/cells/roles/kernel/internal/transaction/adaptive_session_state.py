"""Adaptive session-state helpers (Phase 3.1 / 3.2 / 3.3) for the turn kernel.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Free-function implementations behind the Phase 3.x helpers that used to live on
:class:`TurnTransactionController`. To keep the session-state singletons
(``_turn_outcome_history``, ``_session_token_budget``/``_session_tokens_used``,
…) single-instance *on the controller*, these functions take the relevant state
explicitly and return either a derived value or the next state value; the facade
methods own the assignment back onto ``self``.

Bodies moved verbatim from ``turn_transaction_controller.py``; only ``self``
attribute access was replaced by explicit arguments / return values.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase 3.1: Adaptive Model Routing
# ---------------------------------------------------------------------------


def select_model_for_task(
    turn_outcome_history: list[dict[str, Any]],
    task_complexity: str = "medium",
) -> str | None:
    """Phase 3.1: Select optimal model based on task characteristics.

    Args:
        turn_outcome_history: Cross-turn outcome history (read-only here).
        task_complexity: Estimated task complexity (low/medium/high/complex)

    Returns:
        Model name to use, or None for default model
    """
    complexity_weights = {
        "low": 0.3,
        "medium": 0.5,
        "high": 0.7,
        "complex": 0.9,
    }
    weight = complexity_weights.get(task_complexity, 0.5)

    recent_failures = [outcome for outcome in turn_outcome_history[-10:] if not outcome.get("success", True)]

    if len(recent_failures) >= 3:
        logger.info(
            "adaptive_model_routing: %d recent failures, prioritizing reliability",
            len(recent_failures),
        )
        return None

    if weight >= 0.7:
        logger.debug(
            "adaptive_model_routing: high complexity=%s, considering premium model",
            task_complexity,
        )

    return None


def estimate_task_complexity(context: list[dict]) -> str:
    """Estimate task complexity from context.

    Args:
        context: Conversation context

    Returns:
        Complexity level: low/medium/high/complex
    """
    total_chars = sum(len(str(msg.get("content", ""))) for msg in context if isinstance(msg, dict))
    tool_definitions_count = len(context) // 3 if context else 0
    has_multi_turn = len(context) > 4

    if total_chars > 10000 or tool_definitions_count > 20:
        return "complex"
    elif total_chars > 5000 or tool_definitions_count > 10 or has_multi_turn:
        return "high"
    elif total_chars > 1500:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Phase 3.2: Cross-Turn Learning
# ---------------------------------------------------------------------------


def record_turn_outcome(
    turn_outcome_history: list[dict[str, Any]],
    max_outcome_history: int,
    *,
    turn_id: str,
    success: bool,
    error: str | None = None,
    tokens_used: int = 0,
    cost: float = 0.0,
) -> list[dict[str, Any]]:
    """Phase 3.2: Record turn outcome for learning.

    Mutates ``turn_outcome_history`` in place (append) and returns the (possibly
    trimmed) list so the caller can rebind it onto the controller, preserving the
    original ``self._turn_outcome_history = self._turn_outcome_history[-N:]``
    reassignment semantics.

    Args:
        turn_outcome_history: Cross-turn outcome history to append to.
        max_outcome_history: Maximum number of outcomes to retain.
        turn_id: Turn identifier
        success: Whether turn succeeded
        error: Error message if failed
        tokens_used: Total tokens consumed
        cost: Total cost incurred
    """
    outcome = {
        "turn_id": turn_id,
        "success": success,
        "error": error,
        "tokens_used": tokens_used,
        "cost": cost,
    }

    turn_outcome_history.append(outcome)
    if len(turn_outcome_history) > max_outcome_history:
        turn_outcome_history = turn_outcome_history[-max_outcome_history:]

    if not success and error:
        logger.info(
            "turn_outcome_recorded: turn_id=%s failed=%s error=%s",
            turn_id,
            not success,
            error[:100] if error else None,
        )
    return turn_outcome_history


def learn_from_history(turn_outcome_history: list[dict[str, Any]], error_pattern: str) -> list[str]:
    """Phase 3.2: Generate correction hints based on failure patterns.

    Args:
        turn_outcome_history: Cross-turn outcome history (read-only here).
        error_pattern: Error type to analyze

    Returns:
        List of correction hints
    """
    relevant_failures = [
        outcome
        for outcome in turn_outcome_history[-20:]
        if not outcome.get("success", True) and error_pattern.lower() in str(outcome.get("error", "")).lower()
    ]

    hints: list[str] = []
    if len(relevant_failures) >= 2:
        if "timeout" in error_pattern.lower():
            hints.append("Consider breaking down into smaller steps")
        elif "syntax" in error_pattern.lower():
            hints.append("Check syntax before applying changes")
        elif "not found" in error_pattern.lower():
            hints.append("Ensure all dependencies are available first")
        elif "permission" in error_pattern.lower():
            hints.append("Verify file permissions before writing")

    return hints


def get_learned_constraints(
    turn_outcome_history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Phase 3.2: Get constraints learned from turn history.

    Returns:
        Dict of learned constraints for this session
    """
    recent_outcomes = turn_outcome_history[-20:]
    failed_outcomes = [o for o in recent_outcomes if not o.get("success", True)]

    return {
        "failure_count": len(failed_outcomes),
        "total_turns": len(recent_outcomes),
        "recent_errors": [o.get("error") for o in failed_outcomes[-5:] if o.get("error")],
        "should_defer_complexity": len(failed_outcomes) >= 3,
    }


# ---------------------------------------------------------------------------
# Phase 3.3: Budget-Aware Execution
# ---------------------------------------------------------------------------


def log_session_budget_initialized(token_budget: int, cost_budget: float) -> None:
    """Phase 3.3: Emit the budget-initialized debug log line.

    The numeric assignments stay on the controller (singleton state); only the
    side-effecting log line is centralised here so the body is moved verbatim.
    """
    logger.debug(
        "budget_initialized: token_budget=%s cost_budget=%s",
        token_budget or "unlimited",
        cost_budget or "unlimited",
    )


def check_budget(
    *,
    session_tokens_used: int,
    session_token_budget: int,
    session_cost_used: float,
    session_cost_budget: float,
) -> dict[str, Any]:
    """Phase 3.3: Check budget status and return warnings.

    Returns:
        Budget status with warnings if approaching limits
    """
    status: dict[str, Any] = {
        "tokens_used": session_tokens_used,
        "tokens_budget": session_token_budget,
        "cost_used": session_cost_used,
        "cost_budget": session_cost_budget,
        "token_warning": False,
        "cost_warning": False,
        "token_exceeded": False,
        "cost_exceeded": False,
    }

    if session_token_budget > 0:
        token_ratio = session_tokens_used / session_token_budget
        status["token_ratio"] = round(token_ratio, 3)
        status["token_warning"] = token_ratio >= 0.8
        status["token_exceeded"] = token_ratio >= 1.0

    if session_cost_budget > 0:
        cost_ratio = session_cost_used / session_cost_budget
        status["cost_ratio"] = round(cost_ratio, 3)
        status["cost_warning"] = cost_ratio >= 0.8
        status["cost_exceeded"] = cost_ratio >= 1.0

    return status
