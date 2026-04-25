"""Unit tests for polaris.kernelone.audit.failure_envelope."""

from __future__ import annotations

from polaris.kernelone.audit.failure_envelope import (
    CONTRACT_INVALID,
    FAILURE_NEXT_ACTIONS,
    FAILURE_RETRYABLE,
    FailureClass,
    FailureEnvelope,
    create_failure,
)


class TestFailureClass:
    def test_enum_values(self) -> None:
        assert FailureClass.CONTRACT_INVALID.value == "contract_invalid"
        assert FailureClass.PLANNER_INVALID_OUTPUT.value == "planner_invalid_output"

    def test_retryable_map_coverage(self) -> None:
        for fc in FailureClass:
            assert fc in FAILURE_RETRYABLE

    def test_next_actions_map_coverage(self) -> None:
        for fc in FailureClass:
            assert fc in FAILURE_NEXT_ACTIONS


class TestFailureEnvelope:
    def test_basic_construction(self) -> None:
        fe = FailureEnvelope(
            class_=FailureClass.SCOPE_VIOLATION,
            stage="planning",
            reason="out of bounds",
        )
        assert fe.class_ == FailureClass.SCOPE_VIOLATION
        assert fe.stage == "planning"
        assert fe.reason == "out of bounds"
        assert fe.retryable is True
        assert fe.next_action == "adjust_scope"
        assert fe.evidence == {}

    def test_explicit_retryable_override(self) -> None:
        fe = FailureEnvelope(
            class_=FailureClass.CONTRACT_INVALID,
            stage="validation",
            reason="bad schema",
            retryable=True,
        )
        assert fe.retryable is True

    def test_explicit_next_action_override(self) -> None:
        fe = FailureEnvelope(
            class_=FailureClass.APPLY_FAILED,
            stage="apply",
            reason="patch failed",
            next_action="custom_action",
        )
        assert fe.next_action == "custom_action"

    def test_to_dict_roundtrip(self) -> None:
        fe = FailureEnvelope(
            class_=FailureClass.VERIFY_FAILED,
            stage="verify",
            reason="assertion failed",
            task_id="t1",
            run_id="r1",
            evidence={"file": "x.py"},
        )
        d = fe.to_dict()
        assert d["failure_class"] == "verify_failed"
        assert d["stage"] == "verify"
        assert d["reason"] == "assertion failed"
        assert d["task_id"] == "t1"
        assert d["run_id"] == "r1"
        assert d["evidence"] == {"file": "x.py"}

    def test_from_dict_basic(self) -> None:
        d = {
            "failure_class": "policy_blocked",
            "stage": "gate",
            "reason": "denied",
            "retryable": False,
            "evidence": {},
            "next_action": "review_policy",
            "task_id": "t2",
            "run_id": "r2",
        }
        fe = FailureEnvelope.from_dict(d)
        assert fe.class_ == FailureClass.POLICY_BLOCKED
        assert fe.stage == "gate"
        assert fe.reason == "denied"
        assert fe.retryable is False
        assert fe.next_action == "review_policy"

    def test_from_dict_empty_class_fallback(self) -> None:
        d = {
            "failure_class": "",
            "stage": "unknown",
            "reason": "fallback",
        }
        fe = FailureEnvelope.from_dict(d)
        assert fe.class_ == FailureClass.POLICY_BLOCKED

    def test_from_dict_missing_fields(self) -> None:
        d = {"failure_class": "manual_required"}
        fe = FailureEnvelope.from_dict(d)
        assert fe.class_ == FailureClass.MANUAL_REQUIRED
        assert fe.stage == "unknown"
        assert fe.reason == "Unknown failure"

    def test_repr(self) -> None:
        fe = FailureEnvelope(
            class_=FailureClass.ENVIRONMENT_MISSING,
            stage="setup",
            reason="missing tool",
        )
        assert "environment_missing" in repr(fe)
        assert "setup" in repr(fe)
        assert "retryable=False" in repr(fe)

    def test_create_failure_helper(self) -> None:
        fe = create_failure(FailureClass.TASK_FAILURE, "exec", "crashed", task_id="t3")
        assert fe.class_ == FailureClass.TASK_FAILURE
        assert fe.task_id == "t3"

    def test_module_aliases(self) -> None:
        assert CONTRACT_INVALID == FailureClass.CONTRACT_INVALID
