"""KernelGuard - TransactionKernel protocol invariant enforcement."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

# Module-level prometheus counter singleton (lazy init on first import)
# NOTE: Counter registration is GLOBAL to the process; creating a new instance
# per call causes ValueError: Duplicated timeseries on the second invocation.
try:
    from prometheus_client import Counter

    _FINALIZATION_HALLUCINATION_COUNTER = Counter(
        "kernel_guard_finalization_hallucination_total",
        "Number of hallucinated tool calls during finalization",
    )
except ImportError:
    _FINALIZATION_HALLUCINATION_COUNTER = None


@dataclass(frozen=True)
class KernelGuardError(RuntimeError):
    """Raised when a transactional turn violates a non-negotiable invariant."""

    turn_id: str
    invariant: str
    details: str

    def __str__(self) -> str:
        return f"{self.invariant} violated for turn {self.turn_id}: {self.details}"


# Phase 3.4: Invariant priority levels
class InvariantPriority:
    """Phase 3.4: Priority levels for invariants."""

    CRITICAL = "critical"  # Cannot be relaxed
    HIGH = "high"  # Can be relaxed in low-risk scenarios
    MEDIUM = "medium"  # Can be relaxed with explicit approval
    LOW = "low"  # Can be disabled for testing


# Thread-local storage for risk level context
# This ensures thread-safe access to risk level per thread/coroutine
_thread_local = threading.local()

# Valid risk levels for validation
_VALID_RISK_LEVELS = frozenset({"normal", "low", "high", "critical"})


class KernelGuard:
    """Runtime assertions for the single-decision / single-tool-batch contract.

    Phase 3.4 Enhancements:
    - Speculative execution path protection
    - Time constraints per state
    - Invariant priority system
    - Thread-safe risk level context
    """

    # Phase 3.4: Time constraints per state (milliseconds)
    _STATE_TIME_LIMITS: dict[str, float] = {
        "CONTEXT_BUILT": 5000,
        "DECISION_REQUESTED": 30000,
        "DECISION_RECEIVED": 2000,
        "DECISION_DECODED": 1000,
        "TOOL_BATCH_EXECUTING": 60000,
        "TOOL_BATCH_EXECUTED": 2000,
        "FINALIZATION_REQUESTED": 15000,
        "FINALIZATION_RECEIVED": 2000,
    }

    # Phase 3.4: Default invariant priorities
    _INVARIANT_PRIORITIES: dict[str, str] = {
        "single_decision": InvariantPriority.CRITICAL,
        "single_tool_batch": InvariantPriority.HIGH,
        "no_hidden_continuation": InvariantPriority.CRITICAL,
        "state_time_limit": InvariantPriority.HIGH,
    }

    # Phase 3.4: Class-level constants (immutable)
    _DEFAULT_RISK_LEVEL: str = "normal"

    @classmethod
    def _get_risk_level(cls) -> str:
        """Get current risk level for this thread.

        Returns:
            Current risk level, defaults to "normal" if not set.
        """
        return getattr(_thread_local, "risk_level", cls._DEFAULT_RISK_LEVEL)

    @classmethod
    def _set_risk_level(cls, level: str) -> None:
        """Set risk level for this thread.

        Args:
            level: Risk level to set
        """
        _thread_local.risk_level = level

    @classmethod
    def set_risk_level(cls, level: str) -> None:
        """Phase 3.4: Set current risk level for invariant relaxation.

        This method is thread-safe - each thread maintains its own risk level.

        Args:
            level: Risk level (normal/low/high/critical)
        """
        if level not in _VALID_RISK_LEVELS:
            raise ValueError(f"Invalid risk level: {level}. Must be one of: {_VALID_RISK_LEVELS}")
        cls._set_risk_level(level)

    @classmethod
    def get_risk_level(cls) -> str:
        """Get current risk level for this thread.

        Returns:
            Current risk level
        """
        return cls._get_risk_level()

    @classmethod
    def reset_risk_level(cls) -> None:
        """Reset risk level to default for this thread.

        Useful for cleanup in tests or worker threads.
        """
        cls._set_risk_level(cls._DEFAULT_RISK_LEVEL)

    @classmethod
    def can_relax_invariant(cls, invariant: str) -> bool:
        """Phase 3.4: Check if an invariant can be relaxed in current risk context.

        This method is thread-safe - each thread has its own risk level context.

        Args:
            invariant: Invariant name

        Returns:
            True if invariant can be relaxed
        """
        priority = cls._INVARIANT_PRIORITIES.get(invariant, InvariantPriority.HIGH)
        current_level = cls._get_risk_level()

        # Map risk levels to their allowed priorities
        relax_map: dict[str, frozenset[str]] = {
            "critical": frozenset({InvariantPriority.HIGH, InvariantPriority.MEDIUM, InvariantPriority.LOW}),
            "high": frozenset({InvariantPriority.MEDIUM, InvariantPriority.LOW}),
            "low": frozenset({InvariantPriority.LOW}),
        }
        return priority in relax_map.get(current_level, frozenset())

    @staticmethod
    def assert_single_decision(turn_id: str, decision_count: int, tool_batch_count: int | None = None) -> None:
        if decision_count != 1:
            raise KernelGuardError(
                turn_id=turn_id,
                invariant="single_decision",
                details=f"expected exactly 1 TurnDecision, got {decision_count}",
            )
        if tool_batch_count is not None and tool_batch_count > 1:
            raise KernelGuardError(
                turn_id=turn_id,
                invariant="single_tool_batch",
                details=f"expected at most 1 ToolBatch, got {tool_batch_count}",
            )

    @staticmethod
    def assert_single_tool_batch(turn_id: str, tool_batch_count: int) -> None:
        if tool_batch_count > 1:
            raise KernelGuardError(
                turn_id=turn_id,
                invariant="single_tool_batch",
                details=f"expected at most 1 ToolBatch, got {tool_batch_count}",
            )

    @staticmethod
    def assert_no_hidden_continuation(
        turn_id: str, state_trajectory: list[str] | tuple[str, ...] | None = None, *, ledger: Any | None = None
    ) -> None:
        if ledger is not None:
            decisions = getattr(ledger, "decisions", [])
            if decisions:
                last = decisions[-1]
                kind = last.get("kind", "") if isinstance(last, dict) else ""
                if kind == "tool_batch":
                    tool_count = last.get("tool_count", 0) if isinstance(last, dict) else 0
                    if tool_count > 0:
                        raise KernelGuardError(
                            turn_id=turn_id,
                            invariant="no_hidden_continuation",
                            details="last decision is an unfinalized tool_batch",
                        )
        if state_trajectory is not None:
            decision_requests = sum(1 for state in state_trajectory if state == "DECISION_REQUESTED")
            if decision_requests > 1:
                raise KernelGuardError(
                    turn_id=turn_id,
                    invariant="no_hidden_continuation",
                    details=f"DECISION_REQUESTED appeared {decision_requests} times",
                )

    @staticmethod
    def assert_no_finalization_tool_calls(
        turn_id: str,
        tool_calls: list[Any] | None,
        ledger: Any | None = None,
    ) -> None:
        """收口阶段发现 tool_calls 时的软守卫。

        不再抛异常（不打断用户链路），但留下强观测证据：
        - ledger anomaly flag
        - metrics counter
        - structured warning log
        """
        if not tool_calls:
            return
        import logging

        logger = logging.getLogger(__name__)
        tool_names = []
        for tc in tool_calls:
            if hasattr(tc, "get"):
                tool_names.append(str(tc.get("name", tc)))
            else:
                tool_names.append(str(tc))

        # 1. 记录 ledger anomaly flag
        if ledger is not None and hasattr(ledger, "anomaly_flags"):
            ledger.anomaly_flags.append(
                {
                    "type": "finalize_tool_call_hallucination",
                    "turn_id": turn_id,
                    "tool_count": len(tool_calls),
                    "tool_names": tool_names,
                }
            )

        # 2. metrics counter
        if _FINALIZATION_HALLUCINATION_COUNTER is not None:
            _FINALIZATION_HALLUCINATION_COUNTER.inc()

        # 3. structured warning log
        logger.warning(
            "finalization_tool_calls_soft_guard: turn_id=%s dropped=%d tool_calls=%s anomaly_recorded=%s",
            turn_id,
            len(tool_calls),
            tool_names,
            ledger is not None,
        )

    # Phase 3.4: Speculative execution path protection
    @classmethod
    def assert_speculative_path_safe(
        cls,
        turn_id: str,
        state: str,
        tool_count: int,
        speculative_ratio: float = 0.0,
    ) -> None:
        """Phase 3.4: Verify speculative execution path is safe.

        Args:
            turn_id: Turn identifier
            state: Current state
            tool_count: Number of tools in batch
            speculative_ratio: Ratio of speculative tools to total
        """
        if speculative_ratio > 0.5 and tool_count > 5:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "speculative_path_warning: turn_id=%s state=%s tools=%d speculative_ratio=%.2f",
                turn_id,
                state,
                tool_count,
                speculative_ratio,
            )

    # Phase 3.4: State time constraint checking
    @classmethod
    def assert_state_time_limit(
        cls,
        turn_id: str,
        state: str,
        state_entered_at: float,
    ) -> None:
        """Phase 3.4: Check if state has exceeded time limit.

        Args:
            turn_id: Turn identifier
            state: Current state name
            state_entered_at: Timestamp when state was entered

        Raises:
            KernelGuardError: If time limit exceeded
        """
        time_limit = cls._STATE_TIME_LIMITS.get(state, 0)
        if time_limit <= 0:
            return

        elapsed_ms = (time.time() - state_entered_at) * 1000
        if elapsed_ms > time_limit:
            if cls.can_relax_invariant("state_time_limit"):
                import logging

                logger = logging.getLogger(__name__)
                logger.warning(
                    "state_time_limit_warning: turn_id=%s state=%s elapsed_ms=%d limit_ms=%d (relaxed)",
                    turn_id,
                    state,
                    elapsed_ms,
                    time_limit,
                )
            else:
                raise KernelGuardError(
                    turn_id=turn_id,
                    invariant="state_time_limit",
                    details=f"state {state} exceeded time limit: {elapsed_ms:.0f}ms > {time_limit:.0f}ms",
                )
