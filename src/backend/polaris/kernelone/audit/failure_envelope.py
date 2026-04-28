"""Unified Failure Envelope for Polaris Legacy.

This module provides a standardized failure classification system
to replace the scattered error handling across PM and Director loops.

Failure Classification (8 standard types):
- contract_invalid: PM output doesn't match required schema
- planner_invalid_output: Planner produced invalid output
- scope_violation: File operations outside allowed scope
- apply_failed: Failed to apply patches to workspace
- verify_failed: Verification checks failed
- environment_missing: Required environment/tool not available
- policy_blocked: Policy gate blocked the operation
- manual_required: Human intervention required

Each failure includes:
- class: Failure classification
- stage: Where in the pipeline it occurred
- retryable: Whether the failure can be retried
- reason: Human-readable explanation
- evidence: Additional context for debugging
- next_action: Recommended next step
"""

from enum import Enum
from typing import Any


class FailureClass(str, Enum):
    """Standard failure classification dictionary."""

    CONTRACT_INVALID = "contract_invalid"
    PLANNER_INVALID_OUTPUT = "planner_invalid_output"
    SCOPE_VIOLATION = "scope_violation"
    APPLY_FAILED = "apply_failed"
    VERIFY_FAILED = "verify_failed"
    ENVIRONMENT_MISSING = "environment_missing"
    POLICY_BLOCKED = "policy_blocked"
    TASK_FAILURE = "task_failure"
    MANUAL_REQUIRED = "manual_required"


FAILURE_RETRYABLE = {
    FailureClass.CONTRACT_INVALID: False,
    FailureClass.PLANNER_INVALID_OUTPUT: True,
    FailureClass.SCOPE_VIOLATION: True,
    FailureClass.APPLY_FAILED: True,
    FailureClass.VERIFY_FAILED: True,
    FailureClass.ENVIRONMENT_MISSING: False,
    FailureClass.POLICY_BLOCKED: False,
    FailureClass.TASK_FAILURE: True,
    FailureClass.MANUAL_REQUIRED: False,
}

FAILURE_NEXT_ACTIONS = {
    FailureClass.CONTRACT_INVALID: "fix_contract",
    FailureClass.PLANNER_INVALID_OUTPUT: "retry_planning",
    FailureClass.SCOPE_VIOLATION: "adjust_scope",
    FailureClass.APPLY_FAILED: "retry_apply",
    FailureClass.VERIFY_FAILED: "retry_verify",
    FailureClass.ENVIRONMENT_MISSING: "setup_environment",
    FailureClass.POLICY_BLOCKED: "review_policy",
    FailureClass.TASK_FAILURE: "check_upstream",
    FailureClass.MANUAL_REQUIRED: "await_manual",
}


class FailureEnvelope:
    """Unified failure representation."""

    def __init__(
        self,
        class_: FailureClass,
        stage: str,
        reason: str,
        retryable: bool | None = None,
        evidence: dict[str, Any] | None = None,
        next_action: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.class_ = class_
        self.stage = stage
        self.reason = reason
        self.task_id = task_id
        self.run_id = run_id

        if retryable is not None:
            self.retryable = retryable
        else:
            self.retryable = FAILURE_RETRYABLE.get(class_, False)

        self.evidence = evidence or {}
        self.next_action = next_action or FAILURE_NEXT_ACTIONS.get(class_, "unknown")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "failure_class": self.class_.value,
            "stage": self.stage,
            "reason": self.reason,
            "retryable": self.retryable,
            "evidence": self.evidence,
            "next_action": self.next_action,
            "task_id": self.task_id,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureEnvelope":
        """Create from dictionary."""
        class_str = data.get("failure_class", "")
        class_ = FailureClass(class_str) if class_str else FailureClass.POLICY_BLOCKED

        return cls(
            class_=class_,
            stage=data.get("stage", "unknown"),
            reason=data.get("reason", "Unknown failure"),
            retryable=data.get("retryable"),
            evidence=data.get("evidence", {}),
            next_action=data.get("next_action"),
            task_id=data.get("task_id"),
            run_id=data.get("run_id"),
        )

    def __repr__(self) -> str:
        return f"FailureEnvelope({self.class_.value}, {self.stage}, retryable={self.retryable})"


def create_failure(
    class_: FailureClass,
    stage: str,
    reason: str,
    **kwargs,
) -> FailureEnvelope:
    """Convenience function to create a FailureEnvelope."""
    return FailureEnvelope(class_=class_, stage=stage, reason=reason, **kwargs)


SCOPE_VIOLATION = FailureClass.SCOPE_VIOLATION
APPLY_FAILED = FailureClass.APPLY_FAILED
VERIFY_FAILED = FailureClass.VERIFY_FAILED
CONTRACT_INVALID = FailureClass.CONTRACT_INVALID
PLANNER_INVALID_OUTPUT = FailureClass.PLANNER_INVALID_OUTPUT
ENVIRONMENT_MISSING = FailureClass.ENVIRONMENT_MISSING
POLICY_BLOCKED = FailureClass.POLICY_BLOCKED
TASK_FAILURE = FailureClass.TASK_FAILURE
MANUAL_REQUIRED = FailureClass.MANUAL_REQUIRED
