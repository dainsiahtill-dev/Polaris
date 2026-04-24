"""Tests for polaris.kernelone.audit.failure_envelope."""

from __future__ import annotations

from polaris.kernelone.audit.failure_envelope import (
    FAILURE_NEXT_ACTIONS,
    FAILURE_RETRYABLE,
    APPLY_FAILED,
    CONTRACT_INVALID,
    ENVIRONMENT_MISSING,
    MANUAL_REQUIRED,
    PLANNER_INVALID_OUTPUT,
    POLICY_BLOCKED,
    SCOPE_VIOLATION,
    VERIFY_FAILED,
    FailureClass,
    FailureEnvelope,
    create_failure,
)


class TestFailureClass:
    def test_values(self) -> None:
        assert FailureClass.CONTRACT_INVALID == "contract_invalid"
        assert FailureClass.PLANNER_INVALID_OUTPUT == "planner_invalid_output"
        assert FailureClass.SCOPE_VIOLATION == "scope_violation"
        assert FailureClass.APPLY_FAILED == "apply_failed"
        assert FailureClass.VERIFY_FAILED == "verify_failed"
        assert FailureClass.ENVIRONMENT_MISSING == "environment_missing"
        assert FailureClass.POLICY_BLOCKED == "policy_blocked"
        assert FailureClass.MANUAL_REQUIRED == "manual_required"


class TestFailureRetryable:
    def test_retryable_values(self) -> None:
        assert FAILURE_RETRYABLE[FailureClass.CONTRACT_INVALID] is False
        assert FAILURE_RETRYABLE[FailureClass.PLANNER_INVALID_OUTPUT] is True
        assert FAILURE_RETRYABLE[FailureClass.SCOPE_VIOLATION] is True
        assert FAILURE_RETRYABLE[FailureClass.APPLY_FAILED] is True
        assert FAILURE_RETRYABLE[FailureClass.VERIFY_FAILED] is True
        assert FAILURE_RETRYABLE[FailureClass.ENVIRONMENT_MISSING] is False
        assert FAILURE_RETRYABLE[FailureClass.POLICY_BLOCKED] is False
        assert FAILURE_RETRYABLE[FailureClass.MANUAL_REQUIRED] is False


class TestFailureNextActions:
    def test_next_actions(self) -> None:
        assert FAILURE_NEXT_ACTIONS[FailureClass.CONTRACT_INVALID] == "fix_contract"
        assert FAILURE_NEXT_ACTIONS[FailureClass.PLANNER_INVALID_OUTPUT] == "retry_planning"
        assert FAILURE_NEXT_ACTIONS[FailureClass.SCOPE_VIOLATION] == "adjust_scope"
        assert FAILURE_NEXT_ACTIONS[FailureClass.APPLY_FAILED] == "retry_apply"
        assert FAILURE_NEXT_ACTIONS[FailureClass.VERIFY_FAILED] == "retry_verify"
        assert FAILURE_NEXT_ACTIONS[FailureClass.ENVIRONMENT_MISSING] == "setup_environment"
        assert FAILURE_NEXT_ACTIONS[FailureClass.POLICY_BLOCKED] == "review_policy"
        assert FAILURE_NEXT_ACTIONS[FailureClass.MANUAL_REQUIRED] == "await_manual"


class TestFailureEnvelope:
    def test_defaults(self) -> None:
        env = FailureEnvelope(
            class_=FailureClass.APPLY_FAILED,
            stage="director",
            reason="patch failed",
        )
        assert env.class_ == FailureClass.APPLY_FAILED
        assert env.stage == "director"
        assert env.reason == "patch failed"
        assert env.retryable is True
        assert env.next_action == "retry_apply"
        assert env.evidence == {}
        assert env.task_id is None
        assert env.run_id is None

    def test_override_retryable(self) -> None:
        env = FailureEnvelope(
            class_=FailureClass.APPLY_FAILED,
            stage="director",
            reason="patch failed",
            retryable=False,
        )
        assert env.retryable is False

    def test_override_next_action(self) -> None:
        env = FailureEnvelope(
            class_=FailureClass.APPLY_FAILED,
            stage="director",
            reason="patch failed",
            next_action="custom_action",
        )
        assert env.next_action == "custom_action"

    def test_to_dict(self) -> None:
        env = FailureEnvelope(
            class_=FailureClass.SCOPE_VIOLATION,
            stage="pm",
            reason="out of scope",
            task_id="t1",
            run_id="r1",
        )
        d = env.to_dict()
        assert d["failure_class"] == "scope_violation"
        assert d["stage"] == "pm"
        assert d["reason"] == "out of scope"
        assert d["task_id"] == "t1"
        assert d["run_id"] == "r1"

    def test_from_dict(self) -> None:
        data = {
            "failure_class": "apply_failed",
            "stage": "director",
            "reason": "patch failed",
            "retryable": False,
            "evidence": {"file": "test.py"},
            "next_action": "retry_apply",
        }
        env = FailureEnvelope.from_dict(data)
        assert env.class_ == FailureClass.APPLY_FAILED
        assert env.stage == "director"
        assert env.reason == "patch failed"
        assert env.retryable is False
        assert env.evidence == {"file": "test.py"}

    def test_from_dict_empty_defaults(self) -> None:
        env = FailureEnvelope.from_dict({})
        assert env.class_ == FailureClass.POLICY_BLOCKED
        assert env.stage == "unknown"
        assert env.reason == "Unknown failure"

    def test_repr(self) -> None:
        env = FailureEnvelope(
            class_=FailureClass.VERIFY_FAILED,
            stage="qa",
            reason="check failed",
        )
        assert "verify_failed" in repr(env)
        assert "qa" in repr(env)


class TestCreateFailure:
    def test_convenience_function(self) -> None:
        env = create_failure(
            FailureClass.POLICY_BLOCKED,
            stage="gate",
            reason="not allowed",
            task_id="t1",
        )
        assert env.class_ == FailureClass.POLICY_BLOCKED
        assert env.task_id == "t1"


class TestReexports:
    def test_reexports(self) -> None:
        assert SCOPE_VIOLATION == FailureClass.SCOPE_VIOLATION
        assert APPLY_FAILED == FailureClass.APPLY_FAILED
        assert VERIFY_FAILED == FailureClass.VERIFY_FAILED
        assert CONTRACT_INVALID == FailureClass.CONTRACT_INVALID
        assert PLANNER_INVALID_OUTPUT == FailureClass.PLANNER_INVALID_OUTPUT
        assert ENVIRONMENT_MISSING == FailureClass.ENVIRONMENT_MISSING
        assert POLICY_BLOCKED == FailureClass.POLICY_BLOCKED
        assert MANUAL_REQUIRED == FailureClass.MANUAL_REQUIRED
