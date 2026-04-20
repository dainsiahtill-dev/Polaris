"""Governance State Tracker - Cross-gate accumulation for cognitive pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GovernanceState:
    """Cross-gate accumulation state.

    Accumulates across phases within a single cognitive process() call.
    3 consecutive WARN results auto-escalate to FAIL.
    """

    warn_count: int = 0
    fail_count: int = 0
    last_intent_type: str = ""
    consecutive_unknown_count: int = 0
    confidence_trajectory: list[float] = field(default_factory=list)
    max_warn_before_escalation: int = 3

    def record_result(self, status: str, intent_type: str, confidence: float) -> None:
        """Record a gate result for cross-gate accumulation."""
        if status == "WARN":
            self.warn_count += 1
        elif status == "FAIL":
            self.fail_count += 1

        if intent_type == "unknown":
            self.consecutive_unknown_count += 1
        else:
            self.consecutive_unknown_count = 0

        self.last_intent_type = intent_type
        self.confidence_trajectory.append(confidence)

    def should_escalate(self) -> bool:
        """3 consecutive WARN results auto-escalate to FAIL."""
        return self.warn_count >= self.max_warn_before_escalation

    def is_confidence_declining(self) -> bool:
        """Check whether confidence is monotonically declining over the last 3 samples."""
        if len(self.confidence_trajectory) < 3:
            return False
        recent = self.confidence_trajectory[-3:]
        return all(recent[i] > recent[i + 1] for i in range(len(recent) - 1))

    def reset(self) -> None:
        """Reset state for a new process() round."""
        self.warn_count = 0
        self.fail_count = 0
        self.last_intent_type = ""
        self.consecutive_unknown_count = 0
        self.confidence_trajectory.clear()
