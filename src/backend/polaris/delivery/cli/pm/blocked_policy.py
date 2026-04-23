"""Blocked task policy engine for PM loop.

This module provides configurable strategies for handling Director blocked tasks:
- skip: Mark as skipped and continue loop
- manual: Stop and await human intervention
- degrade_retry: Retry with degraded settings (serial mode, reduced QA)
- auto: Automatically classify and decide based on error patterns
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class BlockedStrategy(str, Enum):
    """Available blocked task handling strategies."""

    SKIP = "skip"
    MANUAL = "manual"
    DEGRADE_RETRY = "degrade_retry"
    AUTO = "auto"


class BlockedDecision(str, Enum):
    """Decision outcomes from blocked policy evaluation."""

    CONTINUE = "continue"
    MANUAL_STOP = "manual_stop"
    DEGRADE_AND_CONTINUE = "degrade_and_continue"
    SKIP_AND_CONTINUE = "skip_and_continue"


@dataclass
class BlockedPolicyResult:
    """Result of blocked policy evaluation."""

    decision: BlockedDecision
    exit_code: int
    pm_state_patch: dict[str, Any] = field(default_factory=dict)
    audit_payload: dict[str, Any] = field(default_factory=dict)
    strategy: str = ""
    reason: str = ""
    task_status_update: dict[str, Any] | None = None  # Task status update to apply


# Error classification patterns for auto strategy
_ERROR_CLASSIFICATION_PATTERNS = {
    "llm_rate_limit": re.compile(r"rate.?limit|429|too.?many.?requests", re.I),
    "llm_quota_exceeded": re.compile(r"quota|insufficient.?quota|billing", re.I),
    "llm_timeout": re.compile(r"timeout|timed.?out|deadline.?exceeded", re.I),
    "llm_context_length": re.compile(r"context.?length|token.?limit|max.?tokens", re.I),
    "tool_execution_fail": re.compile(r"tool.?execution|command.?failed|exit.?code", re.I),
    "permission_denied": re.compile(r"permission|access.?denied|unauthorized|403", re.I),
    "resource_not_found": re.compile(r"not.?found|404|missing|no.?such", re.I),
    "transient_network": re.compile(r"network|connection|unreachable|dns|econnrefused", re.I),
    "syntax_validation": re.compile(r"syntax|parse|validation|schema", re.I),
}

# Strategy recommendation for each error class
_ERROR_CLASS_STRATEGY: dict[str, tuple[BlockedDecision, str]] = {
    "llm_rate_limit": (BlockedDecision.DEGRADE_AND_CONTINUE, "rate_limit_backoff"),
    "llm_quota_exceeded": (BlockedDecision.MANUAL_STOP, "quota_exceeded_requires_billing"),
    "llm_timeout": (BlockedDecision.DEGRADE_AND_CONTINUE, "timeout_reduce_parallelism"),
    "llm_context_length": (BlockedDecision.SKIP_AND_CONTINUE, "context_length_cannot_retry"),
    "tool_execution_fail": (BlockedDecision.SKIP_AND_CONTINUE, "tool_fail_consider_manual"),
    "permission_denied": (BlockedDecision.MANUAL_STOP, "permission_requires_manual_fix"),
    "resource_not_found": (BlockedDecision.SKIP_AND_CONTINUE, "resource_missing_skip"),
    "transient_network": (BlockedDecision.DEGRADE_AND_CONTINUE, "transient_retry"),
    "syntax_validation": (BlockedDecision.SKIP_AND_CONTINUE, "syntax_error_skip"),
}


def _classify_error(error_text: str) -> tuple[str, float]:
    """Classify error text into error class with confidence score.

    Returns:
        Tuple of (error_class, confidence_score)
    """
    if not error_text:
        return ("unknown", 0.0)

    error_lower = error_text.lower()
    matches: list[tuple[str, float]] = []

    for error_class, pattern in _ERROR_CLASSIFICATION_PATTERNS.items():
        if pattern.search(error_lower):
            # Simple scoring: longer match = higher confidence
            match = pattern.search(error_lower)
            if match:
                confidence = min(0.5 + (len(match.group(0)) / len(error_lower)) * 0.5, 1.0)
                matches.append((error_class, confidence))

    if not matches:
        return ("unknown", 0.0)

    # Return highest confidence match
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches[0]


def _get_task_signature(task: dict[str, Any]) -> str:
    """Generate a stable signature for a task."""
    task_id = str(task.get("task_id", "") or task.get("id", "") or "")
    title = str(task.get("title", "") or task.get("subject", "") or "")

    if task_id:
        return f"task:{task_id}"
    if title:
        # Hash long titles
        title_hash = hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]
        return f"title:{title_hash}"
    return "unknown"


def normalize_director_status(status: str | None) -> str:
    """Normalize Director result status to canonical form.

    Maps various status strings to canonical:
    - failed/fail/error/cancelled/timeout -> failed
    - blocked/block -> blocked
    - success/done/completed/pass/passed -> success
    - needs_continue/continue/deferred -> needs_continue
    """
    if not status:
        return "unknown"

    token = str(status).strip().lower()

    if token in ("success", "done", "completed", "pass", "passed"):
        return "success"
    if token in ("fail", "failed", "error", "cancelled", "timeout"):
        return "failed"
    if token in ("blocked", "block"):
        return "blocked"
    if token in ("needs_continue", "need_continue", "continue", "deferred"):
        return "needs_continue"

    return token


def evaluate_blocked_policy(
    strategy: str | BlockedStrategy,
    task: dict[str, Any],
    director_result: dict[str, Any],
    pm_state: dict[str, Any],
    retry_count: int,
    max_retries: int,
    degrade_retry_budget: int = 1,
) -> BlockedPolicyResult:
    """Evaluate blocked policy and return decision.

    Args:
        strategy: The blocked handling strategy to apply
        task: The task that was blocked
        director_result: The Director execution result
        pm_state: Current PM state (for counter tracking)
        retry_count: Current retry count
        max_retries: Maximum retries allowed
        degrade_retry_budget: Budget for degrade retries (for degrade_retry/auto strategies)

    Returns:
        BlockedPolicyResult with decision, exit code, state patch, and audit payload
    """
    # Handle strategy input: could be string or BlockedStrategy enum
    strategy_str = strategy.value if isinstance(strategy, BlockedStrategy) else str(strategy).lower().strip()
    task_signature = _get_task_signature(task)
    error_text = str(
        director_result.get("error", "") or director_result.get("error_code", "") or director_result.get("summary", "")
    )

    # Base audit payload
    audit_payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy_str,
        "task_signature": task_signature,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "error_preview": error_text[:200] if error_text else "",
    }

    pm_state_patch: dict[str, Any] = {}

    # --- SKIP strategy ---
    if strategy_str == BlockedStrategy.SKIP:
        decision = BlockedDecision.SKIP_AND_CONTINUE
        exit_code = 0
        pm_state_patch = {
            "blocked_skip_count": pm_state.get("blocked_skip_count", 0) + 1,
            "blocked_policy_last_signature": task_signature,
            # Reset blocked counter since we're handling this via skip
            "consecutive_blocked": 0,
        }
        audit_payload.update(
            {
                "decision": decision.value,
                "skipped_task_id": task.get("task_id"),
                "skipped_task_title": task.get("title"),
            }
        )
        return BlockedPolicyResult(
            decision=decision,
            exit_code=exit_code,
            pm_state_patch=pm_state_patch,
            audit_payload=audit_payload,
            strategy=strategy_str,
            reason="explicit_skip_strategy",
            # Include task status update for the engine to apply
            task_status_update={
                "status": "skipped",
                "blocked_handled": True,
                "blocked_handle_action": "skip",
                "blocked_handled_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # --- MANUAL strategy ---
    if strategy_str == BlockedStrategy.MANUAL:
        decision = BlockedDecision.MANUAL_STOP
        exit_code = 3  # Manual intervention required
        pm_state_patch = {
            "awaiting_manual_intervention": True,
            "manual_intervention_reason": f"blocked_task:{task_signature}",
            "blocked_policy_last_signature": task_signature,
        }
        audit_payload.update(
            {
                "decision": decision.value,
                "intervention_required": True,
            }
        )
        return BlockedPolicyResult(
            decision=decision,
            exit_code=exit_code,
            pm_state_patch=pm_state_patch,
            audit_payload=audit_payload,
            strategy=strategy_str,
            reason="explicit_manual_strategy",
        )

    # --- DEGRADE_RETRY strategy ---
    if strategy_str == BlockedStrategy.DEGRADE_RETRY:
        current_degrade_count = pm_state.get("degrade_retry_count", 0)

        if current_degrade_count < degrade_retry_budget:
            decision = BlockedDecision.DEGRADE_AND_CONTINUE
            exit_code = 0
            pm_state_patch = {
                "degrade_retry_count": current_degrade_count + 1,
                "degrade_settings": {
                    "serial_mode": True,
                    "max_parallel": 1,
                    "integration_qa": False,
                    "max_verification_retries": 0,
                },
                "blocked_policy_last_signature": task_signature,
            }
            audit_payload.update(
                {
                    "decision": decision.value,
                    "degrade_count": current_degrade_count + 1,
                    "degrade_budget_remaining": degrade_retry_budget - current_degrade_count - 1,
                }
            )
            return BlockedPolicyResult(
                decision=decision,
                exit_code=exit_code,
                pm_state_patch=pm_state_patch,
                audit_payload=audit_payload,
                strategy=strategy_str,
                reason=f"degrade_retry_{current_degrade_count + 1}_of_{degrade_retry_budget}",
            )
        else:
            # Budget exhausted, fallback to manual
            decision = BlockedDecision.MANUAL_STOP
            exit_code = 3
            pm_state_patch = {
                "awaiting_manual_intervention": True,
                "manual_intervention_reason": f"degrade_budget_exhausted:{task_signature}",
                "blocked_policy_last_signature": task_signature,
            }
            audit_payload.update(
                {
                    "decision": decision.value,
                    "intervention_required": True,
                    "degrade_budget_exhausted": True,
                }
            )
            return BlockedPolicyResult(
                decision=decision,
                exit_code=exit_code,
                pm_state_patch=pm_state_patch,
                audit_payload=audit_payload,
                strategy=strategy_str,
                reason="degrade_budget_exhausted_fallback_to_manual",
            )

    # --- AUTO strategy ---
    if strategy_str == BlockedStrategy.AUTO:
        error_class, confidence = _classify_error(error_text)

        audit_payload["error_classification"] = {
            "class": error_class,
            "confidence": confidence,
        }

        # Check for critical tasks (auth, security, core) - prefer manual on retry exhaustion
        task_title = str(task.get("title", "")).lower()
        is_critical_task = any(pattern in task_title for pattern in ["auth", "security", "core"])
        retry_exhausted = retry_count >= max_retries

        if is_critical_task and retry_exhausted:
            recommended_decision = BlockedDecision.MANUAL_STOP
            reason = "critical_task_retry_exhausted"
        else:
            # Get strategy recommendation from error classification
            recommended_decision, reason = _ERROR_CLASS_STRATEGY.get(
                error_class, (BlockedDecision.SKIP_AND_CONTINUE, "unclassified_fallback_to_skip")
            )

        # Check if we need degrade retry and have budget
        if recommended_decision == BlockedDecision.DEGRADE_AND_CONTINUE:
            current_degrade_count = pm_state.get("degrade_retry_count", 0)
            if current_degrade_count >= degrade_retry_budget:
                # No budget, fallback to skip
                recommended_decision = BlockedDecision.SKIP_AND_CONTINUE
                reason = "degrade_budget_exhausted_fallback_to_skip"

        # Build result based on final decision
        if recommended_decision == BlockedDecision.MANUAL_STOP:
            exit_code = 3
            pm_state_patch = {
                "awaiting_manual_intervention": True,
                "manual_intervention_reason": f"{reason}:{task_signature}",
                "blocked_policy_last_signature": task_signature,
            }
        elif recommended_decision == BlockedDecision.DEGRADE_AND_CONTINUE:
            exit_code = 0
            current_degrade_count = pm_state.get("degrade_retry_count", 0)
            pm_state_patch = {
                "degrade_retry_count": current_degrade_count + 1,
                "degrade_settings": {
                    "serial_mode": True,
                    "max_parallel": 1,
                    "integration_qa": False,
                    "max_verification_retries": 0,
                },
                "blocked_policy_last_signature": task_signature,
            }
        else:  # SKIP_AND_CONTINUE or CONTINUE
            exit_code = 0
            pm_state_patch = {
                "blocked_skip_count": pm_state.get("blocked_skip_count", 0) + 1,
                "blocked_policy_last_signature": task_signature,
            }

        audit_payload.update(
            {
                "decision": recommended_decision.value,
                "auto_reason": reason,
            }
        )

        return BlockedPolicyResult(
            decision=recommended_decision,
            exit_code=exit_code,
            pm_state_patch=pm_state_patch,
            audit_payload=audit_payload,
            strategy=strategy_str,
            reason=reason,
        )

    # Unknown strategy fallback to skip
    return BlockedPolicyResult(
        decision=BlockedDecision.SKIP_AND_CONTINUE,
        exit_code=0,
        pm_state_patch={
            "blocked_skip_count": pm_state.get("blocked_skip_count", 0) + 1,
            "blocked_policy_last_signature": task_signature,
        },
        audit_payload={
            **audit_payload,
            "decision": BlockedDecision.SKIP_AND_CONTINUE.value,
            "warning": f"unknown_strategy_{strategy_str}_fallback_to_skip",
        },
        strategy=strategy_str,
        reason="unknown_strategy_fallback",
    )


def should_apply_degrade_settings(pm_state: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Check if degrade settings should be applied and return them.

    Returns:
        Tuple of (should_apply, settings_dict)
    """
    degrade_settings = pm_state.get("degrade_settings")
    if isinstance(degrade_settings, dict) and degrade_settings:
        return True, degrade_settings
    return False, {}


def consume_degrade_settings(pm_state: dict[str, Any]) -> dict[str, Any]:
    """Consume (remove) degrade settings from pm_state after application.

    Returns:
        Updated pm_state dict
    """
    new_state = dict(pm_state)
    new_state.pop("degrade_settings", None)
    return new_state


def get_blocked_policy_from_env() -> tuple[str, int]:
    """Get blocked policy configuration from environment.

    Returns:
        Tuple of (strategy, degrade_max_retries)
    """
    strategy = os.environ.get("KERNELONE_PM_BLOCKED_STRATEGY", "auto")
    try:
        degrade_max_retries = int(os.environ.get("KERNELONE_PM_BLOCKED_DEGRADE_RETRIES", "1"))
    except ValueError:
        degrade_max_retries = 1
    return strategy, degrade_max_retries


__all__ = [
    "BlockedDecision",
    "BlockedPolicyResult",
    "BlockedStrategy",
    "consume_degrade_settings",
    "evaluate_blocked_policy",
    "get_blocked_policy_from_env",
    "normalize_director_status",
    "should_apply_degrade_settings",
]
